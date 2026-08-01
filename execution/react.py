"""Standard ReAct action executor."""

from __future__ import annotations

from context.manager import ContextManager
from core.events import EventBus
from core.models import Action, ActionResult, Effort, ExecutorKind
from core.settings import AppSettings
from execution.base import ActionExecutor
from llm.client import LLMClient
from react.engine import ReActEngine
from execution.models import ReasoningEffort
from tools.base import BaseTool
from tools.router import ToolRouter


def to_legacy_effort(effort: Effort) -> ReasoningEffort:
    if effort == Effort.LOW:
        return ReasoningEffort.LOW
    if effort == Effort.HIGH:
        return ReasoningEffort.HIGH
    return ReasoningEffort.MEDIUM


class ReactActionExecutor(ActionExecutor):
    kind = ExecutorKind.REACT

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        settings: AppSettings,
        events: EventBus,
        context_manager: ContextManager | None = None,
        guardrail=None,
    ) -> None:
        self._events = events
        self._engine = ReActEngine(
            llm_client=llm_client,
            tools=tools,
            max_iterations=settings.engines.max_action_iterations,
            tool_router=ToolRouter(
                available_tools=[tool.name for tool in tools],
                failure_threshold=settings.tools.failure_threshold,
            ),
            context_manager=context_manager,
            agent_name="ReactActionExecutor",
            guardrail=guardrail,
            temperature=settings.engines.react_temperature,
            result_truncation_limit=settings.tools.result_truncation_limit,
            on_event=events.legacy_callback,
        )

    async def execute(
        self,
        action: Action,
        context: str = "",
        effort: Effort = Effort.MEDIUM,
    ) -> ActionResult:
        self._events.emit("action_started", {"action": action.model_dump()})
        prompt = f"Execute this action:\n\n{action.description}"
        if action.success_criteria:
            prompt += f"\n\nSuccess criteria: {action.success_criteria}"
        try:
            legacy = await self._engine.execute(
                prompt=prompt,
                context=context,
                node_id=action.id,
                effort=to_legacy_effort(effort),
            )
        except Exception as exc:
            self._events.emit(
                "action_failed",
                {"action_id": action.id, "error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        result = self.from_legacy(legacy)
        self._events.emit("action_completed", result.model_dump())
        return result

    def set_allowed_tools(self, tool_names: list[str] | None) -> None:
        self._engine.set_allowed_tools(tool_names)
