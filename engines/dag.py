"""Adaptive Plan-and-Execute engine backed by a dependency graph."""

from __future__ import annotations

import time
from typing import Any

from agents.planner import PlannerAgent
from agents.reflector import ReflectorAgent
from core.models import Action, Effort, EngineKind, TaskRequest
from dag.executor import DAGExecutor
from dag.models import NodeStatus, NodeType
from engines.base import PlanAndExecuteEngine
from engines.sequential_models import Reflection
from execution.base import ActionExecutor
from execution.models import resolve_effort


class _DagActionAdapter:
    def __init__(
        self,
        engine: PlanAndExecuteEngine,
        action_executor=None,
        results=None,
    ) -> None:
        self._engine = engine
        self._action_executor = action_executor
        self.results: list[Any] = results if results is not None else []

    def create_for_node(self, _node_id: str) -> "_DagActionAdapter":
        # 每个 DAG 节点拿到独立 ActionExecutor，但共享结果列表。例如两个并行检索节点
        # 各自维护工具对话历史，完成后仍会汇总到同一个 engine.actions 中。
        return _DagActionAdapter(
            self._engine,
            action_executor=self._engine.new_action_executor(),
            results=self.results,
        )

    async def execute_node(self, node, context: str = "", *, effort=None):
        action = Action(
            id=str(node.id),
            description=node.description,
            success_criteria=node.exit_criteria.description if node.exit_criteria else "",
        )
        selected_effort = self._engine.effort
        if effort is not None:
            selected_effort = Effort(getattr(effort, "value", effort))
        if self._action_executor is None:
            self._action_executor = self._engine.new_action_executor()
        core_result = await self._action_executor.execute(
            action,
            context=context,
            effort=selected_effort,
        )
        self._engine.actions.append(core_result)
        result = core_result.to_legacy()
        self.results.append(result)
        return result

    def record_external_result(self, result) -> None:
        """Record timeout/unexpected DAG results created outside the adapter."""
        self._engine.actions.append(ActionExecutor.from_legacy(result))
        self.results.append(result)


class DagPlanAndExecuteEngine(PlanAndExecuteEngine):
    kind = EngineKind.DAG

    async def run(self, request: TaskRequest):
        return await self.run_with_failure_boundary(request, self._run_unchecked)

    async def _run_unchecked(self, request: TaskRequest):
        started_at = time.time()
        records_before = self.usage_marker()
        self.events.emit("engine_started", {"engine": self.kind.value})
        planner = PlannerAgent(
            self.llm_client,
            self.context_manager,
            temperature=self.settings.engines.planner_temperature,
            prompt_capabilities=self.prompt_capabilities,
        )
        reflector = ReflectorAgent(
            self.llm_client,
            self.context_manager,
            temperature=self.settings.engines.reflector_temperature,
            prompt_capabilities=self.prompt_capabilities,
        )
        # Planner 生成带依赖关系的图，而非固定列表。例如“定位”完成后才能运行
        # “查天气”，但“查交通”可与“查天气”在同一就绪层并行。
        self.events.emit("planner_started", {"operation": "create_dag"})
        dag = await self.model_operation(
            planner.create_dag(request.task, request.context),
            label="Planner",
        )
        planned_actions = [
            node for node in dag.nodes.values() if node.node_type == NodeType.ACTION
        ]
        if not planned_actions:
            self.reject_invalid_response(
                "Planner returned a DAG with no executable actions"
            )
        if any(
            not str(node.id).strip() or not node.description.strip()
            for node in planned_actions
        ):
            self.reject_invalid_response(
                "Planner returned a DAG action without an ID or description"
            )
        self.events.emit("planner_completed", {"operation": "create_dag"})
        dag.max_checkpoints = self.settings.engines.dag_checkpoint_history_limit
        self.events.emit("dag_created", dag.to_dict())

        adapter = _DagActionAdapter(self)
        # DAGExecutor 负责依赖判定、并发、条件跳过和超时；Adapter 只把一个就绪节点
        # 转成公共 Action 并交给 ActionExecutor，因此 DAG 层不直接调用具体工具。
        runner = DAGExecutor(
            node_executor=adapter,
            reflector=reflector,
            planner=planner if self.settings.engines.adaptive_planning else None,
            max_parallel=self.settings.engines.max_parallel_nodes,
            on_event=self.events.legacy_callback,
            effort=resolve_effort(self.effort),
            serial_execution=(
                self.settings.engines.dag_serial_execution
                or self.settings.capabilities.skills
            ),
            node_timeout=self.settings.engines.node_timeout_seconds,
            adaptive_enabled=self.settings.engines.adaptive_planning,
            adaptive_interval=self.settings.engines.adaptive_interval,
            adaptive_min_completed=self.settings.engines.adaptive_min_completed,
        )
        self.events.emit("dag_execution_started", {"operation": "execute"})
        raw = await runner.execute(dag)
        self.events.emit("dag_execution_completed", {"operation": "execute"})
        # 等整张图停止后再做任务级反思与答案合成；节点输出本身不是最终用户回答。
        self.events.emit("reflector_started", {"operation": "reflect_dag"})
        reflection = await self.model_operation(
            reflector.reflect_dag(request.task, dag, adapter.results),
            label="Reflector",
        )
        if not isinstance(reflection, Reflection):
            self.reject_invalid_response(
                "Reflector returned an invalid response object"
            )
        self.events.emit(
            "reflector_completed",
            {"operation": "reflect_dag", "success": reflection.passed},
        )
        answer = await self.synthesize(request.task, raw)
        action_nodes = [
            node for node in dag.nodes.values() if node.node_type == NodeType.ACTION
        ]
        completed_actions = [
            node for node in action_nodes if node.status == NodeStatus.COMPLETED
        ]
        # 严格成功要求图完整、至少执行过一个 Action、没有失败节点且反思通过。
        # 例如条件节点让分支合法跳过可以完成，但天气 Action 失败不能仅靠 dag.is_complete 判成功。
        success = (
            dag.is_complete()
            and bool(completed_actions)
            and not runner.failed_action_ids
            and reflection.passed
        )
        failure_reason = reflection.failure_reason or (
            runner.failure_reasons[-1] if runner.failure_reasons else None
        )
        result = self.plan_result(
            request,
            output=answer,
            success=success,
            started_at=started_at,
            records_before=records_before,
            stop_reason=failure_reason,
            metadata={
                "reflection": reflection.model_dump(),
                "dag": dag.to_dict(),
                "failed_action_ids": sorted(runner.failed_action_ids),
                "condition_skipped_ids": sorted(runner.condition_skipped_ids),
                "failure_reasons": [
                    reason.value for reason in runner.failure_reasons
                ],
            },
        )
        self.emit_completed(result)
        return result
