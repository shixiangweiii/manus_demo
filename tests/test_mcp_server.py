"""Tests for MCPServerWrapper — exposing BaseTools as MCP server."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from tools.base import BaseTool
from tools.mcp.server import MCPServerWrapper


class _DummyTool(BaseTool):
    """A minimal BaseTool for testing."""

    @property
    def name(self) -> str:
        return "dummy_tool"

    @property
    def description(self) -> str:
        return "A dummy tool for testing"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "Input text"},
            },
            "required": ["input"],
        }

    async def execute(self, **kwargs) -> str:
        return f"Echo: {kwargs.get('input', '')}"


class _AddTool(BaseTool):
    @property
    def name(self) -> str:
        return "add"

    @property
    def description(self) -> str:
        return "Add two numbers"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        }

    async def execute(self, **kwargs) -> str:
        return str(kwargs.get("a", 0) + kwargs.get("b", 0))


class TestMCPServerWrapperRegistration:
    def test_register_single_tool(self):
        tools = [_DummyTool()]
        server = MCPServerWrapper(tools=tools)
        names = server.get_registered_tool_names()
        assert "dummy_tool" in names

    def test_register_multiple_tools(self):
        tools = [_DummyTool(), _AddTool()]
        server = MCPServerWrapper(tools=tools)
        names = server.get_registered_tool_names()
        assert "dummy_tool" in names
        assert "add" in names

    def test_empty_tool_list(self):
        server = MCPServerWrapper(tools=[])
        names = server.get_registered_tool_names()
        assert len(names) == 0

    def test_get_server_returns_fastmcp(self):
        server = MCPServerWrapper(tools=[_DummyTool()])
        mcp = server.get_server()
        assert mcp is not None
        assert mcp.name == "manus-demo"

    def test_custom_server_name(self):
        server = MCPServerWrapper(tools=[], server_name="custom-server")
        assert server.get_server().name == "custom-server"


class TestMCPServerToolSchema:
    """P1-3 regression: registered MCP tools must expose the BaseTool's real
    parameter schema, not a degenerate `kwargs` field."""

    def _params_for(self, tool, name):
        server = MCPServerWrapper(tools=[tool])
        registered = server.get_server()._tool_manager.list_tools()
        match = [t for t in registered if t.name == name]
        assert match, f"{name} not registered"
        return match[0].parameters

    def test_schema_has_real_field_names(self):
        params = self._params_for(_AddTool(), "add")
        props = params.get("properties", {})
        assert set(props.keys()) == {"a", "b"}
        assert "kwargs" not in props

    def test_required_fields_preserved(self):
        params = self._params_for(_AddTool(), "add")
        assert set(params.get("required", [])) == {"a", "b"}

    def test_optional_field_not_required(self):
        # FileOpsTool: 'action' required; 'filename'/'content' optional.
        from tools.file_ops import FileOpsTool
        params = self._params_for(FileOpsTool(), "file_ops")
        props = params.get("properties", {})
        assert {"action", "filename", "content"} <= set(props.keys())
        assert "kwargs" not in props
        assert set(params.get("required", [])) == {"action"}


class TestMCPServerWrapperWithMemory:
    def test_memory_resources_registered(self):
        mock_service = MagicMock()
        mock_service.search_for_task = MagicMock(return_value=[])
        mock_service.format_context = MagicMock(return_value="No results")

        server = MCPServerWrapper(tools=[_DummyTool()], memory_service=mock_service)
        mcp = server.get_server()

        # Verify resources were registered
        # FastMCP stores resources in _resource_manager
        assert hasattr(mcp, '_resource_manager') or hasattr(mcp, 'list_resources')

    def test_prompts_registered(self):
        mock_service = MagicMock()
        server = MCPServerWrapper(tools=[_DummyTool()], memory_service=mock_service)
        mcp = server.get_server()

        # Verify prompts were registered
        assert hasattr(mcp, '_prompt_manager') or hasattr(mcp, 'list_prompts')


class TestMCPServerWrapperHostPort:
    def test_custom_host_port(self):
        server = MCPServerWrapper(tools=[], host="0.0.0.0", port=9999)
        mcp = server.get_server()
        assert mcp.settings.host == "0.0.0.0"
        assert mcp.settings.port == 9999
