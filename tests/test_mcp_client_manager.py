"""Tests for MCPClientManager — multi-server tool discovery and execution."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tools.mcp.config import MCPServerConfig, MCPBridgeConfig
from tools.mcp.client import MCPClientManager, DiscoveredTool


def _make_config(num_servers=1):
    servers = {}
    for i in range(num_servers):
        name = f"server_{i}"
        servers[name] = MCPServerConfig(
            name=name,
            transport="streamable_http",
            url=f"http://localhost:{9000 + i}",
            enabled=True,
        )
    return MCPBridgeConfig(servers=servers)


class TestMCPClientManagerProperties:
    def test_make_prefixed_name(self):
        config = _make_config()
        manager = MCPClientManager(config)
        assert manager.make_prefixed_name("fs", "read") == "mcp_fs_read"
        assert manager.make_prefixed_name("api", "search") == "mcp_api_search"

    def test_custom_prefix(self):
        servers = {"s": MCPServerConfig(name="s", transport="streamable_http", url="http://x")}
        config = MCPBridgeConfig(servers=servers, tool_prefix="remote")
        manager = MCPClientManager(config)
        assert manager.make_prefixed_name("s", "tool") == "remote_s_tool"

    def test_discovered_tools_empty_initially(self):
        config = _make_config()
        manager = MCPClientManager(config)
        assert len(manager.discovered_tools) == 0


class TestMCPClientManagerDiscover:
    @pytest.mark.asyncio
    async def test_discover_skips_disabled_servers(self):
        servers = {
            "on": MCPServerConfig(name="on", transport="streamable_http", url="http://a", enabled=True),
            "off": MCPServerConfig(name="off", transport="streamable_http", url="http://b", enabled=False),
        }
        config = MCPBridgeConfig(servers=servers)
        manager = MCPClientManager(config)

        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"
        mock_tool.inputSchema = {"type": "object", "properties": {}}

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[mock_tool]))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.mcp.client.open_transport") as mock_transport:
            mock_transport.return_value.__aenter__ = AsyncMock(
                return_value=(AsyncMock(), AsyncMock())
            )
            mock_transport.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("mcp.client.session.ClientSession", return_value=mock_session):
                discovered = await manager.discover_all_tools()

        assert len(discovered) == 1
        assert discovered[0].server_name == "on"

    @pytest.mark.asyncio
    async def test_discover_continues_on_failure(self):
        servers = {
            "bad": MCPServerConfig(name="bad", transport="streamable_http", url="http://fail", enabled=True),
            "good": MCPServerConfig(name="good", transport="streamable_http", url="http://ok", enabled=True),
        }
        config = MCPBridgeConfig(servers=servers)
        manager = MCPClientManager(config)

        call_count = 0

        async def mock_open(cfg):
            nonlocal call_count
            call_count += 1
            if cfg.name == "bad":
                raise RuntimeError("Connection refused")
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        with patch("tools.mcp.client.open_transport", side_effect=mock_open):
            discovered = await manager.discover_all_tools()

        # No exception raised, discovery continued past the failure


class TestDiscoveredTool:
    def test_fields(self):
        dt = DiscoveredTool(
            original_name="read",
            prefixed_name="mcp_fs_read",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            server_name="fs",
            server_config=MCPServerConfig(name="fs", transport="stdio", command="cat"),
        )
        assert dt.original_name == "read"
        assert dt.prefixed_name == "mcp_fs_read"
        assert dt.server_name == "fs"


class TestExtractText:
    def test_extract_text_from_content(self):
        from mcp.types import CallToolResult, TextContent

        result = CallToolResult(
            content=[TextContent(type="text", text="hello"), TextContent(type="text", text="world")],
            isError=False,
        )
        text = MCPClientManager._extract_text(result)
        assert text == "hello\nworld"
