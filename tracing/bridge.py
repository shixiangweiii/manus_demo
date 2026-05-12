"""
Tracing Bridge - Converts _emit event stream to OpenTelemetry Spans.
追踪桥接器 —— 将 _emit 事件流转换为 OpenTelemetry Span。

The TracingBridge subscribes to the existing event callback system (_emit/on_event)
and translates events into structured OTel Spans with proper parent-child relationships.

TracingBridge 订阅现有的事件回调系统，将事件转换为具有正确父子关系的 OTel Span。

Design:
- Maintains a span stack to track parent-child relationships
- Uses an event-to-handler mapping table for extensibility
- Exception-safe: errors in tracing never affect the main execution flow
- Supports concurrent execution (asyncio-safe via contextvars)

设计：
- 维护 Span 栈追踪父子关系
- 使用事件到处理器的映射表，便于扩展
- 异常安全：tracing 中的错误不影响主执行流程
- 支持并发执行（通过 contextvars 实现 asyncio 安全）
"""

from __future__ import annotations

import logging
import time
from typing import Any

from opentelemetry import trace, context as otel_context
from opentelemetry.trace import Span, StatusCode, Tracer

from tracing import config as tracing_config
from tracing.spans import SpanName, AttrKey, EventName

logger = logging.getLogger(__name__)


