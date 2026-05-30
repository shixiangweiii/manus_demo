"""
Compare evaluation matrix variants and generate Markdown/CSV reports.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _iter_variant_modes(raw: dict[str, Any]):
    for variant_id, variant_data in raw.get("variants", {}).items():
        for mode, mode_data in variant_data.get("modes", {}).items():
            yield variant_id, mode, variant_data, mode_data


def _per_task_results(mode_data: dict[str, Any]) -> list[dict[str, Any]]:
    return mode_data.get("per_task_results") or mode_data.get("metrics", {}).get("results") or []


def _verifier_pass_rate(tasks: list[dict[str, Any]]) -> float:
    total = sum(int(t.get("verifier_total", 0) or 0) for t in tasks)
    if total <= 0:
        return 0.0
    passed = sum(int(t.get("verifier_passed", 0) or 0) for t in tasks)
    return passed / total


def _failure_count(tasks: list[dict[str, Any]]) -> int:
    return sum(len(t.get("failures", []) or []) for t in tasks)


def build_summary(raw: dict[str, Any], baseline_variant: str = "react_auto_baseline") -> dict[str, Any]:
    """Build compact variant/mode summary rows from raw matrix output."""
    rows: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for variant_id, mode, variant_data, mode_data in _iter_variant_modes(raw):
        metrics = mode_data.get("metrics", {})
        tasks = _per_task_results(mode_data)
        row = {
            "variant_id": variant_id,
            "mode": mode,
            "total_tasks": metrics.get("total_tasks", len(tasks)),
            "task_success_rate": metrics.get("task_success_rate", 0.0),
            "verifier_pass_rate": _verifier_pass_rate(tasks),
            "avg_overall_score": metrics.get("avg_overall_score", 0.0),
            "avg_total_tokens": metrics.get("avg_total_tokens", 0.0),
            "avg_prompt_tokens": metrics.get("avg_prompt_tokens", 0.0),
            "avg_completion_tokens": metrics.get("avg_completion_tokens", 0.0),
            "avg_reasoning_tokens": metrics.get("avg_reasoning_tokens", 0.0),
            "avg_llm_call_count": metrics.get("avg_llm_call_count", 0.0),
            "avg_execution_time_ms": metrics.get("avg_execution_time_ms", 0.0),
            "avg_total_wall_time_ms": metrics.get("avg_total_wall_time_ms", 0.0),
            "p50_total_wall_time_ms": metrics.get("p50_total_wall_time_ms", 0.0),
            "p95_total_wall_time_ms": metrics.get("p95_total_wall_time_ms", 0.0),
            "avg_pass_at_k": metrics.get("avg_pass_at_k", 0.0),
            # v18.5 multi-agent collaboration signals
            "avg_subagent_calls": metrics.get("avg_subagent_calls", 0.0),
            "avg_subagent_success_rate": metrics.get("avg_subagent_success_rate", 0.0),
            "avg_handoff_calls": metrics.get("avg_handoff_calls", 0.0),
            "avg_handoff_success_rate": metrics.get("avg_handoff_success_rate", 0.0),
            # v19.4 red-team / security signals
            "attack_success_rate": metrics.get("attack_success_rate", 0.0),
            "blocked_benign_rate": metrics.get("blocked_benign_rate", 0.0),
            "guardrail_block_count": metrics.get("guardrail_block_count", 0),
            "failure_count": _failure_count(tasks),
            "description": variant_data.get("variant", {}).get("description", ""),
        }
        rows.append(row)
        by_key[(variant_id, mode)] = row

    for row in rows:
        baseline = by_key.get((baseline_variant, row["mode"]))
        if not baseline:
            row["baseline_available"] = False
            continue
        row["baseline_available"] = True
        row["delta_success_rate"] = row["task_success_rate"] - baseline["task_success_rate"]
        row["delta_score"] = row["avg_overall_score"] - baseline["avg_overall_score"]
        row["token_ratio"] = _ratio(row["avg_total_tokens"], baseline["avg_total_tokens"])
        row["time_ratio"] = _ratio(row["avg_total_wall_time_ms"], baseline["avg_total_wall_time_ms"])
        row["recommendation"] = _recommend(row, baseline)

    return {
        "run_id": raw.get("run_id", ""),
        "suite": raw.get("suite", {}),
        "baseline_variant": baseline_variant,
        "rows": rows,
    }


def _ratio(current: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return current / baseline


def _recommend(row: dict[str, Any], baseline: dict[str, Any]) -> str:
    delta_sr = row.get("delta_success_rate", 0.0)
    token_ratio = row.get("token_ratio", 0.0)
    time_ratio = row.get("time_ratio", 0.0)
    mode = row.get("mode", "")
    variant_id = row.get("variant_id", "")

    if variant_id == baseline.get("variant_id"):
        return "baseline"
    if delta_sr >= 0.08 and (token_ratio == 0.0 or token_ratio <= 2.0) and (time_ratio == 0.0 or time_ratio <= 2.0):
        return "candidate_default"
    if mode == "emergent" and delta_sr >= 0.15 and (token_ratio == 0.0 or token_ratio <= 3.0):
        return "hard_task_candidate"
    if delta_sr <= 0.0 and token_ratio > 1.5:
        return "efficiency_regression"
    if delta_sr < -0.05:
        return "correctness_regression"
    return "neutral"


def build_markdown_report(raw: dict[str, Any], baseline_variant: str = "react_auto_baseline") -> str:
    """Build a Markdown comparison report."""
    summary = build_summary(raw, baseline_variant=baseline_variant)
    suite = summary.get("suite", {})
    rows = summary["rows"]

    lines = [
        f"# Evaluation Variant Comparison: {summary.get('run_id', '')}",
        "",
        f"- Suite: `{suite.get('id', '')}`",
        f"- Baseline variant: `{baseline_variant}`",
        f"- Task count: `{len(suite.get('task_ids', []) or []) or 'all'}`",
        "",
        "## Summary",
        "",
        "| Variant | Mode | Success | Verifier | Score | Tokens | Reasoning Tokens | Calls | Wall Time | ΔSuccess | Token Ratio | Time Ratio | Recommendation |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for row in rows:
        lines.append(
            "| {variant_id} | {mode} | {success} | {verifier} | {score:.3f} | {tokens:.0f} | "
            "{reasoning:.0f} | {calls:.1f} | {wall:.0f}ms | {delta_success} | {token_ratio} | {time_ratio} | {recommendation} |".format(
                variant_id=row["variant_id"],
                mode=row["mode"],
                success=_fmt_pct(row["task_success_rate"]),
                verifier=_fmt_pct(row["verifier_pass_rate"]) if row["verifier_pass_rate"] else "-",
                score=float(row["avg_overall_score"] or 0.0),
                tokens=float(row["avg_total_tokens"] or 0.0),
                reasoning=float(row["avg_reasoning_tokens"] or 0.0),
                calls=float(row["avg_llm_call_count"] or 0.0),
                wall=float(row["avg_total_wall_time_ms"] or 0.0),
                delta_success=_fmt_delta_pct(row.get("delta_success_rate")) if row.get("baseline_available") else "-",
                token_ratio=_fmt_ratio(row.get("token_ratio")) if row.get("baseline_available") else "-",
                time_ratio=_fmt_ratio(row.get("time_ratio")) if row.get("baseline_available") else "-",
                recommendation=row.get("recommendation", "-"),
            )
        )

    lines.extend([
        "",
        "## Decision Rules",
        "",
        "- Correctness is primary. A variant needs at least +8pp success rate with <=2x tokens/time to be a default candidate.",
        "- High effort is only justified for hard/emergent workloads when correctness improves materially.",
        "- Live web suites should be treated as observational because external content can drift.",
        "",
        "## Failures",
        "",
    ])
    failure_lines = build_failures_markdown(raw)
    lines.extend(failure_lines.splitlines()[2:] if failure_lines.startswith("# ") else failure_lines.splitlines())
    return "\n".join(lines).rstrip() + "\n"


def build_failures_markdown(raw: dict[str, Any]) -> str:
    """Build a focused failure list for drill-down."""
    lines = ["# Evaluation Failures", ""]
    found = False
    for variant_id, mode, _, mode_data in _iter_variant_modes(raw):
        for task in _per_task_results(mode_data):
            if task.get("execution", {}).get("task_success", False) and not task.get("failed_by_verifier", False):
                continue
            found = True
            failures = task.get("failures", []) or []
            failure_bits = []
            for failure in failures[:3]:
                category = failure.get("category", "")
                detail = failure.get("detail", "")
                failure_bits.append(f"{category}: {detail}".strip(": "))
            if not failure_bits and task.get("failed_by_verifier"):
                failure_bits.append("verifier_failed")
            if not failure_bits:
                failure_bits.append("task_success_false")
            lines.append(
                f"- `{variant_id}` / `{mode}` / `{task.get('task_id', '')}`: "
                + "; ".join(failure_bits)
            )
    if not found:
        lines.append("- No failed tasks recorded.")
    return "\n".join(lines).rstrip() + "\n"


def write_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    rows = summary.get("rows", [])
    fieldnames = [
        "variant_id",
        "mode",
        "total_tasks",
        "task_success_rate",
        "verifier_pass_rate",
        "avg_overall_score",
        "avg_total_tokens",
        "avg_prompt_tokens",
        "avg_completion_tokens",
        "avg_reasoning_tokens",
        "avg_llm_call_count",
        "avg_total_wall_time_ms",
        "p50_total_wall_time_ms",
        "p95_total_wall_time_ms",
        "avg_subagent_calls",
        "avg_subagent_success_rate",
        "avg_handoff_calls",
        "avg_handoff_success_rate",
        "attack_success_rate",
        "blocked_benign_rate",
        "guardrail_block_count",
        "delta_success_rate",
        "token_ratio",
        "time_ratio",
        "recommendation",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_cost_latency_csv(path: Path, raw: dict[str, Any]) -> None:
    fieldnames = [
        "variant_id",
        "mode",
        "task_id",
        "success",
        "score",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "llm_call_count",
        "total_wall_time_ms",
        "planning_time_ms",
        "execution_time_ms",
        "reflection_time_ms",
        "failure_category",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for variant_id, mode, _, mode_data in _iter_variant_modes(raw):
            for task in _per_task_results(mode_data):
                writer.writerow({
                    "variant_id": variant_id,
                    "mode": mode,
                    "task_id": task.get("task_id", ""),
                    "success": task.get("execution", {}).get("task_success", False),
                    "score": task.get("overall_score", 0.0),
                    "total_tokens": task.get("efficiency", {}).get("total_tokens", 0),
                    "prompt_tokens": task.get("prompt_tokens", 0),
                    "completion_tokens": task.get("completion_tokens", 0),
                    "reasoning_tokens": task.get("reasoning_tokens", 0),
                    "llm_call_count": task.get("llm_call_count", 0),
                    "total_wall_time_ms": task.get("total_wall_time_ms", 0.0),
                    "planning_time_ms": task.get("planning_time_ms", 0.0),
                    "execution_time_ms": task.get("execution_time_ms", 0.0),
                    "reflection_time_ms": task.get("reflection_time_ms", 0.0),
                    "failure_category": task.get("failure_category", ""),
                })


def write_matrix_outputs(run_dir: Path, raw: dict[str, Any], baseline_variant: str) -> None:
    """Write all matrix report artifacts into run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(raw, baseline_variant=baseline_variant)
    _write_json(run_dir / "summary.json", summary)
    write_summary_csv(run_dir / "summary.csv", summary)
    write_cost_latency_csv(run_dir / "cost_latency.csv", raw)
    (run_dir / "variant_comparison.md").write_text(
        build_markdown_report(raw, baseline_variant=baseline_variant),
        encoding="utf-8",
    )
    (run_dir / "failures.md").write_text(build_failures_markdown(raw), encoding="utf-8")


def _fmt_pct(value: float) -> str:
    return f"{float(value or 0.0):.1%}"


def _fmt_delta_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.1%}"


def _fmt_ratio(value: float | None) -> str:
    if value is None or value == 0.0:
        return "-"
    return f"{value:.2f}x"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare evaluation matrix variants")
    parser.add_argument("--run-dir", required=True, help="Directory containing raw_results.json")
    parser.add_argument("--baseline-variant", default="react_auto_baseline")
    parser.add_argument("--format", choices=["markdown"], default="markdown")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    raw_path = run_dir / "raw_results.json"
    raw = _read_json(raw_path)
    report = build_markdown_report(raw, baseline_variant=args.baseline_variant)
    output_path = run_dir / "variant_comparison.md"
    output_path.write_text(report, encoding="utf-8")
    write_matrix_outputs(run_dir, raw, args.baseline_variant)
    print(report)
    print(f"\nReport written to {output_path}")


if __name__ == "__main__":
    main()
