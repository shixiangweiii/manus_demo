"""
Classifier calibration CLI (v17.3).
分类器校准命令行（v17.3）。

Usage / 用法:
  python -m evolution.calibrate
  python -m evolution.calibrate --show-per-task
  python -m evolution.calibrate --simple-range -4:1 --complex-range 1:6 -o /tmp/sug.json

离线运行，无需 API key；只产出建议 JSON 并打印应用方式，绝不改 live config。
"""

from __future__ import annotations

import argparse
import os

import config
from evolution.calibration import (
    DEFAULT_COMPLEX_RANGE,
    DEFAULT_SIMPLE_RANGE,
    ClassifierCalibrator,
    ThresholdEval,
)


def _parse_range(text: str, default: range) -> range:
    """Parse "min:max" (inclusive max) into a range; fall back to default."""
    if not text:
        return default
    try:
        lo_s, hi_s = text.split(":")
        lo, hi = int(lo_s), int(hi_s)
        return range(lo, hi + 1)
    except Exception:
        raise SystemExit(f"Invalid range '{text}', expected form like -4:1")


def _default_output_path() -> str:
    return os.path.join(config.MEMORY_DIR, "classifier_thresholds.suggested.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m evolution.calibrate",
        description="Offline grid-search calibration for task-complexity rule thresholds (v17.3). "
                    "Suggestion-only; never modifies live config.",
    )
    parser.add_argument(
        "--output", "-o", default=_default_output_path(),
        help="Path to write the suggestion JSON (default: ${MEMORY_DIR}/classifier_thresholds.suggested.json)",
    )
    parser.add_argument(
        "--simple-range", default="",
        help="Grid-search range for simple threshold as 'min:max' inclusive (default -4:0)",
    )
    parser.add_argument(
        "--complex-range", default="",
        help="Grid-search range for complex threshold as 'min:max' inclusive (default 1:5)",
    )
    parser.add_argument(
        "--show-per-task", action="store_true",
        help="Print per-task rule score / expected / emergent breakdown",
    )
    args = parser.parse_args()

    simple_range = _parse_range(args.simple_range, DEFAULT_SIMPLE_RANGE)
    complex_range = _parse_range(args.complex_range, DEFAULT_COMPLEX_RANGE)

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        _rich = True
    except Exception:
        console = None
        _rich = False

    calibrator = ClassifierCalibrator()
    if not calibrator.rows:
        msg = "No benchmark tasks with expected_complexity ground truth — nothing to calibrate."
        print(msg)
        return

    suggestion = calibrator.suggest(simple_range, complex_range)
    ClassifierCalibrator.write_suggestion(suggestion, args.output)

    cur = suggestion.current
    sug = suggestion.suggested

    def _fmt(te: ThresholdEval) -> str:
        return (f"simple<={te.simple_threshold}, complex>={te.complex_threshold} | "
                f"acc={te.accuracy:.1%} ambiguous={te.ambiguous_rate:.1%} "
                f"({te.correct}/{te.total})")

    if _rich:
        table = Table(title="Classifier Calibration (v17.3) — offline grid-search")
        table.add_column("", style="bold")
        table.add_column("simple≤", justify="right")
        table.add_column("complex≥", justify="right")
        table.add_column("accuracy", justify="right")
        table.add_column("ambiguous", justify="right")
        table.add_column("correct", justify="right")
        table.add_row("current", str(cur.simple_threshold), str(cur.complex_threshold),
                      f"{cur.accuracy:.1%}", f"{cur.ambiguous_rate:.1%}", f"{cur.correct}/{cur.total}")
        table.add_row("suggested", str(sug.simple_threshold), str(sug.complex_threshold),
                      f"{sug.accuracy:.1%}", f"{sug.ambiguous_rate:.1%}", f"{sug.correct}/{sug.total}",
                      style="green" if suggestion.improved else "dim")
        console.print(table)
    else:
        print("Classifier Calibration (v17.3) — offline grid-search")
        print(f"  current  : {_fmt(cur)}")
        print(f"  suggested: {_fmt(sug)}")

    if args.show_per_task:
        rows = sorted(suggestion.rows, key=lambda r: (r.expected, r.task_id))
        if _rich:
            t2 = Table(title="Per-task rule scores")
            t2.add_column("task_id")
            t2.add_column("expected")
            t2.add_column("score", justify="right")
            t2.add_column("emergent")
            for r in rows:
                t2.add_row(r.task_id, r.expected, str(r.score), "yes" if r.is_emergent else "")
            console.print(t2)
        else:
            for r in rows:
                print(f"  {r.task_id:<14} expected={r.expected:<9} score={r.score:<3} "
                      f"emergent={'yes' if r.is_emergent else 'no'}")

    print(f"\nSuggestion written to: {args.output}")
    if suggestion.improved:
        apply_msg = (
            "\nTo APPLY the suggestion (manual, no silent self-modification):\n"
            f"  export CLASSIFIER_SIMPLE_THRESHOLD={sug.simple_threshold} "
            f"CLASSIFIER_COMPLEX_THRESHOLD={sug.complex_threshold}"
        )
        if _rich:
            console.print(apply_msg, style="yellow")
        else:
            print(apply_msg)
    else:
        print("\nNo improvement over current thresholds — keeping current config is recommended.")


if __name__ == "__main__":
    main()
