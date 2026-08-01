"""Goal-anchored planning and execution engine."""

from __future__ import annotations

import time

from agents.goal_driven_planner import GoalDrivenPlannerAgent
from core.models import EngineKind, TaskRequest
from engines.base import TaskEngine
from execution.models import resolve_effort


class GoalEngine(TaskEngine):
    kind = EngineKind.GOAL

    async def run(self, request: TaskRequest):
        started_at = time.time()
        self.events.emit("engine_started", {"engine": self.kind.value})
        planner = GoalDrivenPlannerAgent(
            llm_client=self.llm_client,
            tools=self.tools,
            max_iterations=self.settings.execution.max_action_iterations,
            context_manager=self.context_manager,
            action_executor=self.executor,
            on_event=self.events.legacy_callback,
            max_outer_iterations=self.settings.engines.max_todo_iterations,
            reanchor_interval=self.settings.engines.goal_reanchor_interval,
            reflection_interval=self.settings.engines.goal_reflection_interval,
            stagnation_window=self.settings.engines.goal_stagnation_window,
            node_timeout=self.settings.engines.node_timeout_seconds,
            max_retries=self.settings.engines.max_todo_retries,
            max_todo_items=self.settings.engines.max_todo_items,
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
        success = planner.goal_satisfied or (
            bool(todo_list and todo_list.todos)
            and all(item.status.value == "completed" for item in todo_list.todos.values())
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
