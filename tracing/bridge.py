"""Translate structured runtime events into OpenTelemetry spans."""

from __future__ import annotations

import json
import logging
from typing import Any

from opentelemetry.context import attach, detach
from opentelemetry.trace import Span, Status, StatusCode, set_span_in_context

from core.events import RuntimeEvent
from core.redaction import redact_text, redact_value
from core.settings import TracingSettings
from tracing.provider import get_tracer

logger = logging.getLogger(__name__)


class TracingBridge:
    """A small event subscriber; routing never depends on display text."""

    def __init__(self, settings: TracingSettings) -> None:
        self._tracer = get_tracer("manus_demo.runtime")
        self._log_prompts = settings.log_prompts
        self._max_attribute_length = settings.max_attribute_length
        self._root_span: Span | None = None
        self._engine_span: Span | None = None
        self._action_spans: dict[str, Span] = {}
        self._tool_spans: dict[str, Span] = {}
        self._context_token: object | None = None

    def on_runtime_event(self, event: RuntimeEvent) -> None:
        try:
            self._handle(event)
        except Exception:
            logger.debug("Tracing event failed: %s", event.name, exc_info=True)

    def on_event(self, name: str, payload: Any = None) -> None:
        """Compatibility callback for retained components."""
        self.on_runtime_event(RuntimeEvent(name=name, payload=payload or {}))

    def _handle(self, event: RuntimeEvent) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.name in {"task_started", "task_start"}:
            self._finish_all(failed=self._root_span is not None)
            self._root_span = self._tracer.start_span("agent.task")
            self._context_token = attach(set_span_in_context(self._root_span))
            self._set_identity(self._root_span, event)
            if self._log_prompts:
                self._root_span.set_attribute(
                    "task.input",
                    redact_text(str(payload.get("task", "")))[
                        :self._max_attribute_length
                    ],
                )
            return
        if event.name == "engine_started":
            self._end_span(self._engine_span)
            self._engine_span = self._tracer.start_span(
                f"engine.{event.engine or payload.get('engine', 'unknown')}",
                context=set_span_in_context(self._root_span) if self._root_span else None,
            )
            self._set_identity(self._engine_span, event)
            return
        if event.name == "engine_completed":
            failed = payload.get("success") is False
            for span in self._tool_spans.values():
                self._end_span(span, failed=True)
            self._tool_spans.clear()
            for span in self._action_spans.values():
                self._end_span(span, failed=failed)
            self._action_spans.clear()
            self._end_span(self._engine_span, failed=failed)
            self._engine_span = None
            return
        if event.name in {"action_started", "node_running", "todo_start", "workflow_step_start"}:
            action_id = self._action_id(payload)
            if action_id:
                existing = self._action_spans.get(action_id)
                if existing is not None:
                    existing.add_event(event.name)
                    return
                parent = self._engine_span or self._root_span
                span = self._tracer.start_span(
                    "agent.action",
                    context=set_span_in_context(parent) if parent else None,
                )
                self._set_identity(span, event)
                span.set_attribute("action.id", action_id)
                self._action_spans[action_id] = span
            return
        if event.name in {
            "action_completed", "action_failed", "node_completed", "node_failed", "todo_complete",
            "todo_failed", "workflow_step_complete", "workflow_step_failed",
        }:
            action_id = self._action_id(payload)
            span = self._action_spans.pop(action_id, None)
            failed = "failed" in event.name or payload.get("success") is False
            self._end_span(span, failed=failed)
            return
        if event.name == "tool_started":
            call_id = str(payload.get("call_id") or "")
            if call_id:
                action_id = self._action_id(payload)
                parent = self._action_spans.get(action_id) or self._engine_span or self._root_span
                span = self._tracer.start_span(
                    f"tool.{payload.get('tool', 'unknown')}",
                    context=set_span_in_context(parent) if parent else None,
                )
                self._set_identity(span, event)
                span.set_attribute("tool.name", str(payload.get("tool", "")))
                span.set_attribute("tool.call_id", call_id)
                self._tool_spans[call_id] = span
            return
        if event.name == "tool_completed":
            call_id = str(payload.get("call_id") or "")
            span = self._tool_spans.pop(call_id, None)
            self._end_span(span, failed=payload.get("success") is False)
            return
        if event.name in {"task_completed", "task_complete"}:
            self._finish_all(failed=payload.get("success") is False)
            return
        if event.name in {"task_failed", "task_cancelled", "execution_error"}:
            self._finish_all(failed=True)
            return

        target = self._engine_span or self._root_span
        if target is not None:
            attributes = (
                {"payload": self._safe_json(payload)}
                if payload and self._log_prompts
                else {}
            )
            target.add_event(event.name, attributes=attributes)

    @staticmethod
    def _action_id(payload: dict[str, Any]) -> str:
        action = payload.get("action") or payload.get("node") or payload.get("todo") or {}
        if hasattr(action, "id"):
            return str(action.id)
        if isinstance(action, dict) and action.get("id") is not None:
            return str(action["id"])
        return str(payload.get("action_id") or payload.get("id") or "")

    @staticmethod
    def _set_identity(span: Span | None, event: RuntimeEvent) -> None:
        if span is None:
            return
        for key, value in (
            ("run.id", event.run_id),
            ("task.id", event.task_id),
            ("agent.engine", event.engine),
            ("agent.executor", event.executor),
        ):
            if value:
                span.set_attribute(key, value)

    def _safe_json(self, value: Any) -> str:
        try:
            serialized = json.dumps(
                redact_value(value),
                ensure_ascii=False,
                default=lambda item: redact_text(str(item)),
            )
            return redact_text(serialized)[:self._max_attribute_length]
        except Exception:
            return redact_text(str(value))[:self._max_attribute_length]

    @staticmethod
    def _end_span(span: Span | None, *, failed: bool = False) -> None:
        if span is None:
            return
        span.set_status(Status(StatusCode.ERROR if failed else StatusCode.OK))
        span.end()

    def _finish_all(self, *, failed: bool = False) -> None:
        for span in self._tool_spans.values():
            self._end_span(span, failed=failed)
        self._tool_spans.clear()
        for span in self._action_spans.values():
            self._end_span(span, failed=failed)
        self._action_spans.clear()
        self._end_span(self._engine_span, failed=failed)
        self._engine_span = None
        self._end_span(self._root_span, failed=failed)
        self._root_span = None
        if self._context_token is not None:
            detach(self._context_token)
            self._context_token = None
