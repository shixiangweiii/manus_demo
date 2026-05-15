"""
Web Search Tool - Real web search via Bailian MCP (primary) or DuckDuckGo (fallback).
Web 搜索工具 —— 优先使用百炼 MCP 搜索，回退到 DuckDuckGo（DDGS）。

v11: 替换 v10 的纯 DDGS 实现为 Bailian MCP 优先 + DDGS 回退。
- Bailian MCP（阿里云百炼）提供更丰富的搜索结果（含摘要而非仅 snippet）
- 需要 DASHSCOPE_API_KEY，为空时自动回退到 DDGS
- DDGS 零密钥、零配置回退：保留原有搜索能力
- 错误透传：失败时返回以 "Error:" 开头的字符串，由 ReAct 循环识别处理
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)

# 兼容旧版本 ddgs 可能没有独立 exceptions 子模块的情况
try:
    from ddgs.exceptions import RatelimitException
except ImportError:  # pragma: no cover
    class RatelimitException(Exception):
        """Fallback when ddgs.exceptions.RatelimitException is unavailable."""


class WebSearchTool(BaseTool):
    """
    Web search tool: Bailian MCP (primary) → DuckDuckGo (fallback).
    网络搜索工具：百炼 MCP（优先）→ DuckDuckGo（回退）。
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for current information. Returns ranked results "
            "with title, content, and URL. When you see promising URLs in "
            "search results, use 'fetch_url' to access full page content "
            "for detailed information. For structured real-time data from "
            "public JSON APIs (e.g. weather from wttr.in), 'execute_python' "
            "may be more direct."
            # 网络搜索；搜索结果中的 URL 可用 fetch_url 获取完整页面；
            # 结构化 JSON API 数据（如 wttr.in 天气）用 execute_python 更直接
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                    "default": config.WEB_SEARCH_MAX_RESULTS,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return "Error: web_search requires a non-empty 'query' parameter."

        count = kwargs.get("count") or config.WEB_SEARCH_MAX_RESULTS

        # Try Bailian MCP first (requires DASHSCOPE_API_KEY)
        if config.DASHSCOPE_API_KEY:
            try:
                return await self._bailian_search(query, count)
            except Exception as exc:
                logger.warning("[WebSearchTool] Bailian MCP failed, falling back to DDGS: %s", exc)

        # Fallback to DDGS (zero-config, always available)
        return await self._ddgs_search_and_format(query, count)

    # ------------------------------------------------------------------
    # Bailian MCP backend
    # ------------------------------------------------------------------

    async def _bailian_search(self, query: str, count: int) -> str:
        """Search via Bailian MCP WebSearch server."""
        from tools.mcp_client import BailianMCPClient

        client = BailianMCPClient()
        raw_result = await asyncio.wait_for(
            client.call_tool(
                server_name="WebSearch",
                tool_name="bailian_web_search",
                arguments={"query": query, "count": count},
            ),
            timeout=config.WEB_SEARCH_TIMEOUT,
        )
        logger.info("[WebSearchTool] Bailian MCP result for '%s': %d chars", query, len(raw_result))
        return self._format_bailian_results(query, raw_result)

    @staticmethod
    def _format_bailian_results(query: str, raw_text: str) -> str:
        """
        Format Bailian MCP search results for the LLM.

        Bailian WebSearch returns a structured JSON object:
            {
              "pages": [{snippet, title, url, hostname, hostlogo}, ...],
              "tools": [{type, result}, ...],   # structured data: stock/weather/...
              "request_id": "...",
              "status": 0
            }

        Strategy:
        1. tools[] structured data goes FIRST (highest signal density)
        2. pages[] rendered as compact numbered list (title + snippet + url only)
        3. Drop hostlogo / request_id / status (no information value)
        4. Fallback to legacy list/raw-text passthrough for unexpected shapes
        """
        if not raw_text:
            return f"No results found for: '{query}'"

        # Attempt JSON parse
        try:
            data = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            data = None

        # Branch 1: Bailian object shape — render structured tools first, then pages
        if isinstance(data, dict) and ("pages" in data or "tools" in data):
            lines = [f"Search results for: '{query}'\n"]

            # Priority 1: structured data (stock prices, weather, etc.)
            tools_blocks = data.get("tools") or []
            for t in tools_blocks:
                if not isinstance(t, dict):
                    continue
                t_type = t.get("type", "data")
                t_result = (t.get("result") or "").strip()
                if t_result:
                    lines.append(f"## Structured data ({t_type})")
                    lines.append(t_result)
                    lines.append("")

            # Priority 2: page snippets (drop hostname/hostlogo to save tokens)
            pages = data.get("pages") or []
            if pages:
                lines.append("## Web results")
                for i, p in enumerate(pages, 1):
                    if not isinstance(p, dict):
                        continue
                    title = (p.get("title") or "(no title)").strip()
                    snippet = (p.get("snippet") or "").strip()
                    url = (p.get("url") or "").strip()
                    lines.append(f"{i}. {title}")
                    if snippet:
                        lines.append(f"   {snippet}")
                    if url:
                        lines.append(f"   URL: {url}")
                    lines.append("")

            # If both tools and pages were empty, fall through to raw text
            if len(lines) > 1:
                return "\n".join(lines)

        # Branch 2: legacy list-of-results shape (some Bailian tool versions)
        if isinstance(data, list):
            lines = [f"Search results for: '{query}'\n"]
            for i, item in enumerate(data, 1):
                if not isinstance(item, dict):
                    continue
                title = item.get("title") or item.get("name") or "(no title)"
                content = (
                    item.get("content") or item.get("body")
                    or item.get("snippet") or item.get("abstract") or ""
                )
                url = item.get("url") or item.get("href") or item.get("link") or ""
                lines.append(f"{i}. {title}")
                if content:
                    lines.append(f"   {content}")
                if url:
                    lines.append(f"   URL: {url}")
                lines.append("")
            return "\n".join(lines)

        # Branch 3: plain text fallback — pass through with header
        if not raw_text.startswith("Search results"):
            return f"Search results for: '{query}'\n\n{raw_text}"
        return raw_text

    # ------------------------------------------------------------------
    # DDGS fallback backend (original v10 implementation)
    # ------------------------------------------------------------------

    async def _ddgs_search_and_format(self, query: str, count: int) -> str:
        """Search via DDGS and format results (fallback path)."""
        logger.info("Web search (DDGS fallback): '%s' (max_results=%d)", query, count)

        try:
            results = await asyncio.wait_for(
                self._ddgs_search(query, count),
                timeout=config.WEB_SEARCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return (
                f"Error: web_search timed out after {config.WEB_SEARCH_TIMEOUT}s "
                f"for query='{query}'."
            )
        except RatelimitException as exc:
            return (
                f"Error: web_search rate-limited by DuckDuckGo: {exc}. "
                "Slow down request frequency or retry later."
            )
        except Exception as exc:
            return f"Error: web_search failed: {type(exc).__name__}: {exc}"

        return self._format_results(query, results)

    @staticmethod
    async def _ddgs_search(query: str, max_results: int) -> list[dict[str, Any]]:
        """
        Run synchronous DDGS in a worker thread to avoid blocking the event loop.
        在工作线程中运行同步 DDGS 调用，避免阻塞事件循环。
        """
        from ddgs import DDGS

        def _sync() -> list[dict[str, Any]]:
            with DDGS() as client:
                return list(client.text(query=query, max_results=max_results))

        return await asyncio.to_thread(_sync)

    @staticmethod
    def _format_results(query: str, results: list[dict[str, Any]]) -> str:
        """
        Format DDGS results into a readable, LLM-friendly text block.
        将 DDGS 返回结果格式化为 LLM 易读的文本块。
        """
        if not results:
            return f"No results found for: '{query}'"

        lines = [f"Search results for: '{query}'\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title") or "(no title)"
            snippet = r.get("body") or r.get("snippet") or "(no snippet)"
            url = r.get("href") or r.get("url") or "(no url)"
            lines.append(f"{i}. {title}")
            lines.append(f"   {snippet}")
            lines.append(f"   URL: {url}\n")
        return "\n".join(lines)