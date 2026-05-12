"""
Tests for the evaluation module — metrics computation, benchmark loading, and report generation.
All tests are mock-based, no LLM API calls required.

评测模块测试 —— 指标计算、基准加载、报告生成。
全部测试基于 mock，无需 LLM API 调用。
"""

from __future__ import annotations

import re

import pytest

from evaluation.benchmark import BenchmarkTask, GroundTruth, get_benchmark_tasks
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
from evaluation.runner import EvaluationProbe


# ======================================================================
# Fixtures
# ======================================================================

def _make_planning(
    classification_correct: bool = True,
    plan_valid: bool = True,
    coverage: float = 1.0,
    plan_time_ms: float = 1000.0,
    classification_forced: bool = True,
) -> PlanningMetrics:
    return PlanningMetrics(
        classified_complexity="simple",
        expected_complexity="simple",
        classification_correct=classification_correct,
        classification_forced=classification_forced,
        plan_step_count=3,
        plan_structure_valid=plan_valid,
        plan_generation_time_ms=plan_time_ms,
        expected_step_count=3,
        step_coverage_ratio=coverage,
    )


def _make_execution(
    task_success: bool = True,
    step_sr: float = 1.0,
    tool_acc: float = 1.0,
    iterations: int = 3,
) -> ExecutionMetrics:
    return ExecutionMetrics(
        total_steps_planned=3,
        steps_completed=3 if task_success else 1,
        steps_failed=0 if task_success else 2,
        step_success_rate=step_sr,
        task_success=task_success,
        total_tool_calls=3,
        successful_tool_calls=3 if tool_acc == 1.0 else 2,
        failed_tool_calls=0 if tool_acc == 1.0 else 1,
        tool_accuracy=tool_acc,
        total_react_iterations=iterations,
        avg_iterations_per_step=iterations / 3,
    )


def _make_efficiency(
    replan_count: int = 0,
    tokens: int = 1000,
    time_ms: float = 5000.0,
) -> EfficiencyMetrics:
    return EfficiencyMetrics(
        total_tokens=tokens,
        replan_count=replan_count,
    )


def _make_reflection(
    passed: bool = True,
    gt_success: bool = True,
    observed: bool = True,
) -> ReflectionMetrics:
    ref_acc = 1.0 if passed == gt_success else 0.0
    return ReflectionMetrics(
        reflection_passed=passed,
        reflection_score=0.9 if passed else 0.3,
        reflection_observed=observed,
        benchmark_task_success=gt_success,
        reflection_accuracy=ref_acc if observed else 0.0,
        is_false_positive=observed and passed and not gt_success,
        is_false_negative=observed and not passed and gt_success,
    )


def _make_result(
    task_id: str = "test_001",
    mode: PlanMode = PlanMode.SIMPLE,
    difficulty: TaskDifficulty = TaskDifficulty.EASY,
    task_success: bool = True,
) -> TaskEvaluationResult:
    planning = _make_planning()
    execution = _make_execution(task_success=task_success)
    efficiency = _make_efficiency()
    reflection = _make_reflection(gt_success=task_success)

    p_score = compute_planning_score(planning)
    e_score = compute_execution_score(execution)
    ef_score = compute_efficiency_score(efficiency, execution)
    o_score = compute_overall_score(p_score, e_score, ef_score, reflection)

    return TaskEvaluationResult(
        task_id=task_id,
        task_description="test task",
        planning_mode=mode,
        task_difficulty=difficulty,
        planning=planning,
        execution=execution,
        efficiency=efficiency,
        reflection=reflection,
        planning_score=p_score,
        execution_score=e_score,
        efficiency_score=ef_score,
        overall_score=o_score,
    )


# ======================================================================
# Test: Metrics Computation
# ======================================================================

