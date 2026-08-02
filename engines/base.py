"""Shared engine contracts for the two retained orchestration paradigms."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TypeVar

from agents.prompt_utils import PromptCapabilities
from context.manager import ContextManager
from core.events import EventBus
from core.models import (
    Action,
    ActionResult,
    Effort,
    EngineKind,
    EngineResult,
    EngineStats,
    EngineStopReason,
    TaskRequest,
)
from core.settings import AppSettings
from execution.base import ActionExecutor
from llm.client import LLMClient
from tools.base import BaseTool


_T = TypeVar("_T")


class _PlanModelError(RuntimeError):
    """A model-backed planning operation failed before producing a response."""


class _PlanInvalidResponse(ValueError):
    """A model-backed planning operation produced an unusable structure."""


class RecordingActionExecutor(ActionExecutor):
    """Record the action trajectory without changing executor behavior."""

    def __init__(
        self,
        inner: ActionExecutor,
        results: list[ActionResult] | None = None,
    ) -> None:
        self.inner = inner
        self.results = results if results is not None else []

    async def execute(
        self,
        action: Action,
        context: str = "",
        effort: Effort = Effort.MEDIUM,
    ) -> ActionResult:
        try:
            result = await self.inner.execute(action, context=context, effort=effort)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.results.append(
                ActionResult(
                    action_id=action.id,
                    success=False,
                    output=f"Action execution failed: {type(exc).__name__}: {exc}",
                    error=f"{type(exc).__name__}: {exc}",
                    failure_reason=EngineStopReason.ENGINE_ERROR,
                )
            )
            raise
        self.results.append(result)
        return result

    def set_allowed_tools(self, tool_names: list[str] | None) -> None:
        setter = getattr(self.inner, "set_allowed_tools", None)
        if callable(setter):
            setter(tool_names)


class TaskEngine(ABC):
    """Engine-neutral task contract; it deliberately has no executor."""

    kind: EngineKind

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
        self.llm_client = llm_client
        self.settings = settings
        self.events = events
        self.effort = effort
        self.context_manager = context_manager or ContextManager()
        self.tools = list(tools.values()) if isinstance(tools, dict) else list(tools or [])
        self.guardrail = guardrail
        activation = next(
            (tool for tool in self.tools if tool.name == "activate_skill"),
            None,
        )
        self.prompt_capabilities = (
            prompt_capabilities
            or PromptCapabilities.from_tools(
                self.tools,
                python_command=self.settings.tools.python_command,
                hitl_configured=self.settings.capabilities.hitl,
                hitl_max_prompts=self.settings.capabilities.hitl_max_prompts,
                skill_descriptions=getattr(activation, "skill_descriptions", ""),
                skills_max_activations=getattr(activation, "max_activations", 1),
            )
        )

    @abstractmethod
    async def run(self, request: TaskRequest) -> EngineResult:
        """Run one task request."""

    def usage_marker(self) -> int:
        return len(self.llm_client.get_call_records())

    def stats_since(
        self,
        records_before: int,
        *,
        actions: list[ActionResult] | None = None,
        local_stats: EngineStats | None = None,
    ) -> EngineStats:
        """Aggregate the whole call tree without counting child work twice.

        Runtime call records are the best whole-tree source when providers
        report usage.  Loop-local counters remain necessary for fake clients,
        disabled/missing usage, and reasoning text that must be estimated.
        Child AgentLoop work is intentionally absent from its parent's local
        loop counters, so it is added once from each tool's aggregate_stats and
        the combined observation is compared with the global record delta.
        """
        records = self.llm_client.get_call_records()[records_before:]
        action_results = actions or []
        calls = [call for action in action_results for call in action.tool_calls]
        if local_stats is None:
            root_stats = EngineStats(
                llm_calls=sum(action.stats.llm_calls for action in action_results),
                agent_turns=sum(
                    action.stats.agent_turns for action in action_results
                ),
                context_compaction_calls=sum(
                    action.stats.context_compaction_calls
                    for action in action_results
                ),
                prompt_tokens=sum(
                    action.stats.prompt_tokens for action in action_results
                ),
                completion_tokens=sum(
                    action.stats.completion_tokens for action in action_results
                ),
                total_tokens=sum(
                    action.stats.total_tokens for action in action_results
                ),
                tool_calls=sum(
                    action.stats.tool_calls for action in action_results
                ),
                reasoning_tokens=sum(
                    action.stats.reasoning_tokens for action in action_results
                ),
                subagent_calls=sum(
                    action.stats.subagent_calls for action in action_results
                ),
            )
        else:
            root_stats = local_stats.model_copy(deep=True)

        child_stats = EngineStats()
        for tool in self.tools:
            aggregate = getattr(tool, "aggregate_stats", None)
            if aggregate is None:
                continue
            child_stats.llm_calls += int(aggregate.llm_calls)
            child_stats.agent_turns += int(aggregate.agent_turns)
            child_stats.context_compaction_calls += int(
                aggregate.context_compaction_calls
            )
            child_stats.prompt_tokens += int(aggregate.prompt_tokens)
            child_stats.completion_tokens += int(aggregate.completion_tokens)
            child_stats.total_tokens += int(aggregate.total_tokens)
            child_stats.tool_calls += int(aggregate.tool_calls)
            child_stats.reasoning_tokens += int(aggregate.reasoning_tokens)
            child_stats.subagent_calls += int(aggregate.subagent_calls)

        root_tool_calls = max(root_stats.tool_calls, len(calls))
        root_subagent_calls = max(
            root_stats.subagent_calls,
            sum(call.tool_name == "subagent" for call in calls),
        )
        locally_observed_llm_calls = root_stats.llm_calls + child_stats.llm_calls
        locally_observed_reasoning = (
            root_stats.reasoning_tokens + child_stats.reasoning_tokens
        )
        recorded_prompt_tokens = sum(
            int(record.prompt_tokens or 0) for record in records
        )
        recorded_completion_tokens = sum(
            int(record.completion_tokens or 0) for record in records
        )
        recorded_total_tokens = sum(
            int(record.total_tokens or 0)
            or int(record.prompt_tokens or 0)
            + int(record.completion_tokens or 0)
            for record in records
        )
        return EngineStats(
            llm_calls=max(len(records), locally_observed_llm_calls),
            agent_turns=root_stats.agent_turns + child_stats.agent_turns,
            context_compaction_calls=(
                root_stats.context_compaction_calls
                + child_stats.context_compaction_calls
            ),
            prompt_tokens=max(
                recorded_prompt_tokens,
                root_stats.prompt_tokens + child_stats.prompt_tokens,
            ),
            completion_tokens=max(
                recorded_completion_tokens,
                root_stats.completion_tokens + child_stats.completion_tokens,
            ),
            total_tokens=max(
                recorded_total_tokens,
                root_stats.total_tokens + child_stats.total_tokens,
            ),
            tool_calls=root_tool_calls + child_stats.tool_calls,
            reasoning_tokens=max(
                sum(record.reasoning_tokens for record in records),
                locally_observed_reasoning,
            ),
            subagent_calls=max(
                root_subagent_calls,
                child_stats.subagent_calls,
            ),
        )

    def make_result(
        self,
        request: TaskRequest,
        *,
        output: str,
        success: bool,
        started_at: float,
        stop_reason: EngineStopReason = EngineStopReason.COMPLETED,
        stats: EngineStats | None = None,
        actions: list[ActionResult] | None = None,
        metadata: dict | None = None,
    ) -> EngineResult:
        return EngineResult(
            output=output,
            success=success,
            engine=self.kind,
            effort=self.effort,
            run_id=request.run_id,
            task_id=request.task_id,
            stop_reason=stop_reason,
            stats=stats or EngineStats(),
            actions=list(actions or []),
            started_at=started_at,
            finished_at=time.time(),
            metadata=metadata or {},
        )

    def emit_completed(self, result: EngineResult) -> None:
        self.events.emit(
            "engine_completed",
            {
                "engine": result.engine.value,
                "success": result.success,
                "stop_reason": result.stop_reason.value,
                "action_count": len(result.actions),
                "duration_ms": result.duration_ms,
                "stats": result.stats.model_dump(),
            },
        )


class PlanAndExecuteEngine(TaskEngine):
    """Shared implementation boundary for Sequential and DAG engines."""

    def __init__(
        self,
        llm_client: LLMClient,
        executor: ActionExecutor | None,
        settings: AppSettings,
        events: EventBus,
        effort: Effort,
        context_manager: ContextManager | None = None,
        tools: list[BaseTool] | dict[str, BaseTool] | None = None,
        executor_factory: Callable[[], ActionExecutor] | None = None,
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
        self.actions: list[ActionResult] = []
        self.executor = (
            RecordingActionExecutor(executor, results=self.actions)
            if executor is not None
            else None
        )
        self.executor_factory = executor_factory

    def new_action_executor(self) -> ActionExecutor:
        if self.executor_factory is not None:
            return self.executor_factory()
        if self.executor is not None:
            return self.executor.inner
        raise RuntimeError("Plan-and-Execute engine has no ActionExecutor")

    async def model_operation(
        self,
        operation: Awaitable[_T],
        *,
        label: str,
    ) -> _T:
        """Classify failures at an explicitly model-backed P&E boundary.

        Planner methods include both the provider call and response-model
        validation.  Keeping that whole operation inside this boundary lets the
        public engine result distinguish a provider failure from an unusable
        response without treating unrelated executor bugs as model failures.
        """
        try:
            return await operation
        except asyncio.CancelledError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            raise _PlanInvalidResponse(
                f"{label} returned an invalid response: {type(exc).__name__}: {exc}"
            ) from exc
        except Exception as exc:
            raise _PlanModelError(
                f"{label} model call failed: {type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def reject_invalid_response(message: str) -> None:
        """Terminate a P&E run whose model output has no executable shape."""
        raise _PlanInvalidResponse(message)

    async def run_with_failure_boundary(
        self,
        request: TaskRequest,
        operation: Callable[[TaskRequest], Awaitable[EngineResult]],
    ) -> EngineResult:
        """Return a typed failure result for every non-cancellation exception."""
        started_at = time.time()
        records_before = self.usage_marker()
        try:
            return await operation(request)
        except asyncio.CancelledError:
            raise
        except _PlanInvalidResponse as exc:
            failure = exc
            stop_reason = EngineStopReason.INVALID_RESPONSE
        except _PlanModelError as exc:
            failure = exc
            stop_reason = EngineStopReason.MODEL_ERROR
        except Exception as exc:
            failure = exc
            stop_reason = EngineStopReason.ENGINE_ERROR

        # Plan-and-Execute 在这里把异常收敛成可比较的 EngineResult。例如 Planner 的
        # 非法 JSON 是 INVALID_RESPONSE，ActionExecutor 自身崩溃则是 ENGINE_ERROR。
        actions = list(self.actions)
        result = self.make_result(
            request,
            output=str(failure),
            success=False,
            started_at=started_at,
            stop_reason=stop_reason,
            stats=self.stats_since(records_before, actions=actions),
            actions=actions,
            metadata={
                "failure_type": type(failure).__name__,
            },
        )
        self.emit_completed(result)
        return result

    async def synthesize(self, task: str, raw_output: str) -> str:
        if not raw_output.strip():
            return "No usable result was produced."
        # Sequential/DAG 的步骤输出面向执行，不一定适合直接展示；因此另调用一次 LLM
        # 合成用户答案。例如把“定位结果 + 预报 JSON”整理成同语言的天气摘要。
        prompt = (
            "Create the final response for the user from the execution results below. "
            "Preserve concrete facts and artifact paths, omit internal planning details, "
            "and answer in the same language as the task.\n\n"
            f"Task:\n{task}\n\nExecution results:\n{raw_output}"
        )
        answer = await self.model_operation(
            self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                caller_tag=f"engine.{self.kind.value}.synthesis",
            ),
            label="Answer synthesizer",
        )
        if not isinstance(answer, str) or not answer.strip():
            self.reject_invalid_response(
                "Answer synthesizer returned an empty response"
            )
        return answer

    def plan_result(
        self,
        request: TaskRequest,
        *,
        output: str,
        success: bool,
        started_at: float,
        records_before: int,
        metadata: dict | None = None,
        stop_reason: EngineStopReason | None = None,
    ) -> EngineResult:
        actions = list(self.actions)
        # success 与 stop_reason 分开解析：成功固定为 COMPLETED；失败优先使用反思/执行器
        # 给出的具体原因，否则回看最后一个失败 Action，最后才退化为 ENGINE_ERROR。
        if success:
            resolved_stop_reason = EngineStopReason.COMPLETED
        elif stop_reason is not None:
            resolved_stop_reason = stop_reason
        else:
            last_failed = next(
                (action for action in reversed(actions) if not action.success),
                None,
            )
            resolved_stop_reason = (
                last_failed.failure_reason
                if last_failed is not None and last_failed.failure_reason is not None
                else EngineStopReason.ENGINE_ERROR
            )
        return self.make_result(
            request,
            output=output,
            success=success,
            started_at=started_at,
            stop_reason=resolved_stop_reason,
            stats=self.stats_since(records_before, actions=actions),
            actions=actions,
            metadata=metadata,
        )
