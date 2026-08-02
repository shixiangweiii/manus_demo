"""FastAPI application for generation, execution, reporting, and analysis."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from core.settings import AppSettings, get_settings
from evaluation.analyzer import analyze_runs
from evaluation.document import DocumentIngestError, ingest_document
from evaluation.executor import EvalSetExecutor, validate_repeat
from evaluation.experiments import build_experiments
from evaluation.generator import EvalSetGenerator
from evaluation.models import EvalRunRecord, EvalSetStatus, RunStatus
from evaluation.reporter import render_markdown
from evaluation.runner import EvaluationRunner
from evaluation.store import EvaluationStore
from llm.client import LLMClient

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


def create_app(
    store: EvaluationStore | None = None,
    settings: AppSettings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    store = store or EvaluationStore(settings.evaluation.output_dir)
    background_tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        tasks = tuple(background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        from tracing import shutdown_tracing

        shutdown_tracing()

    app = FastAPI(
        title="Manus Demo Evaluation",
        version="local",
        lifespan=lifespan,
    )

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        background_tasks.add(task)

        def finish(done: asyncio.Task) -> None:
            background_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception as exc:
                logger.error(
                    "Evaluation background task failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(finish)

    @app.get("/api/evaluation/overview")
    async def overview() -> dict[str, Any]:
        return {
            "documents": [item.model_dump(mode="json") for item in store.list_documents()],
            "evalsets": [item.model_dump(mode="json") for item in store.list_evalsets()],
            "runs": [item.model_dump(mode="json") for item in store.list_runs()],
            "analyses": [item.model_dump(mode="json") for item in store.list_analyses()],
        }

    @app.post("/api/evaluation/documents")
    async def upload_document(body: dict[str, Any]) -> JSONResponse:
        try:
            document = ingest_document(
                str(body.get("filename", "document.md")),
                str(body.get("content", "")),
                max_chars=settings.evaluation.max_document_chars,
            )
        except DocumentIngestError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        store.save_document(document)
        return JSONResponse(document.model_dump(mode="json"))

    @app.post("/api/evaluation/evalsets")
    async def create_evalset(body: dict[str, Any]) -> JSONResponse:
        document = store.get_document(str(body.get("doc_id", "")))
        if document is None:
            return JSONResponse({"error": "document not found"}, status_code=404)
        from evaluation.models import GeneratedEvalSet

        try:
            requested_num_tasks = int(
                body.get("num_tasks") or settings.evaluation.default_num_tasks
            )
        except (TypeError, ValueError):
            return JSONResponse({"error": "num_tasks must be an integer"}, status_code=422)
        if not 1 <= requested_num_tasks <= 20:
            return JSONResponse({"error": "num_tasks must be between 1 and 20"}, status_code=422)

        pending = GeneratedEvalSet(
            name=str(body.get("name") or f"{document.title} evaluation"),
            doc_id=document.doc_id,
            doc_filename=document.filename,
            target_goal=str(body.get("target_goal", "")),
            requested_num_tasks=requested_num_tasks,
        )
        llm = LLMClient.from_settings(settings) if settings.llm.api_key else None

        async def generate_and_save() -> None:
            try:
                generated = await EvalSetGenerator(settings, llm).generate(
                    document,
                    name=pending.name,
                    target_goal=pending.target_goal,
                    num_tasks=pending.requested_num_tasks,
                )
                generated.evalset_id = pending.evalset_id
                generated.created_at = pending.created_at
                store.save_evalset(generated)
            except asyncio.CancelledError:
                pending.status = EvalSetStatus.FAILED
                pending.generation_error = "Evaluation generation cancelled"
                pending.updated_at = time.time()
                store.save_evalset(pending)
                raise
            except Exception as exc:
                pending.status = EvalSetStatus.FAILED
                pending.generation_error = f"{type(exc).__name__}: {exc}"
                pending.updated_at = time.time()
                store.save_evalset(pending)
                raise
            finally:
                if llm is not None:
                    await llm.aclose()

        store.save_evalset(pending)
        spawn(generate_and_save())
        return JSONResponse(pending.model_dump(mode="json"), status_code=202)

    @app.get("/api/evaluation/evalsets/{evalset_id}")
    async def get_evalset(evalset_id: str) -> JSONResponse:
        item = store.get_evalset(evalset_id)
        return JSONResponse(item.model_dump(mode="json") if item else {"error": "not found"}, status_code=200 if item else 404)

    @app.post("/api/evaluation/runs")
    async def create_run(body: dict[str, Any]) -> JSONResponse:
        evalset = store.get_evalset(str(body.get("evalset_id", "")))
        if evalset is None:
            return JSONResponse({"error": "evaluation set not found"}, status_code=404)
        if evalset.status != EvalSetStatus.READY or not evalset.tasks:
            return JSONResponse({"error": "evaluation set is not ready"}, status_code=409)
        try:
            experiments = build_experiments(
                engines=body.get("engines"),
                efforts=body.get("efforts"),
                capability_sets=body.get("capability_sets"),
            )
            EvaluationRunner(settings).validate_experiments(experiments)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        try:
            repeat = validate_repeat(body.get("repeat", 1))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        run = EvalRunRecord(
            evalset_id=evalset.evalset_id,
            evalset_name=evalset.name,
            doc_id=evalset.doc_id,
            experiments=experiments,
            repeat=repeat,
        )
        store.save_run(run)

        async def execute_and_save() -> None:
            try:
                await EvalSetExecutor(settings).execute(evalset, run, store.save_run)
            except asyncio.CancelledError:
                run.status = RunStatus.FAILED
                run.error = "Evaluation run cancelled during server shutdown"
                run.finished_at = time.time()
                store.save_run(run)
                raise
            except Exception as exc:
                run.status = RunStatus.FAILED
                run.error = f"{type(exc).__name__}: {exc}"
                run.finished_at = time.time()
                store.save_run(run)
                raise

        spawn(execute_and_save())
        return JSONResponse(run.model_dump(mode="json"), status_code=202)

    @app.get("/api/evaluation/runs/{run_id}")
    async def get_run(run_id: str) -> JSONResponse:
        item = store.get_run(run_id)
        return JSONResponse(item.model_dump(mode="json") if item else {"error": "not found"}, status_code=200 if item else 404)

    @app.get("/api/evaluation/runs/{run_id}/report.md", response_class=PlainTextResponse)
    async def run_report(run_id: str) -> PlainTextResponse:
        item = store.get_run(run_id)
        if item is None or item.report is None:
            return PlainTextResponse("report not found", status_code=404)
        return PlainTextResponse(render_markdown(item.report), media_type="text/markdown")

    @app.post("/api/evaluation/analyses")
    async def create_analysis(body: dict[str, Any]) -> JSONResponse:
        run_ids = [str(run_id) for run_id in body.get("run_ids", [])]
        runs = [store.get_run(run_id) for run_id in run_ids]
        missing = [run_id for run_id, run in zip(run_ids, runs) if run is None]
        if missing:
            return JSONResponse(
                {"error": f"run(s) not found: {', '.join(missing)}"},
                status_code=404,
            )
        if not runs:
            return JSONResponse({"error": "run_ids must not be empty"}, status_code=422)
        analysis = analyze_runs([run for run in runs if run is not None])
        store.save_analysis(analysis)
        return JSONResponse(analysis.model_dump(mode="json"))

    @app.get("/api/evaluation/analyses/{analysis_id}")
    async def get_analysis(analysis_id: str) -> JSONResponse:
        item = store.get_analysis(analysis_id)
        return JSONResponse(item.model_dump(mode="json") if item else {"error": "not found"}, status_code=200 if item else 404)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="evaluation-static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    return app
