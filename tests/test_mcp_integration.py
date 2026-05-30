"""Integration tests for MCP Bridge — end-to-end with mock MCP server."""

import asyncio
import json
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

from tools.mcp.config import MCPServerConfig, MCPBridgeConfig
from tools.mcp.client import MCPClientManager, DiscoveredTool
from tools.mcp.bridge_tool import MCPBridgeTool
from tools.mcp.schema_adapter import mcp_schema_to_openai


class TestSchemaAdapterIntegration:
    """Test schema adapter with realistic MCP tool schemas."""

    def test_filesystem_read_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "required": ["path"],
            "additionalProperties": False,
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert result["type"] == "object"
        assert "path" in result["properties"]
        assert "encoding" in result["properties"]
        assert "additionalProperties" not in result

    def test_search_with_nullable_fields(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "filter": {
                    "oneOf": [
                        {"type": "null"},
                        {"type": "string"},
                    ],
                    "description": "Optional filter",
                },
            },
            "required": ["query"],
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert result["properties"]["filter"]["type"] == "string"

    def test_complex_nested_with_defs(self):
        schema = {
            "type": "object",
            "$defs": {
                "Pagination": {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "default": 1},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            },
            "properties": {
                "query": {"type": "string"},
                "pagination": {"$ref": "#/$defs/Pagination"},
            },
            "required": ["query"],
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert result["properties"]["pagination"]["type"] == "object"
        assert "page" in result["properties"]["pagination"]["properties"]
        assert "$defs" not in result


class TestBridgeToolIntegration:
    """Test bridge tool with realistic execution patterns."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_mock(self):
        """Simulate full lifecycle: config → discover → bridge tool → execute."""
        # 1. Config
        config = MCPBridgeConfig(
            servers={
                "mock": MCPServerConfig(
                    name="mock",
                    transport="streamable_http",
                    url="http://localhost:9876",
                    enabled=True,
                ),
            },
            schema_mode="loose",
        )

        # 2. Manager
        manager = MCPClientManager(config)

        # 3. Manually add a discovered tool (simulating discovery)
        dt = DiscoveredTool(
            original_name="echo",
            prefixed_name="mcp_mock_echo",
            description="Echo input text",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            server_name="mock",
            server_config=config.servers["mock"],
        )
        manager._discovered["mcp_mock_echo"] = dt

        # 4. Create bridge tool
        bridge = MCPBridgeTool(
            prefixed_name=dt.prefixed_name,
            description=dt.description,
            mcp_tool_schema=dt.input_schema,
            client_manager=manager,
            original_tool_name=dt.original_name,
            server_name=dt.server_name,
        )

        # 5. Verify schema
        schema = bridge.parameters_schema
        assert schema["type"] == "object"
        assert "text" in schema["properties"]

        # 6. Verify OpenAI format
        openai_fmt = bridge.to_openai_tool()
        assert openai_fmt["function"]["name"] == "mcp_mock_echo"

        # 7. Execute (mocked)
        manager.call_tool = AsyncMock(return_value="Hello, world!")
        result = await bridge.execute(text="Hello, world!")
        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_error_propagation(self):
        """Verify error from MCP server is caught and returned as Error: string."""
        config = MCPBridgeConfig(
            servers={
                "mock": MCPServerConfig(
                    name="mock",
                    transport="streamable_http",
                    url="http://localhost:9876",
                ),
            },
        )
        manager = MCPClientManager(config)
        dt = DiscoveredTool(
            original_name="fail",
            prefixed_name="mcp_mock_fail",
            description="Always fails",
            input_schema={"type": "object", "properties": {}},
            server_name="mock",
            server_config=config.servers["mock"],
        )
        manager._discovered["mcp_mock_fail"] = dt

        bridge = MCPBridgeTool(
            prefixed_name=dt.prefixed_name,
            description=dt.description,
            mcp_tool_schema=dt.input_schema,
            client_manager=manager,
            original_tool_name=dt.original_name,
            server_name=dt.server_name,
        )

        # Mock a tool execution error
        manager.call_tool = AsyncMock(side_effect=RuntimeError("MCP tool error: Server error"))
        result = await bridge.execute()
        assert result.startswith("Error:")
        assert "MCP tool error" in result


class TestMultipleServersIntegration:
    """Test with multiple MCP servers configured."""

    def test_multiple_servers_config(self):
        config = MCPBridgeConfig(
            servers={
                "fs": MCPServerConfig(
                    name="fs",
                    transport="stdio",
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                ),
                "search": MCPServerConfig(
                    name="search",
                    transport="streamable_http",
                    url="https://mcp.example.com/search",
                    headers={"Authorization": "Bearer ${API_KEY}"},
                ),
            },
        )
        manager = MCPClientManager(config)
        assert manager.make_prefixed_name("fs", "read") == "mcp_fs_read"
        assert manager.make_prefixed_name("search", "query") == "mcp_search_query"

    def test_tool_name_collision_prevented(self):
        config = MCPBridgeConfig(
            servers={
                "a": MCPServerConfig(name="a", transport="streamable_http", url="http://a"),
                "b": MCPServerConfig(name="b", transport="streamable_http", url="http://b"),
            },
        )
        manager = MCPClientManager(config)
        # Same original tool name from different servers gets different prefixed names
        assert manager.make_prefixed_name("a", "search") == "mcp_a_search"
        assert manager.make_prefixed_name("b", "search") == "mcp_b_search"
        assert manager.make_prefixed_name("a", "search") != manager.make_prefixed_name("b", "search")