class TestPlanningScore:
    def test_perfect_score_forced(self):
        pm = _make_planning(classification_correct=True, plan_valid=True, coverage=1.0, plan_time_ms=500.0, classification_forced=True)
        score = compute_planning_score(pm)
        assert 0.9 <= score <= 1.0

    def test_perfect_score_auto(self):
        pm = _make_planning(classification_correct=True, plan_valid=True, coverage=1.0, plan_time_ms=500.0, classification_forced=False)
        score = compute_planning_score(pm)
        assert 0.9 <= score <= 1.0

    def test_wrong_classification_auto(self):
        pm = _make_planning(classification_correct=False, classification_forced=False)
        score = compute_planning_score(pm)
        assert score < 0.7

    def test_wrong_classification_forced_ignored(self):
        """When forced, classification_correct=False should not reduce score."""
        pm_forced = _make_planning(classification_correct=False, plan_valid=True, coverage=1.0, classification_forced=True)
        pm_auto = _make_planning(classification_correct=False, plan_valid=True, coverage=1.0, classification_forced=False)
        score_forced = compute_planning_score(pm_forced)
        score_auto = compute_planning_score(pm_auto)
        # Forced should be higher because it ignores classification
        assert score_forced > score_auto

    def test_invalid_plan(self):
        pm = _make_planning(plan_valid=False)
        score = compute_planning_score(pm)
        assert score < 0.8

    def test_low_coverage(self):
        pm = _make_planning(coverage=0.3)
        score = compute_planning_score(pm)
        assert score < 0.9

    def test_slow_plan(self):
        pm = _make_planning(plan_time_ms=15000.0)
        score = compute_planning_score(pm)
        assert score < 1.0


class TestExecutionScore:
    def test_perfect_execution(self):
        em = _make_execution(task_success=True, step_sr=1.0, tool_acc=1.0)
        score = compute_execution_score(em)
        assert score == 1.0

    def test_task_failed(self):
        em = _make_execution(task_success=False, step_sr=0.3, tool_acc=0.5)
        score = compute_execution_score(em)
        assert score < 0.5

    def test_no_tool_calls(self):
        em = _make_execution()
        em.total_tool_calls = 0
        em.tool_accuracy = 0.0
        score = compute_execution_score(em)
        assert score >= 0.1


class TestEfficiencyScore:
    def test_efficient_execution(self):
        em_eff = _make_efficiency(replan_count=0, tokens=500)
        em_exec = _make_execution(iterations=3)
        score = compute_efficiency_score(em_eff, em_exec)
        assert score > 0.5

    def test_high_replan_count(self):
        em_eff = _make_efficiency(replan_count=3)
        em_exec = _make_execution()
        score = compute_efficiency_score(em_eff, em_exec)
        assert score < 0.95

    def test_uses_efficiency_tokens_not_execution(self):
        """Efficiency score should use EfficiencyMetrics.total_tokens, not ExecutionMetrics.execution_tokens."""
        em_eff = _make_efficiency(tokens=500)
        em_exec = _make_execution(iterations=3)
        score = compute_efficiency_score(em_eff, em_exec)
        assert score > 0.0
        # Verify no AttributeError — execution_tokens was removed
        assert not hasattr(em_exec, 'execution_tokens') or getattr(em_exec, 'execution_tokens', None) is None


class TestOverallScore:
    def test_weighted_composition(self):
        p = 0.8
        e = 0.7
        ef = 0.6
        ref = _make_reflection(passed=True, gt_success=True)
        score = compute_overall_score(p, e, ef, ref)
        expected = 0.3 * p + 0.4 * e + 0.2 * ef + 0.1 * ref.reflection_accuracy
        assert abs(score - expected) < 0.001

    def test_max_score(self):
        score = compute_overall_score(1.0, 1.0, 1.0, _make_reflection(True, True))
        assert abs(score - 1.0) < 1e-9


# ======================================================================
# Test: Aggregation
# ======================================================================

