"""
Tests for BailianMCPClient (v11 — lightweight MCP Streamable HTTP wrapper).
百炼 MCP 客户端测试（v11 — 轻量 MCP Streamable HTTP 封装）。

All tests are mock-based; no real MCP server calls. Covers:
- call_tool success path (TextContent extraction)
- call_tool error path (isError=True → RuntimeError)
- _extract_text with mixed content blocks
- Missing DASHSCOPE_API_KEY raises ValueError
- Unknown server_name raises ValueError
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.types import CallToolResult, TextContent, ImageContent

from tools.mcp_client import BailianMCPClient


# ======================================================================
# _extract_text
# ======================================================================

class TestExtractText:

    def test_single_text_content(self):
        result = CallToolResult(
            content=[TextContent(type="text", text="Hello world")],
            isError=False,
        )
        text = BailianMCPClient._extract_text(result)
        assert text == "Hello world"

    def test_multiple_text_content_blocks(self):
        result = CallToolResult(
            content=[
                TextContent(type="text", text="First part"),
                TextContent(type="text", text="Second part"),
            ],
            isError=False,
        )
        text = BailianMCPClient._extract_text(result)
        assert "First part" in text
        assert "Second part" in text

    def test_mixed_content_skips_non_text(self):
        # ImageContent should be skipped
        result = CallToolResult(
            content=[
                TextContent(type="text", text="Search results here"),
                ImageContent(type="image", data="base64...", mimeType="image/png"),
            ],
            isError=False,
        )
        text = BailianMCPClient._extract_text(result)
        assert text == "Search results here"

    def test_empty_content_returns_empty_string(self):
        result = CallToolResult(content=[], isError=False)
        text = BailianMCPClient._extract_text(result)
        assert text == ""


# ======================================================================
# call_tool — guard checks
# ======================================================================

class TestCallToolGuards:

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_value_error(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "")

        client = BailianMCPClient()
        with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
            await client.call_tool("WebSearch", "bailian_web_search", {"query": "test"})

    @pytest.mark.asyncio
    async def test_unknown_server_name_raises_value_error(self, monkeypatch):
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        client = BailianMCPClient()
        with pytest.raises(ValueError, match="Unknown MCP server"):
            await client.call_tool("UnknownServer", "some_tool", {"query": "test"})


# ======================================================================
# call_tool — success and error paths (mocked MCP session)
# ======================================================================

class TestCallToolExecution:

    @pytest.mark.asyncio
    async def test_successful_tool_call(self, monkeypatch):
        """call_tool returns text content from a successful MCP tool call."""
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        expected_text = "Search results for 'test query'"
        mock_result = CallToolResult(
            content=[TextContent(type="text", text=expected_text)],
            isError=False,
        )

        # Mock the entire MCP session lifecycle
        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # Mock streamablehttp_client to return mock streams
        mock_read = MagicMock()
        mock_write = MagicMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=(mock_read, mock_write, MagicMock()))
        mock_context.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.mcp_client.streamablehttp_client", return_value=mock_context):
            with patch("tools.mcp_client.ClientSession", return_value=mock_session):
                client = BailianMCPClient()
                result = await client.call_tool("WebSearch", "bailian_web_search", {"query": "test", "count": 5})

        assert result == expected_text

    @pytest.mark.asyncio
    async def test_error_tool_call_raises_runtime_error(self, monkeypatch):
        """When MCP tool returns isError=True, call_tool raises RuntimeError."""
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")

        error_text = "Search service unavailable"
        mock_result = CallToolResult(
            content=[TextContent(type="text", text=error_text)],
            isError=True,
        )

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), MagicMock()))
        mock_context.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.mcp_client.streamablehttp_client", return_value=mock_context):
            with patch("tools.mcp_client.ClientSession", return_value=mock_session):
                client = BailianMCPClient()
                with pytest.raises(RuntimeError, match="MCP tool error"):
                    await client.call_tool("WebSearch", "bailian_web_search", {"query": "test"})

    @pytest.mark.asyncio
    async def test_webparser_calls_are_serialized_by_semaphore(self, monkeypatch):
        """WebParser throttling should cap concurrent calls without affecting WebSearch."""
        import config as cfg
        monkeypatch.setattr(cfg, "DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setattr(cfg, "BAILIAN_WEBPARSER_MAX_CONCURRENT", 1)
        monkeypatch.setattr(cfg, "BAILIAN_WEBPARSER_MIN_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(cfg, "BAILIAN_MCP_MAX_RETRIES", 0)

        BailianMCPClient._webparser_sem = None
        BailianMCPClient._webparser_sem_loop = None
        BailianMCPClient._webparser_lock = None
        BailianMCPClient._webparser_lock_loop = None
        BailianMCPClient._webparser_last_call_at = 0.0

        active = 0
        max_active = 0

        async def fake_call_once(self, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "ok"

        monkeypatch.setattr(BailianMCPClient, "_call_once", fake_call_once)

        client = BailianMCPClient()
        results = await asyncio.gather(
            client.call_tool("WebParser", "bailian_web_parser", {"url": "https://a.example"}),
            client.call_tool("WebParser", "bailian_web_parser", {"url": "https://b.example"}),
        )

        assert results == ["ok", "ok"]
        assert max_active == 1

