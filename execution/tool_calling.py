"""Standard native tool-calling action executor."""

from __future__ import annotations

from agents.prompt_utils import PromptCapabilities, build_system_prompt
from context.manager import ContextManager
from core.events import EventBus
from core.models import Action, ActionResult, Effort
from core.settings import AppSettings
from execution.base import ActionExecutor
from execution.models import ActionLoopStats, resolve_effort
from llm.client import LLMClient
from tool_calling.loop import ActionToolLoop
from tools.base import BaseTool
from tools.router import ToolRouter


class ToolCallingActionExecutor(ActionExecutor):
    """Execute one action through the standard structured tool-calling loop."""

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        settings: AppSettings,
        events: EventBus,
        context_manager: ContextManager | None = None,
        guardrail=None,
        prompt_capabilities: PromptCapabilities | None = None,
    ) -> None:
        self._events = events
        activation = next(
            (tool for tool in tools if tool.name == "activate_skill"),
            None,
        )
        capabilities = prompt_capabilities or PromptCapabilities.from_tools(
            tools,
            python_command=settings.tools.python_command,
            hitl_configured=settings.capabilities.hitl,
            hitl_max_prompts=settings.capabilities.hitl_max_prompts,
            skill_descriptions=getattr(activation, "skill_descriptions", ""),
            skills_max_activations=getattr(activation, "max_activations", 1),
        )
        self._system_hint = build_system_prompt(
            "Complete the assigned action using only the tools available to this loop.",
            capabilities=capabilities,
        )
        self._loop = ActionToolLoop(
            llm_client=llm_client,
            tools=tools,
            max_iterations=settings.execution.max_action_iterations,
            tool_router=ToolRouter(
                available_tools=[tool.name for tool in tools],
                failure_threshold=settings.tools.failure_threshold,
            ),
            context_manager=context_manager,
            agent_name="ToolCallingActionExecutor",
            guardrail=guardrail,
            temperature=settings.execution.tool_calling_temperature,
            result_truncation_limit=settings.tools.result_truncation_limit,
            on_event=events.legacy_callback,
            max_reasoning_tokens=settings.execution.max_reasoning_tokens,
        )
        self._last_stats: ActionLoopStats | None = None

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
            legacy = await self._loop.execute(
                prompt=prompt,
                context=context,
                node_id=action.id,
                system_hint=self._system_hint,
                effort=resolve_effort(effort),
            )
        except Exception as exc:
            self._events.emit(
                "action_failed",
                {"action_id": action.id, "error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        result = self.from_legacy(legacy)
        self._last_stats = legacy.stats
        payload = result.model_dump()
        self._events.emit("action_completed", payload)
        return result

    @property
    def last_stats(self) -> ActionLoopStats | None:
        """Usage from the most recently completed action."""
        return self._last_stats

    def set_allowed_tools(self, tool_names: list[str] | None) -> None:
        self._loop.set_allowed_tools(tool_names)