class TestAggregation:
    def test_empty_results(self):
        agg = aggregate_results([])
        assert agg.total_tasks == 0
        assert agg.task_success_rate == 0.0

    def test_single_success(self):
        results = [_make_result(task_success=True)]
        agg = aggregate_results(results)
        assert agg.total_tasks == 1
        assert agg.task_success_rate == 1.0
        assert agg.avg_overall_score > 0

    def test_mixed_results(self):
        results = [
            _make_result(task_id="t1", task_success=True),
            _make_result(task_id="t2", task_success=False),
        ]
        agg = aggregate_results(results)
        assert agg.total_tasks == 2
        assert agg.task_success_rate == 0.5

    def test_by_difficulty(self):
        results = [
            _make_result(task_id="t1", difficulty=TaskDifficulty.EASY, task_success=True),
            _make_result(task_id="t2", difficulty=TaskDifficulty.EASY, task_success=True),
            _make_result(task_id="t3", difficulty=TaskDifficulty.HARD, task_success=False),
        ]
        agg = aggregate_results(results)
        assert agg.success_rate_by_difficulty.get("easy") == 1.0
        assert agg.success_rate_by_difficulty.get("hard") == 0.0

    def test_failure_distribution(self):
        r = _make_result()
        r.failures.append(FailureRecord(category=FailureCategory.TOOL_EXECUTION_ERROR, detail="test"))
        r.failures.append(FailureRecord(category=FailureCategory.TOOL_EXECUTION_ERROR, detail="test2"))
        r.failures.append(FailureRecord(category=FailureCategory.PARSE_FAILURE, detail="test3"))
        agg = aggregate_results([r])
        assert agg.failure_distribution.get("tool_execution_error") == 2
        assert agg.failure_distribution.get("parse_failure") == 1

    def test_unobserved_reflection_excluded_from_fp_fn(self):
        """FP/FN rates should only count tasks where reflection was observed."""
        r1 = _make_result(task_id="t1", task_success=True)
        r1.reflection = _make_reflection(passed=False, gt_success=True, observed=False)
        r2 = _make_result(task_id="t2", task_success=False)
        r2.reflection = _make_reflection(passed=True, gt_success=False, observed=True)
        r2.reflection.is_false_positive = True
        agg = aggregate_results([r1, r2])
        # Only r2 has observed reflection; r1 is excluded
        assert agg.false_positive_rate == 1.0  # 1 FP out of 1 observed
        assert agg.false_negative_rate == 0.0
        assert agg.reflection_coverage_rate == 0.5  # 1 out of 2

    def test_reflection_coverage_rate(self):
        results = [
            _make_result(task_id="t1", task_success=True),
            _make_result(task_id="t2", task_success=True),
        ]
        # Make one observed, one not
        results[0].reflection = _make_reflection(passed=True, gt_success=True, observed=True)
        results[1].reflection = _make_reflection(passed=True, gt_success=True, observed=False)
        agg = aggregate_results(results)
        assert agg.reflection_coverage_rate == 0.5

    def test_forced_classification_not_counted(self):
        """Forced classifications should not be counted in classification_accuracy."""
        results = [
            _make_result(task_id="t1", task_success=True),
        ]
        # Default fixture uses classification_forced=True
        results[0].planning.classification_forced = True
        results[0].planning.expected_complexity = "simple"
        results[0].planning.classification_correct = False
        agg = aggregate_results(results)
        assert agg.classification_accuracy == 0.0  # No non-forced tasks to count


# ======================================================================
# Test: Benchmark Tasks
# ======================================================================

class TestBenchmark:
    def test_benchmark_tasks_exist(self):
        tasks = get_benchmark_tasks()
        assert len(tasks) > 0

    def test_filter_by_difficulty(self):
        easy = get_benchmark_tasks(difficulty=TaskDifficulty.EASY)
        assert all(t.difficulty == TaskDifficulty.EASY for t in easy)
        assert len(easy) > 0

    def test_filter_by_task_id(self):
        tasks = get_benchmark_tasks(task_ids=["easy_001", "medium_001"])
        assert len(tasks) == 2
        ids = {t.task_id for t in tasks}
        assert "easy_001" in ids
        assert "medium_001" in ids

    def test_ground_truth_structure(self):
        tasks = get_benchmark_tasks()
        for t in tasks:
            assert t.ground_truth is not None
            assert t.ground_truth.expected_complexity in ("", "simple", "complex", "emergent")

    def test_difficulty_coverage(self):
        tasks = get_benchmark_tasks()
        difficulties = {t.difficulty for t in tasks}
        assert TaskDifficulty.EASY in difficulties
        assert TaskDifficulty.MEDIUM in difficulties
        assert TaskDifficulty.HARD in difficulties

    def test_all_tasks_have_must_include(self):
        """All benchmark tasks should have must_include_keywords."""
        tasks = get_benchmark_tasks()
        for t in tasks:
            assert len(t.ground_truth.must_include_keywords) > 0, (
                f"Task {t.task_id} missing must_include_keywords"
            )

    def test_must_not_include_defined(self):
        """All tasks should have must_not_include defined (empty list is valid)."""
        tasks = get_benchmark_tasks()
        for t in tasks:
            assert isinstance(t.ground_truth.must_not_include, list)


