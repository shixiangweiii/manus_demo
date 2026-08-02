"""Action executor contract and legacy result conversion helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.models import Action, ActionResult, Effort, EngineStats, ToolInvocation


class ActionExecutor(ABC):
    @abstractmethod
    async def execute(
        self,
        action: Action,
        context: str = "",
        effort: Effort = Effort.MEDIUM,
    ) -> ActionResult:
        """Execute one action without making orchestration decisions."""

    async def execute_legacy(
        self,
        action: Action,
        context: str = "",
        effort: Effort = Effort.MEDIUM,
    ) -> Any:
        """Return the retained ``schema.StepResult`` shape for old components."""
        from execution.models import ActionLoopStats, StepResult, ToolCallRecord

        result = await self.execute(action, context=context, effort=effort)
        return StepResult(
            step_id=action.id,
            success=result.success,
            output=result.output,
            tool_calls_log=[
                ToolCallRecord(
                    tool_name=call.tool_name,
                    parameters=call.parameters,
                    result=call.result,
                )
                for call in result.tool_calls
            ],
            iterations_completed=result.iterations,
            failure_reason=result.failure_reason,
            stats=ActionLoopStats(
                llm_calls=result.stats.llm_calls,
                context_compaction_calls=result.stats.context_compaction_calls,
                tool_calls=result.stats.tool_calls,
                reasoning_tokens=result.stats.reasoning_tokens,
            ),
        )

    @staticmethod
    def from_legacy(result: Any) -> ActionResult:
        local_stats = getattr(result, "stats", None)
        return ActionResult(
            action_id=str(result.step_id),
            success=bool(result.success),
            output=result.output or "",
            tool_calls=[
                ToolInvocation(
                    tool_name=call.tool_name,
                    parameters=call.parameters,
                    result=call.result,
                )
                for call in result.tool_calls_log
            ],
            iterations=result.iterations_completed,
            failure_reason=result.failure_reason,
            stats=EngineStats(
                llm_calls=int(getattr(local_stats, "llm_calls", 0) or 0),
                context_compaction_calls=int(
                    getattr(local_stats, "context_compaction_calls", 0) or 0
                ),
                tool_calls=int(getattr(local_stats, "tool_calls", 0) or 0),
                reasoning_tokens=int(
                    getattr(local_stats, "reasoning_tokens", 0) or 0
                ),
                subagent_calls=sum(
                    call.tool_name == "subagent"
                    for call in result.tool_calls_log
                ),
            ),
        )
