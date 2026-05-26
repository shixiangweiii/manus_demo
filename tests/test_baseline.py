"""
Tests for evaluation baseline save/load and regression detection.
"""

from __future__ import annotations

from evaluation.baseline import compare_baseline, load_baseline, save_baseline
from evaluation.metrics import (
    AggregatedMetrics,
    EfficiencyMetrics,
    ExecutionMetrics,
    FailureCategory,
    FailureRecord,
    PlanMode,
    TaskEvaluationResult,
)


def _metrics(
    mode: PlanMode = PlanMode.SIMPLE,
    *,
    success_rate: float = 0.8,
    score: float = 0.7,
    tokens: float = 1000,
    time_ms: float = 5000,
    failures: dict[str, int] | None = None,
) -> AggregatedMetrics:
    result = TaskEvaluationResult(
        task_id=f"{mode.value}_task",
        planning_mode=mode,
        execution=ExecutionMetrics(task_success=success_rate > 0),
        efficiency=EfficiencyMetrics(total_tokens=int(tokens)),
        overall_score=score,
    )
    failure_records = []
    for category, count in (failures or {}).items():
        failure_records.extend(
            FailureRecord(category=FailureCategory(category), detail="baseline test")
            for _ in range(count)
        )
    result.failures = failure_records
    return AggregatedMetrics(
        planning_mode=mode,
        total_tasks=1,
        task_success_rate=success_rate,
        avg_overall_score=score,
        avg_total_tokens=tokens,
        avg_execution_time_ms=time_ms,
        failure_distribution=failures or {},
        results=[result],
    )


def test_save_and_load_baseline_roundtrip(tmp_path):
    path = tmp_path / "baseline.json"

    save_baseline({PlanMode.SIMPLE: _metrics()}, str(path))
    loaded = load_baseline(str(path))

    assert loaded["version"] == "v14.6"
    assert "simple" in loaded["modes"]
    assert loaded["modes"]["simple"]["task_success_rate"] == 0.8
    assert "simple_task" in loaded["modes"]["simple"]["per_task"]


def test_empty_baseline_is_regression():
    comparison = compare_baseline(
        {PlanMode.SIMPLE: _metrics()},
        {"version": "v14.6", "modes": {}},
    )

    assert comparison.is_regression
    assert any("no modes" in item.lower() for item in comparison.regressions)


def test_missing_mode_in_baseline_is_regression():
    baseline = {
        "version": "v14.6",
        "modes": {
            "simple": {
                "task_success_rate": 0.8,
                "avg_overall_score": 0.7,
                "avg_total_tokens": 1000,
                "avg_execution_time_ms": 5000,
                "failure_distribution": {},
                "per_task": {},
            }
        },
    }

    comparison = compare_baseline(
        {
            PlanMode.SIMPLE: _metrics(PlanMode.SIMPLE),
            PlanMode.COMPLEX: _metrics(PlanMode.COMPLEX),
        },
        baseline,
    )

    assert comparison.is_regression
    assert any("[complex] No baseline data" in item for item in comparison.regressions)


def test_metric_regressions_are_detected():
    baseline = {
        "version": "v14.6",
        "modes": {
            "simple": {
                "task_success_rate": 0.9,
                "avg_overall_score": 0.8,
                "avg_total_tokens": 1000,
                "avg_execution_time_ms": 5000,
                "failure_distribution": {"tool_execution_error": 1},
                "per_task": {},
            }
        },
    }
    current = {
        PlanMode.SIMPLE: _metrics(
            success_rate=0.7,
            score=0.6,
            tokens=2500,
            time_ms=12000,
            failures={"tool_execution_error": 3},
        )
    }

    comparison = compare_baseline(current, baseline)

    assert comparison.is_regression
    assert any("success_rate" in item for item in comparison.regressions)
    assert any("overall_score" in item for item in comparison.regressions)
    assert any("tokens" in item for item in comparison.regressions)
    assert any("time" in item for item in comparison.regressions)
    assert any("failures" in item for item in comparison.regressions)
