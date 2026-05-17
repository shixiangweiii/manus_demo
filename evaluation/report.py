"""
Evaluation report generation — Rich console output + JSON export.

Generates comparative reports across planning modes with:
  - Summary comparison table (3 modes side by side)
  - Per-mode detail tables
  - Per-task drill-down
  - Failure analysis distribution

评测报告生成 —— Rich 控制台输出 + JSON 导出。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from evaluation.metrics import (
    AggregatedMetrics,
    PlanMode,
    TaskEvaluationResult,
)

logger = logging.getLogger(__name__)
console = Console()


def render_comparison_table(
    metrics_by_mode: dict[PlanMode, AggregatedMetrics],
) -> None:
    """
    Render a side-by-side comparison table of all planning modes.
    渲染三种规划模式的并排对比表。
    """
    if not metrics_by_mode:
        console.print("[red]No evaluation data to display.[/red]")
        return

    modes = sorted(metrics_by_mode.keys(), key=lambda m: m.value)

    # --- Main comparison table ---
    table = Table(
        title="Plan-Execute Paradigm Comparison / 规划执行范式对比",
        border_style="cyan",
        show_lines=True,
    )
    table.add_column("Metric / 指标", style="bold white", width=40)
    for mode in modes:
        table.add_column(
            f"{mode.value.upper()}",
            style="bold cyan",
            justify="center",
            width=18,
        )

    # Helper to find best mode for a metric
    def _best(values: dict[PlanMode, float], higher_better: bool = True) -> PlanMode:
        if higher_better:
            return max(values, key=values.get) if values else modes[0]
        return min(values, key=values.get) if values else modes[0]

    def _fmt_cell(value: float, mode: PlanMode, best_mode: PlanMode, fmt: str = ".3f") -> str:
        if mode == best_mode:
            return f"[bold green]{value:{fmt}}[/bold green]"
        return f"{value:{fmt}}"

    # Detect whether pass^k / new-feature data is present (conditional rows)
    has_passk = any(
        m.avg_pass_at_k > 0 or any(r.trial_count > 1 for r in m.results)
        for m in metrics_by_mode.values()
    )
    has_subagent = any(m.avg_subagent_calls > 0 for m in metrics_by_mode.values())
    has_hitl = any(m.avg_hitl_calls > 0 for m in metrics_by_mode.values())
    has_goal_driven = any(
        m.avg_goal_anchor_count > 0 or m.stagnation_rate > 0
        for m in metrics_by_mode.values()
    )
    has_judge = any(m.judge_override_count > 0 for m in metrics_by_mode.values())

    # Row data
    rows = [
        ("Total Tasks / 总任务数", "total_tasks", "d", False),
        ("Task Success Rate / 任务成功率", "task_success_rate", ".1%", True),
        ("Overall Score / 综合评分", "avg_overall_score", ".3f", True),
        ("Planning Score / 规划评分", "avg_planning_score", ".3f", True),
        ("Execution Score / 执行评分", "avg_execution_score", ".3f", True),
        ("Efficiency Score / 效率评分", "avg_efficiency_score", ".3f", True),
        ("Classification Accuracy / 分类准确率", "classification_accuracy", ".1%", True),
        ("Plan Validity Rate / 计划有效率", "plan_validity_rate", ".1%", True),
        ("Step Coverage / 步骤覆盖率", "avg_step_coverage", ".1%", True),
        ("Avg Step Success Rate / 步骤成功率", "avg_step_success_rate", ".1%", True),
        ("Tool Accuracy / 工具准确率", "avg_tool_accuracy", ".1%", True),
        ("Avg ReAct Iterations / ReAct迭代数", "avg_react_iterations", ".1f", False),
        ("Avg Total Tokens / 平均Token消耗", "avg_total_tokens", ".0f", False),
        ("Avg Execution Time (ms) / 执行耗时", "avg_execution_time_ms", ".0f", False),
        ("Avg Replan Count / 平均重规划次数", "avg_replan_count", ".2f", False),
        ("Reflection Accuracy / 反思准确率", "reflection_accuracy", ".1%", True),
        ("Reflection Coverage / 反思覆盖率", "reflection_coverage_rate", ".1%", True),
        ("False Positive Rate / 误判通过率", "false_positive_rate", ".1%", False),
        ("False Negative Rate / 误判拒绝率", "false_negative_rate", ".1%", False),
    ]
    # v8 conditional rows — only add when relevant data exists
    if has_passk:
        rows.append(("Avg Pass^k (Reliability) / 平均通过率", "avg_pass_at_k", ".1%", True))
        rows.append(("Pass^k Std Dev / 通过率标准差", "pass_at_k_std", ".3f", False))
    if has_subagent:
        rows.append(("Avg SubAgent Calls / 子智能体调用次数", "avg_subagent_calls", ".2f", False))
        rows.append(("SubAgent Success Rate / 子智能体成功率", "avg_subagent_success_rate", ".1%", True))
        rows.append(("Avg SubAgent Tokens / 子智能体平均Token", "avg_subagent_tokens", ".0f", False))
    if has_hitl:
        rows.append(("Avg HITL Calls / 人机交互调用次数", "avg_hitl_calls", ".2f", False))
        rows.append(("Avg HITL Wait (ms) / 人机交互等待", "avg_hitl_wait_ms", ".0f", False))
        rows.append(("HITL Timeouts / 人机交互超时总数", "hitl_timeout_total", "d", False))
        rows.append(("HITL Cancelled / 人机交互取消总数", "hitl_cancelled_total", "d", False))
    if has_goal_driven:
        rows.append(("Avg Goal Anchors / 目标锚定次数", "avg_goal_anchor_count", ".2f", False))
        rows.append(("Avg Goal Re-anchors / 目标重锚定次数", "avg_goal_reanchor_count", ".2f", False))
        rows.append(("Stagnation Rate / 停滞触发率", "stagnation_rate", ".1%", False))
    if has_judge:
        rows.append(("LLM Judge Overrides / 裁判覆盖次数", "judge_override_count", "d", False))

    for label, attr, fmt, higher_better in rows:
        values = {}
        for mode in modes:
            m = metrics_by_mode[mode]
            v = getattr(m, attr, 0)
            if fmt == ".1%":
                values[mode] = v * 100
            else:
                values[mode] = v

        best_mode = _best(values, higher_better)
        row = [label]
        for mode in modes:
            v = values[mode]
            if fmt == ".1%":
                cell = f"{v:.1f}%"
            elif fmt == "d":
                cell = str(int(v))
            else:
                cell = f"{v:{fmt}}"
            if mode == best_mode and all(v > 0 for v in values.values()):
                cell = f"[bold green]{cell} ★[/bold green]"
            row.append(cell)
        table.add_row(*row)

    console.print(table)

    # --- Success rate by difficulty ---
    diff_table = Table(
        title="Success Rate by Difficulty / 各难度成功率",
        border_style="green",
        show_lines=True,
    )
    diff_table.add_column("Difficulty / 难度", style="bold white", width=20)
    for mode in modes:
        diff_table.add_column(mode.value.upper(), justify="center", width=15)

    for diff in ["easy", "medium", "hard"]:
        row = [diff]
        for mode in modes:
            sr = metrics_by_mode[mode].success_rate_by_difficulty.get(diff, 0.0)
            if sr > 0:
                row.append(f"{sr:.1%}")
            else:
                row.append("-")
        diff_table.add_row(*row)
    console.print(diff_table)

    # --- Failure distribution ---
    all_failures: dict[str, dict[str, int]] = {}
    for mode in modes:
        all_failures[mode.value] = metrics_by_mode[mode].failure_distribution

    if any(any(v.values()) for v in all_failures.values() if v):
        fail_table = Table(
            title="Failure Distribution / 失败分布",
            border_style="red",
            show_lines=True,
        )
        fail_table.add_column("Failure Category / 失败类别", style="bold white", width=35)
        for mode in modes:
            fail_table.add_column(mode.value.upper(), justify="center", width=15)

        # Collect all categories
        categories = set()
        for mode_data in all_failures.values():
            categories.update(mode_data.keys())
        for cat in sorted(categories):
            row = [cat]
            for mode in modes:
                count = all_failures[mode.value].get(cat, 0)
                row.append(str(count) if count > 0 else "-")
            fail_table.add_row(*row)
        console.print(fail_table)


def render_mode_detail(metrics: AggregatedMetrics) -> None:
    """
    Render detailed metrics for a single planning mode.
    渲染单个规划模式的详细指标。
    """
    mode = metrics.planning_mode.value.upper()
    console.print(Panel(
        f"[bold]{mode} Mode Detail[/bold]\n\n"
        f"Tasks: {metrics.total_tasks}  |  "
        f"Success Rate: {metrics.task_success_rate:.1%}\n"
        f"Avg Score: {metrics.avg_overall_score:.3f}  |  "
        f"Planning: {metrics.avg_planning_score:.3f}  |  "
        f"Execution: {metrics.avg_execution_score:.3f}  |  "
        f"Efficiency: {metrics.avg_efficiency_score:.3f}\n"
        f"Avg Tokens: {metrics.avg_total_tokens:.0f}  |  "
        f"Avg Time: {metrics.avg_execution_time_ms:.0f}ms  |  "
        f"Replans: {metrics.avg_replan_count:.2f}",
        title=f"[bold cyan]{mode} Mode Report[/bold cyan]",
        border_style="cyan",
    ))

    # Per-task results
    if metrics.results:
        # v8: detect feature presence to decide conditional columns
        show_passk = any(r.trial_count > 1 for r in metrics.results)
        show_subagent = any(r.execution.subagent_calls > 0 for r in metrics.results)
        show_hitl = any(r.execution.hitl_calls > 0 for r in metrics.results)
        show_judge = any(r.judge_overrode for r in metrics.results)

        task_table = Table(
            title=f"Per-Task Results ({mode})",
            border_style="blue",
            show_lines=True,
        )
        task_table.add_column("Task ID", style="cyan", width=18)
        task_table.add_column("Difficulty", width=10)
        task_table.add_column("Success", width=8)
        task_table.add_column("Score", justify="right", width=8)
        task_table.add_column("Plan", justify="right", width=8)
        task_table.add_column("Exec", justify="right", width=8)
        task_table.add_column("Eff.", justify="right", width=8)
        task_table.add_column("Tokens", justify="right", width=8)
        task_table.add_column("Steps", justify="right", width=8)
        if show_passk:
            task_table.add_column("Pass^k", justify="center", width=10)
        if show_subagent:
            task_table.add_column("SubAgent", justify="center", width=10)
        if show_hitl:
            task_table.add_column("HITL", justify="center", width=8)
        if show_judge:
            task_table.add_column("Judge", justify="center", width=6)

        for r in metrics.results:
            success_str = "[green]✓[/green]" if r.execution.task_success else "[red]✗[/red]"
            row = [
                r.task_id,
                r.task_difficulty.value,
                success_str,
                f"{r.overall_score:.3f}",
                f"{r.planning_score:.3f}",
                f"{r.execution_score:.3f}",
                f"{r.efficiency_score:.3f}",
                str(r.efficiency.total_tokens),
                f"{r.execution.steps_completed}/{r.execution.total_steps_planned}",
            ]
            if show_passk:
                if r.trial_count > 1 and r.pass_at_k is not None:
                    row.append(f"{sum(r.trial_results)}/{r.trial_count} ({r.pass_at_k:.1%})")
                else:
                    row.append("-")
            if show_subagent:
                if r.execution.subagent_calls > 0:
                    row.append(f"{r.execution.subagent_success_count}/{r.execution.subagent_calls}")
                else:
                    row.append("-")
            if show_hitl:
                if r.execution.hitl_calls > 0:
                    row.append(str(r.execution.hitl_calls))
                else:
                    row.append("-")
            if show_judge:
                row.append("[yellow]Y[/yellow]" if r.judge_overrode else "-")
            task_table.add_row(*row)
        console.print(task_table)


def render_summary_tree(
    metrics_by_mode: dict[PlanMode, AggregatedMetrics],
) -> None:
    """
    Render a tree view summarizing all modes.
    渲染树形视图汇总所有模式。
    """
    tree = Tree("[bold]Evaluation Summary / 评测总结[/bold]")

    for mode, metrics in metrics_by_mode.items():
        sr = metrics.task_success_rate
        style = "green" if sr >= 0.7 else "yellow" if sr >= 0.4 else "red"
        branch = tree.add(
            f"[bold {style}]{mode.value.upper()}[/bold {style}]: "
            f"SR={sr:.1%} Score={metrics.avg_overall_score:.3f} "
            f"Tokens={metrics.avg_total_tokens:.0f}"
        )
        branch.add(f"Planning: {metrics.avg_planning_score:.3f} (class_acc={metrics.classification_accuracy:.1%})")
        branch.add(f"Execution: {metrics.avg_execution_score:.3f} (tool_acc={metrics.avg_tool_accuracy:.1%})")
        branch.add(f"Efficiency: {metrics.avg_efficiency_score:.3f} (replans={metrics.avg_replan_count:.2f})")
        branch.add(f"Reflection: acc={metrics.reflection_accuracy:.1%} FP={metrics.false_positive_rate:.1%} FN={metrics.false_negative_rate:.1%}")

    console.print(tree)


def export_json(
    metrics_by_mode: dict[PlanMode, AggregatedMetrics],
    output_path: str,
) -> None:
    """
    Export evaluation results to a JSON file.
    将评测结果导出为 JSON 文件。
    """
    export_data = {
        "timestamp": datetime.now().isoformat(),
        "modes": {},
    }

    for mode, metrics in metrics_by_mode.items():
        mode_data = metrics.model_dump(exclude={"results"})
        mode_data["per_task_results"] = [
            r.model_dump() for r in metrics.results
        ]
        export_data["modes"][mode.value] = mode_data

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)

    logger.info("[Report] Results exported to %s", output_path)


def render_full_report(
    metrics_by_mode: dict[PlanMode, AggregatedMetrics],
    output_json: str | None = None,
) -> None:
    """
    Render the complete evaluation report.
    渲染完整的评测报告。
    """
    console.print()
    console.print(Panel(
        "[bold]Manus Demo Evaluation Report[/bold]\n"
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Modes evaluated: {', '.join(m.value for m in metrics_by_mode.keys())}",
        title="[bold blue]Evaluation Report / 评测报告[/bold blue]",
        border_style="blue",
    ))

    # Comparison table
    render_comparison_table(metrics_by_mode)

    # Per-mode details
    for mode, metrics in metrics_by_mode.items():
        render_mode_detail(metrics)

    # Summary tree
    render_summary_tree(metrics_by_mode)

    # JSON export
    if output_json:
        export_json(metrics_by_mode, output_json)
        console.print(f"\n[green]Results exported to: {output_json}[/green]")
