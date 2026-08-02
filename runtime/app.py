"""Unified runtime: construct, execute, observe, and apply lifecycle hooks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents.prompt_utils import PromptCapabilities
from core.models import Effort, EngineKind, EngineResult, TaskRequest
from core.settings import RunSettings
from engines.dag import DagPlanAndExecuteEngine
from engines.sequential import SequentialPlanAndExecuteEngine
from execution.tool_calling import ToolCallingActionExecutor
from runtime.context import RuntimeContext

logger = logging.getLogger(__name__)

_DEFAULT_EFFORT = {
    EngineKind.SEQUENTIAL: Effort.LOW,
    EngineKind.DAG: Effort.MEDIUM,
    EngineKind.AGENT_LOOP: Effort.HIGH,
}


class AgentRuntime:
    def __init__(self, context: RuntimeContext) -> None:
        self.context = context
        self.settings = context.settings
        self.events = context.events
        self._closed = False
        self._closing = False
        self._close_lock = asyncio.Lock()

    async def run(
        self,
        task: str | TaskRequest,
        overrides: dict[str, Any] | RunSettings | None = None,
    ) -> EngineResult:
        if self._closed or self._closing:
            raise RuntimeError("AgentRuntime is closing or closed")
        request = task if isinstance(task, TaskRequest) else TaskRequest(task=task)
        run_settings = RunSettings.from_app(self.settings)
        if isinstance(overrides, RunSettings):
            run_settings = overrides
        else:
            run_settings = run_settings.with_overrides(overrides)
        self._validate_run_capabilities(run_settings)

        self.context.llm_client.reset_usage()
        engine_kind = run_settings.engine
        effort = (
            _DEFAULT_EFFORT[engine_kind]
            if run_settings.effort == Effort.AUTO
            else run_settings.effort
        )
        self.events.set_context(
            run_id=request.run_id,
            task_id=request.task_id,
            engine=engine_kind.value,
        )
        self.events.emit(
            "task_started",
            {
                "task": request.task,
                "engine": engine_kind.value,
                "effort": effort.value,
                "capabilities": list(run_settings.capabilities),
            },
        )

        checkpoint = self._new_checkpoint(request, engine_kind, effort)
        try:
            if checkpoint is not None:
                self.context.checkpoint_store.save(checkpoint)
            for capability in self.context.resettable_capabilities:
                reset = getattr(capability, "reset_task_state", None)
                if callable(reset):
                    reset()

            request.context = self._gather_context(request)
            engine = self._build_engine(engine_kind, effort)
            result = await engine.run(request)
            result.output = self._apply_output_guardrail(result.output)
            self._store_conversation(request, result)
            await self._learn_from_result(request, result)
            if checkpoint is not None:
                from checkpoint.models import CheckpointStatus

                checkpoint.state = (
                    CheckpointStatus.COMPLETED
                    if result.success
                    else CheckpointStatus.FAILED
                )
                checkpoint.output = result.output
                if not result.success:
                    checkpoint.error = "Engine completed without satisfying the task"
                self.context.checkpoint_store.save(checkpoint)
            await self.events.emit_async("task_completed", self._completion_payload(result))
            return result
        except asyncio.CancelledError:
            if checkpoint is not None:
                from checkpoint.models import CheckpointStatus

                checkpoint.state = CheckpointStatus.CANCELLED
                checkpoint.error = "Task cancelled"
                try:
                    self.context.checkpoint_store.save(checkpoint)
                except Exception:
                    logger.error("Could not persist cancelled task checkpoint", exc_info=True)
            await self.events.emit_async("task_cancelled", {"error": "Task cancelled"})
            raise
        except Exception as exc:
            if checkpoint is not None:
                from checkpoint.models import CheckpointStatus

                checkpoint.state = CheckpointStatus.FAILED
                checkpoint.error = f"{type(exc).__name__}: {exc}"
                try:
                    self.context.checkpoint_store.save(checkpoint)
                except Exception:
                    logger.error("Could not persist failed task checkpoint", exc_info=True)
            await self.events.emit_async(
                "task_failed",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        finally:
            await self.events.drain()

    def _validate_run_capabilities(self, run: RunSettings) -> None:
        """Reject capability labels that were not enabled on this runtime."""
        for name in run.capabilities:
            if not hasattr(self.settings.capabilities, name):
                raise ValueError(f"Unknown run capability: {name}")
            value = getattr(self.settings.capabilities, name)
            if not isinstance(value, bool):
                raise ValueError(f"Run capability is not a feature switch: {name}")
            if not value:
                raise ValueError(f"Run capability is not enabled: {name}")

    async def aclose(self) -> None:
        """Release runtime-owned asynchronous resources exactly once.

        Concurrent callers serialize on one lock.  A failed or cancelled close
        leaves the runtime retryable instead of claiming that its resources were
        released successfully.
        """
        async with self._close_lock:
            if self._closed:
                return
            self._closing = True
            try:
                await self.events.drain()
                await self.context.llm_client.aclose()
            except BaseException:
                self._closing = False
                raise
            self._closed = True
            self._closing = False

    async def resume(self, task_id: str, *, run_id: str | None = None) -> EngineResult:
        store = self.context.checkpoint_store
        if store is None:
            raise ValueError("Checkpoint support is disabled in settings.toml")
        checkpoint = store.load(task_id)
        if checkpoint is None:
            raise ValueError(
                "No semantic runtime checkpoint found. Legacy path-specific checkpoints "
                "are intentionally not compatible."
            )
        request = TaskRequest(
            task=checkpoint.task,
            context=checkpoint.context,
            task_id=checkpoint.task_id,
            metadata=dict(checkpoint.metadata),
            **({"run_id": run_id} if run_id else {}),
        )
        return await self.run(
            request,
            RunSettings(
                engine=checkpoint.engine,
                effort=checkpoint.effort,
            ),
        )

    def _controller_tools(self):
        """Return a tool bundle whose mutable activation hook is loop-local."""
        tools = []
        for tool in self.context.tools.values():
            if tool.name == "activate_skill":
                clone = getattr(tool, "clone", None)
                if callable(clone):
                    tool = clone()
            tools.append(tool)
        return tools

    def _prompt_capabilities(self, tools) -> PromptCapabilities:
        activation = next(
            (tool for tool in tools if tool.name == "activate_skill"),
            None,
        )
        return PromptCapabilities.from_tools(
            tools,
            python_command=self.settings.tools.python_command,
            hitl_configured=self.settings.capabilities.hitl,
            hitl_max_prompts=self.settings.capabilities.hitl_max_prompts,
            skill_descriptions=getattr(activation, "skill_descriptions", ""),
            skills_max_activations=getattr(activation, "max_activations", 1),
        )

    def _build_action_executor(self, tools=None):
        controller_tools = list(tools) if tools is not None else self._controller_tools()
        executor = ToolCallingActionExecutor(
            llm_client=self.context.llm_client,
            tools=controller_tools,
            settings=self.settings,
            events=self.events,
            context_manager=self.context.context_manager,
            guardrail=self.context.guardrail,
            prompt_capabilities=self._prompt_capabilities(controller_tools),
        )
        activation = next(
            (tool for tool in controller_tools if tool.name == "activate_skill"),
            None,
        )
        if activation is not None:
            activation.set_tool_filter_callback(executor.set_allowed_tools)
        return executor

    def _build_engine(self, kind: EngineKind, effort: Effort):
        controller_tools = self._controller_tools()
        common = {
            "llm_client": self.context.llm_client,
            "settings": self.settings,
            "events": self.events,
            "effort": effort,
            "context_manager": self.context.context_manager,
            "tools": controller_tools,
            "guardrail": self.context.guardrail,
            "prompt_capabilities": self._prompt_capabilities(controller_tools),
        }
        if kind == EngineKind.AGENT_LOOP:
            from engines.agent_loop import AgentLoopEngine

            engine = AgentLoopEngine(**common)
            activation = next(
                (tool for tool in controller_tools if tool.name == "activate_skill"),
                None,
            )
            if activation is not None:
                activation.set_tool_filter_callback(engine.set_allowed_tools)
            return engine

        executor = self._build_action_executor(controller_tools)
        engine_types = {
            EngineKind.SEQUENTIAL: SequentialPlanAndExecuteEngine,
            EngineKind.DAG: DagPlanAndExecuteEngine,
        }
        return engine_types[kind](
            executor=executor,
            **common,
            executor_factory=self._build_action_executor,
        )

    def _gather_context(self, request: TaskRequest) -> str:
        parts = [request.context] if request.context else []
        if self.settings.capabilities.knowledge:
            try:
                from knowledge.retriever import KnowledgeRetriever

                retriever = KnowledgeRetriever(
                    docs_dir=self.settings.paths.knowledge_docs_dir,
                    chunk_size=self.settings.capabilities.knowledge_chunk_size,
                )
                found = retriever.search(
                    request.task,
                    top_k=self.settings.capabilities.knowledge_top_k,
                )
                if found:
                    parts.append(retriever.format_results(found))
            except Exception:
                logger.debug("Knowledge context unavailable", exc_info=True)
        memory_service = self.context.agentic_memory_service
        if self.settings.capabilities.agentic_memory and memory_service is not None:
            try:
                from memory.models import MemorySearchQuery

                memories = memory_service.search(
                    MemorySearchQuery(
                        query=request.task,
                        top_k=self.settings.capabilities.memory_search_top_k,
                        min_confidence=self.settings.capabilities.memory_min_confidence,
                    )
                )
                memory_context = memory_service.format_context(memories)
                if memory_context:
                    parts.append(memory_context)
            except Exception:
                logger.debug("Agentic memory context unavailable", exc_info=True)
        learner = self.context.experience_learner
        if learner is not None:
            try:
                avoidance = learner.build_avoidance_hints(request.task)
                preferences = learner.build_preference_hints(request.task)
                if avoidance:
                    parts.append(avoidance)
                if preferences:
                    parts.append(preferences)
            except Exception:
                logger.debug("Self-evolution hints unavailable", exc_info=True)
        combined = "\n\n".join(part for part in parts if part)
        if self.context.guardrail is not None and combined:
            decision = self.context.guardrail.scan_memory(combined)
            return decision.transformed_text or combined
        return combined

    def _apply_output_guardrail(self, output: str) -> str:
        guardrail = self.context.guardrail
        if guardrail is None:
            return output
        decision = guardrail.scan_final_output(output)
        return decision.transformed_text or output

    def _store_conversation(self, request: TaskRequest, result: EngineResult) -> None:
        if not self.settings.capabilities.agentic_memory:
            return
        service = self.context.agentic_memory_service
        if service is None:
            return
        try:
            service.store_task_result(
                task=request.task,
                answer=result.output,
                task_id=request.task_id,
                success=result.success,
            )
        except Exception:
            logger.debug("Agentic memory write failed", exc_info=True)

    @staticmethod
    def _completion_payload(result: EngineResult) -> dict[str, Any]:
        """Return the guarded, user-facing subset published to event consumers."""
        return {
            "output": result.output,
            "success": result.success,
            "engine": result.engine.value,
            "effort": result.effort.value,
            "stop_reason": result.stop_reason.value,
            "stats": result.stats.model_dump(),
            "action_count": len(result.actions),
            "duration_ms": result.duration_ms,
        }

    def _new_checkpoint(self, request, engine, effort):
        if self.context.checkpoint_store is None:
            return None
        from checkpoint.models import RuntimeCheckpoint

        return RuntimeCheckpoint(
            task_id=request.task_id,
            run_id=request.run_id,
            task=request.task,
            context=request.context,
            engine=engine,
            effort=effort,
            metadata=request.metadata,
        )

    async def _learn_from_result(self, request: TaskRequest, result: EngineResult) -> None:
        learner = self.context.experience_learner
        if learner is None:
            return
        try:
            from evolution.models import TaskOutcome

            legacy_results = [action.to_legacy() for action in result.actions]
            outcome = TaskOutcome(
                task=request.task,
                task_id=request.task_id,
                complexity=result.engine.value,
                success=result.success,
                final_answer=result.output,
                trajectory=legacy_results,
            )
            await learner.learn_from_task(outcome)
            distiller = self.context.skill_distiller
            if distiller is not None and result.success and distiller.should_distill(request.task):
                await distiller.distill(outcome)
        except Exception:
            logger.debug("Self-evolution hook failed", exc_info=True)
