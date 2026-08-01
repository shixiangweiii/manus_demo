"""Action executor contract and legacy result conversion helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.models import Action, ActionResult, Effort, ExecutorKind, ToolInvocation


class ActionExecutor(ABC):
    kind: ExecutorKind

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
        from execution.models import StepResult, ToolCallRecord

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
        )

    @staticmethod
    def from_legacy(result: Any) -> ActionResult:
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
        )
