"""
Tests for reasoning evaluation matrix suites, variants, and reports.
"""

from __future__ import annotations

import config

from evaluation.benchmark import get_benchmark_tasks
from evaluation.compare_variants import build_markdown_report, build_summary
from evaluation.metrics import PlanMode, TaskEvaluationResult
from evaluation.suites import list_suites, validate_suite_tasks
from evaluation.variants import apply_variant, get_variant, list_variants


def test_registered_suites_reference_existing_tasks():
    known_ids = {task.task_id for task in get_benchmark_tasks()}
    assert known_ids

    for suite in list_suites():
        validate_suite_tasks(suite)
        if suite.task_ids:
            assert set(suite.task_ids).issubset(known_ids)


def test_registered_variants_have_valid_modes_and_config_keys():
    assert list_variants()
    for variant in list_variants():
        assert variant.id
        assert variant.env_overrides
        assert all(isinstance(mode, PlanMode) for mode in variant.modes)
        for key in variant.env_overrides:
            assert hasattr(config, key)


def test_apply_variant_restores_config():
    variant = get_variant("reasoning_high")
    original_engine = config.ENABLE_REASONING_ENGINE
    original_effort = config.REASONING_EFFORT

    with apply_variant(variant):
        assert config.ENABLE_REASONING_ENGINE is True
        assert config.REASONING_EFFORT == "high"

    assert config.ENABLE_REASONING_ENGINE == original_engine
    assert config.REASONING_EFFORT == original_effort


def test_task_result_matrix_fields_default_safe():
    result = TaskEvaluationResult(task_id="matrix_001")

    assert result.variant_id == ""
    assert result.variant_config == {}
    assert result.llm_call_count == 0
    assert result.reasoning_tokens == 0
    assert result.tokens_by_engine == {}
    assert result.total_wall_time_ms == 0.0


def test_build_summary_computes_variant_deltas():
    raw = {
        "run_id": "test-run",
        "suite": {"id": "smoke_reasoning", "task_ids": ["easy_002"]},
        "variants": {
            "react_auto_baseline": {
                "variant": {"description": "baseline"},
                "modes": {
                    "simple": {
                        "metrics": {
                            "total_tasks": 1,
                            "task_success_rate": 0.5,
                            "avg_overall_score": 0.5,
                            "avg_total_tokens": 1000,
                            "avg_total_wall_time_ms": 1000,
                        },
                        "per_task_results": [
                            {
                                "task_id": "easy_002",
                                "verifier_total": 1,
                                "verifier_passed": 1,
                                "execution": {"task_success": True},
                                "failures": [],
                            }
                        ],
                    }
                },
            },
            "reasoning_auto": {
                "variant": {"description": "candidate"},
                "modes": {
                    "simple": {
                        "metrics": {
                            "total_tasks": 1,
                            "task_success_rate": 0.7,
                            "avg_overall_score": 0.8,
                            "avg_total_tokens": 1500,
                            "avg_total_wall_time_ms": 1500,
                            "avg_reasoning_tokens": 300,
                            "avg_llm_call_count": 2,
                        },
                        "per_task_results": [
                            {
                                "task_id": "easy_002",
                                "verifier_total": 1,
                                "verifier_passed": 1,
                                "execution": {"task_success": True},
                                "failures": [],
                            }
                        ],
                    }
                },
            },
        },
    }

    summary = build_summary(raw, baseline_variant="react_auto_baseline")
    candidate = next(
        row for row in summary["rows"]
        if row["variant_id"] == "reasoning_auto" and row["mode"] == "simple"
    )

    assert candidate["delta_success_rate"] == 0.19999999999999996
    assert candidate["token_ratio"] == 1.5
    assert candidate["time_ratio"] == 1.5
    assert candidate["recommendation"] == "candidate_default"


def test_markdown_report_includes_variant_table():
    raw = {
        "run_id": "test-run",
        "suite": {"id": "smoke_reasoning", "task_ids": ["easy_002"]},
        "variants": {
            "react_auto_baseline": {
                "variant": {"description": "baseline"},
                "modes": {
                    "simple": {
                        "metrics": {
                            "total_tasks": 1,
                            "task_success_rate": 1.0,
                            "avg_overall_score": 0.9,
                            "avg_total_tokens": 100,
                            "avg_total_wall_time_ms": 100,
                        },
                        "per_task_results": [
                            {
                                "task_id": "easy_002",
                                "execution": {"task_success": True},
                                "failures": [],
                            }
                        ],
                    }
                },
            }
        },
    }

    report = build_markdown_report(raw)

    assert "Evaluation Variant Comparison" in report
    assert "react_auto_baseline" in report
    assert "| Variant | Mode | Success" in report
