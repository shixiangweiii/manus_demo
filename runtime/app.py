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
        self._run_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()

    async def run(
        self,
        task: str | TaskRequest,
        overrides: dict[str, Any] | RunSettings | None = None,
    ) -> EngineResult:
        """Run at most one task at a time on this mutable runtime instance."""
        if self._closed or self._closing:
            raise RuntimeError("AgentRuntime is closing or closed")
        async with self._run_lock:
            if self._closed or self._closing:
                raise RuntimeError("AgentRuntime is closing or closed")
            return await self._run_once(task, overrides)

    async def _run_once(
        self,
        task: str | TaskRequest,
        overrides: dict[str, Any] | RunSettings | None = None,
    ) -> EngineResult:
        if self._closed or self._closing:
            raise RuntimeError("AgentRuntime is closing or closed")
        request = task if isinstance(task, TaskRequest) else TaskRequest(task=task)
        # 单次设置先继承 settings.toml，再应用调用方覆盖项。例如 CLI 只传入
        # {"engine": "agent_loop"} 时，effort 仍沿用 [runtime] 的配置。
        run_settings = RunSettings.from_app(self.settings)
        if isinstance(overrides, RunSettings):
            run_settings = overrides
        else:
            run_settings = run_settings.with_overrides(overrides)
        self._validate_run_capabilities(run_settings)

        self.context.llm_client.reset_usage()
        # 把公开的 auto effort 落成各引擎的实际档位。例如
        # --engine agent_loop --effort auto 最终使用 high，供预算和温度策略读取。
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
            # 这里建立“新任务”边界：先保存初始 checkpoint，再清空工具的任务级状态。
            # 例如 chat 的第二条任务不能继承第一条任务的 skill 激活次数或 HITL 次数。
            if checkpoint is not None:
                self.context.checkpoint_store.save(checkpoint)
            for capability in self.context.resettable_capabilities:
                reset = getattr(capability, "reset_task_state", None)
                if callable(reset):
                    reset()

            # 在选择引擎前统一补齐上下文。例如“明天天气”可以同时带上调用方 context、
            # knowledge 检索片段和 memory 提示，三种引擎收到的是同一份 TaskRequest。
            request.context = self._gather_context(request)
            # 每次任务只选择一个编排引擎。Sequential/DAG 会再调用 ActionExecutor，
            # AgentLoop 则由任务级循环直接调用工具，不存在第二个“exec 引擎”。
            engine = self._build_engine(engine_kind, effort)
            result = await engine.run(request)

            # 引擎结果返回后再统一做宿主级收尾：过滤最终输出、写入记忆/学习结果，
            # 更新 checkpoint，最后发布 task_completed 给终端、Tracing 等订阅者。
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
            # 等待异步订阅者消费完本任务最后的事件，避免 task_completed 仍在队列中
            # 下一条 chat 任务就已经开始，造成展示或 trace 串线。
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
                async with self._run_lock:
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
        # Plan-and-Execute 的每个 Action 交给一个有界工具循环执行。例如计划步骤
        # “查询洛杉矶明天天气”会在这里获得自己的 assistant/tool 对话历史。
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

            # AgentLoop 用一段持续的任务级对话自行决定下一次工具调用或最终回答。
            engine = AgentLoopEngine(**common)
            activation = next(
                (tool for tool in controller_tools if tool.name == "activate_skill"),
                None,
            )
            if activation is not None:
                activation.set_tool_filter_callback(engine.set_allowed_tools)
            return engine

        if kind == EngineKind.SEQUENTIAL:
            # Sequential 严格逐步执行，整份计划共用一个 ActionExecutor；后一步可读取
            # 前一步累积的输出，例如先定位城市，再用该城市查询天气。
            return SequentialPlanAndExecuteEngine(
                executor=self._build_action_executor(controller_tools),
                **common,
            )
        # DAG 的就绪节点可能并行执行，因此传入 factory 为每个节点创建独立的
        # ActionExecutor，避免不同节点共享可变的工具循环历史和统计状态。
        return DagPlanAndExecuteEngine(
            executor=None,
            executor_factory=self._build_action_executor,
            **common,
        )

    def _gather_context(self, request: TaskRequest) -> str:
        # 按“显式上下文 -> 项目知识 -> 长期记忆 -> 经验提示”的顺序拼接。
        # 例如调用方给出的城市不会被丢弃，检索到的天气工具说明会追加在其后。
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
            # 外部知识和历史记忆在进入模型前统一扫描；例如提示注入内容可在此被中和。
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
