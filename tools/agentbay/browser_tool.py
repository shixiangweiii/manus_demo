"""AgentBay BrowserUse tool."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any
from urllib.parse import urlparse

from core.settings import CapabilitySettings
from tools.agentbay.runtime import create_session, delete_session, get_agentbay_sdk
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class AgentBayBrowserTool(BaseTool):
    """Use AgentBay BrowserUse through Playwright CDP."""

    def __init__(
        self,
        settings: CapabilitySettings,
        sandbox_dir: str,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self._settings = settings
        self._sandbox_dir = sandbox_dir
        self._semaphore = semaphore

    @property
    def name(self) -> str:
        return "agentbay_browser"

    @property
    def description(self) -> str:
        return (
            "Open a public URL in an Alibaba Cloud AgentBay browser and return "
            "the page title, text, or a screenshot path."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Public http(s) URL to open."},
                "operation": {
                    "type": "string",
                    "enum": ["title", "text", "screenshot"],
                    "description": "Browser operation. Defaults to title.",
                },
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector for text extraction.",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture a full-page screenshot when operation=screenshot.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Navigation timeout in milliseconds.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters returned for text extraction.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: Any) -> str:
        url = str(kwargs.get("url", "") or "").strip()
        url_error = validate_public_url(url)
        if url_error:
            return f"Error: {url_error}"

        operation = str(kwargs.get("operation") or "title").lower()
        if operation not in {"title", "text", "screenshot"}:
            return "Error: operation must be one of: title, text, screenshot."

        timeout_ms = _bounded_int(
            kwargs.get("timeout_ms"),
            self._settings.agentbay_browser_timeout_ms,
            1000,
            60000,
        )
        max_chars = _bounded_int(kwargs.get("max_chars"), 4000, 200, 12000)
        selector = str(kwargs.get("selector", "") or "").strip()
        full_page = bool(kwargs.get("full_page", False))

        async with self._semaphore:
            handle = None
            result_text = ""
            deleted = True
            delete_error = ""
            try:
                handle = await create_session(
                    self._settings.agentbay_browser_image,
                    self.name,
                    self._settings,
                )
                logger.info("[AgentBayBrowserTool] Created session %s", handle.session_id)
                result_text = await asyncio.to_thread(
                    self._run_browser_sync,
                    handle.session,
                    handle.session_id,
                    url,
                    operation,
                    selector,
                    full_page,
                    timeout_ms,
                    max_chars,
                    self._settings,
                    self._sandbox_dir,
                )
            except Exception as exc:
                sid = getattr(handle, "session_id", "(no session)")
                result_text = f"Error: AgentBay browser operation failed in session {sid}: {exc}"
            finally:
                if handle is not None:
                    deleted, delete_error = await delete_session(handle)
                    if not deleted:
                        logger.warning(
                            "[AgentBayBrowserTool] Failed to delete session %s: %s",
                            handle.session_id,
                            delete_error,
                        )
            if handle is not None:
                result_text += f"\nsession_deleted: {str(deleted).lower()}"
                if delete_error:
                    result_text += f"\nsession_delete_error: {delete_error}"
            return result_text

    @staticmethod
    def _run_browser_sync(
        session: Any,
        session_id: str,
        url: str,
        operation: str,
        selector: str,
        full_page: bool,
        timeout_ms: int,
        max_chars: int,
        settings: CapabilitySettings,
        sandbox_dir: str,
    ) -> str:
        _, _, _, BrowserOption = get_agentbay_sdk(settings)
        from playwright.sync_api import sync_playwright

        initialized = session.browser.initialize(BrowserOption())
        if not initialized:
            raise RuntimeError("browser initialization failed")

        endpoint_url = session.browser.get_endpoint_url()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint_url)
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                if operation == "title":
                    return "\n".join([
                        "AgentBay BrowserUse operation succeeded.",
                        f"session_id: {session_id}",
                        f"title: {page.title()}",
                        f"url: {page.url}",
                    ])

                if operation == "text":
                    if selector:
                        locator = page.locator(selector).first
                        text = locator.inner_text(timeout=timeout_ms)
                    else:
                        text = page.locator("body").inner_text(timeout=timeout_ms)
                    if len(text) > max_chars:
                        text = text[:max_chars] + f"\n[truncated at {max_chars} chars]"
                    return "\n".join([
                        "AgentBay BrowserUse operation succeeded.",
                        f"session_id: {session_id}",
                        f"url: {page.url}",
                        "text:\n" + text.strip(),
                    ])

                screenshot_dir = os.path.join(sandbox_dir, "agentbay_screenshots")
                os.makedirs(screenshot_dir, exist_ok=True)
                filename = f"{uuid.uuid4().hex}.png"
                path = os.path.join(screenshot_dir, filename)
                page.screenshot(path=path, full_page=full_page, timeout=timeout_ms)
                return "\n".join([
                    "AgentBay BrowserUse operation succeeded.",
                    f"session_id: {session_id}",
                    f"url: {page.url}",
                    f"screenshot_path: {path}",
                ])
            finally:
                browser.close()


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def validate_public_url(url: str) -> str | None:
    if not url:
        return "url parameter is required for agentbay_browser."
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "agentbay_browser only accepts http(s) URLs."
    host = (parsed.hostname or "").lower()
    if not host:
        return "agentbay_browser URL must include a hostname."
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return "agentbay_browser cannot access localhost or local network hostnames."

    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return "agentbay_browser cannot access private, loopback, reserved, or multicast IP addresses."
    return None
