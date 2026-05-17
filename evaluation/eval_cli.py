#!/usr/bin/env python3
"""
Evaluation CLI entry point for Manus Demo.

Usage:
  # Full evaluation (all modes, all tasks) — requires LLM API key
  python -m evaluation.eval_cli

  # Specific modes only
  python -m evaluation.eval_cli --modes simple complex

  # Specific difficulty level
  python -m evaluation.eval_cli --difficulty easy

  # Specific task IDs
  python -m evaluation.eval_cli --tasks easy_001 easy_002

  # Export results to JSON
  python -m evaluation.eval_cli --output results.json

  # Quick smoke test (easy tasks only, skip hard)
  python -m evaluation.eval_cli --difficulty easy --modes simple

  # Dry run (show benchmark tasks without executing)
  python -m evaluation.eval_cli --dry-run

评测命令行入口。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from evaluation.benchmark import get_benchmark_tasks
from evaluation.metrics import PlanMode, TaskDifficulty
from evaluation.report import render_full_report
from evaluation.runner import EvaluationRunner

console = Console()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def show_benchmark_tasks() -> None:
    """Display all benchmark tasks without executing them."""
    tasks = get_benchmark_tasks()

    table = Table(
        title="Benchmark Tasks / 评测基准任务",
        border_style="cyan",
        show_lines=True,
    )
    table.add_column("Task ID", style="cyan", width=14)
    table.add_column("Difficulty", width=10)
    table.add_column("Tags", width=30)
    table.add_column("Expected Mode", width=14)
    table.add_column("Expected Tools", width=25)
    table.add_column("Description", width=50)

    for t in tasks:
        gt = t.ground_truth
        table.add_row(
            t.task_id,
            t.difficulty.value,
            ", ".join(t.tags),
            gt.expected_complexity or "-",
            ", ".join(gt.expected_tools) or "-",
            t.task_description[:50] + "..." if len(t.task_description) > 50 else t.task_description,
        )

    console.print(table)
    console.print(f"\nTotal: [bold]{len(tasks)}[/bold] benchmark tasks")


async def run_evaluation(args: argparse.Namespace) -> None:
    """Execute the evaluation and render results."""
    # Parse modes
    modes: list[PlanMode] = []
    if args.modes:
        for m in args.modes:
            try:
                modes.append(PlanMode(m))
            except ValueError:
                console.print(f"[red]Unknown mode: {m}. Use: simple, complex, emergent[/red]")
                sys.exit(1)
    else:
        modes = list(PlanMode)

    # Parse difficulty filter
    difficulty = None
    if args.difficulty:
        try:
            difficulty = TaskDifficulty(args.difficulty)
        except ValueError:
            console.print(f"[red]Unknown difficulty: {args.difficulty}. Use: easy, medium, hard[/red]")
            sys.exit(1)

    # Get tasks
    tasks = get_benchmark_tasks(
        difficulty=difficulty,
        task_ids=args.tasks if args.tasks else None,
    )

    if not tasks:
        console.print("[red]No matching benchmark tasks found.[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[bold]Evaluation Configuration[/bold]\n\n"
        f"Modes: {', '.join(m.value for m in modes)}\n"
        f"Tasks: {len(tasks)} ({', '.join(t.task_id for t in tasks[:5])}"
        f"{'...' if len(tasks) > 5 else ''})\n"
        f"Difficulty filter: {args.difficulty or 'all'}\n"
        f"Repeat (pass^k): {args.repeat}\n"
        f"Output: {args.output or 'console only'}",
        title="[bold blue]Manus Demo Evaluation[/bold blue]",
        border_style="blue",
    ))

    # Run evaluation
    runner = EvaluationRunner()
    metrics_by_mode = await runner.evaluate_all_modes(
        tasks=tasks, modes=modes, repeat=args.repeat,
    )

    # Render report
    render_full_report(metrics_by_mode, output_json=args.output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manus Demo Evaluation — benchmark 3 plan-and-execute paradigms",
    )
    parser.add_argument(
        "--modes", nargs="+", choices=["simple", "complex", "emergent"],
        help="Planning modes to evaluate (default: all)",
    )
    parser.add_argument(
        "--difficulty", choices=["easy", "medium", "hard"],
        help="Filter tasks by difficulty level",
    )
    parser.add_argument(
        "--tasks", nargs="+",
        help="Specific task IDs to evaluate",
    )
    parser.add_argument(
        "--output", "-o",
        help="Export results to JSON file",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show benchmark tasks without executing",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--repeat", "-k", type=int, default=1,
        help="v8: re-run each (task, mode) k times and report pass^k (TauBench-style reliability). Default: 1 (no repetition).",
    )

    args = parser.parse_args()
    if args.repeat < 1:
        console.print("[red]--repeat must be >= 1[/red]")
        sys.exit(1)
    setup_logging(args.verbose)

    if args.dry_run:
        show_benchmark_tasks()
        return

    asyncio.run(run_evaluation(args))


if __name__ == "__main__":
    main()
