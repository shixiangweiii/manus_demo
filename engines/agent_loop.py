"""Independent task-level model-driven agent loop engine."""

from __future__ import annotations

import time

from agent_loop import AgentLoop
from agents.prompt_utils import PromptCapabilities, build_system_prompt
from context.manager import ContextManager
from core.events import EventBus
from core.models import Effort, EngineKind, TaskRequest
from core.settings import AppSettings
from engines.base import TaskEngine
from llm.client import LLMClient
from tools.base import BaseTool


_SYSTEM_PROMPT = """\
You are an autonomous tool-using agent responsible for completing the user's
entire task. Decide what to do next from the task and the actual tool results.

Use tools whenever they are needed for evidence or execution. For longer tasks,
you may use todo_write to keep a visible working checklist; that checklist is
only display state and never determines whether the task is complete. When the
task is complete, stop calling tools and return the final answer directly.
"""


class AgentLoopEngine(TaskEngine):
    """Adapt the reusable AgentLoop to the common runtime engine contract."""

    kind = EngineKind.AGENT_LOOP

    def __init__(
        self,
        llm_client: LLMClient,
        settings: AppSettings,
        events: EventBus,
        effort: Effort,
        context_manager: ContextManager | None = None,
        tools: list[BaseTool] | dict[str, BaseTool] | None = None,
        guardrail: object | None = None,
        prompt_capabilities: PromptCapabilities | None = None,
    ) -> None:
        super().__init__(
            llm_client=llm_client,
            settings=settings,
            events=events,
            effort=effort,
            context_manager=context_manager,
            tools=tools,
            guardrail=guardrail,
            prompt_capabilities=prompt_capabilities,
        )
        self._loop = AgentLoop(
            llm_client=self.llm_client,
            tools=self.tools,
            max_turns=self.settings.engines.max_agent_turns,
            timeout_seconds=self.settings.engines.agent_timeout_seconds,
            context_manager=self.context_manager,
            agent_name="AgentLoopEngine",
            guardrail=self.guardrail,
            temperature=self.settings.execution.tool_calling_temperature,
            result_truncation_limit=self.settings.tools.result_truncation_limit,
            on_event=self.events.legacy_callback,
            max_reasoning_tokens=self.settings.execution.max_reasoning_tokens,
            max_total_tokens=self.settings.engines.max_agent_total_tokens,
            tool_failure_threshold=self.settings.tools.failure_threshold,
        )

    def set_allowed_tools(self, tool_names: list[str] | None) -> None:
        self._loop.set_allowed_tools(tool_names)

    async def run(self, request: TaskRequest):
        started_at = time.time()
        records_before = self.usage_marker()
        self.events.emit("engine_started", {"engine": self.kind.value})
        loop_result = await self._loop.run(
            request.task,
            context=request.context,
            system_prompt=build_system_prompt(
                _SYSTEM_PROMPT,
                capabilities=self.prompt_capabilities,
            ),
            effort=self.effort,
        )

        stats = self.stats_since(
            records_before,
            local_stats=loop_result.stats,
        )

        result = self.make_result(
            request,
            output=loop_result.output,
            success=loop_result.success,
            started_at=started_at,
            stop_reason=loop_result.stop_reason,
            stats=stats,
            actions=[],
            metadata={
                "turns": loop_result.turns,
                "todo": self._loop.todo_write.metadata,
            },
        )
        self.emit_completed(result)
        return result
