"""Flat plan followed by sequential action execution and reflection."""

from __future__ import annotations

import time

from agents.planner import PlannerAgent
from agents.reflector import ReflectorAgent
from core.models import Action, EngineKind, TaskRequest
from engines.base import TaskEngine
from engines.sequential_models import StepStatus


class SequentialPlanEngine(TaskEngine):
    kind = EngineKind.SEQUENTIAL

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
        plan = await planner.create_plan(request.task, request.context)
        self.events.emit("plan_created", plan.model_dump())

        all_results = []
        accumulated_context = request.context
        replans = 0
        planned_actions = len(plan.steps)
        current_results = []
        reflection = None
        while True:
            current_results = []
            for step in plan.steps:
                step.status = StepStatus.RUNNING
                action = Action(id=str(step.id), description=step.description)
                action_result = await self.executor.execute_legacy(
                    action,
                    context=accumulated_context,
                    effort=self.effort,
                )
                current_results.append(action_result)
                all_results.append(action_result)
                step.status = (
                    StepStatus.COMPLETED if action_result.success else StepStatus.FAILED
                )
                if action_result.output:
                    accumulated_context += f"\n\n[{action.id}] {action_result.output}"
                if not action_result.success:
                    break

            reflection = await reflector.reflect(request.task, plan, current_results)
            self.events.emit("reflection", reflection.model_dump())
            current_success = bool(current_results) and all(
                item.success for item in current_results
            )
            if current_success and reflection.passed:
                break
            if replans >= self.settings.engines.max_replan_attempts:
                break

            replans += 1
            self.events.emit(
                "replan_started",
                {"attempt": replans, "feedback": reflection.feedback},
            )
            failed_steps = [
                step for step in plan.steps if step.status == StepStatus.FAILED
            ]
            plan = await planner.replan(
                request.task,
                all_results,
                failed_steps=failed_steps,
                feedback=reflection.feedback,
            )
            planned_actions += len(plan.steps)
            self.events.emit(
                "plan_created",
                {**plan.model_dump(), "replan_attempt": replans},
            )
            if not plan.steps:
                break

        raw = "\n\n".join(result.output for result in all_results if result.output)
        answer = await self.synthesize(request.task, raw)
        success = bool(current_results) and all(
            result.success for result in current_results
        )
        success = success and bool(reflection and reflection.passed)
        result = self.result(
            request,
            answer=answer,
            success=success,
            started_at=started_at,
            metadata={
                "reflection": reflection.model_dump(),
                "planned_actions": planned_actions,
                "replans": replans,
            },
        )
        self.emit_completed(result)
        return result
