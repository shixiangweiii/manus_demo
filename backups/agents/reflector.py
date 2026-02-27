"""
Reflector Agent - Validates and reflects on task execution results.
Reflector 智能体 —— 验证并反思任务执行结果。

After all plan steps have been executed, the reflector:
  1. Evaluates whether the task was truly completed.
  2. Assesses the quality of the output.
  3. Provides specific feedback and improvement suggestions.
  4. Decides if re-planning is needed.

所有计划步骤执行完毕后，Reflector：
  1. 评估任务是否真正完成
  2. 评估输出质量
  3. 提供具体反馈和改进建议
  4. 决定是否需要触发重规划

v2: Added validate_exit_criteria() for per-node validation in DAG mode,
    and reflect_dag() for evaluating a full TaskDAG.
v2: 新增 validate_exit_criteria() 用于 DAG 模式下的逐节点验证，
    以及 reflect_dag() 用于评估完整 TaskDAG 执行结果。
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base import BaseAgent
from context.manager import ContextManager
from llm.client import LLMClient
from schema import Plan, Reflection, StepResult, TaskNode

logger = logging.getLogger(__name__)

REFLECTOR_SYSTEM_PROMPT = """\
You are a reflection and verification agent. Your job is to evaluate the
quality and completeness of task execution results.

Given the original task, the execution plan, and the results of each step,
you must:

1. Assess whether the task objective was fully achieved.
2. Identify any gaps, errors, or areas for improvement.
3. Provide a quality score from 0.0 to 1.0.
4. Decide if re-planning is needed (passed=false) or if the result is acceptable (passed=true).

Respond with a valid JSON object in this exact format:
{
  "passed": true/false,
  "score": 0.0-1.0,
  "feedback": "Overall evaluation of the results",
  "suggestions": ["suggestion 1", "suggestion 2"]
}

