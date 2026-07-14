"""
FastAPI server for the evaluation platform.
评测平台的 FastAPI 服务。

Thin adapter over store/generator/executor/analyzer:
  POST /api/evalplatform/documents      上传文档（JSON body，前端 FileReader 读文本）
  POST /api/evalplatform/evalsets       从文档生成评测集（后台任务）
  POST /api/evalplatform/runs           执行评测集（后台任务，运行间串行）
  POST /api/evalplatform/analyses       聚合分析（可选 LLM 洞察）
  GET  …（列表 / 详情 / markdown 报告）

Runs are serialized with an asyncio.Lock because EvaluationRunner flips
module-level config flags per task — concurrent runs would race on them.
运行任务间用 asyncio.Lock 串行：EvaluationRunner 会按任务翻转模块级 config
开关，并发运行会互相踩踏。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

import config
from evalplatform.analyzer import ReportAnalyzer
from evalplatform.document import DocumentIngestError, ingest_document
from evalplatform.executor import EvalSetExecutor, clamp_repeat, parse_modes
from evalplatform.generator import EvalSetGenerator
from evalplatform.models import (
    EvalRunRecord,
    EvalSetStatus,
    GeneratedEvalSet,
    RunStatus,
)
from evalplatform.reporting import build_report
from evalplatform.store import EvalPlatformStore

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"

# fire-and-forget 后台任务强引用（项目约定：模块级集合 + discard 回调）
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _err(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse({"error": error, "message": message}, status_code=status_code)


# ======================================================================
# Summaries for the overview endpoint / 概览端点的轻量摘要
# ======================================================================

def _doc_summary(d) -> dict[str, Any]:
    return {
        "doc_id": d.doc_id, "filename": d.filename, "title": d.title,
        "char_count": d.char_count, "created_at": d.created_at,
    }


def _evalset_summary(e: GeneratedEvalSet) -> dict[str, Any]:
    return {
        "evalset_id": e.evalset_id, "name": e.name, "doc_id": e.doc_id,
        "doc_filename": e.doc_filename, "status": e.status.value,
        "generator": e.generator, "generation_model": e.generation_model,
        "generation_error": e.generation_error, "task_count": len(e.tasks),
        "target_goal": e.target_goal, "created_at": e.created_at,
    }


def _run_summary(r: EvalRunRecord) -> dict[str, Any]:
    success_rates = [
        float(m.get("task_success_rate") or 0.0)
        for m in (r.metrics_by_mode or {}).values()
    ]
    return {
        "run_id": r.run_id, "evalset_id": r.evalset_id, "evalset_name": r.evalset_name,
        "status": r.status.value, "modes": r.modes, "repeat": r.repeat,
        "error": r.error, "progress": r.progress.model_dump(),
        "success_rate": (sum(success_rates) / len(success_rates)) if success_rates else None,
        "started_at": r.started_at, "finished_at": r.finished_at,
        "created_at": r.created_at,
    }


def _analysis_summary(a) -> dict[str, Any]:
    return {
        "analysis_id": a.analysis_id, "run_ids": a.run_ids,
        "created_at": a.created_at,
        "suggestion_count": len(a.suggestions),
        "top_suggestion": a.suggestions[0].title if a.suggestions else "",
        "has_llm_insight": bool(a.llm_insight),
    }


# ======================================================================
# Background jobs / 后台任务
# ======================================================================

async def _generate_job(
    store: EvalPlatformStore,
    generator: EvalSetGenerator,
    doc,
    evalset: GeneratedEvalSet,
    target_goal: str,
    num_tasks: int,
    use_llm: bool,
) -> None:
    try:
        await generator.generate(
            doc, target_goal=target_goal, num_tasks=num_tasks,
            use_llm=use_llm, evalset=evalset, name=evalset.name,
        )
    except Exception as exc:
        logger.error("[EvalPlatform] generation job failed: %s", exc, exc_info=True)
        evalset.status = EvalSetStatus.FAILED
        evalset.generation_error = str(exc)[:500]
    store.save_evalset(evalset)


async def _run_job(
    store: EvalPlatformStore,
    run_lock: asyncio.Lock,
    evalset: GeneratedEvalSet,
    run: EvalRunRecord,
) -> None:
    async with run_lock:
        try:
            executor = EvalSetExecutor()
            await executor.execute(evalset, run, on_progress=store.save_run)
        except Exception as exc:
            logger.error("[EvalPlatform] run job failed: %s", exc, exc_info=True)
            run.status = RunStatus.FAILED
            run.error = str(exc)[:500]
        store.save_run(run)
        try:
            report = build_report(run, evalset)
            store.save_report(report)
        except Exception as exc:
            logger.error("[EvalPlatform] report build failed: %s", exc, exc_info=True)


# ======================================================================
# App factory / 应用工厂
# ======================================================================

def create_app(store: EvalPlatformStore | None = None) -> FastAPI:
    """Build the evaluation-platform FastAPI app. 构建评测平台应用。"""
    app = FastAPI(
        title="Manus Demo Evaluation Platform",
        description="文档驱动的评测平台：上传文档 → 生成评测集 → 执行 → 报告 → 聚合分析",
        version="1.0",
    )
    app.state.store = store or EvalPlatformStore()
    app.state.run_lock = asyncio.Lock()

    def _store(request: Request) -> EvalPlatformStore:
        return request.app.state.store

    # ------------------------------------------------------------------
    # Overview / 概览
    # ------------------------------------------------------------------

    @app.get("/api/evalplatform/overview")
    async def overview(request: Request) -> dict:
        s = _store(request)
        return {
            "documents": [_doc_summary(d) for d in s.list_documents()],
            "evalsets": [_evalset_summary(e) for e in s.list_evalsets()],
            "runs": [_run_summary(r) for r in s.list_runs()],
            "analyses": [_analysis_summary(a) for a in s.list_analyses()],
        }

    # ------------------------------------------------------------------
    # Documents / 文档
    # ------------------------------------------------------------------

    @app.post("/api/evalplatform/documents")
    async def upload_document(request: Request) -> JSONResponse:
        body = await request.json()
        filename = str(body.get("filename") or "").strip()
        content = body.get("content")
        if not filename or not isinstance(content, str):
            return _err(422, "invalid_request", "需要 filename 和文本 content")
        try:
            doc = ingest_document(filename, content, _store(request), title=str(body.get("title") or ""))
        except DocumentIngestError as exc:
            return _err(422, "ingest_failed", str(exc))
        return JSONResponse({"document": _doc_summary(doc)})

    @app.get("/api/evalplatform/documents/{doc_id}")
    async def get_document(doc_id: str, request: Request) -> JSONResponse:
        doc = _store(request).get_document(doc_id)
        if doc is None:
            return _err(404, "not_found", f"文档不存在：{doc_id}")
        return JSONResponse({"document": doc.model_dump()})

    # ------------------------------------------------------------------
    # Eval sets / 评测集
    # ------------------------------------------------------------------

    @app.post("/api/evalplatform/evalsets")
    async def create_evalset(request: Request) -> JSONResponse:
        s = _store(request)
        body = await request.json()
        doc = s.get_document(str(body.get("doc_id") or ""))
        if doc is None:
            return _err(404, "not_found", "文档不存在，请先上传")

        num_tasks = int(body.get("num_tasks") or config.EVAL_PLATFORM_DEFAULT_NUM_TASKS)
        num_tasks = max(1, min(20, num_tasks))
        use_llm = bool(body.get("use_llm", True))
        target_goal = str(body.get("target_goal") or "")

        evalset = GeneratedEvalSet(
            name=str(body.get("name") or "") or f"{doc.title or doc.filename} 评测集",
            doc_id=doc.doc_id,
            doc_filename=doc.filename,
            target_goal=target_goal,
            requested_num_tasks=num_tasks,
        )
        s.save_evalset(evalset)
        _spawn(_generate_job(s, EvalSetGenerator(), doc, evalset, target_goal, num_tasks, use_llm))
        return JSONResponse({"evalset": _evalset_summary(evalset)})

    @app.get("/api/evalplatform/evalsets/{evalset_id}")
    async def get_evalset(evalset_id: str, request: Request) -> JSONResponse:
        evalset = _store(request).get_evalset(evalset_id)
        if evalset is None:
            return _err(404, "not_found", f"评测集不存在：{evalset_id}")
        return JSONResponse({"evalset": evalset.model_dump(mode="json")})

    # ------------------------------------------------------------------
    # Runs / 运行
    # ------------------------------------------------------------------

    @app.post("/api/evalplatform/runs")
    async def create_run(request: Request) -> JSONResponse:
        s = _store(request)
        body = await request.json()
        evalset = s.get_evalset(str(body.get("evalset_id") or ""))
        if evalset is None:
            return _err(404, "not_found", "评测集不存在")
        if evalset.status != EvalSetStatus.READY:
            return _err(409, "not_ready", f"评测集状态为 {evalset.status.value}，无法运行")
        try:
            modes = [m.value for m in parse_modes(body.get("modes"))]
        except ValueError as exc:
            return _err(422, "invalid_modes", str(exc))

        run = EvalRunRecord(
            evalset_id=evalset.evalset_id,
            evalset_name=evalset.name,
            doc_id=evalset.doc_id,
            modes=modes,
            repeat=clamp_repeat(body.get("repeat") or 1),
        )
        s.save_run(run)
        _spawn(_run_job(s, request.app.state.run_lock, evalset, run))
        return JSONResponse({"run": _run_summary(run)})

    @app.get("/api/evalplatform/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> JSONResponse:
        run = _store(request).get_run(run_id)
        if run is None:
            return _err(404, "not_found", f"运行不存在：{run_id}")
        return JSONResponse({"run": run.model_dump(mode="json")})

    @app.get("/api/evalplatform/runs/{run_id}/report.md")
    async def get_run_report_md(run_id: str, request: Request):
        report = _store(request).get_report(run_id)
        if report is None:
            return _err(404, "not_found", "报告尚未生成（运行未完成？）")
        return PlainTextResponse(report.markdown, media_type="text/markdown; charset=utf-8")

    @app.get("/reports/{run_id}", response_class=HTMLResponse, include_in_schema=False)
    async def report_page(run_id: str, request: Request) -> HTMLResponse:
        report = _store(request).get_report(run_id)
        if report is None:
            return HTMLResponse("<h3>报告尚未生成</h3>", status_code=404)
        return HTMLResponse(_markdown_page(f"评测报告 {run_id}", report.markdown))

    # ------------------------------------------------------------------
    # Analyses / 聚合分析
    # ------------------------------------------------------------------

    @app.post("/api/evalplatform/analyses")
    async def create_analysis(request: Request) -> JSONResponse:
        s = _store(request)
        body = await request.json()
        run_ids = body.get("run_ids") or []
        if run_ids:
            runs = [s.get_run(str(rid)) for rid in run_ids]
            missing = [rid for rid, r in zip(run_ids, runs) if r is None]
            if missing:
                return _err(404, "not_found", f"运行不存在：{', '.join(missing)}")
            runs = [r for r in runs if r is not None]
            # 与默认分支同口径：未完成的运行指标为空/半成品，明确拒绝而非静默纳入
            # (review V5 fix) explicit run_ids must also be COMPLETED
            not_completed = [r.run_id for r in runs if r.status != RunStatus.COMPLETED]
            if not_completed:
                return _err(
                    422, "run_not_completed",
                    f"运行尚未完成，无法纳入分析：{', '.join(not_completed)}",
                )
        else:
            runs = [r for r in s.list_runs() if r.status == RunStatus.COMPLETED]
        if not runs:
            return _err(422, "no_runs", "没有可分析的已完成运行")

        analyzer = ReportAnalyzer()
        analysis = await analyzer.analyze(runs, use_llm=bool(body.get("use_llm", False)))
        s.save_analysis(analysis)
        return JSONResponse({"analysis": _analysis_summary(analysis)})

    @app.get("/api/evalplatform/analyses/{analysis_id}")
    async def get_analysis(analysis_id: str, request: Request) -> JSONResponse:
        analysis = _store(request).get_analysis(analysis_id)
        if analysis is None:
            return _err(404, "not_found", f"分析不存在：{analysis_id}")
        return JSONResponse({"analysis": analysis.model_dump(mode="json")})

    @app.get("/analyses/{analysis_id}", response_class=HTMLResponse, include_in_schema=False)
    async def analysis_page(analysis_id: str, request: Request) -> HTMLResponse:
        analysis = _store(request).get_analysis(analysis_id)
        if analysis is None:
            return HTMLResponse("<h3>分析不存在</h3>", status_code=404)
        return HTMLResponse(_markdown_page(f"聚合分析 {analysis_id}", analysis.markdown))

    # ------------------------------------------------------------------
    # Frontend / 前端
    # ------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    return app


def _markdown_page(title: str, markdown: str) -> str:
    """Minimal markdown viewer page (rendered client-side is overkill —
    show as preformatted text with monospace styling).
    极简 markdown 查看页（等宽预格式化展示）。"""
    import html
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ background:#0f1419; color:#d8dee9; font-family:ui-monospace,Menlo,monospace;
       max-width:1000px; margin:24px auto; padding:0 16px; }}
pre {{ white-space:pre-wrap; word-break:break-word; line-height:1.55; font-size:13px; }}
a {{ color:#7aa2f7; }}
</style></head>
<body><a href="/">← 返回平台</a><pre>{html.escape(markdown)}</pre></body></html>"""
