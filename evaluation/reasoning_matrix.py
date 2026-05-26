"""
Run reasoning-engine evaluation matrices across suites and variants.

Example:
  python -m evaluation.reasoning_matrix \
    --suite smoke_reasoning \
    --variants react_auto_baseline reasoning_auto reasoning_high \
    --repeat 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.benchmark import get_benchmark_tasks
from evaluation.compare_variants import write_matrix_outputs
from evaluation.metrics import AggregatedMetrics, PlanMode
from evaluation.suites import (
    EvaluationSuite,
    get_suite,
    list_suites,
    resolve_suite_task_ids,
    validate_suite_tasks,
)
from evaluation.variants import EvaluationVariant, apply_variant, get_variant, list_variants

logger = logging.getLogger(__name__)


def _model_dump(obj: Any, **kwargs) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json", **kwargs)
        except TypeError:
            return obj.model_dump(**kwargs)
    return dict(obj)


def _parse_modes(values: list[str] | None) -> list[PlanMode] | None:
    if not values:
        return None
    return [PlanMode(value) for value in values]


def _variant_modes(
    variant: EvaluationVariant,
    suite: EvaluationSuite,
    override_modes: list[PlanMode] | None,
) -> list[PlanMode]:
    if override_modes:
        return override_modes
    if variant.modes:
        return variant.modes
    return suite.default_modes or list(PlanMode)


def _serialize_metrics(metrics: AggregatedMetrics) -> dict[str, Any]:
    return {
        "metrics": _model_dump(metrics, exclude={"results"}),
        "per_task_results": [_model_dump(result) for result in metrics.results],
    }


def _annotate_variant(metrics_by_mode: dict[PlanMode, AggregatedMetrics], variant: EvaluationVariant) -> None:
    for metrics in metrics_by_mode.values():
        for result in metrics.results:
            result.variant_id = variant.id
            result.variant_config = dict(variant.env_overrides)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _run_id(suite_id: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{suite_id}"


def _print_dry_run(
    suite: EvaluationSuite,
    variants: list[EvaluationVariant],
    task_ids: list[str],
    repeat: int,
    override_modes: list[PlanMode] | None,
) -> None:
    print(f"Suite: {suite.id} — {suite.description}")
    print(f"Tasks ({len(task_ids)}): {', '.join(task_ids)}")
    print(f"Repeat: {repeat}")
    print("Variants:")
    for variant in variants:
        modes = _variant_modes(variant, suite, override_modes)
        print(f"  - {variant.id}: modes={','.join(m.value for m in modes)} overrides={variant.env_overrides}")


async def run_matrix(args: argparse.Namespace) -> Path:
    suite = get_suite(args.suite)
    validate_suite_tasks(suite)
    task_ids = resolve_suite_task_ids(suite, args.tasks)
    known_ids = {task.task_id for task in get_benchmark_tasks()}
    missing_ids = [task_id for task_id in task_ids if task_id not in known_ids]
    if missing_ids:
        raise SystemExit(f"Unknown benchmark task ids: {', '.join(missing_ids)}")
    tasks = get_benchmark_tasks(task_ids=task_ids)
    if not tasks:
        raise SystemExit("No benchmark tasks matched the selected suite/task ids.")

    variant_ids = args.variants or suite.recommended_variants
    variants = [get_variant(variant_id) for variant_id in variant_ids]
    repeat = args.repeat if args.repeat is not None else suite.default_repeat
    override_modes = _parse_modes(args.modes)

    if args.dry_run:
        _print_dry_run(suite, variants, [task.task_id for task in tasks], repeat, override_modes)
        return Path(args.output_dir)

    run_id = args.run_id or _run_id(suite.id)
    run_dir = Path(args.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    suite_payload = _model_dump(suite)
    suite_payload["task_ids"] = [task.task_id for task in tasks]

    raw: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "suite": suite_payload,
        "repeat": repeat,
        "baseline_variant": args.baseline_variant,
        "variants": {},
    }

    config_snapshot = {
        "run_id": run_id,
        "suite": suite_payload,
        "repeat": repeat,
        "variants": [_model_dump(variant) for variant in variants],
        "override_modes": [m.value for m in override_modes] if override_modes else None,
    }
    _write_json(run_dir / "config_snapshot.json", config_snapshot)

    from evaluation.runner import EvaluationRunner

    for variant in variants:
        modes = _variant_modes(variant, suite, override_modes)
        print(
            f"[Matrix] Running variant={variant.id} modes={','.join(m.value for m in modes)} "
            f"tasks={len(tasks)} repeat={repeat}"
        )
        with apply_variant(variant):
            runner = EvaluationRunner()
            metrics_by_mode = await runner.evaluate_all_modes(
                tasks=tasks,
                modes=modes,
                repeat=repeat,
            )

        _annotate_variant(metrics_by_mode, variant)
        raw["variants"][variant.id] = {
            "variant": _model_dump(variant),
            "modes": {
                mode.value: _serialize_metrics(metrics)
                for mode, metrics in metrics_by_mode.items()
            },
        }

        # Incremental write makes long, expensive matrix runs inspectable.
        _write_json(run_dir / "raw_results.json", raw)
        write_matrix_outputs(run_dir, raw, args.baseline_variant)

    raw["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(run_dir / "raw_results.json", raw)
    write_matrix_outputs(run_dir, raw, args.baseline_variant)
    print(f"[Matrix] Results written to {run_dir}")
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reasoning-engine evaluation matrix")
    parser.add_argument("--suite", default="smoke_reasoning", choices=[s.id for s in list_suites()])
    parser.add_argument("--variants", nargs="+", choices=[v.id for v in list_variants()])
    parser.add_argument("--modes", nargs="+", choices=["simple", "complex", "emergent"])
    parser.add_argument("--tasks", nargs="+", help="Override suite task ids")
    parser.add_argument("--repeat", type=int, help="Override suite repeat count")
    parser.add_argument("--output-dir", default="evaluation/results")
    parser.add_argument("--run-id", help="Stable run id for reproducible output paths")
    parser.add_argument("--baseline-variant", default="react_auto_baseline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.repeat is not None and args.repeat < 1:
        raise SystemExit("--repeat must be >= 1")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    asyncio.run(run_matrix(args))


if __name__ == "__main__":
    main()
