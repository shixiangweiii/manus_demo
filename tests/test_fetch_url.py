"""
Tests for FetchUrlTool with local WebParser primary path.
URL 页面抓取工具测试：本地解析优先，百炼 WebParser 可选兜底。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.fetch_url import FetchUrlTool
from tools.local_web_parser import LocalWebParser, LocalWebParserResult
from tools.mcp_client import BailianMCPClient


class TestExecuteGuards:

    @pytest.mark.asyncio
    async def test_empty_url_returns_error(self):
        result = await FetchUrlTool().execute(url="")
        assert result.startswith("Error:")
        assert "non-empty" in result.lower() or "url" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_url_returns_error(self):
        result = await FetchUrlTool().execute(url="   \t\n  ")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_missing_url_returns_error(self):
        result = await FetchUrlTool().execute()
        assert result.startswith("Error:")


class TestExecuteLocalSuccess:

    @pytest.mark.asyncio
    async def test_default_local_fetch_does_not_require_dashscope_key(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", True)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "")
        monkeypatch.setattr(LocalWebParser, "fetch", AsyncMock(return_value=_local_result("# Example\n\nFull content.")))

        result = await FetchUrlTool().execute(url="https://example.com")

        assert "parser_backend: local:trafilatura" in result
        assert "Example" in result
        assert not result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_format_parameter_text_passed_to_local_parser(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", True)
        mock_fetch = AsyncMock(return_value=_local_result("Plain text content"))
        monkeypatch.setattr(LocalWebParser, "fetch", mock_fetch)

        result = await FetchUrlTool().execute(url="https://example.com", format="text")

        assert "Plain text content" in result
        assert mock_fetch.call_args.kwargs["format_type"] == "text"

    @pytest.mark.asyncio
    async def test_local_short_content_gets_warning_marker(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", True)
        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_MIN_CONTENT_LENGTH", 500)
        monkeypatch.setattr(LocalWebParser, "fetch", AsyncMock(return_value=_local_result("short")))

        result = await FetchUrlTool().execute(url="https://example.com")

        assert "short" in result
        assert "Warning:" in result
        assert "not treat this as complete page content" in result

    @pytest.mark.asyncio
    async def test_content_truncation(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", True)
        monkeypatch.setattr(cfg, "FETCH_URL_MAX_CONTENT_LENGTH", 100)
        monkeypatch.setattr(LocalWebParser, "fetch", AsyncMock(return_value=_local_result("A" * 500)))

        result = await FetchUrlTool().execute(url="https://example.com")

        assert len(result) < 180
        assert "truncated" in result.lower()


class TestExecuteLocalErrorsAndFallback:

    @pytest.mark.asyncio
    async def test_local_failure_returns_error_when_bailian_fallback_disabled(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", True)
        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN", False)
        monkeypatch.setattr(LocalWebParser, "fetch", AsyncMock(side_effect=RuntimeError("blocked")))

        result = await FetchUrlTool().execute(url="https://example.com")

        assert result.startswith("Error:")
        assert "fetch_url failed locally" in result
        assert "blocked" in result

    @pytest.mark.asyncio
    async def test_bailian_fallback_used_when_enabled(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", True)
        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN", True)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(LocalWebParser, "fetch", AsyncMock(side_effect=RuntimeError("local parse failed")))

        mock_call_tool = AsyncMock(return_value="# Bailian content")
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        result = await FetchUrlTool().execute(url="https://example.com")

        assert "Bailian content" in result
        assert not result.startswith("Error:")
        assert mock_call_tool.call_args.kwargs["server_name"] == "WebParser"

    @pytest.mark.asyncio
    async def test_combined_error_when_local_and_bailian_fallback_fail(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", True)
        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_FALLBACK_TO_BAILIAN", True)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(LocalWebParser, "fetch", AsyncMock(side_effect=RuntimeError("local parse failed")))
        monkeypatch.setattr(BailianMCPClient, "call_tool", AsyncMock(side_effect=RuntimeError("429 Too Many Requests")))

        result = await FetchUrlTool().execute(url="https://limited.example.com")

        assert result.startswith("Error: local parser failed; Bailian fallback failed:")
        assert "local parse failed" in result
        assert "429 Too Many Requests" in result


class TestExecuteBailianCompatibility:

    @pytest.mark.asyncio
    async def test_local_disabled_uses_bailian_path(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", False)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        mock_call_tool = AsyncMock(return_value="Full page content")
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        result = await FetchUrlTool().execute(url="https://example.com")

        assert "Full page content" in result
        assert "Warning:" in result
        assert mock_call_tool.call_args.kwargs["arguments"]["format"] == "markdown"

    @pytest.mark.asyncio
    async def test_local_disabled_missing_api_key_returns_error(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", False)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "")

        result = await FetchUrlTool().execute(url="https://example.com")

        assert result.startswith("Error:")
        assert "DASHSCOPE_API_KEY" in result

    @pytest.mark.asyncio
    async def test_call_tool_receives_fetch_timeout(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", False)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(cfg, "WEB_SEARCH_TIMEOUT", 7)

        mock_call_tool = AsyncMock(return_value="Full page content")
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        await FetchUrlTool().execute(url="https://example.com")

        assert mock_call_tool.call_args.kwargs["timeout"] == 14

    @pytest.mark.asyncio
    async def test_bailian_rate_limit_error_remains_visible(self, monkeypatch):
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", False)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(BailianMCPClient, "call_tool", AsyncMock(side_effect=RuntimeError("429 Too Many Requests")))

        result = await FetchUrlTool().execute(url="https://limited.example.com")

        assert result.startswith("Error:")
        assert "429" in result
        assert "Too Many Requests" in result

    @pytest.mark.asyncio
    async def test_bailian_real_wait_for_timeout(self, monkeypatch):
        import asyncio
        import config as cfg

        monkeypatch.setattr(cfg, "LOCAL_WEBPARSER_ENABLED", False)
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(cfg, "WEB_SEARCH_TIMEOUT", 1)

        async def slow_call_tool(*args, **kwargs):
            await asyncio.sleep(10)

        monkeypatch.setattr(BailianMCPClient, "call_tool", slow_call_tool)

        result = await FetchUrlTool().execute(url="https://slow-real.example.com")

        assert result.startswith("Error:")
        assert "timed out" in result.lower()
        assert "2s" in result


class TestToolContract:

    def test_name_is_fetch_url(self):
        assert FetchUrlTool().name == "fetch_url"

    def test_description_mentions_web_search_companion(self):
        desc = FetchUrlTool().description.lower()
        assert "web_search" in desc or "search" in desc
        assert "url" in desc

    def test_parameters_schema_has_url_required(self):
        schema = FetchUrlTool().parameters_schema
        assert schema["type"] == "object"
        assert "url" in schema["properties"]
        assert "url" in schema["required"]

    def test_parameters_schema_has_format_optional(self):
        schema = FetchUrlTool().parameters_schema
        assert "format" in schema["properties"]
        assert "format" not in schema["required"]


def _local_result(content: str) -> LocalWebParserResult:
    return LocalWebParserResult(
        content=content,
        final_url="https://example.com/final",
        title="Example",
        backend="local:trafilatura",
    )
