"""
Core evaluation metrics and data models for the evaluation framework.

Defines per-task metrics, aggregated metrics, and computation logic.
Inspired by AgentEval (DAG-structured), Odysseys (Trajectory Efficiency),
and GeoAgentBench (Parameter Execution Accuracy).

评测核心指标和数据模型。

指标体系：
  规划质量   — 分类准确性、计划结构有效性、步骤覆盖率
  执行质量   — 任务成功率、步骤成功率、工具使用准确性
  效率指标   — Token 消耗效率、执行步骤效率、迭代效率
  鲁棒性     — 重规划频率、错误恢复率
  反思准确性 — 反思判定与实际结果的吻合度
"""

from __future__ import annotations

import math
import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ======================================================================
# Enums
# ======================================================================

class PlanMode(str, Enum):
    """Three planning modes under evaluation."""
    SIMPLE = "simple"
    COMPLEX = "complex"
    EMERGENT = "emergent"


class TaskDifficulty(str, Enum):
    """Task difficulty tiers for benchmark categorization."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class FailureCategory(str, Enum):
    """
    Failure taxonomy inspired by AgentEval's 3-level, 21-subcategory system.
    Simplified for this project's scope.
    """
    # Planning failures
    CLASSIFICATION_ERROR = "classification_error"          # 错误路由
    PLAN_STRUCTURE_INVALID = "plan_structure_invalid"      # 计划结构无效（如环依赖）
    PLAN_INCOMPLETE = "plan_incomplete"                     # 计划不完整（遗漏关键步骤）

    # Execution failures
    TOOL_SELECTION_ERROR = "tool_selection_error"           # 选错工具
    TOOL_PARAMETER_ERROR = "tool_parameter_error"           # 工具参数错误
    TOOL_EXECUTION_ERROR = "tool_execution_error"           # 工具执行异常
    MAX_ITERATION_EXCEEDED = "max_iteration_exceeded"       # 超过最大迭代次数
    NODE_TIMEOUT = "node_timeout"                           # 节点执行超时

    # Reflection failures
    FALSE_POSITIVE = "false_positive"                       # 反思通过但实际不达标
    FALSE_NEGATIVE = "false_negative"                       # 反思拒绝但实际已达标

    # System failures
    LLM_CALL_FAILURE = "llm_call_failure"                   # LLM 调用失败
    PARSE_FAILURE = "parse_failure"                          # LLM 输出解析失败

    # v9/v13 execution failures
    SUBAGENT_FAILED = "subagent_failed"                     # v9: SubAgent 执行失败 / 超时 / 上限
    HITL_TIMEOUT = "hitl_timeout"                           # v13: ask_user 等待超时
    HITL_CANCELLED = "hitl_cancelled"                       # v13: 用户主动取消 ask_user

    # v8 Goal-driven planning failures
    GOAL_STAGNATION = "goal_stagnation"                     # v8: 检测到目标停滞，无法推进
    TODO_BLOCKED_PERSISTENT = "todo_blocked"               # v5/v8: TODO 持续 blocked 未解决


# ======================================================================
# Per-Task Metrics (collected during a single task run)
# ======================================================================

class PlanningMetrics(BaseModel):
    """
    Metrics for the planning phase.
    规划阶段指标。
    """
    classified_complexity: str = ""             # 实际分类结果
    expected_complexity: str = ""               # 期望分类结果
    classification_correct: bool = False         # 分类是否正确
    classification_forced: bool = True          # 分类是否被 PLAN_MODE 强制（非自动分类）

    plan_step_count: int = 0                    # 计划步骤/节点数
    plan_has_cycle: bool = False                # 计划是否有环（仅 DAG/TODO）
    plan_structure_valid: bool = True           # 计划结构是否有效

    plan_generation_time_ms: float = 0.0        # 计划生成耗时（毫秒）

    # Step/subgoal coverage (vs. ground truth)
    expected_step_count: int = 0                # 参考答案的步骤数
    step_coverage_ratio: float = 0.0            # 步骤覆盖率 = |covered| / |expected|

    # v8 Goal-driven planning specifics
    # v8 目标驱动规划专属指标（仅在 ENABLE_GOAL_DRIVEN_PLANNER=true 时非零）
    goal_anchor_count: int = 0                  # goal_anchor 事件次数
    goal_reanchor_count: int = 0                # goal_reanchor 事件次数（目标漂移后重锚定）


class ExecutionMetrics(BaseModel):
    """
    Metrics for the execution phase.
    执行阶段指标。
    """
    total_steps_planned: int = 0                # 计划中的总步骤数
    steps_completed: int = 0                    # 成功完成的步骤数
    steps_failed: int = 0                       # 失败的步骤数
    steps_skipped: int = 0                      # 跳过的步骤数
    steps_timeout: int = 0                      # 超时的步骤数

    step_success_rate: float = 0.0              # 步骤成功率 = completed / total
    task_success: bool = False                  # 任务整体是否成功

    # Tool usage
    total_tool_calls: int = 0                   # 工具调用总次数
    successful_tool_calls: int = 0              # 成功的工具调用次数
    failed_tool_calls: int = 0                  # 失败的工具调用次数
    tool_accuracy: float = 0.0                  # 工具使用准确率 = successful / total
    unique_tools_used: int = 0                  # 使用到的不同工具数量

    # ReAct loop efficiency
    total_react_iterations: int = 0             # ReAct 总迭代次数
    avg_iterations_per_step: float = 0.0        # 每步平均迭代次数

    execution_time_ms: float = 0.0              # 执行阶段总耗时

    # v9 SubAgent specifics (mirror of subagent_metrics for scoring access)
    # v9 子智能体专属指标（与 subagent_metrics 字典镜像，便于评分公式访问）
    subagent_calls: int = 0                     # SubAgent 调用总次数
    subagent_success_count: int = 0             # SubAgent 成功完成次数（status=completed）

    # v13 HITL specifics
    # v13 人机交互专属指标（仅在 HITL_ENABLED + interactive=True 时非零）
    hitl_calls: int = 0                         # ask_user 调用总次数
    hitl_timeout_count: int = 0                 # ask_user 超时次数
    hitl_cancelled_count: int = 0               # 用户主动取消 ask_user 次数

    # v2 DAG specifics
    # v2 DAG 执行细节
    dag_rollback_count: int = 0                 # 节点回滚次数
    condition_eval_count: int = 0               # 条件边评估次数


class EfficiencyMetrics(BaseModel):
    """
    Efficiency and cost metrics.
    效率和成本指标。
    """
    total_tokens: int = 0                       # 总 token 消耗

    total_time_ms: float = 0.0                  # 端到端总耗时
    trajectory_efficiency: float = 0.0          # 轨迹效率 = score / steps_taken

    # Replan metrics
    replan_count: int = 0                       # 重规划次数
    replan_success: bool = False                # 重规划后是否最终成功

    # v9/v13 cost specifics
    # v9/v13 成本细节（参与 efficiency 报告但不直接影响评分）
    subagent_total_tokens: int = 0              # v9: 所有 SubAgent 调用累计 token 消耗
    hitl_total_wait_ms: float = 0.0             # v13: ask_user 累计等待时间（毫秒）


class ReflectionMetrics(BaseModel):
    """
    Metrics for the reflection/reflector phase.
    反思阶段指标。
    """
    reflection_passed: bool = False             # 反思判定结果
    reflection_score: float = 0.0               # 反思质量评分 (0-1)
    reflection_observed: bool = False           # 是否实际观测到 reflection 事件

    # Ground truth alignment
    benchmark_task_success: Optional[bool] = None  # 基准任务的成功判定（来自 benchmark GT + runner 验证）
    reflection_accuracy: float = 0.0            # 反思判定与 GT 的吻合度 (1.0 = 完全一致)

    is_false_positive: bool = False             # 反思通过但 GT 认为失败
    is_false_negative: bool = False             # 反思拒绝但 GT 认为成功

    # v8 Goal-driven reflection specifics
    # v8 目标驱动反思专属指标
    goal_reflection_count: int = 0              # goal_reflection 事件次数
    stagnation_detected: bool = False           # 是否触发停滞检测


class FailureRecord(BaseModel):
    """
    Record of a single failure event during task execution.
    执行过程中的单次失败事件记录。
    """
    category: FailureCategory                   # 失败类别
    step_id: str | int = ""                     # 发生失败的步骤/节点 ID
    detail: str = ""                            # 失败详情
    timestamp: float = Field(default_factory=time.time)


class TaskEvaluationResult(BaseModel):
    """
    Complete evaluation result for a single task run.
    单次任务执行的完整评测结果。

    This is the primary output of the evaluation framework:
    one TaskEvaluationResult per (task, planning_mode) pair.
    """
    # Task identification
    task_id: str = ""                           # 评测任务 ID
    task_description: str = ""                  # 任务描述
    planning_mode: PlanMode = PlanMode.SIMPLE   # 使用的规划模式
    task_difficulty: TaskDifficulty = TaskDifficulty.MEDIUM  # 任务难度

    # Component metrics
    planning: PlanningMetrics = Field(default_factory=PlanningMetrics)
    execution: ExecutionMetrics = Field(default_factory=ExecutionMetrics)
    efficiency: EfficiencyMetrics = Field(default_factory=EfficiencyMetrics)
    reflection: ReflectionMetrics = Field(default_factory=ReflectionMetrics)

    # Failure tracking
    failures: list[FailureRecord] = Field(default_factory=list)

    # Composite scores
    overall_score: float = 0.0                  # 综合评分 (0-1)
    planning_score: float = 0.0                 # 规划评分 (0-1)
    execution_score: float = 0.0                # 执行评分 (0-1)
    efficiency_score: float = 0.0               # 效率评分 (0-1)

    # v9 SubAgent metrics (Wave C #9: previously collected but unused)
    # 子智能体相关指标（采集后聚合，便于评估 SubAgent 路径的 ROI）
    subagent_metrics: dict[str, Any] = Field(default_factory=dict)

    # v8 Pass^k reliability (TauBench-style; only populated when --repeat k>1)
    # v8 借鉴 TauBench：单任务多次重跑度量稳定性
    trial_count: int = 1                                # 本任务实际重跑次数 k
    trial_results: list[bool] = Field(default_factory=list)  # 每次试验的 task_success 列表
    pass_at_k: Optional[float] = None                    # successes / k；k=1 时为 None

    # v8 LLM-as-Judge fallback (only set when keyword check fails AND judge runs)
    # v8 LLM 兜底裁判：仅在 must_include 关键词缺失且 LLM judge 被调用时记录
    judge_overrode: bool = False                # judge 是否覆盖了关键词失败判定
    judge_reasoning: str = ""                   # judge 给出的语义判定理由（一句话）

    # Final answer (surfaced for transparency and LLM judge access)
    # 最终答案（供 transparency 输出和 LLM judge 访问）
    final_answer: str = ""

    # Metadata
    run_timestamp: float = Field(default_factory=time.time)
    llm_model: str = ""                         # 使用的 LLM 模型名
    config_snapshot: dict[str, Any] = Field(default_factory=dict)  # 运行时配置快照


# ======================================================================
# Aggregated Metrics (across multiple task runs)
# ======================================================================

class AggregatedMetrics(BaseModel):
    """
    Aggregated evaluation metrics across multiple tasks for one planning mode.
    单个规划模式在多个任务上的聚合评测指标。
    """
    planning_mode: PlanMode                     # 规划模式
    total_tasks: int = 0                        # 总评测任务数

    # Task-level aggregates
    task_success_rate: float = 0.0              # 任务成功率
    avg_overall_score: float = 0.0              # 平均综合评分
    avg_planning_score: float = 0.0             # 平均规划评分
    avg_execution_score: float = 0.0            # 平均执行评分
    avg_efficiency_score: float = 0.0           # 平均效率评分

    # By difficulty tier
    success_rate_by_difficulty: dict[str, float] = Field(default_factory=dict)

    # Planning specifics
    classification_accuracy: float = 0.0        # 分类准确率
    avg_step_count: float = 0.0                 # 平均计划步骤数
    plan_validity_rate: float = 0.0             # 计划结构有效率
    avg_step_coverage: float = 0.0              # 平均步骤覆盖率

    # Execution specifics
    avg_step_success_rate: float = 0.0          # 平均步骤成功率
    avg_tool_accuracy: float = 0.0              # 平均工具使用准确率
    avg_react_iterations: float = 0.0           # 平均 ReAct 迭代次数

    # Efficiency specifics
    avg_total_tokens: float = 0.0               # 平均 token 消耗
    avg_execution_time_ms: float = 0.0          # 平均执行耗时
    avg_trajectory_efficiency: float = 0.0      # 平均轨迹效率
    avg_replan_count: float = 0.0               # 平均重规划次数

    # Reflection specifics
    reflection_accuracy: float = 0.0            # 反思判定准确率
    false_positive_rate: float = 0.0            # 误判通过率
    false_negative_rate: float = 0.0            # 误判拒绝率
    reflection_coverage_rate: float = 0.0       # 实际观测到 reflection 事件的任务比例

    # Failure analysis
    failure_distribution: dict[str, int] = Field(default_factory=dict)  # 失败类别分布

    # v9 SubAgent aggregates
    avg_subagent_calls: float = 0.0             # 平均 SubAgent 调用次数
    avg_subagent_success_rate: float = 0.0      # 平均 SubAgent 成功率
    avg_subagent_tokens: float = 0.0            # 平均 SubAgent 累计 token

    # v13 HITL aggregates
    avg_hitl_calls: float = 0.0                 # 平均 ask_user 调用次数
    avg_hitl_wait_ms: float = 0.0               # 平均 ask_user 等待时间
    hitl_timeout_total: int = 0                 # ask_user 超时总次数
    hitl_cancelled_total: int = 0               # ask_user 取消总次数

    # v8 Goal-driven aggregates
    avg_goal_anchor_count: float = 0.0          # 平均 goal_anchor 次数
    avg_goal_reanchor_count: float = 0.0        # 平均 goal_reanchor 次数
    stagnation_rate: float = 0.0                # 触发停滞检测的任务比例

    # v8 DAG detail aggregates
    avg_dag_rollback_count: float = 0.0         # 平均节点回滚次数
    avg_condition_eval_count: float = 0.0       # 平均条件边评估次数

    # v8 Pass^k aggregates (only meaningful when any task has trial_count > 1)
    avg_pass_at_k: float = 0.0                  # 平均 pass^k
    pass_at_k_std: float = 0.0                  # pass^k 标准差（稳定性指标）

    # v8 LLM Judge usage
    judge_override_count: int = 0               # LLM judge 覆盖关键词失败的次数

    # Raw results for drill-down
    results: list[TaskEvaluationResult] = Field(default_factory=list)


# ======================================================================
# Score Computation
# ======================================================================

def compute_planning_score(pm: PlanningMetrics) -> float:
    """
    Compute planning quality score (0-1).
    规划质量评分。

    Weight breakdown (when classification is not forced):
      40% - Classification accuracy
      30% - Plan structure validity
      20% - Step coverage ratio
      10% - Plan generation speed bonus (fast = higher)

    When classification is forced (PLAN_MODE override):
      Classification weight is redistributed to other dimensions:
      50% - Plan structure validity
      35% - Step coverage ratio
      15% - Plan generation speed bonus
    """
    score = 0.0
    if pm.classification_forced:
        # Forced mode: skip classification, redistribute weights
        score += 0.5 if pm.plan_structure_valid else 0.0
        coverage = min(pm.step_coverage_ratio, 1.0) if pm.expected_step_count > 0 else 1.0
        score += 0.35 * coverage
        if pm.plan_generation_time_ms > 0:
            speed_ratio = max(0.0, 1.0 - (pm.plan_generation_time_ms / 10000.0))
            score += 0.15 * speed_ratio
    else:
        # Auto mode: include classification accuracy
        score += 0.4 if pm.classification_correct else 0.0
        score += 0.3 if pm.plan_structure_valid else 0.0
        coverage = min(pm.step_coverage_ratio, 1.0) if pm.expected_step_count > 0 else 1.0
        score += 0.2 * coverage
        if pm.plan_generation_time_ms > 0:
            speed_ratio = max(0.0, 1.0 - (pm.plan_generation_time_ms / 10000.0))
            score += 0.1 * speed_ratio
    return min(score, 1.0)


def compute_execution_score(em: ExecutionMetrics) -> float:
    """
    Compute execution quality score (0-1).
    执行质量评分。

    Weight breakdown (when no SubAgent called — backward compatible):
      50% - Task success
      30% - Step success rate
      20% - Tool accuracy

    Weight breakdown (v8 update: when SubAgent is called, subagent_calls > 0):
      45% - Task success
      25% - Step success rate
      15% - Tool accuracy
      15% - SubAgent success rate (subagent_success_count / subagent_calls)

    Rationale: prevents "SubAgent all failed but task looks superficially OK" inflation.
    设计原则：SubAgent 未触发时不影响打分（向后兼容）；触发后扣分逻辑生效。
    """
    score = 0.0

    if em.subagent_calls > 0:
        # v8 SubAgent-aware weighting
        subagent_sr = em.subagent_success_count / em.subagent_calls
        score += 0.45 if em.task_success else 0.0
        if em.total_steps_planned > 0:
            score += 0.25 * em.step_success_rate
        if em.total_tool_calls > 0:
            score += 0.15 * em.tool_accuracy
        else:
            score += 0.075  # neutral 50% of the 15% slot
        score += 0.15 * subagent_sr
    else:
        # Original 50/30/20 (backward compatible for existing 12 benchmarks)
        score += 0.5 if em.task_success else 0.0
        if em.total_steps_planned > 0:
            score += 0.3 * em.step_success_rate
        if em.total_tool_calls > 0:
            score += 0.2 * em.tool_accuracy
        else:
            score += 0.1

    return min(score, 1.0)


def compute_efficiency_score(
    em: EfficiencyMetrics,
    exec_m: ExecutionMetrics,
) -> float:
    """
    Compute efficiency score (0-1).
    效率评分。

    Weight breakdown:
      40% - Trajectory efficiency (score per step)
      30% - Token efficiency (lower is better)
      20% - Time efficiency (lower is better)
      10% - Replan penalty (fewer is better)
    """
    score = 0.0
    # Trajectory efficiency (40%) — based on step success rate per iteration
    if exec_m.total_react_iterations > 0 and exec_m.total_steps_planned > 0:
        # How many iterations did we need per completed step?
        completed = max(exec_m.steps_completed, 1)
        iters_per_step = exec_m.total_react_iterations / completed
        # Ideal: 1 iteration per step. Worst: max_iterations per step.
        ideal_ratio = max(0.0, 1.0 - (iters_per_step - 1) / 9.0)
        score += 0.4 * ideal_ratio
    else:
        score += 0.2  # No execution: neutral

    # Token efficiency (30%) — normalize to a reasonable range
    # 1000 tokens is excellent, 50000+ is poor
    if em.total_tokens > 0:
        token_score = max(0.0, 1.0 - math.log10(max(em.total_tokens, 1)) / 5.0)
        score += 0.3 * token_score
    else:
        score += 0.15

    # Time efficiency (20%) — < 5s excellent, > 120s poor
    if exec_m.execution_time_ms > 0:
        time_ratio = max(0.0, 1.0 - (exec_m.execution_time_ms / 120000.0))
        score += 0.2 * time_ratio
    else:
        score += 0.1

    # Replan penalty (10%) — 0 replans = full, each replan deducts
    replan_penalty = min(em.replan_count * 0.33, 1.0)
    score += 0.1 * (1.0 - replan_penalty)

    return min(max(score, 0.0), 1.0)


def compute_overall_score(
    planning: float,
    execution: float,
    efficiency: float,
    reflection: ReflectionMetrics,
) -> float:
    """
    Compute overall composite score (0-1).
    综合评分。

    Weight breakdown:
      30% - Planning score
      40% - Execution score
      20% - Efficiency score
      10% - Reflection accuracy bonus
    """
    score = 0.3 * planning + 0.4 * execution + 0.2 * efficiency
    # Reflection bonus: if reflection matches GT, add up to 10%
    if reflection.reflection_accuracy > 0:
        score += 0.1 * reflection.reflection_accuracy
    return min(score, 1.0)


def aggregate_results(results: list[TaskEvaluationResult]) -> AggregatedMetrics:
    """
    Aggregate multiple TaskEvaluationResults into summary metrics.
    将多个任务评测结果聚合为汇总指标。
    """
    if not results:
        return AggregatedMetrics(planning_mode=PlanMode.SIMPLE)

    mode = results[0].planning_mode
    n = len(results)

    # v8 Pass^k-aware success rate:
    # - When k=1: r.execution.task_success is the ONLY trial result (binary)
    # - When k>1: r.pass_at_k = successes/k captures the trial-aggregated rate
    #            but r.execution.task_success snapshot is only the LAST trial.
    # Using the per-task pass rate (or 1.0/0.0 for k=1) makes
    # task_success_rate consistent with avg_pass_at_k under repetition.
    # 当 k>1 时，aggregate 用每任务的 pass_at_k 而非最后一次试验的 task_success，
    # 避免 "task_success_rate=0% 但 avg_pass_at_k=66.7%" 的矛盾报告。
    def _success_score(r: TaskEvaluationResult) -> float:
        if r.trial_count > 1 and r.pass_at_k is not None:
            return r.pass_at_k
        return 1.0 if r.execution.task_success else 0.0

    task_sr = sum(_success_score(r) for r in results) / n if n > 0 else 0.0

    # By difficulty (same pass^k-aware logic)
    by_diff: dict[str, list[TaskEvaluationResult]] = {}
    for r in results:
        key = r.task_difficulty.value
        by_diff.setdefault(key, []).append(r)
    sr_by_diff = {
        k: sum(_success_score(r) for r in v) / len(v)
        for k, v in by_diff.items()
    }

    # Classification accuracy (only meaningful for auto/non-forced mode)
    classifiable = [r for r in results if r.planning.expected_complexity and not r.planning.classification_forced]
    class_acc = (
        sum(1 for r in classifiable if r.planning.classification_correct) / len(classifiable)
        if classifiable else 0.0
    )

    # Plan validity
    plan_valid = sum(1 for r in results if r.planning.plan_structure_valid) / n

    # Reflection accuracy — only count tasks where reflection was actually observed
    observed_reflections = [r for r in results if r.reflection.reflection_observed]
    reflection_coverage = len(observed_reflections) / n if n > 0 else 0.0
    ref_acc = 0.0
    fpr = 0.0
    fnr = 0.0
    if observed_reflections:
        ref_acc = sum(r.reflection.reflection_accuracy for r in observed_reflections) / len(observed_reflections)
        fp_count = sum(1 for r in observed_reflections if r.reflection.is_false_positive)
        fn_count = sum(1 for r in observed_reflections if r.reflection.is_false_negative)
        fpr = fp_count / len(observed_reflections)
        fnr = fn_count / len(observed_reflections)

    # Failure distribution
    failure_dist: dict[str, int] = {}
    for r in results:
        for f in r.failures:
            key = f.category.value
            failure_dist[key] = failure_dist.get(key, 0) + 1

    # v8 SubAgent aggregates (only meaningful when any task triggered SubAgent)
    subagent_results = [r for r in results if r.execution.subagent_calls > 0]
    if subagent_results:
        avg_sa_calls = sum(r.execution.subagent_calls for r in subagent_results) / len(subagent_results)
        avg_sa_sr = sum(
            r.execution.subagent_success_count / max(r.execution.subagent_calls, 1)
            for r in subagent_results
        ) / len(subagent_results)
        avg_sa_tokens = sum(r.efficiency.subagent_total_tokens for r in subagent_results) / len(subagent_results)
    else:
        avg_sa_calls = avg_sa_sr = avg_sa_tokens = 0.0

    # v13 HITL aggregates
    hitl_results = [r for r in results if r.execution.hitl_calls > 0]
    if hitl_results:
        avg_hitl_calls = sum(r.execution.hitl_calls for r in hitl_results) / len(hitl_results)
        avg_hitl_wait = sum(r.efficiency.hitl_total_wait_ms for r in hitl_results) / len(hitl_results)
    else:
        avg_hitl_calls = avg_hitl_wait = 0.0
    hitl_to_total = sum(r.execution.hitl_timeout_count for r in results)
    hitl_cancel_total = sum(r.execution.hitl_cancelled_count for r in results)

    # v8 Goal-driven aggregates
    avg_anchor = sum(r.planning.goal_anchor_count for r in results) / n
    avg_reanchor = sum(r.planning.goal_reanchor_count for r in results) / n
    stagnation_rate = sum(1 for r in results if r.reflection.stagnation_detected) / n

    # v2 DAG aggregates
    avg_rollback = sum(r.execution.dag_rollback_count for r in results) / n
    avg_cond_eval = sum(r.execution.condition_eval_count for r in results) / n

    # v8 Pass^k aggregates (only when some task has trial_count > 1)
    repeated = [r for r in results if r.trial_count > 1 and r.pass_at_k is not None]
    if repeated:
        pk_values = [r.pass_at_k for r in repeated]
        avg_pk = sum(pk_values) / len(pk_values)
        mean = avg_pk
        variance = sum((p - mean) ** 2 for p in pk_values) / len(pk_values) if len(pk_values) > 1 else 0.0
        pk_std = variance ** 0.5
    else:
        avg_pk = 0.0
        pk_std = 0.0

    # v8 LLM Judge usage count
    judge_overrides = sum(1 for r in results if r.judge_overrode)

    return AggregatedMetrics(
        planning_mode=mode,
        total_tasks=n,
        task_success_rate=task_sr,
        avg_overall_score=sum(r.overall_score for r in results) / n,
        avg_planning_score=sum(r.planning_score for r in results) / n,
        avg_execution_score=sum(r.execution_score for r in results) / n,
        avg_efficiency_score=sum(r.efficiency_score for r in results) / n,
        success_rate_by_difficulty=sr_by_diff,
        classification_accuracy=class_acc,
        avg_step_count=sum(r.planning.plan_step_count for r in results) / n,
        plan_validity_rate=plan_valid,
        avg_step_coverage=sum(r.planning.step_coverage_ratio for r in results) / n,
        avg_step_success_rate=sum(r.execution.step_success_rate for r in results) / n,
        avg_tool_accuracy=sum(r.execution.tool_accuracy for r in results) / n,
        avg_react_iterations=sum(r.execution.total_react_iterations for r in results) / n,
        avg_total_tokens=sum(r.efficiency.total_tokens for r in results) / n,
        avg_execution_time_ms=sum(r.execution.execution_time_ms for r in results) / n,
        avg_trajectory_efficiency=sum(r.efficiency.trajectory_efficiency for r in results) / n,
        avg_replan_count=sum(r.efficiency.replan_count for r in results) / n,
        reflection_accuracy=ref_acc,
        false_positive_rate=fpr,
        false_negative_rate=fnr,
        reflection_coverage_rate=reflection_coverage,
        failure_distribution=failure_dist,
        avg_subagent_calls=avg_sa_calls,
        avg_subagent_success_rate=avg_sa_sr,
        avg_subagent_tokens=avg_sa_tokens,
        avg_hitl_calls=avg_hitl_calls,
        avg_hitl_wait_ms=avg_hitl_wait,
        hitl_timeout_total=hitl_to_total,
        hitl_cancelled_total=hitl_cancel_total,
        avg_goal_anchor_count=avg_anchor,
        avg_goal_reanchor_count=avg_reanchor,
        stagnation_rate=stagnation_rate,
        avg_dag_rollback_count=avg_rollback,
        avg_condition_eval_count=avg_cond_eval,
        avg_pass_at_k=avg_pk,
        pass_at_k_std=pk_std,
        judge_override_count=judge_overrides,
        results=results,
    )
