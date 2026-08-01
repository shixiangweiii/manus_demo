"""Hierarchical dependency-graph engine."""

from __future__ import annotations

import time
from typing import Any

from agents.planner import PlannerAgent
from agents.reflector import ReflectorAgent
from core.models import Action, EngineKind, TaskRequest
from dag.executor import DAGExecutor
from engines.base import TaskEngine
from execution.react import to_legacy_effort
from dag.models import NodeStatus, NodeType


class _DagActionAdapter:
    def __init__(self, engine: TaskEngine, action_executor=None, results=None) -> None:
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
        core_result = await self._action_executor.execute(
            action,
            context=context,
            effort=self._engine.effort,
        )
        self._engine.executor.results.append(core_result)
        result = core_result.to_legacy()
        self.results.append(result)
        return result


class DagEngine(TaskEngine):
    kind = EngineKind.DAG

    async def run(self, request: TaskRequest):
        started_at = time.time()
        self.events.emit("engine_started", {"engine": self.kind.value})
        planner = PlannerAgent(
            self.llm_client,
            self.context_manager,
            temperature=self.settings.engines.planner_temperature,
        )
        reflector = ReflectorAgent(
            self.llm_client,
            self.context_manager,
            temperature=self.settings.engines.reflector_temperature,
        )
        dag = await planner.create_dag(request.task, request.context)
        dag.max_checkpoints = self.settings.capabilities.checkpoint_max_per_task
        self.events.emit("dag_created", dag.to_dict())

        adapter = _DagActionAdapter(self)
        runner = DAGExecutor(
            node_executor=adapter,
            reflector=reflector,
            planner=planner if self.settings.engines.adaptive_planning else None,
            max_parallel=self.settings.engines.max_parallel_nodes,
            on_event=self.events.legacy_callback,
            effort=to_legacy_effort(self.effort),
            serial_execution=(
                self.settings.engines.dag_serial_execution
                or self.settings.capabilities.skills
            ),
            node_timeout=self.settings.engines.node_timeout_seconds,
            adaptive_enabled=self.settings.engines.adaptive_planning,
            adaptive_interval=self.settings.engines.adaptive_interval,
            adaptive_min_completed=self.settings.engines.adaptive_min_completed,
        )
        raw = await runner.execute(dag)
        reflection = await reflector.reflect_dag(request.task, dag, adapter.results)
        answer = await self.synthesize(request.task, raw)
        action_nodes = [node for node in dag.nodes.values() if node.node_type == NodeType.ACTION]
        success = bool(action_nodes) and all(
            node.status == NodeStatus.COMPLETED for node in action_nodes
        ) and reflection.passed
        result = self.result(
            request,
            answer=answer,
            success=success,
            started_at=started_at,
            metadata={
                "reflection": reflection.model_dump(),
                "dag": dag.to_dict(),
            },
        )
        self.emit_completed(result)
        return result
