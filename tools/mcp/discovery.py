"""
MCP Bridge discovery — shared async builder for MCPBridgeTool instances.

MCP 桥接发现 —— 共享的异步构建器，供 main.py（CLI）与 evaluation.runner（评测）复用。

Emits evaluation/observability events through an optional `on_event` sink:
  - mcp_tools_discovered {count}       after discovery completes
  - mcp_schema_error     {count, ...}  when schema conversion errors occurred
  - mcp_tool_executed    (emitted by MCPBridgeTool.execute, not here)

抽出此模块前，发现逻辑只在 main.py 内、且不发任何事件，导致评测 probe 的
mcp_* 指标恒为 0（无法证明 v16 收益）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def discover_mcp_bridge_tools(
    on_event: Callable[[str, Any], None] | None = None,
) -> list:
    """
    Discover and instantiate MCPBridgeTool instances from configured servers.
    Async — runs within the existing event loop. Emits mcp_tools_discovered /
    mcp_schema_error through `on_event` when provided.

    从已配置的服务器发现并实例化 MCPBridgeTool，并通过 on_event 发出发现/schema 事件。
    """
    from tools.mcp.config import load_mcp_bridge_config
    from tools.mcp.client import MCPClientManager
    from tools.mcp.bridge_tool import MCPBridgeTool
    from tools.mcp.schema_adapter import get_schema_metrics, reset_schema_metrics

    def _emit(event: str, data: Any = None) -> None:
        if on_event is None:
            return
        try:
            on_event(event, data)
        except Exception:
            logger.debug("[MCPDiscovery] event callback failed for '%s'", event, exc_info=True)

    bridge_config = load_mcp_bridge_config()
    if not bridge_config.servers:
        logger.warning("[MCPDiscovery] MCP_BRIDGE_ENABLED but no servers configured")
        _emit("mcp_tools_discovered", {"count": 0})
        return []

    manager = MCPClientManager(bridge_config)

    try:
        discovered = await manager.discover_all_tools()
    except Exception as exc:
        logger.error("[MCPDiscovery] MCP tool discovery failed: %s", exc)
        _emit("mcp_tools_discovered", {"count": 0})
        return []

    # Schema conversion happens in MCPBridgeTool.__init__; reset metrics first so
    # the post-loop snapshot reflects only THIS discovery pass.
    # schema 转换发生在 MCPBridgeTool 构造时，先重置指标，循环后快照只反映本轮。
    reset_schema_metrics()

    bridge_tools: list = []
    for dt in discovered:
        bridge_tool = MCPBridgeTool(
            prefixed_name=dt.prefixed_name,
            description=dt.description,
            mcp_tool_schema=dt.input_schema,
            client_manager=manager,
            original_tool_name=dt.original_name,
            server_name=dt.server_name,
            schema_mode=bridge_config.schema_mode,
            on_event=on_event,
        )
        bridge_tools.append(bridge_tool)

    _emit("mcp_tools_discovered", {"count": len(bridge_tools)})

    metrics = get_schema_metrics()
    schema_errors = metrics.get("tools_rejected", 0) + metrics.get("tool_parameter_errors", 0)
    if schema_errors:
        _emit("mcp_schema_error", {"count": schema_errors, "metrics": metrics})

    return bridge_tools
