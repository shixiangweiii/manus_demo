"""
Web Search Tool - Real web search via DuckDuckGo (DDGS).
Web 搜索工具 —— 基于 DuckDuckGo（DDGS）的真实网络搜索。

v10: 替换 v1 的 mock 实现为基于 ddgs 包的真实搜索。
- 零密钥、零配置：无需注册任何 API Key
- 错误透传：失败时返回以 "Error:" 开头的字符串，由 ReAct 循环识别处理
- 同步底层包 + asyncio.to_thread 适配项目 async 全栈
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)

# 兼容旧版本 ddgs 可能没有独立 exceptions 子模块的情况：
# 顶部预定义本地占位类，懒导入时若真实模块存在会被覆盖；占位类继承 Exception，仍可被通用 except 捕获。
try:
    from ddgs.exceptions import RatelimitException
except ImportError:  # pragma: no cover - 兼容兜底
    class RatelimitException(Exception):
        """Fallback when ddgs.exceptions.RatelimitException is unavailable."""


class WebSearchTool(BaseTool):
    """
    Real web search tool backed by DuckDuckGo (DDGS).
    基于 DuckDuckGo（DDGS）的真实网络搜索工具。
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for current information using DuckDuckGo. "
            "Returns up to N ranked results, each with title, snippet, and URL. "
            "Use this for general knowledge questions, recent events, fact lookups, "
            "and discovering URLs to fetch. For structured real-time data exposed "
            "as a public JSON API (e.g. weather from wttr.in), `execute_python` "
            "may be more direct."
            # 真实网络搜索；通用知识/近期事件/事实查询优先用此工具；
            # 结构化 JSON API 数据（如 wttr.in 天气）用 execute_python 更直接
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string",  # 搜索查询字符串
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return "Error: web_search requires a non-empty 'query' parameter."

        logger.info("Web search: '%s' (max_results=%d)", query, config.WEB_SEARCH_MAX_RESULTS)

        try:
            results = await asyncio.wait_for(
                self._ddgs_search(query, config.WEB_SEARCH_MAX_RESULTS),
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
        from ddgs import DDGS  # 懒导入：只在真正调用时引入

        def _sync() -> list[dict[str, Any]]:
            with DDGS() as client:
                return list(client.text(query=query, max_results=max_results))

        return await asyncio.to_thread(_sync)

    @staticmethod
    def _format_results(query: str, results: list[dict[str, Any]]) -> str:
        """
        Format DDGS results into a readable, LLM-friendly text block.
        将 DDGS 返回结果格式化为 LLM 易读的文本块。
        DDGS v9 字段：title / body（摘要）/ href（URL）；做 .get 链式兜底以防版本差异。
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
