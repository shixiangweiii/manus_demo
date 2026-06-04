"""
Fetch URL Tool — retrieve full page content from a URL via local WebParser.
URL 页面抓取工具 —— 通过本地 WebParser 获取完整网页内容。

v11: 新增工具，直接解决 web_search 循环重试的核心根因（缺少 URL 页面内容抓取能力）。
v13: 默认改为本地 httpx + trafilatura 解析，百炼 WebParser 仅作为可选 fallback。
- LLM 在搜索结果中看到 URL 后，可调用 fetch_url 获取完整页面内容
- 返回内容超过 FETCH_URL_MAX_CONTENT_LENGTH 时截断，防止上下文膨胀
- 错误透传：失败时返回以 "Error:" 开头的字符串
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class FetchUrlTool(BaseTool):
    """
    Fetch full page content from a URL using the local WebParser.
    URL 页面内容抓取工具（本地解析优先，百炼 WebParser 可选兜底）。

    Use after web_search to access specific pages found in search results.
    """

    @property
    def name(self) -> str:
        return "fetch_url"

    @property
    def description(self) -> str:
        return (
            "Fetch the full content of a web page by URL. Returns the page's "
            "main text content in markdown format. Use this after web_search "
            "when you find a promising URL and need detailed information from "
            "that page. Do NOT use for URLs you haven't verified — always "
            "search first, then fetch specific pages."
            # URL 页面内容抓取；搜索结果中的 URL 可用此工具获取完整页面内容；
            # 先搜索再抓取，不要盲目抓取未经确认的 URL
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the web page to fetch",
                },
                "format": {
                    "type": "string",
                    "description": "Output format: 'markdown' (default) or 'text'",
                    "default": "markdown",
                },
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: Any) -> str:
        url = (kwargs.get("url") or "").strip()
        if not url:
            return "Error: fetch_url requires a non-empty 'url' parameter."

        format_type = kwargs.get("format", "markdown")

        if config.LOCAL_WEBPARSER_ENABLED:
            local_result = await self._execute_local(url, format_type)
            if not local_result.startswith("Error:"):
                return local_result
            if not config.LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN:
                return local_result

            bailian_result = await self._execute_bailian(url, format_type)
            if not bailian_result.startswith("Error:"):
                return bailian_result
            return (
                f"Error: local parser failed; Bailian fallback failed: "
                f"{local_result.removeprefix('Error: ').strip()} | "
                f"{bailian_result.removeprefix('Error: ').strip()}"
            )

        return await self._execute_bailian(url, format_type)

    async def _execute_local(self, url: str, format_type: str) -> str:
        try:
            from tools.local_web_parser import LocalWebParser

            parser = LocalWebParser()
            result = await parser.fetch(url, format_type=format_type)
            content = parser.format_result(result)
            content = parser.add_short_content_warning(content, measured_content=result.content)
            content = self._truncate(content)
            logger.info(
                "[FetchUrlTool] Locally fetched '%s': %d chars (format=%s, backend=%s)",
                url,
                len(content),
                format_type,
                result.backend,
            )
            return content
        except asyncio.TimeoutError:
            return f"Error: fetch_url failed locally: timed out after {config.LOCAL_WEBPARSER_TIMEOUT}s for URL='{url}'."
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            exc_name = type(exc).__name__
            if exc_name.endswith("Timeout") or exc_name.endswith("timeout"):
                return f"Error: fetch_url failed locally: timed out for URL='{url}': {exc_name}: {exc}"
            return f"Error: fetch_url failed locally: {exc_name}: {exc}"

    async def _execute_bailian(self, url: str, format_type: str) -> str:
        if not config.DASHSCOPE_API_KEY:
            return "Error: fetch_url requires DASHSCOPE_API_KEY to use Bailian WebParser fallback. Use local WebParser or web_search for snippet-level results."

        try:
            from tools.mcp_client import BailianMCPClient

            client = BailianMCPClient()
            fetch_timeout = config.WEB_SEARCH_TIMEOUT * 2
            result = await asyncio.wait_for(
                client.call_tool(
                    server_name="WebParser",
                    tool_name="bailian_web_parser",
                    arguments={"url": url, "format": format_type},
                    timeout=fetch_timeout,
                ),
                timeout=fetch_timeout,
            )

            result = self._add_mcp_short_content_warning(result)
            result = self._truncate(result)
            logger.info("[FetchUrlTool] Fetched '%s' via Bailian: %d chars (format=%s)", url, len(result), format_type)
            return result

        except asyncio.TimeoutError:
            return f"Error: fetch_url timed out after {config.WEB_SEARCH_TIMEOUT * 2}s for URL='{url}'."
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            exc_name = type(exc).__name__
            if exc_name.endswith("Timeout") or exc_name.endswith("timeout"):
                return f"Error: fetch_url timed out for URL='{url}': {exc_name}: {exc}"
            return f"Error: fetch_url failed: {exc_name}: {exc}"

    @staticmethod
    def _add_mcp_short_content_warning(result: str) -> str:
        short_len = max(0, config.FETCH_URL_SHORT_CONTENT_WARNING_LENGTH)
        if short_len and 0 < len(result.strip()) < short_len:
            return (
                f"{result}\n\n"
                f"[Warning: fetch_url returned only {len(result.strip())} characters. "
                "The page may be blocked, rate-limited, or poorly parsed; do not treat this as complete page content.]"
            )
        return result

    @staticmethod
    def _truncate(result: str) -> str:
        max_len = config.FETCH_URL_MAX_CONTENT_LENGTH
        if len(result) > max_len:
            return result[:max_len] + f"\n\n[Content truncated at {max_len} characters]"
        return result
