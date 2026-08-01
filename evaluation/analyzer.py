"""Cross-run observations based on separate metric dimensions."""

from __future__ import annotations

from evaluation.models import AggregateAnalysis, EvalRunRecord, Suggestion


def analyze_runs(runs: list[EvalRunRecord]) -> AggregateAnalysis:
    summaries = [summary for run in runs if run.report for summary in run.report.summaries]
    analysis = AggregateAnalysis(run_ids=[run.run_id for run in runs])
    if not summaries:
        analysis.findings.append("没有已完成且包含报告的运行。")
        return analysis
    best_success = max(summaries, key=lambda item: item.success_rate)
    fastest = min(summaries, key=lambda item: item.average_latency_ms)
    analysis.findings.extend(
        [
            f"成功率最高：{best_success.experiment_id} ({best_success.success_rate:.1%})。",
            f"平均延迟最低：{fastest.experiment_id} ({fastest.average_latency_ms:.0f} ms)。",
        ]
    )
    unstable = [
        item for item in summaries
        if item.stability is not None and item.stability < 0.8
    ]
    if unstable:
        analysis.suggestions.append(
            Suggestion(
                title="优先检查不稳定实验",
                severity="warning",
                evidence=", ".join(f"{item.experiment_id}={item.stability:.2f}" for item in unstable),
                action="增加 repeat，并对比同一 case 的候选工具、迭代次数和失败事件。",
            )
        )
    return analysis
