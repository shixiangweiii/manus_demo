"""
EventBridge: sync on_event sink → asyncio.Queue → drain task → WS broadcast.
EventBridge：同步 on_event 汇入 → asyncio.Queue → drain 任务 → WS 广播。

Zero FastAPI imports. AgentRuntime runs on the same uvicorn event loop and
publishes synchronously, so queue.put_nowait needs no thread handoff.
零 FastAPI 依赖。AgentRuntime 与 uvicorn 位于同一事件循环并同步发布事件，
因此 queue.put_nowait 无需线程切换。

Safety contract: on_event never raises back into AgentRuntime.
安全契约：on_event 绝不向 AgentRuntime 抛回异常。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable

from core.events import RuntimeEvent

from webui.serializer import serialize_event

logger = logging.getLogger(__name__)

BUFFER_MAX = 2000  # 重放环形缓冲上限 / replay ring-buffer cap


class EventBridge:
    def __init__(self, buffer_max: int = BUFFER_MAX) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._buffer: deque[dict[str, Any]] = deque(maxlen=buffer_max)
        self._seq = 0
        self._drain_task: asyncio.Task | None = None
        # 注入点（由 app 装配）/ injection points (wired by the app)
        self._broadcast: Callable[[dict], Any] | None = None       # async fn
        self._run_id_provider: Callable[[], str | None] = lambda: None
        self._prompt_hook: Callable[[str, asyncio.Future, str], None] | None = None
        self._event_observer: Callable[[str], None] | None = None  # e.g. trace_id 捕获

    # -- 装配 / wiring ---------------------------------------------------

    def set_broadcast(self, broadcast: Callable[[dict], Any]) -> None:
        self._broadcast = broadcast

    def set_run_id_provider(self, provider: Callable[[], str | None]) -> None:
        self._run_id_provider = provider

    def set_prompt_hook(self, hook: Callable[[str, asyncio.Future, str], None]) -> None:
        self._prompt_hook = hook

    def set_event_observer(self, observer: Callable[[str], None]) -> None:
        self._event_observer = observer

    # -- 属性 / properties -----------------------------------------------

    @property
    def current_seq(self) -> int:
        return self._seq

    # -- 同步事件入口（由 AgentRuntime 发布）-----------------------------
    # -- sync event sink (published by AgentRuntime) ----------------------

    def on_event(self, event: str, data: Any) -> None:
        self.on_runtime_event(RuntimeEvent(name=event, payload=data))

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        try:
            event_name = event.name
            data = event.payload
            payload: Any
            truncated = False
            if event_name == "ask_user_prompt" and isinstance(data, dict):
                # 序列化之前注册 Future（序列化会剥离它）
                # register the Future BEFORE serialization (which strips it)
                if self._prompt_hook is not None and "response_future" in data:
                    self._prompt_hook(
                        str(data.get("prompt_id", "")),
                        data["response_future"],
                        str(data.get("question", "")),
                    )
                payload = {
                    "question": str(data.get("question", "")),
                    "prompt_id": str(data.get("prompt_id", "")),
                    "timeout_seconds": int(data.get("timeout_seconds", 120)),
                }
            else:
                payload, truncated = serialize_event(event_name, data)

            self._enqueue({
                "type": "agent_event",
                "run_id": event.run_id or self._run_id_provider(),
                "task_id": event.task_id,
                "engine": event.engine,
                "executor": event.executor,
                "ts": event.timestamp,
                "event": event_name,
                "data": payload,
                "truncated": truncated,
            })

            if self._event_observer is not None:
                self._event_observer(event_name)
        except Exception:
            # 绝不抛回 AgentRuntime / never raise back into AgentRuntime
            logger.debug("EventBridge.on_event failed for %s", event.name, exc_info=True)

    # -- 系统消息（run_started/run_finished 等）---------------------------
    # -- system messages (run_started/run_finished etc.) -------------------

    def emit_system(self, message: dict[str, Any]) -> None:
        try:
            self._enqueue(message)
        except Exception:
            logger.debug("EventBridge.emit_system failed", exc_info=True)

    def _enqueue(self, message: dict[str, Any]) -> None:
        self._seq += 1
        message["seq"] = self._seq
        self._buffer.append(message)
        self._queue.put_nowait(message)

    # -- 重放 / replay -----------------------------------------------------

    def replay(self, last_seq: int) -> list[dict[str, Any]]:
        return [m for m in self._buffer if m["seq"] > last_seq]

    # -- drain 生命周期 / drain lifecycle ----------------------------------

    def start(self) -> None:
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except (asyncio.CancelledError, Exception):
                pass
            self._drain_task = None

    async def _drain_loop(self) -> None:
        while True:
            message = await self._queue.get()
            if self._broadcast is None:
                continue
            try:
                await self._broadcast(message)
            except Exception:
                # 广播失败不影响后续消息 / broadcast failure never stalls the loop
                logger.debug("EventBridge broadcast failed", exc_info=True)
