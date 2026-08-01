"""Structured event transport shared by CLI, tracing, WebUI, and evaluation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeEvent:
    name: str
    run_id: str = ""
    task_id: str = ""
    engine: str = ""
    executor: str = ""
    payload: Any = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


EventHandler = Callable[[RuntimeEvent], None | Awaitable[None]]
SyncEventHandler = Callable[[RuntimeEvent], None]
AsyncEventHandler = Callable[[RuntimeEvent], Awaitable[None]]


class EventBus:
    """In-process fan-out with failure isolation between subscribers."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._context: dict[str, str] = {}

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def subscribe_sync(self, handler: SyncEventHandler) -> Callable[[], None]:
        return self.subscribe(handler)

    def subscribe_async(self, handler: AsyncEventHandler) -> Callable[[], None]:
        return self.subscribe(handler)

    def set_context(
        self,
        *,
        run_id: str = "",
        task_id: str = "",
        engine: str = "",
        executor: str = "",
    ) -> None:
        self._context = {
            "run_id": run_id,
            "task_id": task_id,
            "engine": engine,
            "executor": executor,
        }

    def emit(self, name: str, payload: Any = None, **identity: str) -> RuntimeEvent:
        values = dict(self._context)
        values.update({key: value for key, value in identity.items() if value})
        event = RuntimeEvent(
            name=name,
            run_id=values.get("run_id", ""),
            task_id=values.get("task_id", ""),
            engine=values.get("engine", ""),
            executor=values.get("executor", ""),
            payload={} if payload is None else payload,
        )
        for handler in tuple(self._handlers):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    self._schedule_async_handler(result, name)
            except Exception:
                logger.debug("Runtime event subscriber failed: %s", name, exc_info=True)
        return event

    @staticmethod
    async def _await_handler(result: Awaitable[None]) -> None:
        await result

    @classmethod
    def _schedule_async_handler(cls, result: Awaitable[None], event_name: str) -> None:
        """Run async subscribers both inside and outside an active event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(cls._await_handler(result))
            except Exception:
                logger.debug(
                    "Async runtime event subscriber failed: %s",
                    event_name,
                    exc_info=True,
                )
            return

        task = loop.create_task(cls._await_handler(result))

        def report_failure(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:
                logger.debug(
                    "Async runtime event subscriber failed: %s",
                    event_name,
                    exc_info=True,
                )

        task.add_done_callback(report_failure)

    def legacy_callback(self, name: str, payload: Any) -> None:
        """Adapter for retained components that still emit ``(name, payload)``."""
        self.emit(name, payload)