# ======================================================================
# Test: Reflection Accuracy
# ======================================================================

class TestReflectionAccuracy:
    def test_true_positive(self):
        ref = _make_reflection(passed=True, gt_success=True)
        assert ref.reflection_accuracy == 1.0
        assert not ref.is_false_positive
        assert not ref.is_false_negative

    def test_true_negative(self):
        ref = _make_reflection(passed=False, gt_success=False)
        assert ref.reflection_accuracy == 1.0

    def test_false_positive(self):
        ref = _make_reflection(passed=True, gt_success=False)
        assert ref.is_false_positive
        assert not ref.is_false_negative

    def test_false_negative(self):
        ref = _make_reflection(passed=False, gt_success=True)
        assert ref.is_false_negative
        assert not ref.is_false_positive

    def test_aggregate_fp_fn_rates(self):
        results = [
            _make_result(task_id="t1", task_success=False),
        ]
        results[0].reflection = _make_reflection(passed=True, gt_success=False, observed=True)
        results[0].reflection.is_false_positive = True
        agg = aggregate_results(results)
        assert agg.false_positive_rate == 1.0

    def test_unobserved_reflection_zero_accuracy(self):
        ref = _make_reflection(passed=True, gt_success=True, observed=False)
        assert ref.reflection_accuracy == 0.0
        assert not ref.is_false_positive
        assert not ref.is_false_negative

    def test_benchmark_task_success_naming(self):
        """Field should be benchmark_task_success (not gt_task_success)."""
        ref = ReflectionMetrics()
        assert hasattr(ref, 'benchmark_task_success')
        assert ref.benchmark_task_success is None
        assert not hasattr(ref, 'gt_task_success') or ref.benchmark_task_success is None


# ======================================================================
# Test: Probe Event Handling
# ======================================================================

