"""
Tests for the WebSearchTool (v10 - real DDGS-backed implementation).
WebSearch 工具测试（v10 真实搜索实现）。

All tests are mock-based; no real network calls. Covers:
- _format_results 输出格式与字段兼容性
- execute 成功/超时/限速/通用异常/空 query 路径
- query 与 max_results 透传给 DDGS
- traced_execute 与 execute 的契约一致性（TRACING_ENABLED=false）
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 保证项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.web_search import RatelimitException, WebSearchTool


# ======================================================================
# Helper: a fake DDGS class supporting context-manager + .text()
# 帮助类：模拟 DDGS 的 context-manager 行为及 .text() 方法
# ======================================================================

class _FakeDDGS:
    """Drop-in replacement for ddgs.DDGS used as a `with`-context client."""

    def __init__(self, results=None, exc: Exception | None = None, sleep: float = 0.0):
        self._results = results or []
        self._exc = exc
        self._sleep = sleep
        self.last_call: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def text(self, query: str, max_results: int):
        self.last_call = {"query": query, "max_results": max_results}
        if self._sleep:
            import time
            time.sleep(self._sleep)
        if self._exc is not None:
            raise self._exc
        # DDGS 的 text() 返回生成器；这里直接返回 list 也兼容（execute 用 list(...) 包裹）
        return iter(self._results)


# ======================================================================
# _format_results
# ======================================================================

class TestFormatResults:

    def test_basic(self):
        results = [
            {"title": "Foo", "body": "Foo summary", "href": "https://foo.com"},
            {"title": "Bar", "body": "Bar summary", "href": "https://bar.com"},
        ]
        text = WebSearchTool._format_results("hello", results)

        assert "Search results for: 'hello'" in text
        assert "1. Foo" in text
        assert "Foo summary" in text
        assert "URL: https://foo.com" in text
        assert "2. Bar" in text
        assert "URL: https://bar.com" in text

    def test_empty_returns_no_results_message(self):
        text = WebSearchTool._format_results("nothing", [])
        assert text == "No results found for: 'nothing'"
        # 关键：不以 "Error:" 开头，因为空结果不算错误
        assert not text.startswith("Error:")

    def test_field_fallback_snippet_url(self):
        # 模拟旧字段命名（`snippet` / `url`）的兼容情况
        results = [
            {"title": "T", "snippet": "S-old", "url": "https://old.example"},
        ]
        text = WebSearchTool._format_results("q", results)
        assert "S-old" in text
        assert "URL: https://old.example" in text

    def test_field_fallback_missing_keys(self):
        results = [{}]  # 完全缺字段
        text = WebSearchTool._format_results("q", results)
        assert "(no title)" in text
        assert "(no snippet)" in text
        assert "(no url)" in text


# ======================================================================
# execute - guard against empty query
# ======================================================================

class TestExecuteGuards:

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        tool = WebSearchTool()
        result = await tool.execute(query="")
        assert result.startswith("Error:")
        assert "non-empty" in result.lower() or "query" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_query_returns_error(self):
        tool = WebSearchTool()
        result = await tool.execute(query="   \t\n  ")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_missing_query_returns_error(self):
        tool = WebSearchTool()
        result = await tool.execute()  # 没传 query
        assert result.startswith("Error:")


# ======================================================================
# execute - success path with mocked DDGS
# ======================================================================

class TestExecuteSuccess:

    @pytest.mark.asyncio
    async def test_success_formats_results(self, monkeypatch):
        fake_results = [
            {"title": "Python.org", "body": "official site", "href": "https://python.org"},
            {"title": "Real Python", "body": "tutorials", "href": "https://realpython.com"},
        ]
        fake = _FakeDDGS(results=fake_results)

        # 通过替换 ddgs.DDGS 模块属性来拦截懒导入
        import ddgs
        monkeypatch.setattr(ddgs, "DDGS", lambda: fake)

        tool = WebSearchTool()
        result = await tool.execute(query="python")
        assert "Search results for: 'python'" in result
        assert "Python.org" in result
        assert "Real Python" in result
        # query/max_results 正确传递
        assert fake.last_call is not None
        assert fake.last_call["query"] == "python"

    @pytest.mark.asyncio
    async def test_query_and_max_results_passthrough(self, monkeypatch):
        fake = _FakeDDGS(results=[])
        import ddgs
        monkeypatch.setattr(ddgs, "DDGS", lambda: fake)
        # 临时改 max_results 验证透传
        import config as cfg
        monkeypatch.setattr(cfg, "WEB_SEARCH_MAX_RESULTS", 3)

        tool = WebSearchTool()
        await tool.execute(query="rust")
        assert fake.last_call == {"query": "rust", "max_results": 3}


# ======================================================================
# execute - error paths
# ======================================================================

class TestExecuteErrors:

    @pytest.mark.asyncio
    async def test_ratelimit_returns_friendly_error(self, monkeypatch):
        fake = _FakeDDGS(exc=RatelimitException("too many requests"))
        import ddgs
        monkeypatch.setattr(ddgs, "DDGS", lambda: fake)

        tool = WebSearchTool()
        result = await tool.execute(query="overloaded")
        assert result.startswith("Error:")
        assert "rate-limited" in result.lower()
        assert "too many requests" in result.lower()

    @pytest.mark.asyncio
    async def test_generic_exception_returns_error(self, monkeypatch):
        fake = _FakeDDGS(exc=ValueError("bad arg"))
        import ddgs
        monkeypatch.setattr(ddgs, "DDGS", lambda: fake)

        tool = WebSearchTool()
        result = await tool.execute(query="anything")
        assert result.startswith("Error:")
        # 错误类型与消息都被透出
        assert "ValueError" in result
        assert "bad arg" in result

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, monkeypatch):
        # 用 asyncio 永久阻塞模拟超时
        async def hang(*args, **kwargs):
            await asyncio.sleep(60)

        monkeypatch.setattr(WebSearchTool, "_ddgs_search", staticmethod(hang))
        import config as cfg
        monkeypatch.setattr(cfg, "WEB_SEARCH_TIMEOUT", 1)  # 1 秒超时便于测试

        tool = WebSearchTool()
        result = await tool.execute(query="slow")
        assert result.startswith("Error:")
        assert "timed out" in result.lower()
        assert "1s" in result


# ======================================================================
# Tool interface contract
# 工具契约：name / parameters_schema / traced_execute 一致性
# ======================================================================

class TestToolContract:

    def test_name_is_web_search(self):
        assert WebSearchTool().name == "web_search"

    def test_parameters_schema_has_query(self):
        schema = WebSearchTool().parameters_schema
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert "query" in schema["required"]

    def test_description_is_real_not_simulated(self):
        desc = WebSearchTool().description.lower()
        # mock 时代的标记词应当被移除
        assert "simulated" not in desc
        assert "in this demo" not in desc
        # 应明确提及 DuckDuckGo 或真实搜索
        assert "duckduckgo" in desc

    @pytest.mark.asyncio
    async def test_traced_execute_passthrough_when_disabled(self, monkeypatch):
        """TRACING_ENABLED=false 时 traced_execute 直通 execute。"""
        fake = _FakeDDGS(results=[
            {"title": "X", "body": "Y", "href": "https://z"}
        ])
        import ddgs
        monkeypatch.setattr(ddgs, "DDGS", lambda: fake)
        import config as cfg
        monkeypatch.setattr(cfg, "TRACING_ENABLED", False)

        tool = WebSearchTool()
        result = await tool.traced_execute(query="ping")
        assert "X" in result
        assert "https://z" in result