class TracingBridge:
    """
    Event-to-Span bridge for the Manus Demo event system.
    Manus Demo 事件系统的事件到 Span 桥接器。

    Subscribes to _emit events and creates/manages OpenTelemetry Spans
    with proper parent-child hierarchy.

    订阅 _emit 事件并创建/管理具有正确父子层级的 OpenTelemetry Span。

    Usage:
        bridge = TracingBridge()
        # Register as event subscriber
        orchestrator = OrchestratorAgent(on_event=bridge.on_event)
    """

    def __init__(self):
        from tracing.provider import get_tracer
        self._tracer: Tracer = get_tracer("manus_demo.bridge")

        self._root_span: Span | None = None
        self._root_token: Any = None

        # Phase tracking for smart span management
        # 阶段追踪，用于智能 Span 管理
        self._current_phase: str = ""
        self._phase_span: Span | None = None
        self._phase_token: Any = None

        # Execution mode tracking
        self._execution_mode: str = ""

        # DAG super-step tracking
        self._superstep_span: Span | None = None
        self._superstep_token: Any = None

        # Node-level tracking
        self._node_spans: dict[str, tuple[Span, Any]] = {}

        # TODO-level tracking (emergent mode)
        self._todo_spans: dict[str, tuple[Span, Any]] = {}

        # Timing
        self._task_start_time: float = 0.0

        # Event dispatch table: event name -> handler method
        # 事件分发表：事件名 → 处理方法
        self._event_handlers: dict[str, Any] = {
            "task_start": self._on_task_start,
            "task_complexity": self._on_task_complexity,
            "phase": self._on_phase,
            "plan": self._on_plan_created,
            "dag_created": self._on_dag_created,
            "todo_list_initialized": self._on_todo_list_initialized,
            "todo_start": self._on_todo_start,
            "todo_complete": self._on_todo_complete,
            "todo_failed": self._on_todo_failed,
            "todo_blocked": self._on_todo_blocked,
            "superstep": self._on_superstep,
            "node_running": self._on_node_running,
            "node_completed": self._on_node_completed,
            "node_failed": self._on_node_failed,
            "step_start": self._on_step_start,
            "step_complete": self._on_step_complete,
            "step_failed": self._on_step_failed,
            "reflection": self._on_reflection,
            "plan_adaptation": self._on_adaptation,
            "adaptive_planning": self._on_adaptation,
            "token_usage_summary": self._on_token_usage,
            "task_complete": self._on_task_complete,
            "memory_stored": self._on_memory_stored,
            # v8 Goal-Driven events
            "goal_anchor": self._on_goal_anchor,
            "goal_reflection": self._on_goal_reflection,
            "goal_reanchor": self._on_goal_reanchor,
        }

    def on_event(self, event: str, data: Any = None) -> None:
        """
        Main event handler. Routes events to specific handlers.
        主事件处理器。将事件路由到特定的处理方法。

        This method is exception-safe: any error is caught and logged,
        never propagated to the caller.

        此方法异常安全：任何错误都被捕获并记录，不会传播到调用方。
        """
        try:
            self._handle_event(event, data)
        except Exception as exc:
            logger.debug(
                "[TracingBridge] Error handling event '%s': %s",
                event, exc, exc_info=True,
            )

    def _handle_event(self, event: str, data: Any) -> None:
        """
        Internal event routing via dispatch table.
        内部事件路由，通过分发表分发。
        """
        handler = self._event_handlers.get(event)
        if handler:
            handler(data)

    # ------------------------------------------------------------------
    # Task Lifecycle
    # 任务生命周期
    # ------------------------------------------------------------------

    def _on_task_start(self, data: Any) -> None:
        """Create the root trace span for the entire task."""
        self._task_start_time = time.perf_counter()

        # Create root span
        self._root_span = self._tracer.start_span(SpanName.TASK_EXECUTION)
        self._root_token = otel_context.attach(
            trace.set_span_in_context(self._root_span)
        )

        # Set task attributes
        if isinstance(data, dict):
            task_input = data.get("task", "")
            self._safe_set_attr(self._root_span, AttrKey.TASK_INPUT, task_input)

    def _on_task_complexity(self, data: Any) -> None:
        """Record task complexity classification result."""
        if self._root_span and isinstance(data, dict):
            complexity = data.get("complexity", "")
            self._root_span.set_attribute(AttrKey.TASK_COMPLEXITY, complexity)
            self._execution_mode = complexity

    def _on_task_complete(self, data: Any) -> None:
        """End the root span when task completes."""
        # End any open phase span
        self._end_phase_span()

        if self._root_span:
            # Record total duration
            elapsed_ms = (time.perf_counter() - self._task_start_time) * 1000
            self._root_span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
            self._root_span.set_attribute(AttrKey.TASK_SUCCESS, True)

            if isinstance(data, dict):
                answer = data.get("answer", "")
                if answer:
                    self._safe_set_attr(self._root_span, AttrKey.TASK_OUTPUT, answer[:500])

            self._root_span.set_status(StatusCode.OK)
            self._root_span.end()

            # Detach context
            if self._root_token:
                otel_context.detach(self._root_token)

            self._root_span = None
            self._root_token = None

    # ------------------------------------------------------------------
    # Phase Management
    # 阶段管理
    # ------------------------------------------------------------------

    def _on_phase(self, data: Any) -> None:
        """
        Handle phase transitions. Creates/ends phase spans.
        处理阶段转换。创建/结束阶段 Span。

        Phase events carry a string like:
        - "Gathering context..."
        - "Classifying task complexity..."
        - "Planning (v2 hierarchical DAG)..."
        - "Executing DAG (attempt 1)..."
        - "Reflecting on results..."
        - "Emergent planning completed."
        """
        phase_text = str(data) if data else ""

        # End previous phase span if exists
        self._end_phase_span()

        # Determine span name from phase text
        span_name = self._phase_to_span_name(phase_text)
        if not span_name:
            return  # Unknown phase, skip

        self._current_phase = phase_text

        # Create new phase span (child of root)
        self._phase_span = self._tracer.start_span(
            span_name,
            context=trace.set_span_in_context(self._root_span) if self._root_span else None,
        )
        self._phase_token = otel_context.attach(
            trace.set_span_in_context(self._phase_span)
        )

    def _end_phase_span(self) -> None:
        """End the current phase span if one is active."""
        # End any open TODO spans (emergent mode cleanup)
        for todo_id in list(self._todo_spans.keys()):
            self._end_todo_span(todo_id, StatusCode.OK)

        # End any super-step span (DAG mode cleanup)
        self._end_superstep_span()

        if self._phase_span:
            self._phase_span.set_status(StatusCode.OK)
            self._phase_span.end()
            if self._phase_token:
                otel_context.detach(self._phase_token)
            self._phase_span = None
            self._phase_token = None

    @staticmethod
    def _phase_to_span_name(phase_text: str) -> str:
        """Map phase text to a structured span name."""
        text_lower = phase_text.lower()

        if "gathering context" in text_lower:
            return SpanName.GATHER_CONTEXT
        elif "classifying" in text_lower:
            return SpanName.CLASSIFY_TASK
        elif "planning" in text_lower and "v1" in text_lower:
            return SpanName.CREATE_PLAN
        elif "planning" in text_lower and "v2" in text_lower:
            return SpanName.CREATE_DAG
        elif "planning" in text_lower and ("dag" in text_lower or "hierarchical" in text_lower):
            return SpanName.CREATE_DAG
        elif "planning" in text_lower and "emergent" in text_lower:
            return SpanName.CREATE_TODO_LIST
        elif "planning" in text_lower and "todo" in text_lower:
            return SpanName.CREATE_TODO_LIST
        elif "executing" in text_lower and "simple" in text_lower:
            return SpanName.EXECUTION_SIMPLE
        elif "executing" in text_lower and "dag" in text_lower:
            return SpanName.EXECUTION_DAG
        elif "executing" in text_lower and "emergent" in text_lower:
            return SpanName.EXECUTION_EMERGENT
        elif "executing" in text_lower and ("goal-driven" in text_lower or "v8" in text_lower):
            return SpanName.EXECUTION_GOAL_DRIVEN
        elif "building goal" in text_lower or ("backward" in text_lower and "planning" in text_lower):
            return SpanName.GOAL_ANCHOR
        elif "compiling" in text_lower:
            return SpanName.GOAL_ANCHOR
        elif "reflecting" in text_lower:
            return SpanName.REFLECT
        elif "re-planning" in text_lower or "replan" in text_lower:
            return SpanName.REPLAN
        elif "partial replan" in text_lower:
            return SpanName.REPLAN_SUBTREE
        elif "adaptive" in text_lower:
            return SpanName.ADAPTIVE_PLANNING
        else:
            return ""

    # ------------------------------------------------------------------
    # Plan Events
    # 规划事件
    # ------------------------------------------------------------------

    def _on_plan_created(self, data: Any) -> None:
        """Record plan metadata when a simple plan is created."""
        if self._phase_span and data:
            self._phase_span.set_attribute(AttrKey.PLAN_TYPE, "simple")
            if hasattr(data, "steps"):
                self._phase_span.set_attribute(AttrKey.PLAN_STEPS_COUNT, len(data.steps))

    def _on_dag_created(self, data: Any) -> None:
        """Record DAG metadata when a DAG plan is created."""
        if self._phase_span and data:
            self._phase_span.set_attribute(AttrKey.PLAN_TYPE, "dag")
            if hasattr(data, "nodes"):
                self._phase_span.set_attribute(AttrKey.PLAN_NODES_COUNT, len(data.nodes))
                self._phase_span.set_attribute(AttrKey.DAG_TOTAL_NODES, len(data.nodes))
            if hasattr(data, "edges"):
                self._phase_span.set_attribute(AttrKey.DAG_TOTAL_EDGES, len(data.edges))

    def _on_todo_list_initialized(self, data: Any) -> None:
        """Record TODO list metadata when emergent planning initializes."""
        if self._phase_span and isinstance(data, dict):
            items = data.get("items", [])
            self._phase_span.set_attribute(AttrKey.PLAN_TYPE, "emergent")
            self._phase_span.set_attribute(AttrKey.TODO_LIST_SIZE, len(items))

    # ------------------------------------------------------------------
    # TODO Execution Events (Emergent Mode)
    # TODO 执行事件（涌现模式）
    # ------------------------------------------------------------------

    def _on_todo_start(self, data: Any) -> None:
        """Create a span when a TODO item starts executing."""
        if not isinstance(data, dict):
            return

        todo = data.get("todo")
        if not todo:
            return

        todo_id = str(getattr(todo, "id", "unknown"))
        description = getattr(todo, "description", "")
        retry_count = getattr(todo, "retry_count", 0)

        span_name = f"todo.execute.{todo_id}"

        # Parent is the execution phase span
        parent_context = (
            trace.set_span_in_context(self._phase_span)
            if self._phase_span
            else None
        )

        span = self._tracer.start_span(span_name, context=parent_context)
        token = otel_context.attach(trace.set_span_in_context(span))

        span.set_attribute(AttrKey.TODO_ID, todo_id)
        if description:
            self._safe_set_attr(span, AttrKey.TODO_DESCRIPTION, description)
        if retry_count > 0:
            span.set_attribute(AttrKey.TODO_RETRY_COUNT, retry_count)

        self._todo_spans[todo_id] = (span, token)

    def _on_todo_complete(self, data: Any) -> None:
        """End a TODO span with success status."""
        if not isinstance(data, dict):
            return
        todo = data.get("todo")
        if not todo:
            return
        todo_id = str(getattr(todo, "id", "unknown"))
        self._end_todo_span(todo_id, StatusCode.OK)

    def _on_todo_failed(self, data: Any) -> None:
        """End a TODO span with error status (will be retried)."""
        if not isinstance(data, dict):
            return
        todo = data.get("todo")
        if not todo:
            return
        todo_id = str(getattr(todo, "id", "unknown"))

        if todo_id in self._todo_spans:
            span, _ = self._todo_spans[todo_id]
            result = data.get("result")
            if result:
                output = getattr(result, "output", "")
                if output:
                    span.set_attribute("todo.error", str(output)[:500])
            span.set_attribute("todo.will_retry", True)

        self._end_todo_span(todo_id, StatusCode.ERROR)

    def _on_todo_blocked(self, data: Any) -> None:
        """End a TODO span with error status (max retries exceeded)."""
        if not isinstance(data, dict):
            return
        todo = data.get("todo")
        if not todo:
            return
        todo_id = str(getattr(todo, "id", "unknown"))

        if todo_id in self._todo_spans:
            span, _ = self._todo_spans[todo_id]
            span.set_attribute("todo.blocked", True)
            result = data.get("result")
            if result:
                output = getattr(result, "output", "")
                if output:
                    span.set_attribute("todo.error", str(output)[:500])

        self._end_todo_span(todo_id, StatusCode.ERROR)

    def _end_todo_span(self, todo_id: str, status_code: StatusCode) -> None:
        """End a specific TODO span."""
        if todo_id in self._todo_spans:
            span, token = self._todo_spans.pop(todo_id)
            span.set_status(status_code)
            span.end()
            otel_context.detach(token)

    # ------------------------------------------------------------------
    # DAG Execution Events
    # DAG 执行事件
    # ------------------------------------------------------------------

    def _on_superstep(self, data: Any) -> None:
        """Create a span for each DAG super-step."""
        # End previous super-step span
        self._end_superstep_span()

        if isinstance(data, dict):
            step_index = data.get("step", 0)
            nodes = data.get("nodes", [])
            total_ready = data.get("total_ready", len(nodes))

            span_name = f"{SpanName.DAG_SUPER_STEP}.{step_index}"

            # Parent is the execution phase span
            parent_context = (
                trace.set_span_in_context(self._phase_span)
                if self._phase_span
                else None
            )

            self._superstep_span = self._tracer.start_span(
                span_name, context=parent_context
            )
            self._superstep_token = otel_context.attach(
                trace.set_span_in_context(self._superstep_span)
            )

            self._superstep_span.set_attribute(AttrKey.DAG_SUPERSTEP_INDEX, step_index)
            self._superstep_span.set_attribute(AttrKey.DAG_PARALLEL_COUNT, total_ready)

    def _end_superstep_span(self) -> None:
        """End the current super-step span."""
        # End any open node spans first
        for node_id in list(self._node_spans.keys()):
            self._end_node_span(node_id, StatusCode.OK)

        if self._superstep_span:
            self._superstep_span.set_status(StatusCode.OK)
            self._superstep_span.end()
            if self._superstep_token:
                otel_context.detach(self._superstep_token)
            self._superstep_span = None
            self._superstep_token = None

    def _on_node_running(self, data: Any) -> None:
        """Create a span when a node starts executing."""
        if not isinstance(data, dict):
            return

        node = data.get("node")
        if not node:
            return

        node_id = getattr(node, "id", str(node))
        node_type = getattr(node, "node_type", None)
        description = getattr(node, "description", "")

        span_name = f"{SpanName.NODE_EXECUTE}.{node_id}"

        # Parent is the super-step span or phase span
        parent_context = None
        if self._superstep_span:
            parent_context = trace.set_span_in_context(self._superstep_span)
        elif self._phase_span:
            parent_context = trace.set_span_in_context(self._phase_span)

        span = self._tracer.start_span(span_name, context=parent_context)
        token = otel_context.attach(trace.set_span_in_context(span))

        span.set_attribute(AttrKey.NODE_ID, node_id)
        if node_type:
            span.set_attribute(AttrKey.NODE_TYPE, str(node_type.value) if hasattr(node_type, "value") else str(node_type))
        if description:
            self._safe_set_attr(span, AttrKey.NODE_DESCRIPTION, description)

        self._node_spans[node_id] = (span, token)

    def _on_node_completed(self, data: Any) -> None:
        """End a node span with success status."""
        if not isinstance(data, dict):
            return
        node = data.get("node")
        if not node:
            return
        node_id = getattr(node, "id", str(node))
        self._end_node_span(node_id, StatusCode.OK)

    def _on_node_failed(self, data: Any) -> None:
        """End a node span with error status."""
        if not isinstance(data, dict):
            return
        node = data.get("node")
        if not node:
            return
        node_id = getattr(node, "id", str(node))
        reason = data.get("reason", "")

        if node_id in self._node_spans:
            span, _ = self._node_spans[node_id]
            if reason:
                span.set_attribute(AttrKey.ERROR_MESSAGE, str(reason)[:500])

        self._end_node_span(node_id, StatusCode.ERROR)

    def _end_node_span(self, node_id: str, status_code: StatusCode) -> None:
        """End a specific node span."""
        if node_id in self._node_spans:
            span, token = self._node_spans.pop(node_id)
            span.set_status(status_code)
            span.end()
            otel_context.detach(token)

    # ------------------------------------------------------------------
    # Simple Path Events
    # Simple 路径事件
    # ------------------------------------------------------------------

    def _on_step_start(self, data: Any) -> None:
        """Create a span for a simple plan step."""
        if not isinstance(data, dict):
            return
        step = data.get("step")
        if not step:
            return

        step_id = getattr(step, "id", "unknown")
        description = getattr(step, "description", "")

        span_name = f"{SpanName.STEP_EXECUTE}.{step_id}"

        parent_context = (
            trace.set_span_in_context(self._phase_span)
            if self._phase_span
            else None
        )

        span = self._tracer.start_span(span_name, context=parent_context)
        token = otel_context.attach(trace.set_span_in_context(span))

        span.set_attribute(AttrKey.STEP_ID, str(step_id))
        if description:
            self._safe_set_attr(span, AttrKey.STEP_DESCRIPTION, description)

        # Use node_spans dict to track step spans too (reusing mechanism)
        self._node_spans[f"step_{step_id}"] = (span, token)

    def _on_step_complete(self, data: Any) -> None:
        """End a step span with success status."""
        if not isinstance(data, dict):
            return
        step = data.get("step")
        if not step:
            return
        step_id = getattr(step, "id", "unknown")
        self._end_node_span(f"step_{step_id}", StatusCode.OK)

    def _on_step_failed(self, data: Any) -> None:
        """End a step span with error status."""
        if not isinstance(data, dict):
            return
        step = data.get("step")
        if not step:
            return
        step_id = getattr(step, "id", "unknown")
        self._end_node_span(f"step_{step_id}", StatusCode.ERROR)

    # ------------------------------------------------------------------
    # Reflection Events
    # 反思事件
    # ------------------------------------------------------------------

    def _on_reflection(self, data: Any) -> None:
        """Record reflection results on the current phase span."""
        if self._phase_span and data:
            # Support both dict and object-style data
            if isinstance(data, dict):
                passed = data.get("passed", data.get("approved", None))
                score = data.get("score", None)
                feedback = data.get("feedback", data.get("note", ""))
            else:
                passed = getattr(data, "passed", getattr(data, "approved", None))
                score = getattr(data, "score", None)
                feedback = getattr(data, "feedback", getattr(data, "note", ""))

            if passed is not None:
                self._phase_span.set_attribute(AttrKey.REFLECTION_PASSED, bool(passed))
            if score is not None:
                self._phase_span.set_attribute(AttrKey.REFLECTION_SCORE, float(score))
            if feedback:
                self._safe_set_attr(self._phase_span, AttrKey.REFLECTION_FEEDBACK, feedback)

            # Only include non-None values in event attributes
            event_attrs: dict[str, bool | float] = {}
            if passed is not None:
                event_attrs["passed"] = bool(passed)
            if score is not None:
                event_attrs["score"] = float(score)
            else:
                event_attrs["score"] = 0.0

            self._phase_span.add_event(
                EventName.REFLECTION_COMPLETE,
                attributes=event_attrs,
            )

    # ------------------------------------------------------------------
    # Adaptation Events
    # 自适应规划事件
    # ------------------------------------------------------------------

    def _on_adaptation(self, data: Any) -> None:
        """Record adaptive planning events."""
        if self._phase_span:
            self._phase_span.add_event(EventName.PLAN_ADAPTATION_TRIGGERED)
            if isinstance(data, dict):
                action_count = data.get("action_count", 0)
                self._phase_span.set_attribute(AttrKey.ADAPTATION_ACTION_COUNT, action_count)

    # ------------------------------------------------------------------
    # Token Usage Events
    # Token 用量事件
    # ------------------------------------------------------------------

    def _on_token_usage(self, data: Any) -> None:
        """Record token usage summary on the root span."""
        if self._root_span and data:
            total = getattr(data, "total", None)
            if total:
                prompt_tokens = getattr(total, "prompt_tokens", 0)
                completion_tokens = getattr(total, "completion_tokens", 0)
                total_tokens = getattr(total, "total_tokens", 0)

                self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_INPUT_TOKENS, prompt_tokens)
                self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_OUTPUT_TOKENS, completion_tokens)
                self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_TOTAL_TOKENS, total_tokens)

    # ------------------------------------------------------------------
    # Memory Events
    # 记忆事件
    # ------------------------------------------------------------------

    def _on_memory_stored(self, data: Any) -> None:
        """Record memory storage event."""
        if self._root_span:
            self._root_span.add_event(
                "memory.stored",
                attributes={"task_summary": str(getattr(data, "task", ""))[:200]},
            )

    # ------------------------------------------------------------------
    # Goal-Driven (v8) Events
    # 目标驱动规划事件
    # ------------------------------------------------------------------

    def _on_goal_anchor(self, data: Any) -> None:
        """Record initial goal document."""
        if not isinstance(data, dict):
            return
        if self._phase_span:
            self._safe_set_attr(
                self._phase_span, AttrKey.GOAL_SUCCESS_CRITERIA,
                data.get("success_criteria", ""),
            )
            self._phase_span.set_attribute(
                AttrKey.GOAL_PROGRESS_PCT,
                data.get("progress_pct", 0.0),
            )

    def _on_goal_reflection(self, data: Any) -> None:
        """Record goal-state reflection as a span event."""
        if not isinstance(data, dict):
            return
        if self._phase_span:
            event_attrs = {
                "progress_pct": float(data.get("progress_pct", 0.0)),
                "suggested_action": str(data.get("suggested_action", "")),
            }
            gap = data.get("gap_analysis", "")
            if gap:
                event_attrs["gap_analysis"] = gap[:200]
            next_ms = data.get("next_milestone", "")
            if next_ms:
                event_attrs["next_milestone"] = next_ms[:200]
            self._phase_span.add_event("goal.reflection", attributes=event_attrs)

    def _on_goal_reanchor(self, data: Any) -> None:
        """Record goal re-anchoring events."""
        if not isinstance(data, dict):
            return
        if self._phase_span:
            updated_doc = data.get("updated_goal_doc", {})
            self._phase_span.add_event("goal.reanchor", attributes={
                "goal_drift_detected": bool(data.get("goal_drift_detected", False)),
                "progress_pct": float(
                    updated_doc.get("progress_pct", 0.0)
                    if isinstance(updated_doc, dict) else 0.0
                ),
            })

    # ------------------------------------------------------------------
    # Helpers
    # 辅助方法
    # ------------------------------------------------------------------

    def _safe_set_attr(self, span: Span, key: str, value: Any) -> None:
        """Set attribute with truncation and sensitive data protection."""
        if span is None:
            return

        from tracing.decorators import _safe_set_attribute
        _safe_set_attribute(span, key, value)
