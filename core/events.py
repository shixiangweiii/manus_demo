"""Structured event transport shared by CLI, tracing, WebUI, and evaluation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeEvent:
    name: str
    run_id: str = ""
    task_id: str = ""
    engine: str = ""
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
        self._pending: set[asyncio.Task] = set()

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
    ) -> None:
        self._context = {
            "run_id": run_id,
            "task_id": task_id,
            "engine": engine,
        }

    def emit(self, name: str, payload: Any = None, **identity: str) -> RuntimeEvent:
        event = self._make_event(name, payload, identity)
        for handler in tuple(self._handlers):
            try:
                result = handler(self._copy_event(event))
                if inspect.isawaitable(result):
                    self._schedule_async_handler(result, name)
            except Exception:
                logger.debug("Runtime event subscriber failed: %s", name, exc_info=True)
        return event

    async def emit_async(
        self,
        name: str,
        payload: Any = None,
        **identity: str,
    ) -> RuntimeEvent:
        """Publish one event and await every asynchronous subscriber in order."""
        event = self._make_event(name, payload, identity)
        for handler in tuple(self._handlers):
            try:
                result = handler(self._copy_event(event))
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Runtime event subscriber failed: %s", name, exc_info=True)
        return event

    def _make_event(
        self,
        name: str,
        payload: Any,
        identity: dict[str, str],
    ) -> RuntimeEvent:
        values = dict(self._context)
        values.update({key: value for key, value in identity.items() if value})
        return RuntimeEvent(
            name=name,
            run_id=values.get("run_id", ""),
            task_id=values.get("task_id", ""),
            engine=values.get("engine", ""),
            payload=self._copy_payload({} if payload is None else payload),
        )

    @classmethod
    def _copy_payload(cls, value: Any) -> Any:
        """Copy mutable containers while preserving opaque runtime objects."""
        if isinstance(value, dict):
            return {key: cls._copy_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._copy_payload(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._copy_payload(item) for item in value)
        if isinstance(value, set):
            return {cls._copy_payload(item) for item in value}
        return value

    @classmethod
    def _copy_event(cls, event: RuntimeEvent) -> RuntimeEvent:
        return replace(event, payload=cls._copy_payload(event.payload))

    @staticmethod
    async def _await_handler(result: Awaitable[None]) -> None:
        await result

    def _schedule_async_handler(self, result: Awaitable[None], event_name: str) -> None:
        """Run async subscribers both inside and outside an active event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            close = getattr(result, "close", None)
            if callable(close):
                close()
            logger.warning(
                "Async runtime event subscriber requires emit_async or an active loop: %s",
                event_name,
            )
            return

        task = loop.create_task(self._await_handler(result))
        self._pending.add(task)

        def report_failure(done: asyncio.Task) -> None:
            self._pending.discard(done)
            if done.cancelled():
                logger.debug("Async runtime event subscriber cancelled: %s", event_name)
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

    async def drain(self) -> None:
        """Wait until all fire-and-forget asynchronous subscribers settle."""
        while self._pending:
            pending = tuple(self._pending)
            await asyncio.gather(*pending, return_exceptions=True)

    def legacy_callback(self, name: str, payload: Any) -> None:
        """Adapter for retained components that still emit ``(name, payload)``."""
        self.emit(name, payload)