Be fair but rigorous. Only set passed=false if there are significant issues.
"""


class ReflectorAgent(BaseAgent):
    """
    Validates execution results and provides quality feedback.
    验证执行结果并提供质量反馈。

    The reflector acts as a quality gate: if the results are inadequate,
    it returns passed=false with specific feedback, triggering re-planning.
    Reflector 充当质量门控：若结果不足，返回 passed=false 并附具体反馈，触发重规划。

    v2 additions:
      - validate_exit_criteria(): per-node LLM check (lightweight)
      - reflect_dag(): full DAG evaluation

    v2 新增：
      - validate_exit_criteria()：逐节点的轻量级 LLM 验证（yes/no 问题）
      - reflect_dag()：对完整 TaskDAG 执行结果进行全面评估
    """

    def __init__(self, llm_client: LLMClient, context_manager: ContextManager | None = None):
        super().__init__(
            name="Reflector",
            system_prompt=REFLECTOR_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )

    # ------------------------------------------------------------------
    # Per-node exit criteria validation (v2 - DAG mode)
    # 逐节点完成判据验证（v2 - DAG 模式）
    # ------------------------------------------------------------------

    async def validate_exit_criteria(self, node: TaskNode, result: StepResult) -> bool:
        """
        Lightweight LLM check: does the node's result satisfy its exit criteria?
        轻量级 LLM 验证：节点的执行结果是否满足其完成判据？

        This runs after each node execution in the DAG super-step loop.
        It's much cheaper than a full reflect() call — just a yes/no question.
        在 DAG Super-step 循环中每个节点执行完毕后调用。
        比完整的 reflect() 调用代价低得多——只是一个 yes/no 问题。

        Returns:
            True 表示完成判据满足，False 表示不满足（将触发节点失败处理）。
        """
        if not node.exit_criteria.validation_prompt:
            return result.success  # 无自定义验证 prompt，以执行结果为准

        self.reset()  # 清空历史，使用干净上下文进行判断
        prompt = (
            f"Evaluate whether this action's result meets the exit criteria.\n\n"
            f"ACTION: {node.description}\n"
            f"EXIT CRITERIA: {node.exit_criteria.description}\n"
            f"RESULT:\n{result.output[:1000]}\n\n"
            f"Respond with JSON: {{\"passed\": true/false, \"reason\": \"brief explanation\"}}"
        )

        try:
            data = await self.think_json(prompt, temperature=0.1)  # 低温度保证判断稳定性
            passed = data.get("passed", True)
            reason = data.get("reason", "")
            logger.info(
                "[Reflector] Exit criteria for %s: %s (%s)",
                node.id, "PASSED" if passed else "FAILED", reason[:100],
            )
            return passed
        except Exception as exc:
            # 验证失败时默认通过，避免因 LLM 异常阻断正常流程
            logger.warning("[Reflector] Exit criteria check failed for %s: %s. Defaulting to pass.", node.id, exc)
            return True

    # ------------------------------------------------------------------
    # Full DAG reflection (v2)
    # 完整 DAG 反思（v2）
    # ------------------------------------------------------------------

    async def reflect_dag(self, task: str, dag: Any, results: list[StepResult]) -> Reflection:
        """
        Evaluate the full DAG execution against the original task.
        对完整 DAG 执行结果进行综合评估，与原始任务目标对比。

        Args:
            task:    原始用户任务。
            dag:     已执行的 TaskDAG（包含所有节点状态和结果）。
            results: 各节点的执行结果列表。

        Returns:
            包含通过/失败判定、质量评分和具体反馈的 Reflection 对象。
        """
        self.reset()

        # 构建节点状态摘要（供 LLM 一览全局执行情况）
        nodes_summary = "\n".join(
            f"  {n.id} [{n.node_type.value}]: {n.description} -> {n.status.value}"
            for n in dag.nodes.values()
        )
        # 构建结果摘要（只截取前 300 字符避免 prompt 过长）
        results_summary = "\n".join(
            f"  {nid} [{'OK' if output else 'empty'}]: {output[:300]}"
            for nid, output in dag.state.node_results.items()
        )

        prompt = (
            f"Evaluate the following task execution:\n\n"
            f"TASK: {task}\n\n"
            f"PLAN (DAG nodes):\n{nodes_summary}\n\n"
            f"RESULTS:\n{results_summary}\n\n"
            f"Provide your evaluation as JSON."
        )

        logger.info("[Reflector] Evaluating DAG results for: %s", task[:80])

        try:
            data = await self.think_json(prompt, temperature=0.2)
            reflection = Reflection(
                passed=data.get("passed", False),
                score=float(data.get("score", 0.5)),
                feedback=data.get("feedback", ""),
                suggestions=data.get("suggestions", []),
            )
        except Exception as exc:
            logger.error("[Reflector] Failed to parse reflection: %s", exc)
            # 解析失败时默认通过，避免因 LLM 异常导致无限重规划
            reflection = Reflection(
                passed=True,
                score=0.5,
                feedback=f"Reflection parsing failed: {exc}. Defaulting to pass.",
                suggestions=[],
            )

        logger.info(
            "[Reflector] DAG verdict: %s (score: %.2f)",
            "PASSED" if reflection.passed else "NEEDS REWORK",
            reflection.score,
        )
        return reflection

    # ------------------------------------------------------------------
    # Legacy reflection (v1 - flat plan)
    # 旧版反思（v1 - 扁平计划）
    # ------------------------------------------------------------------

    async def reflect(
        self,
        task: str,
        plan: Plan,
        results: list[StepResult],
    ) -> Reflection:
        """
        Evaluate the execution results against the original task.
        评估执行结果是否满足原始任务要求（旧版 v1 接口，保留向后兼容）。
        """
        self.reset()

        steps_summary = "\n".join(
            f"  Step {s.id}: {s.description}" for s in plan.steps
        )
        results_summary = "\n".join(
            f"  Step {r.step_id} [{'OK' if r.success else 'FAIL'}]: {r.output[:300]}"
            for r in results
        )

        prompt = (
            f"Evaluate the following task execution:\n\n"
            f"TASK: {task}\n\n"
            f"PLAN:\n{steps_summary}\n\n"
            f"RESULTS:\n{results_summary}\n\n"
            f"Provide your evaluation as JSON."
        )

        logger.info("[Reflector] Evaluating results for: %s", task[:80])

        try:
            data = await self.think_json(prompt, temperature=0.2)
            reflection = Reflection(
                passed=data.get("passed", False),
                score=float(data.get("score", 0.5)),
                feedback=data.get("feedback", ""),
                suggestions=data.get("suggestions", []),
            )
        except Exception as exc:
            logger.error("[Reflector] Failed to parse reflection: %s", exc)
            reflection = Reflection(
                passed=True,
                score=0.5,
                feedback=f"Reflection parsing failed: {exc}. Defaulting to pass.",
                suggestions=[],
            )

        logger.info(
            "[Reflector] Verdict: %s (score: %.2f)",
            "PASSED" if reflection.passed else "NEEDS REWORK",
            reflection.score,
        )
        return reflection
