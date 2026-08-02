"""Web session lifecycle over the unified runtime."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.events import EventBus
from core.models import TaskRequest
from runtime.factory import build_runtime
from webui import config_schema
from webui.events import EventBridge
from webui.serializer import truncate_str

logger = logging.getLogger(__name__)
ANSWER_MAX = 20000
CANCEL_SENTINEL = "(user cancelled)"


class BusyError(RuntimeError):
    pass


class NoSessionError(RuntimeError):
    pass


@dataclass
class RunContext:
    run_id: str
    kind: str
    task_text: str
    task: asyncio.Task | None = None
    trace_id: str | None = None


@dataclass
class WebSession:
    session_id: str
    overrides: dict[str, Any]
    run_overrides: dict[str, Any]
    runtime: Any
    created_at: float = field(default_factory=time.time)
    turn_count: int = 0
    pending_prompts: dict[str, tuple[asyncio.Future, str]] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "overrides": dict(self.overrides),
            "turn_count": self.turn_count,
            "created_at": self.created_at,
        }


async def _default_runtime_factory(event_bridge: EventBridge, overrides: dict[str, Any]):
    settings, run_overrides = config_schema.settings_for_session(overrides)
    events = EventBus()
    events.subscribe(event_bridge.on_runtime_event)
    runtime = await build_runtime(settings, events, interactive=True)
    return runtime, run_overrides


class SessionManager:
    def __init__(
        self,
        event_bridge: EventBridge,
        runtime_factory: Callable[[EventBridge, dict[str, Any]], Awaitable[tuple[Any, dict[str, Any]]]] | None = None,
    ) -> None:
        self.session: WebSession | None = None
        self._event_bridge = event_bridge
        self._runtime_factory = runtime_factory or _default_runtime_factory
        self._run_lock = asyncio.Lock()
        self._current_run: RunContext | None = None
        self._run_counter = 0
        event_bridge.set_run_id_provider(lambda: self.current_run_id)
        event_bridge.set_prompt_hook(self._register_prompt)
        event_bridge.set_event_observer(self._maybe_capture_trace_id)

    @property
    def is_running(self) -> bool:
        return self._current_run is not None

    @property
    def current_run_id(self) -> str | None:
        return self._current_run.run_id if self._current_run else None

    def pending_prompt(self) -> dict[str, Any] | None:
        if self.session is None:
            return None
        for prompt_id, (future, question) in self.session.pending_prompts.items():
            if not future.done():
                return {"prompt_id": prompt_id, "question": question}
        return None

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "type": "state",
            "session": self.session.describe() if self.session else None,
            "running": self.is_running,
            "run_id": self.current_run_id,
            "pending_prompt": self.pending_prompt(),
            "seq": self._event_bridge.current_seq,
        }

    async def create_session(self, overrides: dict[str, Any]) -> WebSession:
        if self.is_running:
            raise BusyError("任务运行中，无法变更会话")
        await self.close_session()
        runtime, run_overrides = await self._runtime_factory(self._event_bridge, overrides)
        self.session = WebSession(
            session_id=f"s-{uuid.uuid4().hex[:8]}",
            overrides=dict(overrides),
            run_overrides=run_overrides,
            runtime=runtime,
        )
        return self.session

    async def close_session(self) -> None:
        if self.is_running:
            raise BusyError("任务运行中，无法关闭会话")
        session = self.session
        self.session = None
        if session is not None:
            await session.runtime.aclose()

    async def shutdown(self) -> None:
        session = self.session
        if session is not None:
            for future, _question in session.pending_prompts.values():
                if not future.done():
                    future.set_result(CANCEL_SENTINEL)
        run = self._current_run
        if run is not None and run.task is not None and not run.task.done():
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
        self._current_run = None
        if session is not None:
            await session.runtime.aclose()
        self.session = None

    def start_run(self, text: str) -> str:
        return self._start("run", text)

    def start_resume(self, task_id: str) -> str:
        return self._start("resume", task_id)

    def _start(self, kind: str, text: str) -> str:
        if self.session is None:
            raise NoSessionError("请先创建会话")
        if self.is_running:
            raise BusyError("已有任务在运行")
        self._run_counter += 1
        run = RunContext(run_id=f"r-{self._run_counter}", kind=kind, task_text=text)
        self._current_run = run
        run.task = asyncio.create_task(self._execute(run))
        return run.run_id

    async def _execute(self, run: RunContext) -> None:
        session = self.session
        assert session is not None
        async with self._run_lock:
            self._event_bridge.emit_system({
                "type": "run_started",
                "run_id": run.run_id,
                "session_id": session.session_id,
                "kind": run.kind,
                "task": run.task_text,
                "overrides": dict(session.overrides),
                "ts": time.time(),
            })
            status, answer, error, stop_reason = "completed", None, None, None
            try:
                if run.kind == "run":
                    result = await session.runtime.run(
                        TaskRequest(task=run.task_text, run_id=run.run_id),
                        session.run_overrides,
                    )
                else:
                    result = await session.runtime.resume(run.task_text, run_id=run.run_id)
                answer = result.output
                stop_reason = result.stop_reason.value
                if not result.success:
                    status = "failed"
                    error = f"Engine stopped: {stop_reason}"
            except asyncio.CancelledError:
                status, error = "cancelled", "Run cancelled"
            except Exception as exc:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
                logger.exception("WebUI run failed")
            finally:
                trace_ref = await self._capture_trace_ref(run)
                if answer is not None:
                    answer, _ = truncate_str(answer, ANSWER_MAX)
                self._event_bridge.emit_system({
                    "type": "run_finished",
                    "run_id": run.run_id,
                    "status": status,
                    "answer": answer,
                    "error": error,
                    "stop_reason": stop_reason,
                    "trace": trace_ref,
                    "ts": time.time(),
                })
                session.turn_count += 1
                if self._current_run is run:
                    self._current_run = None

    def _register_prompt(self, prompt_id: str, future: asyncio.Future, question: str) -> None:
        if self.session is None:
            return
        self.session.pending_prompts[prompt_id] = (future, question)
        future.add_done_callback(lambda _future: self.session and self.session.pending_prompts.pop(prompt_id, None))

    def resolve_prompt(self, prompt_id: str, text: str) -> bool:
        if self.session is None:
            return False
        item = self.session.pending_prompts.get(prompt_id)
        if item is None or item[0].done():
            return False
        item[0].set_result(text)
        return True

    def cancel_prompt(self, prompt_id: str) -> bool:
        return self.resolve_prompt(prompt_id, CANCEL_SENTINEL)

    def _maybe_capture_trace_id(self, _event_name: str) -> None:
        if self._current_run is None or self.session is None:
            return
        bridge = self.session.runtime.context.tracing_bridge
        span = getattr(bridge, "_root_span", None)
        if span is not None:
            self._current_run.trace_id = format(span.get_span_context().trace_id, "032x")

    async def _capture_trace_ref(self, run: RunContext) -> dict[str, str] | None:
        if run.trace_id is None:
            return None
        try:
            from opentelemetry import trace

            provider = trace.get_tracer_provider()
            if hasattr(provider, "force_flush"):
                await asyncio.to_thread(provider.force_flush, 3000)
        except Exception:
            logger.debug("Trace flush failed", exc_info=True)
        return {"trace_id": run.trace_id, "url": f"/traces/{run.trace_id}"}
