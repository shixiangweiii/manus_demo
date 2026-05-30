"""Tests for MCPBridgeTool — BaseTool wrapper for discovered MCP tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from tools.mcp.bridge_tool import MCPBridgeTool


def _make_manager(tools_dict=None, call_result="mock result"):
    """Create a mock MCPClientManager."""
    manager = MagicMock()
    manager.call_tool = AsyncMock(return_value=call_result)
    manager._discovered = tools_dict or {}
    return manager


class TestMCPBridgeToolProperties:
    def test_name_returns_prefixed(self):
        manager = _make_manager()
        tool = MCPBridgeTool(
            prefixed_name="mcp_fs_read_file",
            description="Read a file",
            mcp_tool_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            client_manager=manager,
            original_tool_name="read_file",
            server_name="fs",
        )
        assert tool.name == "mcp_fs_read_file"

    def test_description_preserved(self):
        manager = _make_manager()
        tool = MCPBridgeTool(
            prefixed_name="mcp_fs_read",
            description="Read file contents",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager,
            original_tool_name="read",
            server_name="fs",
        )
        assert tool.description == "Read file contents"

    def test_server_name_property(self):
        manager = _make_manager()
        tool = MCPBridgeTool(
            prefixed_name="mcp_api_search",
            description="Search",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager,
            original_tool_name="search",
            server_name="api",
        )
        assert tool.server_name == "api"

    def test_original_tool_name_property(self):
        manager = _make_manager()
        tool = MCPBridgeTool(
            prefixed_name="mcp_api_search",
            description="Search",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager,
            original_tool_name="search",
            server_name="api",
        )
        assert tool.original_tool_name == "search"


class TestMCPBridgeToolSchema:
    def test_parameters_schema_converted(self):
        manager = _make_manager()
        mcp_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        }
        tool = MCPBridgeTool(
            prefixed_name="mcp_api_search",
            description="Search API",
            mcp_tool_schema=mcp_schema,
            client_manager=manager,
            original_tool_name="search",
            server_name="api",
        )
        schema = tool.parameters_schema
        assert schema["type"] == "object"
        assert "query" in schema["properties"]

    def test_to_openai_tool_format(self):
        manager = _make_manager()
        tool = MCPBridgeTool(
            prefixed_name="mcp_api_search",
            description="Search API",
            mcp_tool_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            client_manager=manager,
            original_tool_name="search",
            server_name="api",
        )
        openai_fmt = tool.to_openai_tool()
        assert openai_fmt["type"] == "function"
        assert openai_fmt["function"]["name"] == "mcp_api_search"
        assert openai_fmt["function"]["description"] == "Search API"


class TestMCPBridgeToolExecute:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        manager = _make_manager(call_result="Hello, world!")
        tool = MCPBridgeTool(
            prefixed_name="mcp_test_echo",
            description="Echo tool",
            mcp_tool_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            client_manager=manager,
            original_tool_name="echo",
            server_name="test",
        )
        result = await tool.execute(text="hello")
        assert result == "Hello, world!"
        manager.call_tool.assert_called_once_with("mcp_test_echo", {"text": "hello"})

    @pytest.mark.asyncio
    async def test_execute_error_returns_error_string(self):
        manager = _make_manager()
        manager.call_tool = AsyncMock(side_effect=RuntimeError("Connection refused"))
        tool = MCPBridgeTool(
            prefixed_name="mcp_test_fail",
            description="Fail tool",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager,
            original_tool_name="fail",
            server_name="test",
        )
        result = await tool.execute()
        assert result.startswith("Error:")
        assert "Connection refused" in result

    @pytest.mark.asyncio
    async def test_execute_exception_group_unwrapped(self):
        manager = _make_manager()
        exc_group = ExceptionGroup("test", [RuntimeError("inner error")])
        manager.call_tool = AsyncMock(side_effect=exc_group)
        tool = MCPBridgeTool(
            prefixed_name="mcp_test_eg",
            description="Test",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager,
            original_tool_name="eg",
            server_name="test",
        )
        result = await tool.execute()
        assert result.startswith("Error:")
        assert "inner error" in result


class TestMCPBridgeToolEvents:
    """P1-4: MCPBridgeTool must emit mcp_tool_executed so the eval probe can
    count MCP tool executions."""

    @pytest.mark.asyncio
    async def test_emits_executed_on_success(self):
        events = []
        manager = _make_manager(call_result="ok")
        tool = MCPBridgeTool(
            prefixed_name="mcp_test_echo", description="Echo",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager, original_tool_name="echo", server_name="test",
            on_event=lambda e, d: events.append((e, d)),
        )
        await tool.execute()
        executed = [d for e, d in events if e == "mcp_tool_executed"]
        assert len(executed) == 1
        assert executed[0]["error"] is False

    @pytest.mark.asyncio
    async def test_emits_executed_with_error_flag_on_failure(self):
        events = []
        manager = _make_manager()
        manager.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
        tool = MCPBridgeTool(
            prefixed_name="mcp_test_fail", description="Fail",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager, original_tool_name="fail", server_name="test",
            on_event=lambda e, d: events.append((e, d)),
        )
        await tool.execute()
        executed = [d for e, d in events if e == "mcp_tool_executed"]
        assert len(executed) == 1
        assert executed[0]["error"] is True

    @pytest.mark.asyncio
    async def test_no_event_when_no_sink(self):
        # on_event=None must not raise.
        manager = _make_manager(call_result="ok")
        tool = MCPBridgeTool(
            prefixed_name="mcp_test_echo", description="Echo",
            mcp_tool_schema={"type": "object", "properties": {}},
            client_manager=manager, original_tool_name="echo", server_name="test",
        )
        assert await tool.execute() == "ok"


class TestMCPBridgeToolStrictMode:
    def test_strict_mode_rejects_complex_schema(self):
        manager = _make_manager()
        tool = MCPBridgeTool(
            prefixed_name="mcp_test_complex",
            description="Complex",
            mcp_tool_schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            },
            client_manager=manager,
            original_tool_name="complex",
            server_name="test",
            schema_mode="strict",
        )
        # Strict mode should have rejected the schema
        schema = tool.parameters_schema
        assert schema.get("properties") == {} or "(schema rejected" in schema.get("description", "")
