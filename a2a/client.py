"""
A2AClient - Talk to a remote agent over MCP using the A2A envelope.
A2A 客户端——通过 MCP 与远端 agent 交互（AgentCard 发现 + 任务信封）。

Uses MCPClientManager for transport, discovery, and calling; this is a thin
A2A semantics layer on top — it does NOT reimplement any transport.
复用 MCPClientManager 做传输/发现/调用；本类只是其上的 A2A 语义薄层。
"""

from __future__ import annotations

import logging

from a2a.models import A2ATaskRequest, A2ATaskResponse, AgentCard
from tools.mcp.client import MCPClientManager
from tools.mcp.config import MCPBridgeConfig, MCPServerConfig

logger = logging.getLogger(__name__)


class A2AClient:
    """A2A client for one remote agent server. / 面向单个远端 agent server 的 A2A 客户端。"""

    def __init__(self, server_config: MCPServerConfig, call_timeout: int = 300):
        self._server = server_config
        bridge = MCPBridgeConfig(
            servers={server_config.name: server_config},
            call_timeout_seconds=int(call_timeout),
        )
        self._manager = MCPClientManager(bridge)

    def _prefixed(self, tool: str) -> str:
        return self._manager.make_prefixed_name(self._server.name, tool)

    async def fetch_agent_card(self) -> AgentCard:
        """Fetch the remote AgentCard (capability discovery). Raises on failure.
        拉取远端 AgentCard（能力发现）；失败时抛出。"""
        raw = await self._manager.call_tool(self._prefixed("get_agent_card"), {})
        return AgentCard.model_validate_json(raw)

    async def run_task(self, request: A2ATaskRequest) -> A2ATaskResponse:
        """Send an A2A task request; never raises — failures become a failed response.
        发送 A2A 任务请求；不抛裸异常，失败转为 failed 响应。"""
        try:
            raw = await self._manager.call_tool(
                self._prefixed("a2a_run_task"),
                {
                    "input": request.input,
                    "context": request.context,
                    "task_id": request.task_id,
                },
            )
            return A2ATaskResponse.model_validate_json(raw)
        except Exception as exc:
            logger.warning("[A2AClient] run_task failed: %s", exc)
            return A2ATaskResponse(
                task_id=request.task_id,
                status="failed",
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
