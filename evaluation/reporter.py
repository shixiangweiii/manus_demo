"""JSON and Markdown rendering for raw evaluation dimensions."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.models import EvaluationReport


def render_markdown(report: EvaluationReport) -> str:
    lines = [
        "# Evaluation Report",
        "",
        "No composite score is used. Compare each dimension directly.",
        "",
        "| Experiment | Success | Verifier | Tokens | Latency ms | Tools | Iterations | Replans | Stability | Selector |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report.summaries:
        verifier = "-" if row.verifier_rate is None else f"{row.verifier_rate:.1%}"
        selector = "-" if row.selector_accuracy is None else f"{row.selector_accuracy:.1%}"
        stability = "-" if row.stability is None else f"{row.stability:.2f}"
        lines.append(
            f"| {row.experiment_id} | {row.success_rate:.1%} | {verifier} | "
            f"{row.average_tokens:.0f} | {row.average_latency_ms:.0f} | "
            f"{row.average_tool_calls:.1f} | {row.average_iterations:.1f} | "
            f"{row.average_replans:.1f} | {stability} | {selector} |"
        )
    lines.extend(["", "## Case Results", ""])
    for result in report.results:
        status = "PASS" if result.metrics.success else "FAIL"
        lines.append(
            f"- `{result.case_id}` / `{result.experiment.id}` / trial {result.trial}: "
            f"**{status}**, engine={result.actual_engine.value}, "
            f"latency={result.metrics.latency_ms:.0f}ms, tokens={result.metrics.tokens}"
        )
        if result.error:
            lines.append(f"  - Error: {result.error}")
    return "\n".join(lines) + "\n"


def save_report(report: EvaluationReport, output_dir: str | Path) -> Path:
    target = Path(output_dir).expanduser() / report.report_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (target / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return target
