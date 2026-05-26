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

from evaluation.benchmark import get_benchmark_tasks
from evaluation.metrics import PlanMode, TaskDifficulty

# Conditional rich import — dry-run and offline tests must work without rich
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.table import Table
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


class _PlainConsole:
    """Minimal Console replacement when rich is unavailable."""

    def print(self, *args, **kwargs):
        for a in args:
            text = str(a)
            # Strip basic rich markup
            import re
            text = re.sub(r'\[/?[a-z_]+\]', '', text)
            sys.stdout.write(text + "\n")


console = Console() if _HAS_RICH else _PlainConsole()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if _HAS_RICH:
        logging.basicConfig(
            level=level,
            format="%(message)s",
            handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
        )
    else:
        logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def show_benchmark_tasks() -> None:
    """Display all benchmark tasks without executing them."""
    tasks = get_benchmark_tasks()

    def _verifier_label(task) -> str:
        labels = [str(v.get("type", "?")) for v in task.verifiers]
        return ", ".join(labels) if labels else "-"

    if _HAS_RICH:
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
        table.add_column("Verifiers", width=22)
        table.add_column("Description", width=50)

        for t in tasks:
            gt = t.ground_truth
            table.add_row(
                t.task_id,
                t.difficulty.value,
                ", ".join(t.tags),
                gt.expected_complexity or "-",
                ", ".join(gt.expected_tools) or "-",
                _verifier_label(t),
                t.task_description[:50] + "..." if len(t.task_description) > 50 else t.task_description,
            )

        console.print(table)
        console.print(f"\nTotal: [bold]{len(tasks)}[/bold] benchmark tasks")
    else:
        # Plain-text fallback for environments without rich
        print(f"{'Task ID':<14} {'Diff':<8} {'Mode':<10} {'Verifier':<18} {'Description'}")
        print("-" * 104)
        for t in tasks:
            gt = t.ground_truth
            desc = t.task_description[:48] + ".." if len(t.task_description) > 50 else t.task_description
            verifier = _verifier_label(t)
            verifier = verifier[:16] + ".." if len(verifier) > 18 else verifier
            print(f"{t.task_id:<14} {t.difficulty.value:<8} {gt.expected_complexity or '-':<10} {verifier:<18} {desc}")
        print(f"\nTotal: {len(tasks)} benchmark tasks")


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

    if _HAS_RICH:
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
    else:
        print(f"Evaluation Configuration:")
        print(f"  Modes: {', '.join(m.value for m in modes)}")
        print(f"  Tasks: {len(tasks)}")
        print(f"  Difficulty: {args.difficulty or 'all'}")

    # Run evaluation (lazy import to keep --dry-run free of runtime deps)
    from evaluation.runner import EvaluationRunner
    runner = EvaluationRunner()
    metrics_by_mode = await runner.evaluate_all_modes(
        tasks=tasks, modes=modes, repeat=args.repeat,
    )

    # Render report (lazy import — requires rich)
    if _HAS_RICH:
        from evaluation.report import render_full_report
        render_full_report(metrics_by_mode, output_json=args.output)
    elif args.output:
        # JSON-only export when rich unavailable
        from evaluation.report import export_json
        export_json(metrics_by_mode, args.output)
        print(f"Results exported to {args.output}")

    # Baseline comparison / save (v14.6.4)
    if args.save_baseline:
        from evaluation.baseline import save_baseline
        save_baseline(metrics_by_mode, args.save_baseline)
        console.print(f"[green]Baseline saved to {args.save_baseline}[/green]")

    if args.baseline:
        from evaluation.baseline import load_baseline, compare_baseline
        try:
            bl = load_baseline(args.baseline)
            comp = compare_baseline(metrics_by_mode, bl)
            if comp.regressions:
                console.print("\n[bold red]Regressions detected:[/bold red]")
                for r in comp.regressions:
                    console.print(f"  [red]▼[/red] {r}")
            if comp.improvements:
                console.print("\n[bold green]Improvements detected:[/bold green]")
                for i in comp.improvements:
                    console.print(f"  [green]▲[/green] {i}")
            if not comp.regressions and not comp.improvements:
                console.print("\n[bold]No significant changes vs baseline.[/bold]")
            if args.fail_on_regression and comp.is_regression:
                console.print("\n[bold red]Exiting with code 1 due to regression.[/bold red]")
                sys.exit(1)
        except FileNotFoundError:
            console.print(f"[yellow]Baseline file not found: {args.baseline}[/yellow]")


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
    parser.add_argument(
        "--baseline",
        help="v14.6: Path to baseline JSON for comparison",
    )
    parser.add_argument(
        "--save-baseline",
        help="v14.6: Save results as new baseline to this path",
    )
    parser.add_argument(
        "--fail-on-regression", action="store_true",
        help="v14.6: Exit with code 1 if regression detected vs baseline",
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
