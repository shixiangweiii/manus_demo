"""
Evaluation runner — orchestrates benchmark execution and metric collection.

Hooks into the existing OrchestratorAgent pipeline via event listeners,
collecting per-phase metrics without modifying the core execution path.
Then computes evaluation results using the metrics module.

评测执行器 —— 编排基准任务执行并收集指标。

通过事件监听器挂接到现有 OrchestratorAgent 流水线，
在不修改核心执行路径的前提下收集各阶段指标。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import re
from typing import Any, Callable

import config
from agents.orchestrator import OrchestratorAgent
from evaluation.benchmark import BenchmarkTask, get_benchmark_tasks
from evaluation.metrics import (
    AggregatedMetrics,
    ExecutionMetrics,
    EfficiencyMetrics,
    FailureCategory,
    FailureRecord,
    PlanMode,
    PlanningMetrics,
    ReflectionMetrics,
    TaskDifficulty,
    TaskEvaluationResult,
    aggregate_results,
    compute_efficiency_score,
    compute_execution_score,
    compute_overall_score,
    compute_planning_score,
)
from llm.client import LLMClient
from schema import Reflection, TokenUsageSummary
from tools.base import BaseTool

logger = logging.getLogger(__name__)


# ======================================================================
# Event Probe — hooks into the Orchestrator's event stream
# 事件探针 —— 挂接到 Orchestrator 的事件流
# ======================================================================

class EvaluationProbe:
    """
    Instruments a single task run by intercepting Orchestrator events.
    通过拦截 Orchestrator 事件，对单次任务执行进行仪器化监测。

    Attaches to OrchestratorAgent._on_event callback to capture:
      - Task classification result
      - Plan structure (step count, node count, edges)
      - Step/node execution outcomes (success, failure, timeout)
      - Tool call records
      - Reflection verdict
      - Token usage
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset all collected data for a new task run."""
        # Planning phase
        self.classified_complexity: str = ""
        self.plan_step_count: int = 0
        self.plan_generation_start: float = 0.0
        self.plan_generation_end: float = 0.0

        # Execution phase
        self.execution_start: float = 0.0
        self.execution_end: float = 0.0
        self.task_start_time: float = 0.0
        self.steps_completed: int = 0
        self.steps_failed: int = 0
        self.steps_skipped: int = 0
        self.steps_timeout: int = 0
        self.total_tool_calls: int = 0
        self.successful_tool_calls: int = 0
        self.failed_tool_calls: int = 0
        self.unique_tools: set[str] = set()
        self.total_react_iterations: int = 0

        # Reflection phase
        self.reflection_passed: bool = False
        self.reflection_score: float = 0.0
        self.reflection_start: float = 0.0
        self.reflection_end: float = 0.0
        self.reflection_observed: bool = False

        # Replan tracking
        self.replan_count: int = 0

        # Failures
        self.failures: list[FailureRecord] = []

        # Token tracking
        self.total_tokens: int = 0
        self.tokens_snapshot_before: int = 0

        # Task result
        self.final_answer: str = ""
        self.task_success: bool = False

        # DAG specifics
        self.dag_node_count: int = 0
        self.dag_has_cycle: bool = False

        # Emergent specifics
        self.todo_count: int = 0
        self.todo_completed: int = 0
        self.todo_blocked: int = 0

        # v9 SubAgent specifics (anti-pattern #10 defense)
        self.subagent_results: list[dict] = []
        # Wave C #6: count of times the subagent call-limit was hit
        self.subagent_limits_hit: int = 0

        # Phase tracking
        self._phase_started: dict[str, float] = {}

        # Classification tracking
        self.classification_forced: bool = True

    @staticmethod
    def _is_tool_error(result_str: str) -> bool:
        """Check if a tool result string indicates an error. More precise than substring matching."""
        if not result_str:
            return False
        # Common error prefixes from ShellTool, CodeExecutorTool, etc.
        error_prefixes = ("Error:", "error:", "ERROR:", "Exception:", "Traceback")
        return result_str.strip().startswith(error_prefixes)

    def on_event(self, event: str, data: Any) -> None:
        """
        Event callback — attached to OrchestratorAgent._on_event.
        事件回调 —— 挂接到 OrchestratorAgent._on_event。

        Non-intrusive: reads event data without modifying it.
        """
        try:
            self._handle_event(event, data)
        except Exception as exc:
            logger.warning("[EvaluationProbe] Error handling event '%s': %s", event, exc)

    def _handle_event(self, event: str, data: Any) -> None:
        # --- Task start ---
        if event == "task_start":
            self.task_start_time = time.time()
            self._phase_started["task"] = time.time()
            self.classification_forced = (config.PLAN_MODE != "auto")

        # --- Classification ---
        elif event == "task_complexity":
            self.classified_complexity = data.get("complexity", "")

        # --- Planning ---
        elif event == "phase" and "Planning" in str(data):
            self.plan_generation_start = time.time()

        elif event == "plan":
            plan = data
            self.plan_step_count = len(plan.steps) if hasattr(plan, 'steps') else 0
            self.plan_generation_end = time.time()

        elif event == "dag_created":
            dag = data
            self.dag_node_count = len(dag.nodes) if hasattr(dag, 'nodes') else 0
            self.plan_generation_end = time.time()
            # Check for cycle
            topo = dag.topological_sort() if hasattr(dag, 'topological_sort') else []
            self.dag_has_cycle = len(topo) != self.dag_node_count if self.dag_node_count > 0 else False

        elif event == "todo_list_initialized":
            self.plan_generation_end = time.time()
            # Extract todo count from data
            if isinstance(data, dict):
                items = data.get("items", data.get("todos", []))
                if isinstance(items, (list, tuple)):
                    self.todo_count = len(items)
            elif hasattr(data, '__len__'):
                self.todo_count = len(data)

        # --- Execution ---
        elif event == "phase" and "Executing" in str(data):
            self.execution_start = time.time()

        elif event == "step_complete":
            self.steps_completed += 1
            result = data.get("result")
            if result and hasattr(result, 'tool_calls_log'):
                for tc in result.tool_calls_log:
                    self.total_tool_calls += 1
                    self.unique_tools.add(tc.tool_name)
                    if self._is_tool_error(tc.result):
                        self.failed_tool_calls += 1
                    else:
                        self.successful_tool_calls += 1
                self.total_react_iterations += len(result.tool_calls_log) + 1  # +1 for final answer iteration

        elif event == "step_failed":
            self.steps_failed += 1
            self.failures.append(FailureRecord(
                category=FailureCategory.TOOL_EXECUTION_ERROR,
                step_id=data.get("step", {}).get("id", "") if hasattr(data.get("step", ""), 'id') else "",
                detail=str(data.get("result", ""))[:200],
            ))
            result = data.get("result")
            if result and hasattr(result, 'tool_calls_log'):
                for tc in result.tool_calls_log:
                    self.total_tool_calls += 1
                    self.failed_tool_calls += 1
                self.total_react_iterations += len(result.tool_calls_log) + 1  # +1 for final answer iteration

        elif event == "step_skipped":
            self.steps_skipped += 1

        elif event in ("node_completed",):
            self.steps_completed += 1
            result = data.get("result")
            if result:
                if hasattr(result, 'tool_calls_log'):
                    for tc in result.tool_calls_log:
                        self.total_tool_calls += 1
                        self.unique_tools.add(tc.tool_name)
                        if self._is_tool_error(tc.result):
                            self.failed_tool_calls += 1
                        else:
                            self.successful_tool_calls += 1
                    self.total_react_iterations += len(result.tool_calls_log) + 1  # +1 for final answer iteration

        elif event == "node_failed":
            self.steps_failed += 1
            node = data.get("node")
            reason = str(data.get("reason", "execution")).lower()
            if "timeout" in reason:
                cat = FailureCategory.NODE_TIMEOUT
            elif "parse" in reason or "json" in reason:
                cat = FailureCategory.PARSE_FAILURE
            elif "tool" in reason and "param" in reason:
                cat = FailureCategory.TOOL_PARAMETER_ERROR
            elif "tool" in reason and "select" in reason:
                cat = FailureCategory.TOOL_SELECTION_ERROR
            else:
                cat = FailureCategory.TOOL_EXECUTION_ERROR
            self.failures.append(FailureRecord(
                category=cat,
                step_id=node.id if hasattr(node, "id") else str(node) if node else "",
                detail=f"Node failed: {data.get('reason', 'execution')}",
            ))

        elif event == "todo_complete":
            self.todo_completed += 1

        elif event == "todo_blocked":
            self.todo_blocked += 1

        elif event == "todo_start":
            self.total_react_iterations += 1

        # --- Replan ---
        elif event in ("plan_adaptation", "adaptive_planning"):
            self.replan_count += 1
        elif event == "phase" and any(
            kw in str(data) for kw in ("Re-planning", "Partial replan")
        ):
            self.replan_count += 1

        # --- Reflection ---
        elif event == "phase" and "Reflecting" in str(data):
            self.reflection_start = time.time()

        elif event == "reflection":
            ref: Reflection = data
            self.reflection_passed = ref.passed
            self.reflection_score = ref.score
            self.reflection_end = time.time()
            self.reflection_observed = True

        # --- Task complete ---
        elif event == "token_usage_summary":
            summary: TokenUsageSummary = data
            self.total_tokens = summary.total.total_tokens

        elif event == "task_complete":
            self.execution_end = time.time()
            self.final_answer = data.get("answer", "")
            self.task_success = len(self.final_answer) > 0 and "无法完成" not in self.final_answer

        # --- SubAgent (v9) ---
        elif event == "subagent_complete":
            self.subagent_results.append({
                "status": "completed",
                "subagent_id": data.get("subagent_id", ""),
                "iterations_used": data.get("iterations_used", 0),
                "tokens_used": data.get("tokens_used", 0),
                "duration_ms": data.get("duration_ms", 0.0),
                "tool_calls_count": data.get("tool_calls_count", 0),
            })

        elif event in ("subagent_failed", "subagent_timed_out"):
            self.subagent_results.append({
                "status": "failed" if event == "subagent_failed" else "timed_out",
                "subagent_id": data.get("subagent_id", ""),
                "iterations_used": data.get("iterations_used", 0),
                "tokens_used": data.get("tokens_used", 0),
                "duration_ms": data.get("duration_ms", 0.0),
                "tool_calls_count": data.get("tool_calls_count", 0),
            })

        elif event == "subagent_limit_exceeded":
            # Wave C #6: previously this event had no eval consumer
            self.subagent_limits_hit += 1

    def build_result(
        self,
        task: BenchmarkTask,
        forced_mode: PlanMode,
        llm_model: str,
    ) -> TaskEvaluationResult:
        """
        Build a TaskEvaluationResult from collected probe data.
        根据收集的探针数据构建评测结果。
        """
        gt = task.ground_truth

        # Planning metrics
        plan_time_ms = (self.plan_generation_end - self.plan_generation_start) * 1000 if self.plan_generation_end > self.plan_generation_start else 0.0
        class_correct = (
            self.classified_complexity == gt.expected_complexity
            if gt.expected_complexity else True
        )

        # Step coverage against ground truth
        step_coverage = 1.0
        if gt.expected_subtasks:
            answer_lower = self.final_answer.lower()
            covered = 0
            for sub in gt.expected_subtasks:
                sub_lower = sub.lower()
                # 英文分词匹配（按空白/标点切分后检查各 token）
                tokens = [t for t in re.split(r'[\s,，、;；]+', sub_lower) if len(t) > 1]
                if any(tok in answer_lower for tok in tokens):
                    covered += 1
                # 英文分词未匹配时，用中文 2-gram 滑动窗口匹配
                elif len(sub_lower) >= 2:
                    ngrams = [sub_lower[i:i+2] for i in range(len(sub_lower) - 1)]
                    if any(ng in answer_lower for ng in ngrams):
                        covered += 1
            step_coverage = covered / len(gt.expected_subtasks) if gt.expected_subtasks else 1.0

        planning = PlanningMetrics(
            classified_complexity=self.classified_complexity,
            expected_complexity=gt.expected_complexity,
            classification_correct=class_correct,
            classification_forced=self.classification_forced,
            plan_step_count=self.plan_step_count or self.dag_node_count or self.todo_count,
            plan_has_cycle=self.dag_has_cycle,
            plan_structure_valid=not self.dag_has_cycle,
            plan_generation_time_ms=plan_time_ms,
            expected_step_count=sum(gt.expected_step_count_range) // 2,
            step_coverage_ratio=step_coverage,
        )

        # Execution metrics
        total_planned = self.steps_completed + self.steps_failed + self.steps_skipped
        if total_planned == 0:
            total_planned = self.plan_step_count or 1
        step_sr = self.steps_completed / total_planned if total_planned > 0 else 0.0
        tool_acc = self.successful_tool_calls / self.total_tool_calls if self.total_tool_calls > 0 else 0.0
        avg_iters = self.total_react_iterations / max(self.steps_completed, 1)

        # Determine task success
        task_success = self.task_success
        if gt.must_include_keywords:
            answer_lower = self.final_answer.lower()
            task_success = task_success and all(kw.lower() in answer_lower for kw in gt.must_include_keywords)
        if gt.must_not_include:
            answer_lower = self.final_answer.lower()
            task_success = task_success and not any(kw.lower() in answer_lower for kw in gt.must_not_include)

        exec_time_ms = (self.execution_end - self.execution_start) * 1000 if self.execution_end > self.execution_start else 0.0

        execution = ExecutionMetrics(
            total_steps_planned=total_planned,
            steps_completed=self.steps_completed,
            steps_failed=self.steps_failed,
            steps_skipped=self.steps_skipped,
            steps_timeout=self.steps_timeout,
            step_success_rate=step_sr,
            task_success=task_success,
            total_tool_calls=self.total_tool_calls,
            successful_tool_calls=self.successful_tool_calls,
            failed_tool_calls=self.failed_tool_calls,
            tool_accuracy=tool_acc,
            unique_tools_used=len(self.unique_tools),
            total_react_iterations=self.total_react_iterations,
            avg_iterations_per_step=avg_iters,
            execution_time_ms=exec_time_ms,
        )

        # Efficiency metrics
        replan_success = self.replan_count > 0 and task_success
        trajectory_eff = step_sr / max(self.total_react_iterations / max(self.steps_completed, 1), 1)

        efficiency = EfficiencyMetrics(
            total_tokens=self.total_tokens,
            trajectory_efficiency=trajectory_eff,
            replan_count=self.replan_count,
            replan_success=replan_success,
        )

        # Reflection metrics
        ref_accuracy = 1.0 if self.reflection_passed == task_success else 0.0
        reflection = ReflectionMetrics(
            reflection_passed=self.reflection_passed,
            reflection_score=self.reflection_score,
            reflection_observed=self.reflection_observed,
            benchmark_task_success=task_success,
            reflection_accuracy=ref_accuracy if self.reflection_observed else 0.0,
            is_false_positive=self.reflection_observed and self.reflection_passed and not task_success,
            is_false_negative=self.reflection_observed and not self.reflection_passed and task_success,
        )

        # Compute scores
        planning_score = compute_planning_score(planning)
        execution_score = compute_execution_score(execution)
        efficiency_score = compute_efficiency_score(efficiency, execution)
        overall_score = compute_overall_score(planning_score, execution_score, efficiency_score, reflection)

        # Wave C #9: aggregate SubAgent metrics from collected subagent_results
        # (previously appended in _handle_event but never surfaced in result)
        # 聚合 SubAgent 调用维度指标，让评估输出能反映 SubAgent 路径的实际产出
        sa = self.subagent_results
        sa_total = len(sa)
        sa_succ = sum(1 for r in sa if r.get("status") == "completed")
        subagent_metrics: dict[str, Any] = {}
        if sa_total > 0 or self.subagent_limits_hit > 0:
            subagent_metrics = {
                "count": sa_total,
                "success_rate": (sa_succ / sa_total) if sa_total else 1.0,
                "tokens_total": sum(int(r.get("tokens_used", 0) or 0) for r in sa),
                "avg_iterations": (
                    sum(int(r.get("iterations_used", 0) or 0) for r in sa) / sa_total
                ) if sa_total else 0.0,
                "limits_hit": self.subagent_limits_hit,
            }

        return TaskEvaluationResult(
            task_id=task.task_id,
            task_description=task.task_description,
            planning_mode=forced_mode,
            task_difficulty=task.difficulty,
            planning=planning,
            execution=execution,
            efficiency=efficiency,
            reflection=reflection,
            failures=self.failures,
            overall_score=overall_score,
            planning_score=planning_score,
            execution_score=execution_score,
            efficiency_score=efficiency_score,
            llm_model=llm_model,
            subagent_metrics=subagent_metrics,
            config_snapshot={
                "plan_mode": forced_mode.value,
                "max_react_iterations": getattr(config, 'MAX_REACT_ITERATIONS', None),
                "max_parallel_nodes": getattr(config, 'MAX_PARALLEL_NODES', None),
                "max_replan_attempts": getattr(config, 'MAX_REPLAN_ATTEMPTS', None),
                "emergent_planning_enabled": getattr(config, 'EMERGENT_PLANNING_ENABLED', None),
                "adaptive_planning_enabled": getattr(config, 'ADAPTIVE_PLANNING_ENABLED', None),
            },
        )


# ======================================================================
# Evaluation Runner
# ======================================================================

class EvaluationRunner:
    """
    Runs evaluation benchmarks across one or all planning modes.
    对一个或全部规划模式运行评测基准。

    For each benchmark task × planning mode, the runner:
      1. Creates a fresh OrchestratorAgent
      2. Attaches an EvaluationProbe to capture events
      3. Forces the specified planning mode via config override
      4. Executes the task
      5. Collects metrics from the probe
      6. Returns TaskEvaluationResult
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tools: list[BaseTool] | None = None,
    ):
        self.llm_client = llm_client
        self.tools = tools

    async def evaluate_task(
        self,
        task: BenchmarkTask,
        mode: PlanMode,
    ) -> TaskEvaluationResult:
        """
        Evaluate a single benchmark task with a specific planning mode.
        以指定规划模式评测单个基准任务。
        """
        # Import tools lazily to avoid circular imports
        if self.tools is None:
            from tools.code_executor import CodeExecutorTool
            from tools.file_ops import FileOpsTool
            from tools.shell_tool import ShellTool
            from tools.web_search import WebSearchTool
            self.tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool(), ShellTool()]

        if self.llm_client is None:
            self.llm_client = LLMClient()

        probe = EvaluationProbe()
        llm_client = self.llm_client  # Use shared client

        # Force the planning mode
        original_plan_mode = config.PLAN_MODE
        original_emergent = config.EMERGENT_PLANNING_ENABLED
        config.PLAN_MODE = mode.value
        if mode == PlanMode.EMERGENT:
            config.EMERGENT_PLANNING_ENABLED = True

        try:
            orchestrator = OrchestratorAgent(
                llm_client=llm_client,
                tools=self.tools,
                on_event=probe.on_event,
            )

            logger.info(
                "[EvalRunner] Running task '%s' with mode '%s'",
                task.task_id, mode.value,
            )

            await orchestrator.run(task.task_description)

        except Exception as exc:
            logger.error(
                "[EvalRunner] Task '%s' crashed with mode '%s': %s",
                task.task_id, mode.value, exc,
            )
            probe.failures.append(FailureRecord(
                category=FailureCategory.LLM_CALL_FAILURE,
                detail=str(exc)[:300],
            ))
        finally:
            # Restore config
            config.PLAN_MODE = original_plan_mode
            config.EMERGENT_PLANNING_ENABLED = original_emergent

        return probe.build_result(
            task=task,
            forced_mode=mode,
            llm_model=self.llm_client.model if self.llm_client else "unknown",
        )

    async def evaluate_mode(
        self,
        mode: PlanMode,
        tasks: list[BenchmarkTask] | None = None,
    ) -> AggregatedMetrics:
        """
        Evaluate all (or filtered) benchmark tasks with a specific planning mode.
        以指定规划模式评测全部（或筛选后的）基准任务。
        """
        if tasks is None:
            tasks = get_benchmark_tasks()

        results: list[TaskEvaluationResult] = []
        for task in tasks:
            result = await self.evaluate_task(task, mode)
            results.append(result)
            logger.info(
                "[EvalRunner] Task '%s' (%s): score=%.3f success=%s",
                task.task_id, mode.value, result.overall_score, result.execution.task_success,
            )

        return aggregate_results(results)

    async def evaluate_all_modes(
        self,
        tasks: list[BenchmarkTask] | None = None,
        modes: list[PlanMode] | None = None,
    ) -> dict[PlanMode, AggregatedMetrics]:
        """
        Evaluate all benchmark tasks across all planning modes.
        以全部规划模式评测全部基准任务，返回各模式的聚合指标。
        """
        if tasks is None:
            tasks = get_benchmark_tasks()
        if modes is None:
            modes = list(PlanMode)

        all_metrics: dict[PlanMode, AggregatedMetrics] = {}
        for mode in modes:
            logger.info("[EvalRunner] ===== Evaluating mode: %s =====", mode.value)
            metrics = await self.evaluate_mode(mode, tasks)
            all_metrics[mode] = metrics

        return all_metrics
