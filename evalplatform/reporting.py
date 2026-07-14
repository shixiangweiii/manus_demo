"""
Single-run report builder — structured EvalReport + rendered Markdown.
单次运行报告生成 —— 结构化 EvalReport + 渲染 Markdown。

Consumes the AggregatedMetrics dumps stored on EvalRunRecord.metrics_by_mode
(dict form), so report building never needs a live runner or LLM.
消费 EvalRunRecord.metrics_by_mode 中的聚合指标 dump（dict 形式），
报告生成不依赖运行器或 LLM。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from evalplatform.models import EvalReport, EvalRunRecord, GeneratedEvalSet

# 维度得分低于该阈值视为薄弱维度
WEAK_SCORE_THRESHOLD = 0.6
WEAK_RATE_THRESHOLD = 0.7


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _pct(v: Any) -> str:
    try:
        return f"{float(v):.1%}"
    except (TypeError, ValueError):
        return "-"


def _num(v: Any, fmt: str = ".3f") -> str:
    try:
        return f"{float(v):{fmt}}"
    except (TypeError, ValueError):
        return "-"


def build_report(run: EvalRunRecord, evalset: GeneratedEvalSet | None = None) -> EvalReport:
    """Build the structured report for one completed (or failed) run.
    为一次运行构建结构化报告。"""
    report = EvalReport(
        run_id=run.run_id,
        evalset_id=run.evalset_id,
        evalset_name=run.evalset_name or (evalset.name if evalset else ""),
        doc_filename=evalset.doc_filename if evalset else "",
        generated_at=time.time(),
    )

    failure_distribution: dict[str, int] = {}
    weak: list[str] = []
    highlights: list[str] = []

    for mode, metrics in sorted(run.metrics_by_mode.items()):
        results = metrics.get("results") or []
        # 分类准确率仅在存在"非强制分类且有期望复杂度"的任务时才有意义；
        # 平台强制规划模式时 aggregate_results 恒得 0.0（未测量，评审 V4 修复）
        # classification is only measured for non-forced tasks with an expected
        # complexity; forced-mode runs structurally yield 0.0 (= "not measured").
        classification_measured = any(
            (r.get("planning") or {}).get("expected_complexity")
            and not (r.get("planning") or {}).get("classification_forced", True)
            for r in results
        )
        report.mode_summaries.append({
            "mode": mode,
            "total_tasks": metrics.get("total_tasks", 0),
            "task_success_rate": metrics.get("task_success_rate", 0.0),
            "avg_overall_score": metrics.get("avg_overall_score", 0.0),
            "avg_planning_score": metrics.get("avg_planning_score", 0.0),
            "avg_execution_score": metrics.get("avg_execution_score", 0.0),
            "avg_efficiency_score": metrics.get("avg_efficiency_score", 0.0),
            "classification_accuracy": metrics.get("classification_accuracy", 0.0),
            "classification_measured": classification_measured,
            "avg_total_tokens": metrics.get("avg_total_tokens", 0.0),
            "avg_execution_time_ms": metrics.get("avg_execution_time_ms", 0.0),
            "avg_replan_count": metrics.get("avg_replan_count", 0.0),
        })

        for cat, count in (metrics.get("failure_distribution") or {}).items():
            failure_distribution[cat] = failure_distribution.get(cat, 0) + int(count)

        # 薄弱维度检测 / weak-dimension detection
        checks = [
            ("task_success_rate", WEAK_RATE_THRESHOLD, f"[{mode}] 任务成功率偏低"),
            ("avg_planning_score", WEAK_SCORE_THRESHOLD, f"[{mode}] 规划评分偏低"),
            ("avg_execution_score", WEAK_SCORE_THRESHOLD, f"[{mode}] 执行评分偏低"),
            ("avg_efficiency_score", WEAK_SCORE_THRESHOLD, f"[{mode}] 效率评分偏低"),
        ]
        # 反思准确率仅在实际观测到 reflection 事件时才是"分数"，否则 0.0 表示
        # 未测量，不应报薄弱（评审 V4 修复）/ only meaningful when observed
        if float(metrics.get("reflection_coverage_rate") or 0.0) > 0:
            checks.append(
                ("reflection_accuracy", WEAK_RATE_THRESHOLD, f"[{mode}] 反思准确率偏低"),
            )
        for key, threshold, label in checks:
            value = float(metrics.get(key) or 0.0)
            if metrics.get("total_tasks", 0) and value < threshold:
                weak.append(f"{label}（{value:.1%}，阈值 {threshold:.0%}）")

        judge_overrides = int(metrics.get("judge_override_count") or 0)
        if judge_overrides:
            highlights.append(f"[{mode}] LLM 裁判覆盖关键词失败判定 {judge_overrides} 次（关键词标准可能过严）")

        for r in results:
            execution = r.get("execution") or {}
            report.per_task_rows.append({
                "task_id": r.get("task_id", ""),
                "mode": mode,
                "difficulty": r.get("task_difficulty", ""),
                "success": bool(execution.get("task_success")),
                "overall_score": r.get("overall_score", 0.0),
                "total_tokens": (r.get("efficiency") or {}).get("total_tokens", 0),
                "failure_category": r.get("failure_category", ""),
                "verifier": (
                    f"{r.get('verifier_passed', 0)}/{r.get('verifier_total', 0)}"
                    if r.get("verifier_total") else "-"
                ),
                "judge_overrode": bool(r.get("judge_overrode")),
                "final_answer_preview": str(r.get("final_answer", ""))[:200],
            })

    report.failure_distribution = failure_distribution
    report.weak_dimensions = weak

    # 最佳 / 最差任务提示
    scored = [row for row in report.per_task_rows if row.get("overall_score") is not None]
    if scored:
        best = max(scored, key=lambda r: r["overall_score"])
        worst = min(scored, key=lambda r: r["overall_score"])
        highlights.append(f"最佳任务：{best['task_id']}（{best['mode']}，{best['overall_score']:.3f}）")
        highlights.append(f"最差任务：{worst['task_id']}（{worst['mode']}，{worst['overall_score']:.3f}）")
    if run.status.value == "failed" and run.error:
        highlights.insert(0, f"运行以失败结束：{run.error}")
    report.highlights = highlights

    report.markdown = render_markdown(run, report)
    return report


def render_markdown(run: EvalRunRecord, report: EvalReport) -> str:
    """Render the report as Markdown. 渲染 Markdown 报告。"""
    lines: list[str] = []
    lines.append(f"# 评测报告 — {report.evalset_name or run.evalset_id}")
    lines.append("")
    lines.append(f"- **运行 ID**：`{run.run_id}`  |  **评测集**：`{run.evalset_id}`")
    if report.doc_filename:
        lines.append(f"- **来源文档**：{report.doc_filename}")
    lines.append(f"- **状态**：{run.status.value}  |  **模式**：{', '.join(run.modes)}  |  **repeat**：{run.repeat}")
    lines.append(f"- **模型**：{run.llm_model or '-'}")
    lines.append(f"- **开始**：{_fmt_ts(run.started_at)}  |  **结束**：{_fmt_ts(run.finished_at)}")
    lines.append("")

    if run.error:
        lines.append(f"> ⚠️ 运行错误：{run.error}")
        lines.append("")

    if report.mode_summaries:
        lines.append("## 模式概要")
        lines.append("")
        lines.append("| 模式 | 任务数 | 成功率 | 综合 | 规划 | 执行 | 效率 | 分类准确率 | 平均Token | 平均耗时(ms) |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for s in report.mode_summaries:
            # 强制模式下分类从未发生，显示 "-" 而非误导性的 0.0%（评审 V4 修复）
            class_cell = (
                _pct(s["classification_accuracy"])
                if s.get("classification_measured") else "-"
            )
            lines.append(
                f"| {s['mode']} | {s['total_tasks']} | {_pct(s['task_success_rate'])} "
                f"| {_num(s['avg_overall_score'])} | {_num(s['avg_planning_score'])} "
                f"| {_num(s['avg_execution_score'])} | {_num(s['avg_efficiency_score'])} "
                f"| {class_cell} | {_num(s['avg_total_tokens'], '.0f')} "
                f"| {_num(s['avg_execution_time_ms'], '.0f')} |"
            )
        lines.append("")

    if report.per_task_rows:
        lines.append("## 逐任务结果")
        lines.append("")
        lines.append("| 任务 ID | 模式 | 难度 | 成功 | 综合评分 | Token | Verifier | 失败类别 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for row in report.per_task_rows:
            success = "✅" if row["success"] else "❌"
            judge = " (judge)" if row.get("judge_overrode") else ""
            lines.append(
                f"| {row['task_id']} | {row['mode']} | {row['difficulty']} | {success}{judge} "
                f"| {_num(row['overall_score'])} | {row['total_tokens']} "
                f"| {row['verifier']} | {row['failure_category'] or '-'} |"
            )
        lines.append("")

    if report.failure_distribution:
        lines.append("## 失败分布")
        lines.append("")
        for cat, count in sorted(report.failure_distribution.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{cat}`：{count} 次")
        lines.append("")

    if report.weak_dimensions:
        lines.append("## 薄弱维度")
        lines.append("")
        for w in report.weak_dimensions:
            lines.append(f"- ⚠️ {w}")
        lines.append("")

    if report.highlights:
        lines.append("## 要点")
        lines.append("")
        for h in report.highlights:
            lines.append(f"- {h}")
        lines.append("")

    lines.append("---")
    lines.append(f"*报告生成时间：{_fmt_ts(report.generated_at)}（manus_demo 评测平台）*")
    return "\n".join(lines)
