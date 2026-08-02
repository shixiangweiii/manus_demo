"""Unified command line for static cases and the local evaluation platform."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from core.models import Effort, EngineKind
from core.settings import get_settings
from evaluation.analyzer import analyze_runs
from evaluation.case_loader import load_cases
from evaluation.document import ingest_document
from evaluation.executor import validate_repeat
from evaluation.experiments import build_experiments
from evaluation.generator import EvalSetGenerator
from evaluation.models import EvalRunRecord, EvalSetStatus, RunStatus
from evaluation.reporter import render_markdown, save_report
from evaluation.runner import EvaluationRunner
from evaluation.store import EvaluationStore


def _comma_sets(values: list[str] | None) -> list[list[str]]:
    if not values:
        return [[]]
    return [[item.strip() for item in value.split(",") if item.strip()] for value in values]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="Unified local evaluation runner and platform",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run built-in, file-based, or stored cases")
    run.add_argument("--case-file", action="append", help="JSON case file; may be repeated")
    run.add_argument("--cases", nargs="+", help="Case IDs")
    run.add_argument("--tags", nargs="+", help="Case tags")
    run.add_argument("--difficulty", choices=["easy", "medium", "hard"])
    run.add_argument("--evalset", help="Stored generated evaluation set ID")
    run.add_argument("--engines", nargs="+", choices=[k.value for k in EngineKind], default=[k.value for k in EngineKind])
    run.add_argument("--efforts", nargs="+", choices=[k.value for k in Effort], default=["auto"])
    run.add_argument("--capability-set", action="append", help="Comma-separated enabled capabilities; repeat for matrix rows")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--dry-run", action="store_true", help="Print matrix without executing")
    run.add_argument("--output-dir", help="Result root; defaults to settings.toml")

    serve = subparsers.add_parser("serve", help="Start the local evaluation UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int)

    upload = subparsers.add_parser("upload", help="Store a local text document")
    upload.add_argument("path")

    generate = subparsers.add_parser("generate", help="Generate cases from a stored document")
    generate.add_argument("doc_id")
    generate.add_argument("--name", default="generated-evaluation")
    generate.add_argument("--goal", default="")
    generate.add_argument("--num-tasks", type=int)

    report = subparsers.add_parser("report", help="Render a stored run report")
    report.add_argument("run_id")

    analyze = subparsers.add_parser("analyze", help="Analyze completed runs")
    analyze.add_argument("run_ids", nargs="+")

    listing = subparsers.add_parser("list", help="List stored platform records")
    listing.add_argument("kind", choices=["documents", "evalsets", "runs", "analyses", "cases"])
    return parser


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    args.repeat = validate_repeat(args.repeat)
    output_dir = args.output_dir or settings.evaluation.output_dir
    store = None
    if args.evalset:
        store = EvaluationStore(output_dir)
        evalset = store.get_evalset(args.evalset)
        if evalset is None:
            raise ValueError(f"Evaluation set not found: {args.evalset}")
        if evalset.status != EvalSetStatus.READY or not evalset.tasks:
            raise ValueError(f"Evaluation set is not ready: {args.evalset}")
        cases = evalset.tasks
    else:
        cases = load_cases(
            paths=args.case_file,
            difficulty=args.difficulty,
            tags=args.tags,
            task_ids=args.cases,
        )
    if not cases:
        raise ValueError("No evaluation cases matched the requested filters")
    experiments = build_experiments(
        args.engines,
        args.efforts,
        _comma_sets(args.capability_set),
    )
    runner = EvaluationRunner(settings)
    runner.validate_experiments(experiments)
    if args.dry_run:
        print(json.dumps({
            "cases": [case.task_id for case in cases],
            "experiments": [experiment.model_dump(mode="json") for experiment in experiments],
            "units": len(cases) * len(experiments) * args.repeat,
        }, ensure_ascii=False, indent=2))
        return

    store = store or EvaluationStore(output_dir)

    run_record = EvalRunRecord(
        evalset_id=args.evalset or "built-in",
        evalset_name=args.evalset or "built-in cases",
        experiments=experiments,
        repeat=args.repeat,
        status=RunStatus.RUNNING,
    )
    run_record.started_at = time.time()
    run_record.progress.total_units = len(cases) * len(experiments) * args.repeat
    store.save_run(run_record)

    def progress(completed, total, case, experiment):
        run_record.progress.completed_units = completed
        run_record.progress.total_units = total
        run_record.progress.current_task_id = case.task_id
        run_record.progress.current_experiment = experiment.id
        store.save_run(run_record)
        print(f"[{completed}/{total}] {case.task_id} × {experiment.id}")

    try:
        report = await runner.evaluate_matrix(
            cases,
            experiments,
            repeat=args.repeat,
            on_progress=progress,
        )
    except Exception as exc:
        run_record.status = RunStatus.FAILED
        run_record.error = f"{type(exc).__name__}: {exc}"
        run_record.finished_at = time.time()
        store.save_run(run_record)
        raise
    run_record.report = report
    run_record.status = RunStatus.COMPLETED
    run_record.finished_at = time.time()
    run_record.llm_model = settings.llm.model
    run_record.progress.current_task_id = ""
    run_record.progress.current_experiment = ""
    store.save_run(run_record)
    target = save_report(report, args.output_dir or settings.evaluation.output_dir)
    print(render_markdown(report))
    print(f"Saved: {target}")


async def _generate(args: argparse.Namespace, store: EvaluationStore) -> None:
    settings = get_settings()
    document = store.get_document(args.doc_id)
    if document is None:
        raise ValueError(f"Document not found: {args.doc_id}")
    llm = None
    if settings.llm.api_key:
        from llm.client import LLMClient

        llm = LLMClient.from_settings(settings)
    if args.num_tasks is not None and not 1 <= args.num_tasks <= 20:
        raise ValueError("num-tasks must be between 1 and 20")
    try:
        evalset = await EvalSetGenerator(settings, llm).generate(
            document,
            name=args.name,
            target_goal=args.goal,
            num_tasks=args.num_tasks,
        )
        store.save_evalset(evalset)
        print(json.dumps(evalset.model_dump(mode="json"), ensure_ascii=False, indent=2))
    finally:
        if llm is not None:
            await llm.aclose()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.command == "run":
        asyncio.run(_run(args))
        return

    store = EvaluationStore(settings.evaluation.output_dir)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "evaluation.server:create_app",
            factory=True,
            host=args.host,
            port=args.port or settings.evaluation.port,
        )
    elif args.command == "upload":
        path = Path(args.path)
        document = ingest_document(
            path.name,
            path.read_text(encoding="utf-8"),
            max_chars=settings.evaluation.max_document_chars,
        )
        store.save_document(document)
        print(json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "generate":
        asyncio.run(_generate(args, store))
    elif args.command == "report":
        run = store.get_run(args.run_id)
        if run is None or run.report is None:
            raise SystemExit(f"Report not found for run: {args.run_id}")
        print(render_markdown(run.report))
    elif args.command == "analyze":
        runs = [store.get_run(run_id) for run_id in args.run_ids]
        missing = [run_id for run_id, run in zip(args.run_ids, runs) if run is None]
        if missing:
            raise SystemExit(f"Run(s) not found: {', '.join(missing)}")
        analysis = analyze_runs([run for run in runs if run is not None])
        store.save_analysis(analysis)
        print(json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        if args.kind == "documents":
            items = store.list_documents()
        elif args.kind == "evalsets":
            items = store.list_evalsets()
        elif args.kind == "runs":
            items = store.list_runs()
        elif args.kind == "analyses":
            items = store.list_analyses()
        else:
            items = load_cases()
        print(json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False, indent=2))
