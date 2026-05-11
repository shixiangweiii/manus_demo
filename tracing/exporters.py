"""
Custom Span Exporters - File and Rich Console exporters.
自定义 Span 导出器 —— 文件导出和 Rich 控制台导出。

FileSpanExporter: Exports traces to JSON files for offline analysis.
RichConsoleExporter: Renders span tree in Rich format for development.

FileSpanExporter：将 Trace 导出为 JSON 文件，便于离线分析。
RichConsoleExporter：以 Rich Tree 格式渲染 Span 树，用于开发调试。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)


class FileSpanExporter(SpanExporter):
    """
    Exports completed spans to JSON files.
    将完成的 Span 导出为 JSON 文件。

    Each trace is written to a separate file:
      traces/{trace_id}_{timestamp}.json

    每个 Trace 写入单独文件：
      traces/{trace_id}_{timestamp}.json
    """

    def __init__(self, output_dir: str = "traces"):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._buffer: dict[str, list[dict]] = defaultdict(list)
        logger.info("[FileSpanExporter] Output directory: %s", self._output_dir.resolve())

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """
        Export a batch of spans to JSON files.
        将一批 Span 导出为 JSON 文件。
        """
        try:
            # Group spans by trace_id
            traces: dict[str, list[dict]] = defaultdict(list)
            for span in spans:
                trace_id = format(span.context.trace_id, "032x")
                traces[trace_id].append(self._span_to_dict(span))

            # Write each trace to a stable file (keyed by trace_id only)
            for trace_id, span_dicts in traces.items():
                filename = f"{trace_id}.json"
                filepath = self._output_dir / filename

                # Merge with existing file if present (for multi-batch exports of same trace)
                existing_spans = []
                if filepath.exists():
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            existing_spans = data.get("spans", [])
                    except (json.JSONDecodeError, OSError):
                        existing_spans = []

                output = {
                    "trace_id": trace_id,
                    "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                    "spans": existing_spans + span_dicts,
                }

                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(output, f, indent=2, ensure_ascii=False)

            return SpanExportResult.SUCCESS

        except Exception as e:
            logger.error("[FileSpanExporter] Export failed: %s", e, exc_info=True)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        """Flush any remaining data."""
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush is a no-op for file exporter."""
        return True

    @staticmethod
    def _span_to_dict(span: ReadableSpan) -> dict:
        """Convert a ReadableSpan to a JSON-serializable dictionary.
        将 ReadableSpan 转换为 JSON 可序列化的字典。"""
        context = span.context
        parent_id = None
        if span.parent and span.parent.span_id:
            parent_id = format(span.parent.span_id, "016x")

        # Convert timestamps (nanoseconds to ISO string)
        start_time = ""
        end_time = ""
        duration_ms = 0.0
        if span.start_time:
            start_time = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(span.start_time / 1e9)
            ) + f".{int(span.start_time % 1e9 // 1e6):03d}Z"
        if span.end_time:
            end_time = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(span.end_time / 1e9)
            ) + f".{int(span.end_time % 1e9 // 1e6):03d}Z"
        if span.start_time and span.end_time:
            duration_ms = (span.end_time - span.start_time) / 1e6

        # Convert attributes
        attributes = {}
        if span.attributes:
            for key, value in span.attributes.items():
                attributes[key] = value

        # Convert events
        events = []
        if span.events:
            for event in span.events:
                event_dict = {"name": event.name, "timestamp": ""}
                if event.timestamp:
                    event_dict["timestamp"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.gmtime(event.timestamp / 1e9)
                    ) + f".{int(event.timestamp % 1e9 // 1e6):03d}Z"
                if event.attributes:
                    event_dict["attributes"] = dict(event.attributes)
                events.append(event_dict)

        # Status
        status = "UNSET"
        if span.status:
            status = span.status.status_code.name

        return {
            "span_id": format(context.span_id, "016x"),
            "parent_span_id": parent_id,
            "name": span.name,
            "start_time": start_time,
            "end_time": end_time,
            "duration_ms": round(duration_ms, 2),
            "attributes": attributes,
            "events": events,
            "status": status,
        }


