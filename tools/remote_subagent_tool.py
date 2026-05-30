"""
RemoteSubAgentTool (v18.3) - Delegate a subtask to a remote agent over MCP/A2A.
远端子智能体工具（v18.3）—— 通过 MCP/A2A 把子任务委派给远端 agent server。

Unlike the in-process SubAgent (v9), this delegates across a (configurable)
transport to a separate MCP-hosted agent — for cross-process isolation and
long-task stability. The result is returned to the parent loop (NOT control
transfer). Built on the v16 MCPClientManager + v18.4 A2AClient.
与进程内 SubAgent 不同，本工具通过可配传输把任务委派给独立的 MCP agent server
（跨进程隔离 / 长任务稳定性）；结果回灌父循环（非控制权转移）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)


def build_remote_server_config(server_json: str):
    """Parse REMOTE_AGENT_SERVER_JSON into an MCPServerConfig (or None if invalid).
    把 REMOTE_AGENT_SERVER_JSON 解析为 MCPServerConfig（无效返回 None）。"""
    from tools.mcp.config import _parse_server_entry

    raw = (server_json or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[RemoteSubAgentTool] REMOTE_AGENT_SERVER_JSON parse error: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name", "remote_agent")
    return _parse_server_entry(name, data)


class RemoteSubAgentTool(BaseTool):
    """Delegate a subtask to a remote MCP-hosted agent. / 委派子任务给远端 MCP agent。"""

    def __init__(
        self,
        server_config: Any,
        on_event: Callable[[str, Any], None] | None = None,
        max_calls_per_task: int | None = None,
        timeout: int | None = None,
        parent_name: str = "OrchestratorAgent",
        fetch_card: bool | None = None,
    ):
        from a2a.client import A2AClient

        self._server_config = server_config
        self._on_event = on_event or (lambda *_: None)
        self._max_calls = max_calls_per_task or config.REMOTE_SUBAGENT_MAX_CALLS_PER_TASK
        self._timeout = timeout or config.REMOTE_SUBAGENT_TIMEOUT
        self._parent_name = parent_name
        self._fetch_card = config.REMOTE_AGENT_FETCH_CARD if fetch_card is None else fetch_card
        self._call_count = 0
        self._client = A2AClient(server_config, call_timeout=self._timeout)

    @property
    def name(self) -> str:
        return "remote_subagent"

    @property
    def description(self) -> str:
        return (
            "Delegate a self-contained subtask to a REMOTE agent running on a "
            "separate MCP-hosted server (cross-process isolation). Pass a context "
            "briefing — the remote agent does not see your conversation. Returns "
            "the remote agent's result. Use for heavy/independent work you want "
            "isolated from this process."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The subtask for the remote agent to complete.",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Context briefing for the remote agent (it does not see "
                        "your conversation)."
                    ),
                },
            },
            "required": ["task"],
        }

    async def execute(self, **kwargs: Any) -> str:
        from a2a.models import A2ATaskRequest

        local_parent = self._parent_name
        task = kwargs.get("task", "")
        context = kwargs.get("context", "") or ""
        if not task:
            return "Error: task is required for remote_subagent tool."

        if self._call_count >= self._max_calls:
            logger.warning("[RemoteSubAgentTool] Call limit reached: %d/%d", self._call_count, self._max_calls)
            return (
                f"Error: Remote SubAgent call limit reached ({self._max_calls} per task). "
                "Please continue without delegating remotely again."
            )
        self._call_count += 1

        task_id = uuid.uuid4().hex[:12]
        self._on_event("remote_subagent_start", {
            "task_id": task_id,
            "parent_agent": local_parent,
            "server": getattr(self._server_config, "name", "remote_agent"),
            "task": task[:120],
        })

        # v18.4: optional capability discovery (read the AgentCard first).
        if self._fetch_card:
            try:
                card = await self._client.fetch_agent_card()
                self._on_event("a2a_card_fetched", {
                    "name": card.name,
                    "version": card.version,
                    "skills": [s.name for s in card.skills],
                })
            except Exception as exc:
                logger.debug("[RemoteSubAgentTool] AgentCard fetch failed (continuing): %s", exc)

        try:
            response = await asyncio.wait_for(
                self._client.run_task(A2ATaskRequest(task_id=task_id, input=task, context=context)),
                timeout=self._timeout,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            msg = f"Error: Remote SubAgent timed out after {self._timeout}s."
            logger.warning("[RemoteSubAgentTool] %s", msg)
            self._on_event("remote_subagent_failed", {"task_id": task_id, "error": msg})
            return msg
        except Exception as exc:
            msg = f"Error: Remote SubAgent failed: {str(exc)[:300]}"
            logger.error("[RemoteSubAgentTool] %s", msg, exc_info=True)
            self._on_event("remote_subagent_failed", {"task_id": task_id, "error": msg})
            return msg

        if response.status == "completed":
            self._on_event("remote_subagent_complete", {
                "task_id": task_id,
                "output_preview": str(response.output)[:200],
            })
            return response.output or "(remote agent returned no output)"

        msg = f"Error: Remote agent reported failure: {response.error[:300]}"
        self._on_event("remote_subagent_failed", {"task_id": task_id, "error": msg})
        return msg

    def reset_task_state(self) -> None:
        """Reset per-task call counter (called by OrchestratorAgent.run())."""
        logger.debug("[RemoteSubAgentTool] Resetting task state: call_count=%d→0", self._call_count)
        self._call_count = 0

    def set_caller(self, name: str) -> None:
        if name:
            self._parent_name = name
