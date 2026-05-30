"""
HandoffTool (v18.2) - Context-passing specialist delegation with control transfer.
Handoff 工具（v18.2）—— 上下文传递式专家委派 + 控制权转移。

When the LLM calls this tool, a SpecialistAgent takes over with the caller's
context briefing and runs to completion; its FULL output becomes the final
answer for the current ReAct loop (control transfer). This complements the
isolated, summary-only SubAgent.

LLM 调用本工具时，专家 agent 带调用方上下文接管并跑完，其完整输出成为当前
ReAct 循环的最终答案（控制权转移）。与隔离式、仅摘要的 SubAgent 互补。

Control-transfer mechanism: this tool sets `is_handoff = True`; on success it
stores the full output on `self._last_output` / `self._last_ok`, which
ReActEngine reads to terminate the loop with that output (untruncated).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import config
from context.manager import ContextManager
from llm.client import LLMClient
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class HandoffTool(BaseTool):
    """Delegate to a specialist agent with control transfer. / 控制权转移式专家委派。"""

    # ReActEngine recognizes this to end the loop on success (control transfer).
    is_handoff = True

    def __init__(
        self,
        llm_client: LLMClient,
        available_tools: dict[str, BaseTool],
        context_manager: ContextManager | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        allow_ask_user: bool = False,
        interactive: bool = False,
        max_calls_per_task: int | None = None,
        timeout: int | None = None,
        parent_name: str = "OrchestratorAgent",  # ReActEngine overrides via set_caller()
    ):
        self._llm_client = llm_client
        self._available_tools = available_tools
        self._context_manager = context_manager or ContextManager()
        self._on_event = on_event or (lambda *_: None)
        self._allow_ask_user = allow_ask_user
        self._interactive = interactive
        self._max_calls = max_calls_per_task or config.HANDOFF_MAX_CALLS_PER_TASK
        self._timeout = timeout or config.HANDOFF_TIMEOUT
        self._parent_name = parent_name
        self._call_count = 0

        # Control-transfer hand-off state read by ReActEngine after execution.
        # 控制权转移状态：ReActEngine 在执行后读取（取完整、未截断输出）。
        self._last_ok: bool = False
        self._last_output: str = ""

    @property
    def name(self) -> str:
        return "handoff"

    @property
    def description(self) -> str:
        from agents.specialist import SPECIALIST_REGISTRY
        roles = "; ".join(f"{k}: {v.description}" for k, v in SPECIALIST_REGISTRY.items())
        return (
            "Hand off the task to a specialist agent that TAKES OVER and produces "
            "the final answer (control transfer). Pass a context briefing so the "
            "specialist has what it needs. Use this when a focused expert should "
            "own the rest of the task end-to-end. "
            f"Available specialists — {roles}"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        from agents.specialist import SPECIALIST_REGISTRY
        return {
            "type": "object",
            "properties": {
                "target_specialist": {
                    "type": "string",
                    "enum": list(SPECIALIST_REGISTRY.keys()),
                    "description": "Which specialist should take over.",
                },
                "task": {
                    "type": "string",
                    "description": "The task for the specialist to complete end-to-end.",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "A context briefing to pass to the specialist: relevant "
                        "background, findings so far, constraints. The specialist "
                        "does NOT see your conversation, so include what it needs."
                    ),
                },
            },
            "required": ["target_specialist", "task"],
        }

    async def execute(self, **kwargs: Any) -> str:
        # capture parent name before any await (anti concurrent set_caller overwrite)
        local_parent = self._parent_name
        # reset transfer state for this call
        self._last_ok = False
        self._last_output = ""

        from agents.specialist import SPECIALIST_REGISTRY, SpecialistAgent

        target = kwargs.get("target_specialist", "")
        task = kwargs.get("task", "")
        context = kwargs.get("context", "") or ""

        if not task:
            return "Error: task is required for handoff tool."
        if target not in SPECIALIST_REGISTRY:
            return (
                f"Error: unknown specialist '{target}'. "
                f"Available: {', '.join(SPECIALIST_REGISTRY.keys())}."
            )

        # per-task call limit (reserve before await; no refund on failure)
        if self._call_count >= self._max_calls:
            logger.warning("[HandoffTool] Call limit reached: %d/%d", self._call_count, self._max_calls)
            return (
                f"Error: Handoff call limit reached ({self._max_calls} per task). "
                "Please continue without handing off again."
            )
        self._call_count += 1

        spec = SPECIALIST_REGISTRY[target]
        self._on_event("handoff_start", {
            "target": target,
            "parent_agent": local_parent,
            "task": task[:120],
        })

        try:
            agent = SpecialistAgent(
                spec=spec,
                llm_client=self._llm_client,
                available_tools=self._available_tools,
                context_manager=self._context_manager,
                on_event=self._on_event,
                allow_ask_user=self._allow_ask_user,
                interactive=self._interactive,
                parent_name=local_parent,
            )
            output = await asyncio.wait_for(
                agent.run(task=task, context=context),
                timeout=self._timeout,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            msg = f"Error: Handoff to '{target}' timed out after {self._timeout}s."
            logger.warning("[HandoffTool] %s", msg)
            self._on_event("handoff_failed", {"target": target, "error": msg})
            return msg
        except Exception as exc:
            msg = f"Error: Handoff to '{target}' failed: {str(exc)[:300]}"
            logger.error("[HandoffTool] %s", msg, exc_info=True)
            self._on_event("handoff_failed", {"target": target, "error": msg})
            return msg

        # Success → record control-transfer state for ReActEngine to pick up.
        self._last_ok = True
        self._last_output = output or ""
        self._on_event("handoff_complete", {
            "target": target,
            "output_preview": str(output)[:200],
        })
        logger.info("[HandoffTool] Handoff to '%s' complete (%d chars)", target, len(output or ""))
        return output or "(specialist returned no output)"

    def reset_task_state(self) -> None:
        """Reset per-task call counter (called by OrchestratorAgent.run())."""
        logger.debug("[HandoffTool] Resetting task state: call_count=%d→0", self._call_count)
        self._call_count = 0
        self._last_ok = False
        self._last_output = ""

    def set_caller(self, name: str) -> None:
        """ReActEngine injects the actual caller name before execution (attribution)."""
        if name:
            self._parent_name = name
