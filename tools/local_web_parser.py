"""
Local WebParser for fetch_url.
本地网页解析器，用于替代外部 WebParser MCP 作为 fetch_url 主路径。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as html_to_markdown
import trafilatura

import config

logger = logging.getLogger(__name__)


ALLOWED_SCHEMES = {"http", "https"}
HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
JSON_CONTENT_TYPES = {"application/json"}
XML_CONTENT_TYPES = {"application/xml", "text/xml", "application/rss+xml", "application/atom+xml"}
REMOVED_TAGS = ("script", "style", "nav", "footer", "header", "aside", "form", "noscript")
BENCHMARK_PROXY_NET = ipaddress.ip_network("198.18.0.0/15")


@dataclass(slots=True)
class LocalWebParserResult:
    content: str
    final_url: str
    title: str
    backend: str


class LocalWebParserError(RuntimeError):
    """Raised when local web parsing cannot produce page content."""


class LocalWebParser:
    """Download and extract readable page content without external MCP calls."""

    _robots_cache: dict[str, RobotFileParser] = {}
    _fetch_cache: OrderedDict[str, tuple[str, str]] = OrderedDict()
    _cache_lock = asyncio.Lock()

    async def fetch(self, url: str, format_type: str = "markdown") -> LocalWebParserResult:
        html, final_url = await self.fetch_html(url)
        result = self.extract_content(html, final_url, format_type=format_type)

        if self._is_too_short(result.content):
            fallback = self._extract_with_bs4(html, final_url, format_type=format_type)
            if fallback and len(fallback.content.strip()) > len(result.content.strip()):
                result = fallback

        if self._is_too_short(result.content) and config.LOCAL_WEBPARSER_BROWSER_FALLBACK:
            rendered_html, rendered_url = await self.fetch_rendered_html(final_url)
            rendered_result = self.extract_content(
                rendered_html,
                rendered_url,
                format_type=format_type,
                backend_prefix="local:playwright+",
            )
            if self._is_too_short(rendered_result.content):
                rendered_fallback = self._extract_with_bs4(
                    rendered_html,
                    rendered_url,
                    format_type=format_type,
                    backend_prefix="local:playwright+",
                )
                if rendered_fallback and len(rendered_fallback.content.strip()) > len(rendered_result.content.strip()):
                    rendered_result = rendered_fallback
            if len(rendered_result.content.strip()) > len(result.content.strip()):
                result = rendered_result

        if not result.content.strip():
            raise LocalWebParserError("no readable content extracted")

        return result

    async def fetch_html(self, url: str) -> tuple[str, str]:
        parsed = await self._ensure_public_url(url)
        cached = await self._get_cached_fetch(parsed.geturl())
        if cached is not None:
            return cached

        if config.LOCAL_WEBPARSER_RESPECT_ROBOTS:
            allowed = await self._robots_allowed(url)
            if not allowed:
                raise LocalWebParserError("robots.txt disallows fetching this URL")

        timeout_seconds = max(0.1, float(config.LOCAL_WEBPARSER_TIMEOUT))
        timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(timeout_seconds, 10.0),
            read=timeout_seconds,
            write=timeout_seconds,
            pool=min(timeout_seconds, 10.0),
        )
        headers = {
            "User-Agent": config.LOCAL_WEBPARSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain,application/xml;q=0.9,*/*;q=0.1",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        max_bytes = max(1, int(config.LOCAL_WEBPARSER_MAX_BYTES))
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=5,
            timeout=timeout,
            headers=headers,
        ) as client:
            async with client.stream("GET", parsed.geturl()) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not self._is_supported_content_type(content_type):
                    raise LocalWebParserError(f"unsupported content-type: {content_type}")

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise LocalWebParserError(f"response too large: exceeded {max_bytes} bytes")
                    chunks.append(chunk)

        final_url = str(response.url)
        await self._ensure_public_url(final_url)
        encoding = response.encoding or "utf-8"
        html = b"".join(chunks).decode(encoding, errors="replace")
        fetched = (html, final_url)
        await self._set_cached_fetch(parsed.geturl(), fetched)
        return fetched

    def extract_content(
        self,
        html: str,
        url: str,
        format_type: str = "markdown",
        backend_prefix: str = "local:",
    ) -> LocalWebParserResult:
        normalized_format = self._normalize_format(format_type)
        raw_content = html.strip()
        if raw_content.startswith(("{", "[")):
            parsed_json = self._format_json_content(raw_content)
            if parsed_json:
                return LocalWebParserResult(
                    content=parsed_json,
                    final_url=url,
                    title=self._extract_title(html),
                    backend="local:raw-json",
                )
        if raw_content and not self._looks_like_markup(raw_content):
            return LocalWebParserResult(
                content=self._compact_content(raw_content),
                final_url=url,
                title="",
                backend="local:raw-text",
            )

        output_format = "markdown" if normalized_format == "markdown" else "txt"

        extracted = trafilatura.extract(
            html,
            url=url,
            output_format=output_format,
            with_metadata=True,
            include_links=True,
            include_tables=True,
        )
        content = (extracted or "").strip()
        if not content:
            fallback = self._extract_with_bs4(html, url, format_type=normalized_format, backend_prefix=backend_prefix)
            if fallback:
                return fallback

        return LocalWebParserResult(
            content=content,
            final_url=url,
            title=self._extract_title(html),
            backend=f"{backend_prefix}trafilatura",
        )

    async def fetch_rendered_html(self, url: str) -> tuple[str, str]:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - import availability depends on environment
            raise LocalWebParserError(f"Playwright fallback unavailable: {type(exc).__name__}: {exc}") from exc

        timeout_ms = int(max(1.0, float(config.LOCAL_WEBPARSER_TIMEOUT)) * 1000)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=config.LOCAL_WEBPARSER_USER_AGENT)
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
                except Exception:
                    logger.debug("[LocalWebParser] networkidle wait skipped for %s", url, exc_info=True)
                html = await page.content()
                final_url = page.url
            finally:
                await browser.close()
        return html, final_url

    def format_result(self, result: LocalWebParserResult) -> str:
        title = result.title.strip() or "(untitled)"
        content = result.content.strip()
        return (
            f"[fetch_url metadata]\n"
            f"final_url: {result.final_url}\n"
            f"title: {title}\n"
            f"content_length: {len(content)}\n"
            f"parser_backend: {result.backend}\n\n"
            f"{content}"
        )

    def add_short_content_warning(self, content: str, measured_content: str | None = None) -> str:
        measured = content if measured_content is None else measured_content
        stripped_len = len(measured.strip())
        if not self._is_too_short(measured):
            return content
        return (
            f"{content}\n\n"
            f"[Warning: fetch_url returned only {stripped_len} characters. "
            "The page may be blocked, script-rendered, or poorly parsed; do not treat this as complete page content.]"
        )

    def _extract_with_bs4(
        self,
        html: str,
        url: str,
        format_type: str = "markdown",
        backend_prefix: str = "local:",
    ) -> LocalWebParserResult | None:
        soup = BeautifulSoup(html, "html.parser")
        title = self._title_from_soup(soup)
        for tag in soup(REMOVED_TAGS):
            tag.decompose()

        node = soup.find("main") or soup.find("article") or soup.body or soup
        for tag in node.find_all(["a", "img"]):
            attr_name = "href" if tag.name == "a" else "src"
            attr_value = tag.get(attr_name)
            if attr_value:
                tag[attr_name] = urljoin(url, attr_value)

        normalized_format = self._normalize_format(format_type)
        if normalized_format == "markdown":
            content = html_to_markdown(
                str(node),
                heading_style="ATX",
                bullets="-",
                strip=["script", "style"],
            )
        else:
            content = node.get_text("\n", strip=True)

        content = self._compact_content(content)
        if not content:
            return None
        return LocalWebParserResult(
            content=content,
            final_url=url,
            title=title,
            backend=f"{backend_prefix}bs4-markdownify" if normalized_format == "markdown" else f"{backend_prefix}bs4-text",
        )

    async def _ensure_public_url(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            raise ValueError("fetch_url only supports http and https URLs.")
        if not parsed.hostname:
            raise ValueError("fetch_url requires a URL with a hostname.")

        host = parsed.hostname.strip().lower().rstrip(".")
        if host in {"localhost", "0", "0.0.0.0"} or host.endswith(".localhost"):
            raise ValueError("fetch_url blocks localhost/private URLs for SSRF protection.")

        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None

        if ip is not None:
            if self._is_blocked_ip(ip):
                raise ValueError("fetch_url blocks localhost/private URLs for SSRF protection.")
            return parsed

        infos = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or self._default_port(parsed.scheme), type=socket.SOCK_STREAM)
        for info in infos:
            resolved_ip = ipaddress.ip_address(info[4][0])
            if self._is_blocked_ip(resolved_ip, allow_benchmark_proxy=True):
                raise ValueError("fetch_url blocks localhost/private URLs for SSRF protection.")
        return parsed

    async def _robots_allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots_cache.get(origin)
        if parser is None:
            robots_url = urljoin(origin, "/robots.txt")
            parser = RobotFileParser(robots_url)
            try:
                async with httpx.AsyncClient(timeout=min(float(config.LOCAL_WEBPARSER_TIMEOUT), 10.0)) as client:
                    response = await client.get(robots_url, headers={"User-Agent": config.LOCAL_WEBPARSER_USER_AGENT})
                if response.status_code >= 500:
                    return False
                if response.status_code == 404:
                    parser.parse([])
                elif response.status_code >= 400:
                    parser.parse([])
                else:
                    parser.parse(response.text.splitlines())
            except Exception:
                return False
            self._robots_cache[origin] = parser
        return parser.can_fetch(config.LOCAL_WEBPARSER_USER_AGENT, url)

    @staticmethod
    def _normalize_format(format_type: str) -> str:
        return "text" if str(format_type).lower() == "text" else "markdown"

    @staticmethod
    def _default_port(scheme: str) -> int:
        return 443 if scheme == "https" else 80

    @staticmethod
    def _is_supported_content_type(content_type: str) -> bool:
        media_type = content_type.split(";", 1)[0].strip().lower()
        return (
            media_type in HTML_CONTENT_TYPES
            or media_type in JSON_CONTENT_TYPES
            or media_type in XML_CONTENT_TYPES
            or media_type.startswith("text/")
            or media_type.endswith("+json")
            or media_type.endswith("+xml")
        )

    @staticmethod
    def _is_blocked_ip(ip: ipaddress._BaseAddress, allow_benchmark_proxy: bool = False) -> bool:
        if allow_benchmark_proxy and ip.version == 4 and ip in BENCHMARK_PROXY_NET:
            return False
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        )

    @staticmethod
    def _extract_title(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        return LocalWebParser._title_from_soup(soup)

    @staticmethod
    def _title_from_soup(soup: BeautifulSoup) -> str:
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        heading = soup.find(["h1", "h2"])
        return heading.get_text(" ", strip=True) if heading else ""

    @staticmethod
    def _compact_content(content: str) -> str:
        lines = [line.rstrip() for line in content.splitlines()]
        compacted: list[str] = []
        blank = False
        for line in lines:
            if not line.strip():
                if not blank:
                    compacted.append("")
                blank = True
                continue
            compacted.append(line)
            blank = False
        return "\n".join(compacted).strip()

    @staticmethod
    def _is_too_short(content: str) -> bool:
        min_len = max(0, int(config.LOCAL_WEBPARSER_MIN_CONTENT_LENGTH))
        return bool(min_len and len(content.strip()) < min_len)

    @staticmethod
    def _format_json_content(content: str) -> str:
        try:
            return json.dumps(json.loads(content), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return ""

    @staticmethod
    def _looks_like_markup(content: str) -> bool:
        return content.lstrip().startswith("<")

    @classmethod
    async def _get_cached_fetch(cls, url: str) -> tuple[str, str] | None:
        max_size = max(0, int(config.LOCAL_WEBPARSER_CACHE_SIZE))
        if max_size == 0:
            return None
        async with cls._cache_lock:
            cached = cls._fetch_cache.get(url)
            if cached is None:
                return None
            cls._fetch_cache.move_to_end(url)
            return cached

    @classmethod
    async def _set_cached_fetch(cls, url: str, fetched: tuple[str, str]) -> None:
        max_size = max(0, int(config.LOCAL_WEBPARSER_CACHE_SIZE))
        if max_size == 0:
            return
        async with cls._cache_lock:
            cls._fetch_cache[url] = fetched
            cls._fetch_cache.move_to_end(url)
            while len(cls._fetch_cache) > max_size:
                cls._fetch_cache.popitem(last=False)
