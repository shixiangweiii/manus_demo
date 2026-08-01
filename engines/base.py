"""Base class and shared helpers for orchestration engines."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from context.manager import ContextManager
from core.events import EventBus
from core.models import Action, ActionResult, Effort, EngineKind, EngineResult, TaskRequest
from core.settings import AppSettings
from execution.base import ActionExecutor
from llm.client import LLMClient
from tools.base import BaseTool


class RecordingActionExecutor(ActionExecutor):
    """Small decorator used by engines to expose their action trajectory."""

    def __init__(self, inner: ActionExecutor) -> None:
        self.inner = inner
        self.kind = inner.kind
        self.results: list[ActionResult] = []

    async def execute(
        self,
        action: Action,
        context: str = "",
        effort: Effort = Effort.MEDIUM,
    ) -> ActionResult:
        result = await self.inner.execute(action, context=context, effort=effort)
        self.results.append(result)
        return result


class TaskEngine(ABC):
    kind: EngineKind

    def __init__(
        self,
        llm_client: LLMClient,
        executor: ActionExecutor,
        settings: AppSettings,
        events: EventBus,
        effort: Effort,
        context_manager: ContextManager | None = None,
        tools: list[BaseTool] | dict[str, BaseTool] | None = None,
        executor_factory: Callable[[], ActionExecutor] | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.executor = RecordingActionExecutor(executor)
        self.settings = settings
        self.events = events
        self.effort = effort
        self.context_manager = context_manager or ContextManager()
        self.tools = list(tools.values()) if isinstance(tools, dict) else list(tools or [])
        self.executor_factory = executor_factory

    def new_action_executor(self) -> ActionExecutor:
        if self.executor_factory is not None:
            return self.executor_factory()
        return self.executor.inner

    @abstractmethod
    async def run(self, request: TaskRequest) -> EngineResult:
        """Run one task request."""

    async def synthesize(self, task: str, raw_output: str) -> str:
        if not raw_output.strip():
            return "No usable result was produced."
        prompt = (
            "Create the final response for the user from the execution results below. "
            "Preserve concrete facts and artifact paths, omit internal planning details, "
            "and answer in the same language as the task.\n\n"
            f"Task:\n{task}\n\nExecution results:\n{raw_output}"
        )
        try:
            return await self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                caller_tag=f"engine.{self.kind.value}.synthesis",
            )
        except Exception:
            return raw_output

    def result(
        self,
        request: TaskRequest,
        *,
        answer: str,
        success: bool,
        started_at: float,
        metadata: dict | None = None,
    ) -> EngineResult:
        return EngineResult(
            answer=answer,
            success=success,
            engine=self.kind,
            executor=self.executor.kind,
            effort=self.effort,
            run_id=request.run_id,
            task_id=request.task_id,
            actions=list(self.executor.results),
            started_at=started_at,
            finished_at=time.time(),
            metadata=metadata or {},
        )

    def emit_completed(self, result: EngineResult) -> None:
        """Publish lifecycle metadata without exposing raw engine output."""
        self.events.emit(
            "engine_completed",
            {
                "engine": result.engine.value,
                "success": result.success,
                "action_count": len(result.actions),
                "duration_ms": result.duration_ms,
            },
        )
