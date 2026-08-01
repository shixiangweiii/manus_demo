"""
FastAPI app factory for the WebUI.
WebUI 的 FastAPI 应用工厂。

Composition (thin adapter layer only / 仅做薄编排):
- static frontend (no-build ES modules)  静态前端（无构建 ES Modules）
- tracing viewer handlers re-registered at their ORIGINAL absolute paths
  (templates hardcode /traces links, so mounting under a prefix would break
  them; re-registration keeps `python -m tracing` standalone untouched)
  tracing viewer 的 handler 按原绝对路径复注册（模板内硬编码 /traces
  链接，挂 prefix 会失效；复注册不影响独立的 `python -m tracing`）
- EventBridge + ConnectionManager + SessionManager wired on app.state,
  drain task managed by lifespan
  EventBridge/连接管理/会话管理挂 app.state，drain 任务由 lifespan 管理
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    """Build the WebUI FastAPI app. 构建 WebUI FastAPI 应用。"""
    from webui.events import EventBridge
    from webui.session import SessionManager
    from webui.ws import ConnectionManager

    event_bridge = EventBridge()
    connection_manager = ConnectionManager()
    session_manager = SessionManager(event_bridge)
    event_bridge.set_broadcast(connection_manager.broadcast)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        event_bridge.start()
        try:
            yield
        finally:
            try:
                await session_manager.shutdown()
            finally:
                try:
                    await event_bridge.stop()
                finally:
                    from tracing import shutdown_tracing

                    shutdown_tracing()

    app = FastAPI(
        title="Manus Demo WebUI",
        description="Local debugging web UI / 本地调试 Web 界面",
        version="local",
        lifespan=lifespan,
    )
    app.state.event_bridge = event_bridge
    app.state.connection_manager = connection_manager
    app.state.session_manager = session_manager

    # --- Trace viewer reuse / 复用 tracing viewer -------------------------
    # Plain module-level async functions (tracing/server.py) — safe to
    # re-register. The tracing app's "/" redirect is deliberately NOT
    # imported: webui owns "/".
    # tracing/server.py 的 handler 是普通模块级函数，可直接复注册；
    # tracing 应用的 "/" 重定向刻意不注册：webui 自己占用 "/"。
    from tracing import server as trace_server

    app.get("/traces", response_class=HTMLResponse)(trace_server.trace_list_page)
    app.get("/traces/{file_id}", response_class=HTMLResponse)(trace_server.trace_detail_page)
    app.get("/api/traces")(trace_server.api_trace_list)
    app.get("/api/traces/{file_id}")(trace_server.api_trace_detail)

    # --- REST + WS 路由 / REST + WS routers --------------------------------
    from webui.api import router as api_router
    from webui.ws import router as ws_router

    app.include_router(api_router)
    app.include_router(ws_router)

    # --- Static frontend / 静态前端 ---------------------------------------
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    return app
