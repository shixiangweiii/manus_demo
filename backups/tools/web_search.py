"""
Web Search Tool - Simulated web search for the Manus Demo.
Web 搜索工具 —— Manus Demo 的模拟网络搜索。

Returns mock search results by default. Can be extended to call a real
search API (e.g., SerpAPI, Tavily, DuckDuckGo) by replacing the
`_mock_search` method.
默认返回预设的 mock 搜索结果。
可通过替换 `_mock_search` 方法对接真实搜索 API（如 SerpAPI、Tavily、DuckDuckGo）。
"""

from __future__ import annotations

import logging
from typing import Any

from tools.base import BaseTool

logger = logging.getLogger(__name__)

# Pre-defined mock results for common queries (educational demo)
# 预定义的 mock 搜索结果，按关键词分类（教学 demo 用）
MOCK_RESULTS: dict[str, list[dict[str, str]]] = {
    "default": [
        {
            "title": "Search Result 1",
            "snippet": "This is a simulated search result. In a production system, "
                       "this would be replaced by real web search API results.",
            "url": "https://example.com/result1",
        },
        {
            "title": "Search Result 2",
            "snippet": "Another simulated result providing additional context "
                       "for the agent to reason about.",
            "url": "https://example.com/result2",
        },
    ],
    "python": [
        {
            "title": "Python Official Documentation",
            "snippet": "Python is a high-level, interpreted programming language. "
                       "It supports multiple programming paradigms including procedural, "
                       "object-oriented, and functional programming.",
            "url": "https://docs.python.org",
        },
        {
            "title": "Python Tutorial - W3Schools",
            "snippet": "Python can be used for web development, data analysis, "
                       "artificial intelligence, scientific computing, and more.",
            "url": "https://www.w3schools.com/python/",
        },
    ],
}


class WebSearchTool(BaseTool):
    """
    Simulated web search tool that returns mock results.
    模拟网络搜索工具，返回预设的 mock 结果。
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for information on a given query. "
            "Returns a list of relevant search results with titles and snippets."
            # 根据查询词搜索网络信息，返回标题和摘要列表
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
        query = kwargs.get("query", "")
        logger.info("Web search: '%s'", query)

        results = self._mock_search(query)

        # Format results as readable text（将结果格式化为 LLM 可读的文本）
        lines = [f"Search results for: '{query}'\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   {r['snippet']}")
            lines.append(f"   URL: {r['url']}\n")
        return "\n".join(lines)

    @staticmethod
    def _mock_search(query: str) -> list[dict[str, str]]:
        """
        Return mock results. Override this for real search integration.
        返回 mock 结果。替换此方法可对接真实搜索 API。
        匹配逻辑：查询词小写后包含 MOCK_RESULTS 的 key 则返回对应结果，否则返回默认结果。
        """
        query_lower = query.lower()
        for keyword, results in MOCK_RESULTS.items():
            if keyword in query_lower:
                return results
        return MOCK_RESULTS["default"]