class RichConsoleExporter(SpanExporter):
    """
    Renders completed spans as a Rich tree in the terminal.
    以 Rich Tree 格式在终端渲染完成的 Span。

    Best for development/debugging: shows span hierarchy with timing.
    适合开发/调试：展示 Span 层级和耗时。
    """

    # Emoji mapping for span types
    _ICONS = {
        "task_execution": "🔍",
        "orchestrator": "🎯",
        "planner": "📋",
        "execution": "⚡",
        "dag": "🔄",
        "node": "🎯",
        "react": "💭",
        "llm": "🤖",
        "tool": "🔧",
        "reflector": "🪞",
        "memory": "🧠",
        "knowledge": "📚",
        "todo": "📝",
        "step": "👣",
    }

    def __init__(self):
        try:
            from rich.console import Console
            self._console = Console()
        except ImportError:
            self._console = None
            logger.warning("[RichConsoleExporter] 'rich' package not installed, falling back to print")

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """
        Render spans grouped by trace as a tree structure.
        按 trace 聚合 Span 并以树形结构渲染。
        """
        try:
            # Group spans by trace_id
            traces: dict[str, list[ReadableSpan]] = defaultdict(list)
            for span in spans:
                trace_id = format(span.context.trace_id, "032x")
                traces[trace_id].append(span)

            for trace_id, trace_spans in traces.items():
                self._render_trace_tree(trace_id, trace_spans)
            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.error("[RichConsoleExporter] Export failed: %s", e)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def _render_trace_tree(self, trace_id: str, spans: list[ReadableSpan]) -> None:
        """
        Rebuild span tree from parent_span_id relationships and render as indented tree.
        基于 parent_span_id 重建 Span 树并渲染为缩进树形结构。
        """
        from opentelemetry.trace import StatusCode

        # Build lookup: span_id -> span
        span_map: dict[int, ReadableSpan] = {}
        children: dict[int, list[int]] = defaultdict(list)
        roots: list[int] = []

        for span in spans:
            span_id = span.context.span_id
            span_map[span_id] = span
            if span.parent and span.parent.span_id:
                children[span.parent.span_id].append(span_id)
            else:
                roots.append(span_id)

        # Also treat spans whose parent is not in this batch as roots
        all_ids = set(span_map.keys())
        for span in spans:
            if span.parent and span.parent.span_id and span.parent.span_id not in all_ids:
                span_id = span.context.span_id
                if span_id not in roots:
                    roots.append(span_id)

        # Sort roots by start_time
        roots.sort(key=lambda sid: span_map[sid].start_time or 0)

        # Print trace header
        header = f"─── Trace {trace_id[:16]}… ({len(spans)} spans) ───"
        if self._console:
            self._console.print(f"\n[bold]{header}[/bold]", highlight=False)
        else:
            print(f"\n{header}")

        # Recursive render
        def render_node(span_id: int, depth: int) -> None:
            span = span_map[span_id]
            line = self._format_span_line(span, depth)
            if self._console:
                self._console.print(line, highlight=False)
            else:
                print(line)

            # Sort children by start_time
            child_ids = sorted(
                children.get(span_id, []),
                key=lambda sid: span_map[sid].start_time or 0,
            )
            for child_id in child_ids:
                render_node(child_id, depth + 1)

        for root_id in roots:
            render_node(root_id, 0)

    def _format_span_line(self, span: ReadableSpan, depth: int) -> str:
        """Format a single span line with icon, name, duration, status, and key attributes."""
        from opentelemetry.trace import StatusCode

        # Determine icon
        icon = "📌"
        for prefix, emoji in self._ICONS.items():
            if span.name.startswith(prefix) or prefix in span.name:
                icon = emoji
                break

        # Calculate duration
        duration_str = ""
        if span.start_time and span.end_time:
            duration_ms = (span.end_time - span.start_time) / 1e6
            if duration_ms >= 1000:
                duration_str = f" ({duration_ms / 1000:.1f}s)"
            else:
                duration_str = f" ({duration_ms:.0f}ms)"

        # Status indicator
        status_icon = ""
        if span.status:
            if span.status.status_code == StatusCode.OK:
                status_icon = " ✅"
            elif span.status.status_code == StatusCode.ERROR:
                status_icon = " ❌"

        # Key attributes summary
        attr_summary = ""
        if span.attributes:
            key_attrs = []
            for key in ("task.complexity", "reflection.passed", "reflection.score",
                        "tool.name", "gen_ai.request.model", "gen_ai.usage.total_tokens",
                        "dag.parallel_count", "node.status", "todo.id"):
                if key in span.attributes:
                    short_key = key.split(".")[-1]
                    key_attrs.append(f"{short_key}={span.attributes[key]}")
            if key_attrs:
                attr_summary = f" [{', '.join(key_attrs)}]"

        # Tree connector
        connector = "  " * depth + ("├─ " if depth > 0 else "")
        return f"{connector}{icon} {span.name}{duration_str}{status_icon}{attr_summary}"
