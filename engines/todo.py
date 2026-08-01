"""Adaptive TODO-list orchestration engine."""

from __future__ import annotations

import time

from agents.emergent_planner import EmergentPlannerAgent
from core.models import EngineKind, TaskRequest
from engines.base import TaskEngine
from execution.models import resolve_effort


class TodoEngine(TaskEngine):
    kind = EngineKind.TODO

    async def run(self, request: TaskRequest):
        started_at = time.time()
        self.events.emit("engine_started", {"engine": self.kind.value})
        planner = EmergentPlannerAgent(
            llm_client=self.llm_client,
            tools=self.tools,
            max_iterations=self.settings.execution.max_action_iterations,
            max_outer_iterations=self.settings.engines.max_todo_iterations,
            context_manager=self.context_manager,
            action_executor=self.executor,
            on_event=self.events.legacy_callback,
            max_retries=self.settings.engines.max_todo_retries,
            max_todo_items=self.settings.engines.max_todo_items,
            node_timeout=self.settings.engines.node_timeout_seconds,
            parallel_todos=self.settings.capabilities.parallel_todos,
            subagent_enabled=self.settings.capabilities.subagent,
        )
        answer = await planner.execute(
            request.task,
            request.context,
            effort=resolve_effort(self.effort),
        )
        self.executor.results = [
            self.executor.from_legacy(item) for item in planner.last_results
        ]
        todo_list = planner._todo_list
        success = bool(todo_list and todo_list.todos) and all(
            item.status.value == "completed" for item in todo_list.todos.values()
        )
        result = self.result(
            request,
            answer=answer,
            success=success,
            started_at=started_at,
            metadata={"todos": todo_list.model_dump() if todo_list else {}},
        )
        self.emit_completed(result)
        return result
