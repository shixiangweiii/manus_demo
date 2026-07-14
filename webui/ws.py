"""
WebSocket endpoint /ws + ConnectionManager.
WebSocket 端点 /ws 与连接管理器。

Protocol (all JSON, discriminated by `type`) / 协议（JSON，按 type 区分）:
  client → server: hello{last_seq} / user_message{session_id,text} /
                   resume_task{session_id,task_id} /
                   hitl_response{prompt_id,text} / hitl_cancel{prompt_id} / ping
  server → client: state / run_started / agent_event / run_finished /
                   error{code,message} / pong

All connected clients share one broadcast (single-user tool; multiple
tabs mirror each other).
所有连接共享同一广播（单用户工具；多标签页互为镜像）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from webui.session import BusyError, NoSessionError, SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._sockets.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._sockets.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """逐 socket 发送；死连接直接丢弃，绝不抛给调用方。
        Per-socket send; drop dead sockets, never raise to the caller."""
        for websocket in list(self._sockets):
            try:
                await websocket.send_json(message)
            except Exception:
                self._sockets.discard(websocket)


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    try:
        await websocket.send_json({"type": "error", "code": code, "message": message})
    except Exception:
        pass


async def _handle_message(websocket: WebSocket, msg: dict[str, Any]) -> None:
    mgr: SessionManager = websocket.app.state.session_manager
    bridge = websocket.app.state.event_bridge
    msg_type = msg.get("type")

    if msg_type == "hello":
        # 重放增量 → state 快照（顺序保证客户端先补齐历史再对齐状态）
        # replay missed messages, THEN the state snapshot
        last_seq = int(msg.get("last_seq") or 0)
        for buffered in bridge.replay(last_seq):
            await websocket.send_json(buffered)
        await websocket.send_json(mgr.state_snapshot())

    elif msg_type == "user_message":
        text = str(msg.get("text") or "").strip()
        if not text:
            await _send_error(websocket, "bad_message", "消息内容为空")
            return
        try:
            mgr.start_run(text)
        except BusyError as exc:
            await _send_error(websocket, "busy", str(exc))
        except NoSessionError as exc:
            await _send_error(websocket, "no_session", str(exc))

    elif msg_type == "resume_task":
        task_id = str(msg.get("task_id") or "").strip()
        if not task_id:
            await _send_error(websocket, "bad_message", "task_id 为空")
            return
        try:
            mgr.start_resume(task_id)
        except BusyError as exc:
            await _send_error(websocket, "busy", str(exc))
        except NoSessionError as exc:
            await _send_error(websocket, "no_session", str(exc))

    elif msg_type == "hitl_response":
        prompt_id = str(msg.get("prompt_id") or "")
        if not mgr.resolve_prompt(prompt_id, str(msg.get("text") or "")):
            await _send_error(websocket, "bad_message", f"提问 {prompt_id} 不存在或已回答")

    elif msg_type == "hitl_cancel":
        prompt_id = str(msg.get("prompt_id") or "")
        if not mgr.cancel_prompt(prompt_id):
            await _send_error(websocket, "bad_message", f"提问 {prompt_id} 不存在或已回答")

    elif msg_type == "ping":
        await websocket.send_json({"type": "pong"})

    else:
        await _send_error(websocket, "bad_message", f"未知消息类型: {msg_type!r}")


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    conn: ConnectionManager = websocket.app.state.connection_manager
    await conn.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError("message must be a JSON object")
            except (json.JSONDecodeError, ValueError):
                await _send_error(websocket, "bad_message", "无法解析的 JSON 消息")
                continue
            try:
                await _handle_message(websocket, msg)
            except Exception as exc:
                logger.exception("[webui] WS message handling failed")
                await _send_error(websocket, "internal", f"{type(exc).__name__}: {exc}")
    except WebSocketDisconnect:
        pass
    finally:
        conn.disconnect(websocket)
