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
        self._action_turn_spans: dict[str, Span] = {}
        self._tool_spans: dict[str, Span] = {}
        self._turn_spans: dict[str, Span] = {}
        self._phase_spans: dict[str, Span] = {}
        self._context_token: object | None = None
        self._engine_token: object | None = None
        self._action_tokens: dict[str, object] = {}
        self._action_turn_tokens: dict[str, object] = {}
        self._tool_tokens: dict[str, object] = {}
        self._turn_tokens: dict[str, object] = {}
        self._phase_tokens: dict[str, object] = {}

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
            self._safe_detach(self._engine_token)
            self._engine_token = None
            self._end_span(self._engine_span)
            self._engine_span = self._tracer.start_span(
                f"engine.{event.engine or payload.get('engine', 'unknown')}",
                context=set_span_in_context(self._root_span) if self._root_span else None,
            )
            self._set_identity(self._engine_span, event)
            self._engine_token = attach(set_span_in_context(self._engine_span))
            return
        if event.name == "engine_completed":
            failed = payload.get("success") is False
            self._finish_tools(failed=True)
            self._finish_action_turns(failed=failed)
            self._finish_turns(failed=failed)
            for span in self._action_spans.values():
                self._end_span(span, failed=failed)
            self._action_spans.clear()
            self._action_tokens.clear()
            self._finish_phases(failed=failed)
            self._safe_detach(self._engine_token)
            self._engine_token = None
            self._end_span(self._engine_span, failed=failed)
            self._engine_span = None
            return
        if event.name == "agent_turn_started":
            turn_key = self._turn_key(payload)
            existing = self._turn_spans.pop(turn_key, None)
            if existing is not None:
                self._safe_detach(self._turn_tokens.pop(turn_key, None))
                self._end_span(existing, failed=True)
            span = self._tracer.start_span("agent_loop.turn")
            self._set_identity(span, event)
            span.set_attribute("agent.turn", int(payload.get("turn", 0) or 0))
            if payload.get("subagent_id"):
                span.set_attribute("subagent.id", str(payload["subagent_id"]))
            self._turn_spans[turn_key] = span
            self._turn_tokens[turn_key] = attach(set_span_in_context(span))
            return
        if event.name == "agent_turn_completed":
            self._finish_turn(self._turn_key(payload), failed=False)
            return
        if event.name == "agent_loop_completed":
            self._finish_turn(
                self._turn_key(payload),
                failed=payload.get("success") is False,
            )
            target = self._engine_span or self._root_span
            if target is not None:
                target.add_event(event.name)
            return
        if event.name == "action_turn_started":
            action_id = self._action_id(payload)
            turn_key = self._action_turn_key(payload)
            existing = self._action_turn_spans.pop(turn_key, None)
            if existing is not None:
                self._safe_detach(self._action_turn_tokens.pop(turn_key, None))
                self._end_span(existing, failed=True)
            parent = self._action_spans.get(action_id)
            span = self._tracer.start_span(
                "action_loop.turn",
                context=set_span_in_context(parent) if parent is not None else None,
            )
            self._set_identity(span, event)
            span.set_attribute("action.id", action_id)
            span.set_attribute("action.turn", int(payload.get("turn", 0) or 0))
            self._action_turn_spans[turn_key] = span
            self._action_turn_tokens[turn_key] = attach(set_span_in_context(span))
            return
        if event.name == "action_turn_completed":
            self._finish_action_turn(
                self._action_turn_key(payload),
                payload=payload,
                failed=payload.get("success") is False,
            )
            return
        if event.name in {"planner_started", "reflector_started", "dag_execution_started"}:
            phase = event.name.removesuffix("_started")
            self._finish_phase(phase, failed=True)
            span = self._tracer.start_span(phase.replace("_", "."))
            self._set_identity(span, event)
            operation = payload.get("operation")
            if operation:
                span.set_attribute("agent.operation", str(operation))
            self._phase_spans[phase] = span
            self._phase_tokens[phase] = attach(set_span_in_context(span))
            return
        if event.name in {"planner_completed", "reflector_completed", "dag_execution_completed"}:
            phase = event.name.removesuffix("_completed")
            self._finish_phase(phase, failed=payload.get("success") is False)
            return
        if event.name in {"action_started", "node_running"}:
            action_id = self._action_id(payload)
            if action_id:
                existing = self._action_spans.get(action_id)
                if existing is not None:
                    existing.add_event(event.name)
                    return
                span = self._tracer.start_span("agent.action")
                self._set_identity(span, event)
                span.set_attribute("action.id", action_id)
                self._action_spans[action_id] = span
                self._action_tokens[action_id] = attach(set_span_in_context(span))
            return
        if event.name in {
            "action_completed", "action_failed", "node_completed", "node_failed",
        }:
            action_id = self._action_id(payload)
            self._finish_action_turns_for_action(
                action_id,
                failed="failed" in event.name or payload.get("success") is False,
            )
            span = self._action_spans.pop(action_id, None)
            self._safe_detach(self._action_tokens.pop(action_id, None))
            failed = "failed" in event.name or payload.get("success") is False
            self._end_span(span, failed=failed)
            return
        if event.name == "tool_started":
            call_id = str(payload.get("call_id") or "")
            if call_id:
                tool_key = self._tool_key(payload, call_id)
                action_id = self._action_id(payload)
                existing = self._tool_spans.pop(tool_key, None)
                if existing is not None:
                    self._safe_detach(self._tool_tokens.pop(tool_key, None))
                    self._end_span(existing, failed=True)
                parent = (
                    self._action_turn_spans.get(self._action_turn_key(payload))
                    or self._turn_spans.get(self._turn_key(payload))
                    or self._action_spans.get(action_id)
                )
                span = self._tracer.start_span(
                    f"tool.{payload.get('tool', 'unknown')}",
                    context=(
                        set_span_in_context(parent)
                        if parent is not None
                        else None
                    ),
                )
                self._set_identity(span, event)
                span.set_attribute("tool.name", str(payload.get("tool", "")))
                span.set_attribute("tool.call_id", call_id)
                if action_id:
                    span.set_attribute("action.id", action_id)
                if payload.get("turn") is not None:
                    span.set_attribute(
                        "model.turn",
                        int(payload.get("turn", 0) or 0),
                    )
                self._tool_spans[tool_key] = span
                self._tool_tokens[tool_key] = attach(set_span_in_context(span))
            return
        if event.name == "tool_completed":
            call_id = str(payload.get("call_id") or "")
            tool_key = self._tool_key(payload, call_id)
            self._finish_tool(
                tool_key,
                failed=payload.get("success") is False,
            )
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
        action = payload.get("action") or payload.get("node") or {}
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
        ):
            if value:
                span.set_attribute(key, value)

    @staticmethod
    def _turn_key(payload: dict[str, Any]) -> str:
        return str(payload.get("subagent_id") or "root")

    @staticmethod
    def _action_turn_key(payload: dict[str, Any]) -> str:
        owner = str(payload.get("subagent_id") or "root")
        action_id = TracingBridge._action_id(payload) or "no-action"
        turn = int(payload.get("turn", 0) or 0)
        return f"{owner}:{action_id}:{turn}"

    @staticmethod
    def _tool_key(payload: dict[str, Any], call_id: str) -> str:
        owner = str(payload.get("subagent_id") or "root")
        # OpenAI only requires a tool-call ID to be unique within one assistant
        # response.  Concurrent DAG actions may therefore both emit ``call_1``.
        # Include the action boundary so one action cannot overwrite another's
        # active span in the bridge's shared dictionaries.
        action_id = TracingBridge._action_id(payload) or "no-action"
        return f"{owner}:{action_id}:{call_id}"

    def _finish_turn(self, key: str, *, failed: bool) -> None:
        span = self._turn_spans.pop(key, None)
        self._safe_detach(self._turn_tokens.pop(key, None))
        self._end_span(span, failed=failed)

    def _finish_turns(self, *, failed: bool) -> None:
        for key in tuple(self._turn_spans):
            self._finish_turn(key, failed=failed)

    def _finish_action_turn(
        self,
        key: str,
        *,
        payload: dict[str, Any] | None = None,
        failed: bool,
    ) -> None:
        action_id = key.rsplit(":", 1)[0].split(":", 1)[-1]
        self._finish_tools_for_action(action_id, failed=failed)
        span = self._action_turn_spans.pop(key, None)
        self._safe_detach(self._action_turn_tokens.pop(key, None))
        if span is not None and payload:
            outcome = payload.get("outcome")
            if outcome:
                span.set_attribute("action.turn.outcome", str(outcome))
            span.set_attribute(
                "action.turn.tool_calls",
                int(payload.get("tool_calls", 0) or 0),
            )
            if payload.get("final") is not None:
                span.set_attribute("action.turn.final", bool(payload["final"]))
            stop_reason = payload.get("stop_reason")
            if stop_reason:
                span.set_attribute("action.stop_reason", str(stop_reason))
        self._end_span(span, failed=failed)

    def _finish_action_turns_for_action(
        self,
        action_id: str,
        *,
        failed: bool,
    ) -> None:
        marker = f":{action_id}:"
        for key in tuple(self._action_turn_spans):
            if marker in key:
                self._finish_action_turn(key, failed=failed)

    def _finish_action_turns(self, *, failed: bool) -> None:
        for key in tuple(self._action_turn_spans):
            self._finish_action_turn(key, failed=failed)

    def _finish_tool(self, key: str, *, failed: bool) -> None:
        span = self._tool_spans.pop(key, None)
        self._safe_detach(self._tool_tokens.pop(key, None))
        self._end_span(span, failed=failed)

    def _finish_tools_for_action(self, action_id: str, *, failed: bool) -> None:
        marker = f":{action_id}:"
        for key in tuple(self._tool_spans):
            if marker in key:
                self._finish_tool(key, failed=failed)

    def _finish_tools(self, *, failed: bool) -> None:
        for key in tuple(self._tool_spans):
            self._finish_tool(key, failed=failed)

    def _finish_phase(self, key: str, *, failed: bool) -> None:
        span = self._phase_spans.pop(key, None)
        self._safe_detach(self._phase_tokens.pop(key, None))
        self._end_span(span, failed=failed)

    def _finish_phases(self, *, failed: bool) -> None:
        for key in tuple(self._phase_spans):
            self._finish_phase(key, failed=failed)

    @staticmethod
    def _safe_detach(token: object | None) -> None:
        if token is None:
            return
        try:
            detach(token)
        except Exception:
            # Emergency cleanup can run after a child task/context ended.  The
            # span still needs to close even when its ContextVar token belongs
            # to that completed child context.
            logger.debug("Could not detach tracing context token", exc_info=True)

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
        self._finish_tools(failed=failed)
        self._finish_action_turns(failed=failed)
        self._finish_turns(failed=failed)
        for span in self._action_spans.values():
            self._end_span(span, failed=failed)
        self._action_spans.clear()
        self._action_tokens.clear()
        self._finish_phases(failed=failed)
        self._safe_detach(self._engine_token)
        self._engine_token = None
        self._end_span(self._engine_span, failed=failed)
        self._engine_span = None
        self._end_span(self._root_span, failed=failed)
        self._root_span = None
        if self._context_token is not None:
            self._safe_detach(self._context_token)
            self._context_token = None