class TestProbeEventHandling:
    def test_replan_detection_partial_replan(self):
        """B3 fix: should detect 'Partial replan' in phase event."""
        probe = EvaluationProbe()
        probe.on_event("phase", "Partial replan: replanning subtree for node X")
        assert probe.replan_count == 1

    def test_replan_detection_adaptation_event(self):
        """B3 fix: should detect plan_adaptation event."""
        probe = EvaluationProbe()
        probe.on_event("plan_adaptation", {"reason": "node failure", "action": "replan"})
        assert probe.replan_count == 1

    def test_replan_detection_replanning(self):
        """B3 fix: original 'Re-planning' still works."""
        probe = EvaluationProbe()
        probe.on_event("phase", "Re-planning after reflection failure")
        assert probe.replan_count == 1

    def test_tool_error_precise_detection(self):
        """M1 fix: should detect errors by prefix, not substring."""
        probe = EvaluationProbe()
        assert probe._is_tool_error("Error: command failed")
        assert probe._is_tool_error("error: something went wrong")
        assert probe._is_tool_error("ERROR: timeout")
        assert probe._is_tool_error("Exception: ValueError")
        assert probe._is_tool_error("Traceback (most recent call last):")
        # Should NOT match "Error" as substring
        assert not probe._is_tool_error("The word Error is in the middle of text")
        assert not probe._is_tool_error("No errors occurred")
        assert not probe._is_tool_error("")

    def test_chinese_step_coverage(self):
        """M2 fix: Chinese text should be split correctly for step coverage."""
        from evaluation.runner import EvaluationProbe as EP
        # Test the regex pattern used in build_result
        text = "搜索Python和JavaScript的区别"
        tokens = re.split(r'[\s,，、;；]+', text.lower())
        assert any("python" in t for t in tokens)
        assert any("javascript" in t for t in tokens)

    def test_chinese_step_coverage_actual_match(self):
        """Fix 1: Chinese subtasks should be matched via n-gram in build_result."""
        probe = EvaluationProbe()
        probe.final_answer = (
            "Step 1 已完成。使用 execute_python 工具成功计算并输出了前 10 个斐波那契数，"
            "结果为：[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]。"
            "Step 2 已完成。使用 file_ops 工具将斐波那契数列保存至本地文件 fibonacci.txt 中。"
        )
        probe.task_success = True
        task = BenchmarkTask(
            task_id="test_zh_coverage",
            task_description="计算斐波那契数并保存",
            ground_truth=GroundTruth(
                expected_complexity="simple",
                expected_subtasks=[
                    "计算斐波那契数列",
                    "将结果保存到文件",
                ],
                must_include_keywords=["fibonacci"],
            ),
        )
        result = probe.build_result(task=task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        # Both Chinese subtasks should be covered via n-gram matching
        assert result.planning.step_coverage_ratio == 1.0

    def test_chinese_step_coverage_partial(self):
        """Fix 1: Only matched subtasks should be counted."""
        probe = EvaluationProbe()
        probe.final_answer = "成功计算了斐波那契数列，但未进行数据可视化展示。"
        probe.task_success = True
        task = BenchmarkTask(
            task_id="test_zh_partial",
            task_description="test",
            ground_truth=GroundTruth(
                expected_subtasks=[
                    "计算斐波那契数列",
                    "将结果上传到远程服务器",
                ],
                must_include_keywords=[],
            ),
        )
        result = probe.build_result(task=task, forced_mode=PlanMode.SIMPLE, llm_model="test")
        # Only first subtask matched; second has no n-gram overlap with answer
        assert result.planning.step_coverage_ratio == 0.5

    def test_todo_count_extraction(self):
        """M4 fix: todo_list_initialized should extract count."""
        probe = EvaluationProbe()
        probe.on_event("todo_list_initialized", {"items": ["task1", "task2", "task3"]})
        assert probe.todo_count == 3

    def test_todo_count_extraction_dict(self):
        """M4 fix: alternative data format."""
        probe = EvaluationProbe()
        probe.on_event("todo_list_initialized", {"todos": ["a", "b"]})
        assert probe.todo_count == 2

    def test_reflection_observed_tracking(self):
        """B4 fix: reflection event sets reflection_observed=True."""
        probe = EvaluationProbe()
        assert not probe.reflection_observed
        from schema import Reflection
        probe.on_event("reflection", Reflection(passed=True, score=0.9, feedback="good", suggestions=[]))
        assert probe.reflection_observed

    def test_emergent_no_reflection_observed(self):
        """B4 fix: task completion without reflection leaves observed=False."""
        probe = EvaluationProbe()
        probe.on_event("task_start", {})
        probe.on_event("task_complete", {"answer": "some answer"})
        assert not probe.reflection_observed

    def test_must_not_include_check(self):
        """m1 fix: must_not_include should reject answers containing banned keywords."""
        probe = EvaluationProbe()
        probe.final_answer = "The result includes ERROR and failed content"
        probe.task_success = True

        # Simulate build_result logic for must_not_include
        gt = GroundTruth(
            must_not_include=["error", "failed"],
            must_include_keywords=["result"],
        )
        answer_lower = probe.final_answer.lower()
        passes_not_include = not any(kw.lower() in answer_lower for kw in gt.must_not_include)
        assert not passes_not_include  # Should fail because "error" and "failed" are in answer

    def test_config_snapshot_populated(self):
        """config_snapshot should be populated in build_result."""
        probe = EvaluationProbe()
        probe.final_answer = "test answer"
        probe.task_success = True
        task = BenchmarkTask(
            task_id="test_cfg",
            task_description="test",
        )
        result = probe.build_result(task=task, forced_mode=PlanMode.SIMPLE, llm_model="test-model")
        assert len(result.config_snapshot) > 0
        assert result.config_snapshot.get("plan_mode") == "simple"


# ======================================================================
# Test: Report Module (smoke test)
# ======================================================================

class TestReportSmoke:
    def test_render_comparison_table_no_crash(self):
        """Ensure report rendering doesn't crash with valid data."""
        from evaluation.report import render_comparison_table
        r1 = _make_result(task_success=True)
        r2 = _make_result(task_success=False)

        agg1 = aggregate_results([r1])
        agg1.planning_mode = PlanMode.SIMPLE

        agg2 = aggregate_results([r2])
        agg2.planning_mode = PlanMode.COMPLEX

        # Should not raise
        render_comparison_table({PlanMode.SIMPLE: agg1, PlanMode.COMPLEX: agg2})
