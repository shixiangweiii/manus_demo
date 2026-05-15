"""
Tests for FetchUrlTool (v11 — Bailian WebParser MCP-backed URL fetcher).
URL 页面抓取工具测试（v11 — 百炼 MCP WebParser 抓取）。

All tests are mock-based; no real MCP calls. Covers:
- Empty/missing URL → Error
- Missing DASHSCOPE_API_KEY → Error
- Successful fetch with content truncation
- Timeout returns Error
- Tool contract (name, description, parameters_schema)
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.fetch_url import FetchUrlTool
from tools.mcp_client import BailianMCPClient


# ======================================================================
# Execute — guard checks
# ======================================================================

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

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_error(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "")
        result = await FetchUrlTool().execute(url="https://example.com")
        assert result.startswith("Error:")
        assert "DASHSCOPE_API_KEY" in result


# ======================================================================
# Execute — success path
# ======================================================================

class TestExecuteSuccess:

    @pytest.mark.asyncio
    async def test_successful_fetch(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        page_content = "# Example Page\n\nThis is the full content of the page."

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=page_content)
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_client.call_tool)

        tool = FetchUrlTool()
        result = await tool.execute(url="https://example.com")
        assert "Example Page" in result
        assert "full content" in result

    @pytest.mark.asyncio
    async def test_content_truncation(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(cfg, "FETCH_URL_MAX_CONTENT_LENGTH", 100)

        long_content = "A" * 500  # 500 chars, exceeds limit of 100

        mock_call_tool = AsyncMock(return_value=long_content)
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        tool = FetchUrlTool()
        result = await tool.execute(url="https://example.com")
        assert len(result) < 200  # truncated + notice text
        assert "truncated" in result.lower()

    @pytest.mark.asyncio
    async def test_format_parameter_default(self, monkeypatch):
        """Default format is 'markdown'."""
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        mock_call_tool = AsyncMock(return_value="Content")
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        tool = FetchUrlTool()
        await tool.execute(url="https://example.com")

        # Verify call_tool received format="markdown"
        call_args = mock_call_tool.call_args
        assert call_args[1]["arguments"]["format"] == "markdown"

    @pytest.mark.asyncio
    async def test_format_parameter_text(self, monkeypatch):
        """Can override format to 'text'."""
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        mock_call_tool = AsyncMock(return_value="Plain text content")
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        tool = FetchUrlTool()
        result = await tool.execute(url="https://example.com", format="text")
        assert "Plain text content" in result

        call_args = mock_call_tool.call_args
        assert call_args[1]["arguments"]["format"] == "text"


# ======================================================================
# Execute — error paths
# ======================================================================

class TestExecuteErrors:

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(cfg, "WEB_SEARCH_TIMEOUT", 1)

        mock_call_tool = AsyncMock(side_effect=TimeoutError("MCP timeout"))
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        tool = FetchUrlTool()
        result = await tool.execute(url="https://slow.example.com")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_connection_failure_returns_error(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        mock_call_tool = AsyncMock(side_effect=RuntimeError("Connection refused"))
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        tool = FetchUrlTool()
        result = await tool.execute(url="https://bad.example.com")
        assert result.startswith("Error:")
        assert "RuntimeError" in result

    @pytest.mark.asyncio
    async def test_real_wait_for_timeout(self, monkeypatch):
        """Verify asyncio.wait_for catches a genuinely slow coroutine."""
        import asyncio
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(cfg, "WEB_SEARCH_TIMEOUT", 1)

        async def slow_call_tool(*args, **kwargs):
            await asyncio.sleep(10)  # far exceeds the 2s (1*2) wait_for timeout

        monkeypatch.setattr(BailianMCPClient, "call_tool", slow_call_tool)

        tool = FetchUrlTool()
        result = await tool.execute(url="https://slow-real.example.com")
        assert result.startswith("Error:")
        assert "timed out" in result.lower()
        assert "2s" in result

    @pytest.mark.asyncio
    async def test_httpx_timeout_classified_as_timeout(self, monkeypatch):
        """httpx ConnectTimeout/ReadTimeout should be classified as 'timed out'."""
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        # Simulate httpx.ConnectTimeout by creating a class with Timeout suffix
        class ConnectTimeout(Exception):
            pass

        mock_call_tool = AsyncMock(side_effect=ConnectTimeout("connection timed out"))
        monkeypatch.setattr(BailianMCPClient, "call_tool", mock_call_tool)

        tool = FetchUrlTool()
        result = await tool.execute(url="https://unreachable.example.com")
        assert result.startswith("Error:")
        assert "timed out" in result.lower()


# ======================================================================
# Tool contract
# ======================================================================

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