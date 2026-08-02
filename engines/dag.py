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
        self._action_executor = action_executor or engine.new_action_executor()
        self.results: list[Any] = results if results is not None else []

    def create_for_node(self, _node_id: str) -> "_DagActionAdapter":
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
        core_result = await self._action_executor.execute(
            action,
            context=context,
            effort=selected_effort,
        )
        self._engine.executor.results.append(core_result)
        result = core_result.to_legacy()
        self.results.append(result)
        return result

    def record_external_result(self, result) -> None:
        """Record timeout/unexpected DAG results created outside the adapter."""
        self._engine.executor.results.append(ActionExecutor.from_legacy(result))
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
        dag.max_checkpoints = self.settings.capabilities.checkpoint_max_per_task
        self.events.emit("dag_created", dag.to_dict())

        adapter = _DagActionAdapter(self)
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
