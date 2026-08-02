"""Flat plan followed by sequential action execution and reflection."""

from __future__ import annotations

import time

from agents.planner import PlannerAgent
from agents.reflector import ReflectorAgent
from core.models import Action, EngineKind, TaskRequest
from engines.base import PlanAndExecuteEngine
from engines.sequential_models import Reflection, StepStatus


class SequentialPlanAndExecuteEngine(PlanAndExecuteEngine):
    kind = EngineKind.SEQUENTIAL

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
        # Planner 先把整项任务拆成有序的扁平步骤。例如“明天天气”可拆成
        # step 1 获取位置、step 2 查询预报、step 3 整理结论。
        self.events.emit("planner_started", {"operation": "create_plan"})
        plan = await self.model_operation(
            planner.create_plan(request.task, request.context),
            label="Planner",
        )
        if not plan.steps:
            self.reject_invalid_response("Planner returned no executable steps")
        if any(not step.description.strip() for step in plan.steps):
            self.reject_invalid_response(
                "Planner returned a step without a description"
            )
        if len({step.id for step in plan.steps}) != len(plan.steps):
            self.reject_invalid_response("Planner returned duplicate step IDs")
        self.events.emit("planner_completed", {"operation": "create_plan"})
        self.events.emit("plan_created", plan.model_dump())

        all_results = []
        accumulated_context = request.context
        replans = 0
        planned_actions = len(plan.steps)
        current_results = []
        reflection = None
        if self.executor is None:
            raise RuntimeError("Sequential engine requires one ActionExecutor")
        # 外层每轮代表一次完整计划尝试；反思不通过时，Planner 会生成一份新计划，
        # 并在 max_replan_attempts 预算内回到这里重新执行。
        while True:
            current_results = []
            # 同一份计划严格按顺序执行，并把每步输出追加到 accumulated_context。
            # 例如 step 1 输出“Los Angeles”，step 2 的 ActionToolLoop 就能读取该城市。
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

            # Action 成功只表示步骤循环正常结束，Reflector 还要判断结果是否满足原任务。
            # 例如天气工具请求成功但返回了今天而非明天，reflection 仍应判为不通过。
            self.events.emit("reflector_started", {"operation": "reflect"})
            reflection = await self.model_operation(
                reflector.reflect(request.task, plan, current_results),
                label="Reflector",
            )
            if not isinstance(reflection, Reflection):
                self.reject_invalid_response(
                    "Reflector returned an invalid response object"
                )
            self.events.emit(
                "reflector_completed",
                {"operation": "reflect", "success": reflection.passed},
            )
            self.events.emit("reflection", reflection.model_dump())
            current_success = bool(current_results) and all(
                item.success for item in current_results
            )
            if current_success and reflection.passed:
                break
            if replans >= self.settings.engines.max_replan_attempts:
                break

            # 重新规划会同时参考全部历史结果、失败步骤和反思反馈，避免只机械重跑原计划。
            replans += 1
            self.events.emit(
                "replan_started",
                {"attempt": replans, "feedback": reflection.feedback},
            )
            failed_steps = [
                step for step in plan.steps if step.status == StepStatus.FAILED
            ]
            self.events.emit(
                "planner_started",
                {"operation": "replan", "attempt": replans},
            )
            plan = await self.model_operation(
                planner.replan(
                    request.task,
                    all_results,
                    failed_steps=failed_steps,
                    feedback=reflection.feedback,
                ),
                label="Replanner",
            )
            self.events.emit(
                "planner_completed",
                {"operation": "replan", "attempt": replans},
            )
            planned_actions += len(plan.steps)
            self.events.emit(
                "plan_created",
                {**plan.model_dump(), "replan_attempt": replans},
            )
            if not plan.steps:
                self.reject_invalid_response("Replanner returned no executable steps")
            if any(not step.description.strip() for step in plan.steps):
                self.reject_invalid_response(
                    "Replanner returned a step without a description"
                )
            if len({step.id for step in plan.steps}) != len(plan.steps):
                self.reject_invalid_response("Replanner returned duplicate step IDs")

        # 最终 synthesis 汇总所有尝试中产生的可用输出，而 success 只取决于
        # 最后一轮步骤是否全成功且 reflection.passed，二者不能混为一谈。
        raw = "\n\n".join(result.output for result in all_results if result.output)
        answer = await self.synthesize(request.task, raw)
        success = bool(current_results) and all(
            result.success for result in current_results
        )
        success = success and bool(reflection and reflection.passed)
        failure_reason = reflection.failure_reason or next(
            (
                item.failure_reason
                for item in reversed(current_results)
                if not item.success
            ),
            None,
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
                "plan": plan.model_dump(),
                "planned_actions": planned_actions,
                "replans": replans,
            },
        )
        self.emit_completed(result)
        return result
