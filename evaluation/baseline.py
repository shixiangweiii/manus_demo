"""
Baseline management for evaluation regression detection.

Provides save/load/compare operations against a stable baseline JSON file.
Used by eval_cli --baseline / --save-baseline / --fail-on-regression.

基线管理 —— 用于评测回归检测。

参考 SWE-bench 的 pass/fail gate 模式：
先建立 baseline，后续每次改动与 baseline 对比，超过阈值则判定为回归。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from evaluation.metrics import (
    AggregatedMetrics,
    PlanMode,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Data Models
# ======================================================================

@dataclass
class RegressionThresholds:
    """Thresholds for regression detection. Negative deltas mean decline."""
    success_rate_delta: float = -0.10    # Fail if success_rate drops > 10%
    score_delta: float = -0.05           # Fail if avg_score drops > 5%
    token_increase_ratio: float = 2.0    # Fail if tokens > 2x baseline
    time_increase_ratio: float = 2.0     # Fail if time > 2x baseline
    failure_increase_ratio: float = 2.0  # Fail if failures > 2x baseline


@dataclass
class BaselineComparison:
    """Result of comparing current metrics against a baseline."""
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    is_regression: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# ======================================================================
# Baseline JSON Schema
# ======================================================================

BASELINE_VERSION = "v14.6"


def _mode_to_dict(metrics: AggregatedMetrics) -> dict[str, Any]:
    """Extract baseline-relevant fields from AggregatedMetrics."""
    return {
        "task_success_rate": metrics.task_success_rate,
        "avg_overall_score": metrics.avg_overall_score,
        "avg_total_tokens": metrics.avg_total_tokens,
        "avg_execution_time_ms": metrics.avg_execution_time_ms,
        "failure_distribution": metrics.failure_distribution if hasattr(metrics, 'failure_distribution') else {},
        "per_task": {
            r.task_id: {
                "success": r.execution.task_success,
                "score": r.overall_score,
                "tokens": r.efficiency.total_tokens,
            }
            for r in metrics.results
        } if hasattr(metrics, 'results') else {},
    }


# ======================================================================
# Public API
# ======================================================================

def save_baseline(
    metrics_by_mode: dict[PlanMode, AggregatedMetrics],
    path: str,
    version: str = BASELINE_VERSION,
) -> None:
    """Save current evaluation results as a baseline JSON file."""
    baseline = {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "modes": {},
    }

    for mode, metrics in metrics_by_mode.items():
        baseline["modes"][mode.value] = _mode_to_dict(metrics)

    # Atomic write
    tmp_path = path + ".tmp"
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)
    logger.info("[Baseline] Saved to %s", path)


def load_baseline(path: str) -> dict[str, Any]:
    """Load baseline from JSON file. Raises FileNotFoundError if missing."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare_baseline(
    current: dict[PlanMode, AggregatedMetrics],
    baseline: dict[str, Any],
    thresholds: RegressionThresholds | None = None,
) -> BaselineComparison:
    """Compare current metrics against a baseline."""
    if thresholds is None:
        thresholds = RegressionThresholds()

    comparison = BaselineComparison()

    baseline_modes = set(baseline.get("modes", {}).keys())
    current_modes = {m.value for m in current.keys()}

    # Detect missing baseline modes (only warn if baseline is non-empty)
    missing_in_baseline = current_modes - baseline_modes
    if missing_in_baseline and baseline_modes:
        for m in sorted(missing_in_baseline):
            comparison.regressions.append(
                f"[{m}] No baseline data — cannot verify regression"
            )

    for mode, current_metrics in current.items():
        mode_key = mode.value
        baseline_mode = baseline.get("modes", {}).get(mode_key)
        if baseline_mode is None:
            continue

        mode_details: dict[str, Any] = {}
        prefix = f"[{mode_key}]"

        # Task success rate
        base_sr = baseline_mode.get("task_success_rate", 0)
        curr_sr = current_metrics.task_success_rate
        delta_sr = curr_sr - base_sr
        mode_details["success_rate"] = {"current": curr_sr, "baseline": base_sr, "delta": delta_sr}
        if delta_sr < thresholds.success_rate_delta:
            comparison.regressions.append(
                f"{prefix} success_rate: {curr_sr:.1%} (was {base_sr:.1%}, delta={delta_sr:+.1%})"
            )
        elif delta_sr > abs(thresholds.success_rate_delta):
            comparison.improvements.append(
                f"{prefix} success_rate: {curr_sr:.1%} (was {base_sr:.1%}, delta={delta_sr:+.1%})"
            )

        # Overall score
        base_score = baseline_mode.get("avg_overall_score", 0)
        curr_score = current_metrics.avg_overall_score
        delta_score = curr_score - base_score
        mode_details["overall_score"] = {"current": curr_score, "baseline": base_score, "delta": delta_score}
        if delta_score < thresholds.score_delta:
            comparison.regressions.append(
                f"{prefix} overall_score: {curr_score:.3f} (was {base_score:.3f}, delta={delta_score:+.3f})"
            )
        elif delta_score > abs(thresholds.score_delta):
            comparison.improvements.append(
                f"{prefix} overall_score: {curr_score:.3f} (was {base_score:.3f}, delta={delta_score:+.3f})"
            )

        # Total tokens
        base_tokens = baseline_mode.get("avg_total_tokens", 0)
        curr_tokens = current_metrics.avg_total_tokens
        if base_tokens > 0:
            ratio = curr_tokens / base_tokens
            mode_details["total_tokens"] = {"current": curr_tokens, "baseline": base_tokens, "ratio": ratio}
            if ratio > thresholds.token_increase_ratio:
                comparison.regressions.append(
                    f"{prefix} tokens: {curr_tokens:.0f} ({ratio:.1f}x baseline {base_tokens:.0f})"
                )

        # Execution time
        base_time = baseline_mode.get("avg_execution_time_ms", 0)
        curr_time = current_metrics.avg_execution_time_ms
        if base_time > 0:
            time_ratio = curr_time / base_time
            mode_details["execution_time_ms"] = {"current": curr_time, "baseline": base_time, "ratio": time_ratio}
            if time_ratio > thresholds.time_increase_ratio:
                comparison.regressions.append(
                    f"{prefix} time: {curr_time:.0f}ms ({time_ratio:.1f}x baseline {base_time:.0f}ms)"
                )

        # Failure count
        base_failures = sum(baseline_mode.get("failure_distribution", {}).values())
        curr_failures = sum(current_metrics.failure_distribution.values()) if current_metrics.failure_distribution else 0
        if base_failures > 0:
            fail_ratio = curr_failures / base_failures
            mode_details["failure_count"] = {"current": curr_failures, "baseline": base_failures, "ratio": fail_ratio}
            if fail_ratio > thresholds.failure_increase_ratio:
                comparison.regressions.append(
                    f"{prefix} failures: {curr_failures} ({fail_ratio:.1f}x baseline {base_failures})"
                )

        # Per-task comparison
        per_task = {}
        baseline_tasks = baseline_mode.get("per_task", {})
        if hasattr(current_metrics, 'results'):
            for r in current_metrics.results:
                bt = baseline_tasks.get(r.task_id)
                if bt:
                    per_task[r.task_id] = {
                        "success": r.execution.task_success,
                        "baseline_success": bt.get("success"),
                        "score": r.overall_score,
                        "baseline_score": bt.get("score"),
                    }
        mode_details["per_task"] = per_task

        comparison.details[mode_key] = mode_details

    comparison.is_regression = len(comparison.regressions) > 0
    return comparison
