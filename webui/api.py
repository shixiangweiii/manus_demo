"""
REST router for the WebUI (prefix /api/webui).
WebUI 的 REST 路由（前缀 /api/webui）。

Thin adapter layer: no business logic here — delegates to
config_schema / SessionManager / TaskStateStore.
薄适配层：不含业务逻辑，仅转发给 config_schema / SessionManager /
TaskStateStore。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from webui import config_schema

router = APIRouter(prefix="/api/webui")


# ---------------------------------------------------------------------
# 配置 / config
# ---------------------------------------------------------------------

@router.get("/config/schema")
async def config_schema_endpoint() -> dict:
    """分组配置 schema（敏感项只带 configured 布尔）。"""
    return config_schema.get_schema()


@router.get("/config/values")
async def config_values_endpoint() -> dict:
    """当前生效配置值（不含敏感项）。"""
    return config_schema.get_values()


# ---------------------------------------------------------------------
# 会话 / session（Phase 3 接入 SessionManager）
# ---------------------------------------------------------------------

def _session_manager(request: Request) -> Any:
    return getattr(request.app.state, "session_manager", None)


@router.post("/session")
async def create_session(request: Request) -> JSONResponse:
    """校验 overrides → 关旧会话（恢复配置）→ 建新会话。运行中 409。"""
    mgr = _session_manager(request)
    if mgr is None:
        return JSONResponse({"error": "not_ready", "message": "会话管理器未初始化"}, status_code=503)
    if mgr.is_running:
        return JSONResponse({"error": "run_in_progress", "message": "任务运行中，无法变更会话"}, status_code=409)

    body = await request.json()
    overrides = body.get("overrides") or {}
    try:
        coerced = config_schema.validate(overrides)
    except config_schema.ConfigValidationError as exc:
        return JSONResponse({"error": "invalid_config", "errors": exc.errors}, status_code=422)

    session = await mgr.create_session(coerced)
    return JSONResponse({"session": session.describe()})


@router.get("/session")
async def get_session(request: Request) -> JSONResponse:
    """当前会话信息（无会话返回 session=null）。"""
    mgr = _session_manager(request)
    if mgr is None or mgr.session is None:
        return JSONResponse({"session": None})
    return JSONResponse({"session": mgr.session.describe()})


@router.delete("/session")
async def delete_session(request: Request) -> JSONResponse:
    """关闭会话并恢复配置。运行中 409。"""
    mgr = _session_manager(request)
    if mgr is None or mgr.session is None:
        return JSONResponse({"session": None})
    if mgr.is_running:
        return JSONResponse({"error": "run_in_progress", "message": "任务运行中，无法关闭会话"}, status_code=409)
    await mgr.close_session()
    return JSONResponse({"session": None})


# ---------------------------------------------------------------------
# checkpoint 任务 / checkpointed tasks
# ---------------------------------------------------------------------

@router.get("/checkpoints")
async def list_checkpoints() -> dict:
    """列出可恢复的 checkpoint 任务（新→旧）。"""
    from checkpoint.store import TaskStateStore

    summaries = TaskStateStore().list_tasks()
    return {
        "tasks": [
            {
                "task_id": s.task_id,
                "task": s.task,
                "complexity": s.complexity,
                "state": s.state.value if hasattr(s.state, "value") else str(s.state),
                "updated_at": s.updated_at,
            }
            for s in summaries
        ]
    }


# ---------------------------------------------------------------------
# 状态 / status
# ---------------------------------------------------------------------

@router.get("/status")
async def status(request: Request) -> dict:
    """轻量轮询目标：是否在跑、当前 run/session。"""
    mgr = _session_manager(request)
    if mgr is None:
        return {"running": False, "run_id": None, "session_id": None}
    return {
        "running": mgr.is_running,
        "run_id": mgr.current_run_id,
        "session_id": mgr.session.session_id if mgr.session else None,
    }
