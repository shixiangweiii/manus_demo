"""Unified runtime: route, execute, observe, and apply shared lifecycle hooks."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.models import Effort, EngineKind, EngineResult, ExecutorKind, TaskRequest
from core.settings import RunSettings
from engines.dag_engine import DagEngine
from engines.goal import GoalEngine
from engines.sequential import SequentialPlanEngine
from engines.todo import TodoEngine
from engines.workflow import WorkflowEngine
from engines.selector import EffortPolicy, EngineSelector, select_executor
from execution.react import ReactActionExecutor
from execution.thinking import ThinkingAwareActionExecutor
from runtime.context import RuntimeContext

logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(self, context: RuntimeContext) -> None:
        self.context = context
        self.settings = context.settings
        self.events = context.events
        self._closed = False

    async def run(
        self,
        task: str | TaskRequest,
        overrides: dict[str, Any] | RunSettings | None = None,
    ) -> EngineResult:
        if self._closed:
            raise RuntimeError("AgentRuntime is closed")
        request = task if isinstance(task, TaskRequest) else TaskRequest(task=task)
        run_settings = RunSettings.from_app(self.settings)
        if isinstance(overrides, RunSettings):
            run_settings = overrides
        else:
            run_settings = run_settings.with_overrides(overrides)
        self._validate_run_capabilities(run_settings)

        self.context.llm_client.reset_usage()
        selector = EngineSelector()
        engine_kind, reason = await selector.select(request.task, run_settings)
        effort = EffortPolicy.select(engine_kind, run_settings.effort)
        executor_kind = select_executor(self.settings, run_settings.executor)
        self.events.set_context(
            run_id=request.run_id,
            task_id=request.task_id,
            engine=engine_kind.value,
            executor=executor_kind.value,
        )
        self.events.emit(
            "task_started",
            {
                "task": request.task,
                "selection_reason": reason,
                "effort": effort.value,
                "capabilities": list(run_settings.capabilities),
            },
        )

        checkpoint = self._new_checkpoint(request, engine_kind, executor_kind, effort)
        try:
            if checkpoint is not None:
                self.context.checkpoint_store.save(checkpoint)
            for capability in self.context.resettable_capabilities:
                reset = getattr(capability, "reset_task_state", None)
                if callable(reset):
                    reset()

            request.context = self._gather_context(request)
            executor = self._build_executor(executor_kind)
            engine = self._build_engine(engine_kind, executor, effort)
            result = await engine.run(request)
            result.answer = self._apply_output_guardrail(result.answer)
            self._store_conversation(request, result)
            await self._learn_from_result(request, result)
            if checkpoint is not None:
                from checkpoint.models import CheckpointStatus

                checkpoint.state = (
                    CheckpointStatus.COMPLETED
                    if result.success
                    else CheckpointStatus.FAILED
                )
                checkpoint.answer = result.answer
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

    async def run_workflow(
        self,
        spec: Any,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> EngineResult:
        if self._closed:
            raise RuntimeError("AgentRuntime is closed")
        if not self.settings.capabilities.workflow:
            raise ValueError("Workflow capability is disabled in settings.toml")
        from workflow.models import WorkflowSpec

        workflow_spec = (
            spec if isinstance(spec, WorkflowSpec) else WorkflowSpec.model_validate(spec)
        )
        request = TaskRequest(
            task=workflow_spec.name.strip() or "workflow",
            metadata={"workflow_spec": workflow_spec},
            **({"task_id": task_id} if task_id else {}),
            **({"run_id": run_id} if run_id else {}),
        )
        self.events.set_context(
            run_id=request.run_id,
            task_id=request.task_id,
            engine=EngineKind.WORKFLOW.value,
            executor=ExecutorKind.AUTO.value,
        )
        self.events.emit(
            "task_started",
            {"task": request.task, "selection_reason": "explicit workflow", "effort": "low"},
        )
        self.context.llm_client.reset_usage()
        checkpoint = self._new_checkpoint(
            request,
            EngineKind.WORKFLOW,
            ExecutorKind.AUTO,
            Effort.LOW,
        )
        try:
            if checkpoint is not None:
                self.context.checkpoint_store.save(checkpoint)
            for capability in self.context.resettable_capabilities:
                reset = getattr(capability, "reset_task_state", None)
                if callable(reset):
                    reset()
            executor = self._build_executor(ExecutorKind.REACT)
            engine = self._build_engine(EngineKind.WORKFLOW, executor, Effort.LOW)
            result = await engine.run(request)
            result.answer = self._apply_output_guardrail(result.answer)
            self._store_conversation(request, result)
            await self._learn_from_result(request, result)
            if checkpoint is not None:
                from checkpoint.models import CheckpointStatus

                checkpoint.state = (
                    CheckpointStatus.COMPLETED
                    if result.success
                    else CheckpointStatus.FAILED
                )
                checkpoint.answer = result.answer
                if not result.success:
                    checkpoint.error = "Workflow completed unsuccessfully"
                self.context.checkpoint_store.save(checkpoint)
            await self.events.emit_async("task_completed", self._completion_payload(result))
            return result
        except asyncio.CancelledError:
            if checkpoint is not None:
                from checkpoint.models import CheckpointStatus

                checkpoint.state = CheckpointStatus.CANCELLED
                checkpoint.error = "Workflow cancelled"
                try:
                    self.context.checkpoint_store.save(checkpoint)
                except Exception:
                    logger.error("Could not persist cancelled workflow checkpoint", exc_info=True)
            await self.events.emit_async("task_cancelled", {"error": "Workflow cancelled"})
            raise
        except Exception as exc:
            if checkpoint is not None:
                from checkpoint.models import CheckpointStatus

                checkpoint.state = CheckpointStatus.FAILED
                checkpoint.error = f"{type(exc).__name__}: {exc}"
                try:
                    self.context.checkpoint_store.save(checkpoint)
                except Exception:
                    logger.error("Could not persist failed workflow checkpoint", exc_info=True)
            await self.events.emit_async(
                "task_failed",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            raise
        finally:
            await self.events.drain()

    async def aclose(self) -> None:
        """Release runtime-owned asynchronous resources."""
        if self._closed:
            return
        self._closed = True
        await self.events.drain()
        await self.context.llm_client.aclose()

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
        if checkpoint.engine == EngineKind.WORKFLOW:
            raw_spec = checkpoint.metadata.get("workflow_spec")
            if raw_spec is None:
                raise ValueError("Workflow checkpoint does not contain a workflow specification")
            return await self.run_workflow(
                raw_spec,
                task_id=checkpoint.task_id,
                run_id=run_id,
            )
        request = TaskRequest(
            task=checkpoint.task,
            context=checkpoint.context,
            task_id=checkpoint.task_id,
            **({"run_id": run_id} if run_id else {}),
        )
        return await self.run(
            request,
            RunSettings(
                engine=checkpoint.engine,
                executor=checkpoint.executor,
                effort=checkpoint.effort,
            ),
        )

    def _build_executor(self, kind: ExecutorKind):
        executor_type = (
            ThinkingAwareActionExecutor
            if kind == ExecutorKind.THINKING
            else ReactActionExecutor
        )
        executor = executor_type(
            llm_client=self.context.llm_client,
            tools=self.context.tools.values(),
            settings=self.settings,
            events=self.events,
            context_manager=self.context.context_manager,
            guardrail=self.context.guardrail,
        )
        if self.context.skill_activation is not None:
            self.context.skill_activation.set_tool_filter_callback(
                executor.set_allowed_tools
            )
        return executor

    def _build_engine(self, kind: EngineKind, executor, effort: Effort):
        engine_types = {
            EngineKind.SEQUENTIAL: SequentialPlanEngine,
            EngineKind.DAG: DagEngine,
            EngineKind.TODO: TodoEngine,
            EngineKind.GOAL: GoalEngine,
            EngineKind.WORKFLOW: WorkflowEngine,
        }
        engine_type = engine_types[kind]
        kwargs = {
            "llm_client": self.context.llm_client,
            "executor": executor,
            "settings": self.settings,
            "events": self.events,
            "effort": effort,
            "context_manager": self.context.context_manager,
            "tools": self.context.tools.values(),
            "executor_factory": lambda: self._build_executor(executor.kind),
        }
        if kind == EngineKind.WORKFLOW:
            kwargs["tools"] = self.context.tools.as_dict()
            kwargs["guardrail"] = self.context.guardrail
        return engine_type(**kwargs)

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

    def _apply_output_guardrail(self, answer: str) -> str:
        guardrail = self.context.guardrail
        if guardrail is None:
            return answer
        decision = guardrail.scan_final_output(answer)
        return decision.transformed_text or answer

    def _store_conversation(self, request: TaskRequest, result: EngineResult) -> None:
        if not self.settings.capabilities.agentic_memory:
            return
        service = self.context.agentic_memory_service
        if service is None:
            return
        try:
            service.store_task_result(
                task=request.task,
                answer=result.answer,
                task_id=request.task_id,
                success=result.success,
            )
        except Exception:
            logger.debug("Agentic memory write failed", exc_info=True)

    @staticmethod
    def _completion_payload(result: EngineResult) -> dict[str, Any]:
        """Return the guarded, user-facing subset published to event consumers."""
        return {
            "answer": result.answer,
            "success": result.success,
            "engine": result.engine.value,
            "executor": result.executor.value,
            "effort": result.effort.value,
            "action_count": len(result.actions),
            "duration_ms": result.duration_ms,
        }

    def _new_checkpoint(self, request, engine, executor, effort):
        if self.context.checkpoint_store is None:
            return None
        from checkpoint.models import RuntimeCheckpoint

        return RuntimeCheckpoint(
            task_id=request.task_id,
            run_id=request.run_id,
            task=request.task,
            context=request.context,
            engine=engine,
            executor=executor,
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
                final_answer=result.answer,
                trajectory=legacy_results,
            )
            await learner.learn_from_task(outcome)
            distiller = self.context.skill_distiller
            if distiller is not None and result.success and distiller.should_distill(request.task):
                await distiller.distill(outcome)
        except Exception:
            logger.debug("Self-evolution hook failed", exc_info=True)
