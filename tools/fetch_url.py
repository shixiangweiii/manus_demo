"""
Fetch URL Tool — retrieve full page content from a URL via Bailian WebParser MCP.
URL 页面抓取工具 —— 通过百炼 WebParser MCP 获取完整网页内容。

v11: 新增工具，直接解决 web_search 循环重试的核心根因（缺少 URL 页面内容抓取能力）。
- LLM 在搜索结果中看到 URL 后，可调用 fetch_url 获取完整页面内容
- 需要配置 DASHSCOPE_API_KEY（百炼 MCP 认证）
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
    Fetch full page content from a URL using Bailian WebParser MCP.
    URL 页面内容抓取工具（基于百炼 MCP WebParser）。

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

        if not config.DASHSCOPE_API_KEY:
            return "Error: fetch_url requires DASHSCOPE_API_KEY to be configured. Use web_search for snippet-level results."

        try:
            from tools.mcp_client import BailianMCPClient

            client = BailianMCPClient()
            # Double timeout for page fetch (pages can be larger than search results)
            fetch_timeout = config.WEB_SEARCH_TIMEOUT * 2

            result = await asyncio.wait_for(
                client.call_tool(
                    server_name="WebParser",
                    tool_name="bailian_web_parser",
                    arguments={"url": url, "format": format_type},
                ),
                timeout=fetch_timeout,
            )

            # Truncate very long pages to prevent context explosion
            max_len = config.FETCH_URL_MAX_CONTENT_LENGTH
            if len(result) > max_len:
                result = result[:max_len] + f"\n\n[Content truncated at {max_len} characters]"

            logger.info("[FetchUrlTool] Fetched '%s': %d chars (format=%s)", url, len(result), format_type)
            return result

        except asyncio.TimeoutError:
            return f"Error: fetch_url timed out after {config.WEB_SEARCH_TIMEOUT * 2}s for URL='{url}'."
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            # httpx timeout exceptions should be classified as timeout errors
            exc_name = type(exc).__name__
            if exc_name.endswith("Timeout") or exc_name.endswith("timeout"):
                return f"Error: fetch_url timed out for URL='{url}': {exc_name}: {exc}"
            return f"Error: fetch_url failed: {exc_name}: {exc}"