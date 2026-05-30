"""
P1-4 regression: discover_mcp_bridge_tools must emit mcp_tools_discovered (and
mcp_schema_error when conversion fails) so the eval probe can prove v16 worked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.mcp.client import DiscoveredTool
from tools.mcp.discovery import discover_mcp_bridge_tools


def _fake_discovered(n=2):
    out = []
    for i in range(n):
        out.append(DiscoveredTool(
            original_name=f"tool{i}",
            prefixed_name=f"mcp_srv_tool{i}",
            description=f"tool {i}",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            server_name="srv",
            server_config=MagicMock(),
        ))
    return out


@pytest.mark.asyncio
async def test_emits_discovered_count():
    events = []
    cfg = MagicMock()
    cfg.servers = {"srv": MagicMock()}
    cfg.schema_mode = "loose"

    fake_manager = MagicMock()
    fake_manager.discover_all_tools = AsyncMock(return_value=_fake_discovered(2))

    with patch("tools.mcp.config.load_mcp_bridge_config", return_value=cfg), \
         patch("tools.mcp.client.MCPClientManager", return_value=fake_manager):
        tools = await discover_mcp_bridge_tools(on_event=lambda e, d: events.append((e, d)))

    assert len(tools) == 2
    discovered = [d for e, d in events if e == "mcp_tools_discovered"]
    assert discovered and discovered[-1]["count"] == 2
    # discovered tools must carry the event sink so executions are counted later
    assert tools[0]._on_event is not None


@pytest.mark.asyncio
async def test_no_servers_emits_zero():
    events = []
    cfg = MagicMock()
    cfg.servers = {}

    with patch("tools.mcp.config.load_mcp_bridge_config", return_value=cfg):
        tools = await discover_mcp_bridge_tools(on_event=lambda e, d: events.append((e, d)))

    assert tools == []
    discovered = [d for e, d in events if e == "mcp_tools_discovered"]
    assert discovered and discovered[-1]["count"] == 0
