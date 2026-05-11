"""
Tests for the Tracing Module (v7).
全链路追踪模块测试。

Tests cover:
- TracingBridge event-to-span mapping
- Span parent-child relationships
- Decorators behavior (sync/async)
- Feature flag (TRACING_ENABLED=false) zero overhead
- FileSpanExporter output format
- RichConsoleExporter rendering
- BaseTool.traced_execute integration
- LLMClient span helpers
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ======================================================================
# Feature Flag Tests
# 特性开关测试
# ======================================================================

class TestFeatureFlag:
    """Test that tracing is a no-op when disabled."""

    def test_tracing_disabled_imports_noop(self):
        """When TRACING_ENABLED=false, __init__.py provides no-op stubs."""
        # Force reload with tracing disabled
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = False
        try:
            # The module-level check in __init__.py determines behavior at import time
            # Since we can't easily re-import, test the no-op class directly
            from tracing import TracingBridge as TB
            bridge = TB()
            # Should not raise regardless of events
            bridge.on_event("task_start", {"task": "test"})
            bridge.on_event("task_complete", {"answer": "done"})
            bridge.on_event("unknown_event", None)
        finally:
            config.TRACING_ENABLED = original

    def test_traced_execute_no_tracing(self):
        """BaseTool.traced_execute delegates to execute when tracing disabled."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = False
        try:
            from tools.web_search import WebSearchTool
            tool = WebSearchTool()
            # traced_execute should just call execute directly
            result = asyncio.get_event_loop().run_until_complete(
                tool.traced_execute(query="python test")
            )
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            config.TRACING_ENABLED = original


# ======================================================================
# Spans Constants Tests
# Span 常量测试
# ======================================================================

class TestSpanConstants:
    """Test that span constants are properly defined."""

    def test_span_names_not_empty(self):
        """All span names should be non-empty strings."""
        from tracing.spans import SpanName
        for attr in dir(SpanName):
            if attr.startswith("_"):
                continue
            value = getattr(SpanName, attr)
            assert isinstance(value, str), f"SpanName.{attr} is not a string"
            assert len(value) > 0, f"SpanName.{attr} is empty"

    def test_attr_keys_not_empty(self):
        """All attribute keys should be non-empty strings."""
        from tracing.spans import AttrKey
        for attr in dir(AttrKey):
            if attr.startswith("_"):
                continue
            value = getattr(AttrKey, attr)
            assert isinstance(value, str), f"AttrKey.{attr} is not a string"
            assert len(value) > 0, f"AttrKey.{attr} is empty"

    def test_event_names_not_empty(self):
        """All event names should be non-empty strings."""
        from tracing.spans import EventName
        for attr in dir(EventName):
            if attr.startswith("_"):
                continue
            value = getattr(EventName, attr)
            assert isinstance(value, str), f"EventName.{attr} is not a string"
            assert len(value) > 0, f"EventName.{attr} is empty"

    def test_genai_attributes_follow_convention(self):
        """GenAI attributes should follow OTel semantic conventions (gen_ai.*)."""
        from tracing.spans import AttrKey
        genai_attrs = [
            AttrKey.GEN_AI_SYSTEM,
            AttrKey.GEN_AI_REQUEST_MODEL,
            AttrKey.GEN_AI_REQUEST_TEMPERATURE,
            AttrKey.GEN_AI_USAGE_INPUT_TOKENS,
            AttrKey.GEN_AI_USAGE_OUTPUT_TOKENS,
            AttrKey.GEN_AI_USAGE_TOTAL_TOKENS,
        ]
        for attr in genai_attrs:
            assert attr.startswith("gen_ai."), f"Attribute '{attr}' doesn't follow gen_ai.* convention"


# ======================================================================
# TracingBridge Tests (requires OpenTelemetry)
# TracingBridge 测试（需要 OpenTelemetry）
# ======================================================================

class _InMemoryExporter:
    """Simple in-memory span exporter for testing (replaces InMemorySpanExporter)."""

    def __init__(self):
        self._spans: list = []

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def get_finished_spans(self) -> list:
        return list(self._spans)

    def clear(self):
        self._spans.clear()

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


@pytest.fixture
def setup_tracing(monkeypatch):
    """
    Setup a test TracerProvider and patch trace.get_tracer globally.

    This avoids the "Overriding of current TracerProvider is not allowed" issue
    by patching at the module level rather than using set_tracer_provider.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        pytest.skip("OpenTelemetry SDK not installed")

    exporter = _InMemoryExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Patch trace.get_tracer to use our test provider's get_tracer
    original_get_tracer = trace.get_tracer
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    # Also patch the ProxyTracerProvider so any existing references work
    try:
        monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    except AttributeError:
        pass

    yield exporter

    provider.shutdown()


class TestTracingBridge:
    """Test TracingBridge event handling with real OTel SDK."""

    def test_task_lifecycle_creates_root_span(self, setup_tracing):
        """task_start + task_complete should create a root span."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            exporter = setup_tracing
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "test task"})
            bridge.on_event("task_complete", {"answer": "done"})

            spans = exporter.get_finished_spans()
            assert len(spans) >= 1

            root_span = spans[-1]  # Root span ends last
            assert "task_execution" in root_span.name
            assert root_span.attributes.get("task.input") == "test task"
        finally:
            config.TRACING_ENABLED = original

    def test_phase_creates_child_spans(self, setup_tracing):
        """Phase events should create child spans."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            exporter = setup_tracing
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "test"})
            bridge.on_event("phase", "Gathering context...")
            bridge.on_event("phase", "Classifying task complexity...")
            bridge.on_event("task_complete", {"answer": "done"})

            spans = exporter.get_finished_spans()
            # Should have: gather_context, classify_task, task_execution
            assert len(spans) >= 3

            span_names = [s.name for s in spans]
            assert "orchestrator.gather_context" in span_names
            assert "planner.classify_task" in span_names
        finally:
            config.TRACING_ENABLED = original

    def test_task_complexity_recorded(self, setup_tracing):
        """Task complexity should be recorded on the root span."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            exporter = setup_tracing
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "test"})
            bridge.on_event("task_complexity", {"complexity": "complex"})
            bridge.on_event("task_complete", {"answer": "done"})

            spans = exporter.get_finished_spans()
            root_span = [s for s in spans if "task_execution" in s.name][0]
            assert root_span.attributes.get("task.complexity") == "complex"
        finally:
            config.TRACING_ENABLED = original

    def test_bridge_exception_safety(self, setup_tracing):
        """Bridge should never raise exceptions to the caller."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            bridge = TracingBridge()

            # These should not raise even with invalid data
            bridge.on_event("task_start", None)
            bridge.on_event("unknown_event", {"foo": "bar"})
            bridge.on_event("node_running", "invalid_data_type")
            bridge.on_event("task_complete", 12345)
        finally:
            config.TRACING_ENABLED = original

    def test_phase_to_span_name_mapping(self):
        """Test the static phase-to-span-name mapping method."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            assert TracingBridge._phase_to_span_name("Gathering context...") == "orchestrator.gather_context"
            assert TracingBridge._phase_to_span_name("Classifying task complexity...") == "planner.classify_task"
            assert TracingBridge._phase_to_span_name("Planning (v2 hierarchical DAG)...") == "planner.create_dag"
            assert TracingBridge._phase_to_span_name("Planning (v1 simple flat plan)...") == "planner.create_plan"
            assert TracingBridge._phase_to_span_name("Planning (v5 emergent via TODO list)...") == "planner.create_todo_list"
            assert TracingBridge._phase_to_span_name("Executing DAG (attempt 1)...") == "execution.dag"
            assert TracingBridge._phase_to_span_name("Reflecting on results...") == "reflector.reflect"
            assert TracingBridge._phase_to_span_name("Re-planning based on feedback...") == "planner.replan"
            assert TracingBridge._phase_to_span_name("Unknown phase text") == ""
        finally:
            config.TRACING_ENABLED = original


# ======================================================================
# FileSpanExporter Tests
# FileSpanExporter 测试
# ======================================================================

class TestFileSpanExporter:
    """Test FileSpanExporter JSON output."""

    def test_export_creates_json_file(self, setup_tracing):
        """Exporter should create a valid JSON file."""
        try:
            from opentelemetry import trace
            from tracing.exporters import FileSpanExporter
        except ImportError:
            pytest.skip("OpenTelemetry SDK not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_exporter = FileSpanExporter(output_dir=tmpdir)

            # Use the global test tracer (already set)
            tracer = trace.get_tracer("test.file_exporter")
            with tracer.start_as_current_span("test_span") as span:
                span.set_attribute("test.key", "test_value")

            # Get finished spans from test exporter and manually export
            finished_spans = setup_tracing.get_finished_spans()
            test_spans = [s for s in finished_spans if s.name == "test_span"]
            if test_spans:
                file_exporter.export(test_spans)

            # Check that a JSON file was created
            files = list(Path(tmpdir).glob("*.json"))
            assert len(files) >= 1

            # Validate JSON structure
            with open(files[0], "r") as f:
                data = json.load(f)

            assert "trace_id" in data
            assert "spans" in data
            assert len(data["spans"]) >= 1

            span_data = data["spans"][0]
            assert "span_id" in span_data
            assert "name" in span_data
            assert span_data["name"] == "test_span"
            assert "start_time" in span_data
            assert "end_time" in span_data
            assert "duration_ms" in span_data
            assert span_data["duration_ms"] >= 0
            assert "attributes" in span_data
            assert span_data["attributes"].get("test.key") == "test_value"

    def test_export_with_events(self, setup_tracing):
        """Exporter should correctly serialize span events."""
        try:
            from opentelemetry import trace
            from tracing.exporters import FileSpanExporter
        except ImportError:
            pytest.skip("OpenTelemetry SDK not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_exporter = FileSpanExporter(output_dir=tmpdir)

            tracer = trace.get_tracer("test.file_exporter_events")
            with tracer.start_as_current_span("test_with_events") as span:
                span.add_event("test_event", attributes={"key": "value"})

            finished_spans = setup_tracing.get_finished_spans()
            event_spans = [s for s in finished_spans if s.name == "test_with_events"]
            if event_spans:
                file_exporter.export(event_spans)

            files = list(Path(tmpdir).glob("*.json"))
            assert len(files) >= 1
            with open(files[0], "r") as f:
                data = json.load(f)

            span_data = data["spans"][0]
            assert len(span_data["events"]) >= 1
            assert span_data["events"][0]["name"] == "test_event"


# ======================================================================
# Decorators Tests
# 装饰器测试
# ======================================================================

class TestDecorators:
    """Test tracing decorators."""

    def test_traced_decorator_async(self, setup_tracing):
        """@traced should create spans for async functions."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.decorators import traced

            exporter = setup_tracing

            @traced("test.operation")
            async def my_async_func(x: int) -> int:
                return x * 2

            result = asyncio.get_event_loop().run_until_complete(my_async_func(5))
            assert result == 10

            spans = exporter.get_finished_spans()
            matching = [s for s in spans if s.name == "test.operation"]
            assert len(matching) == 1
            assert matching[0].attributes.get("latency_ms") is not None
        finally:
            config.TRACING_ENABLED = original

    def test_traced_decorator_sync(self, setup_tracing):
        """@traced should create spans for sync functions."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.decorators import traced

            exporter = setup_tracing

            @traced("test.sync_op")
            def my_sync_func(x: int) -> int:
                return x + 1

            result = my_sync_func(10)
            assert result == 11

            spans = exporter.get_finished_spans()
            matching = [s for s in spans if s.name == "test.sync_op"]
            assert len(matching) == 1
        finally:
            config.TRACING_ENABLED = original

    def test_traced_decorator_records_exceptions(self, setup_tracing):
        """@traced should record exceptions in the span."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.decorators import traced
            from opentelemetry.trace import StatusCode

            exporter = setup_tracing

            @traced("test.failing")
            async def failing_func():
                raise ValueError("test error")

            with pytest.raises(ValueError, match="test error"):
                asyncio.get_event_loop().run_until_complete(failing_func())

            spans = exporter.get_finished_spans()
            matching = [s for s in spans if s.name == "test.failing"]
            assert len(matching) == 1
            assert matching[0].status.status_code == StatusCode.ERROR
        finally:
            config.TRACING_ENABLED = original

    def test_truncation(self):
        """_truncate should truncate long values."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.decorators import _truncate

            short = "hello"
            assert _truncate(short, 100) == "hello"

            long_str = "x" * 2000
            result = _truncate(long_str, 100)
            assert len(result) < 150  # 100 + "...[truncated]"
            assert result.endswith("...[truncated]")
        finally:
            config.TRACING_ENABLED = original

    def test_sensitive_key_detection(self):
        """_is_sensitive_key should detect sensitive keys."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.decorators import _is_sensitive_key

            assert _is_sensitive_key("api_key") is True
            assert _is_sensitive_key("API_KEY") is True
            assert _is_sensitive_key("my_password") is True
            assert _is_sensitive_key("authorization") is True
            assert _is_sensitive_key("model_name") is False
            assert _is_sensitive_key("temperature") is False
        finally:
            config.TRACING_ENABLED = original


# ======================================================================
# LLMClient Tracing Helpers Tests
# LLMClient 追踪辅助方法测试
# ======================================================================

class TestLLMClientTracing:
    """Test LLMClient._start_llm_span and _end_llm_span."""

    def test_start_span_returns_none_when_disabled(self):
        """_start_llm_span returns None when tracing is disabled."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = False
        try:
            from llm.client import LLMClient
            # Create a client without actually connecting
            with patch("openai.AsyncOpenAI"):
                client = LLMClient(api_key="test", base_url="http://fake")
                result = client._start_llm_span("chat", [], 0.7, 4096)
                assert result is None
        finally:
            config.TRACING_ENABLED = original

    def test_end_span_handles_none(self):
        """_end_llm_span should be a no-op when span_ctx is None."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = False
        try:
            from llm.client import LLMClient
            with patch("openai.AsyncOpenAI"):
                client = LLMClient(api_key="test", base_url="http://fake")
                # Should not raise
                client._end_llm_span(None, success=True)
                client._end_llm_span(None, success=False, error=ValueError("test"))
        finally:
            config.TRACING_ENABLED = original

    def test_start_span_creates_context_when_enabled(self, setup_tracing):
        """_start_llm_span should return a context dict when tracing is enabled."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from llm.client import LLMClient
            with patch("openai.AsyncOpenAI"):
                client = LLMClient(api_key="test", base_url="http://fake")
                ctx = client._start_llm_span(
                    "chat",
                    [{"role": "user", "content": "hello"}],
                    0.7,
                    4096,
                )
                assert ctx is not None
                assert "span" in ctx
                assert "start_time" in ctx

                # Clean up: end the span
                client._end_llm_span(ctx, success=True)
        finally:
            config.TRACING_ENABLED = original


# ======================================================================
# BaseTool.traced_execute Tests
# BaseTool.traced_execute 测试
# ======================================================================

class TestTracedExecute:
    """Test BaseTool.traced_execute method."""

    @pytest.mark.asyncio
    async def test_traced_execute_calls_execute(self):
        """traced_execute should call the underlying execute method."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = False
        try:
            from tools.web_search import WebSearchTool
            tool = WebSearchTool()
            result = await tool.traced_execute(query="test")
            assert isinstance(result, str)
            assert len(result) > 0
        finally:
            config.TRACING_ENABLED = original

    @pytest.mark.asyncio
    async def test_traced_execute_with_tracing_enabled(self, setup_tracing):
        """traced_execute should create spans when tracing is enabled."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tools.web_search import WebSearchTool

            exporter = setup_tracing
            tool = WebSearchTool()
            result = await tool.traced_execute(query="python programming")

            assert isinstance(result, str)

            spans = exporter.get_finished_spans()
            tool_spans = [s for s in spans if "tool.execute" in s.name]
            assert len(tool_spans) >= 1

            span = tool_spans[0]
            assert span.attributes.get("tool.name") == "web_search"
            assert span.attributes.get("tool.success") is True
            assert span.attributes.get("latency_ms") is not None
        finally:
            config.TRACING_ENABLED = original


# ======================================================================
# Config Tests
# 配置测试
# ======================================================================

class TestTracingConfig:
    """Test tracing configuration module."""

    def test_config_values_loaded(self):
        """Tracing config should load values from root config."""
        from tracing.config import (
            ENABLED, BACKEND, ENDPOINT, SERVICE_NAME,
            SAMPLE_RATE, LOG_PROMPTS, MAX_ATTRIBUTE_LENGTH,
        )
        assert isinstance(ENABLED, bool)
        assert isinstance(BACKEND, str)
        assert BACKEND in ("console", "file", "rich", "otlp", "phoenix")
        assert isinstance(ENDPOINT, str)
        assert ENDPOINT.startswith("http")
        assert isinstance(SERVICE_NAME, str)
        assert len(SERVICE_NAME) > 0
        assert 0.0 <= SAMPLE_RATE <= 1.0
        assert isinstance(LOG_PROMPTS, bool)
        assert isinstance(MAX_ATTRIBUTE_LENGTH, int)
        assert MAX_ATTRIBUTE_LENGTH > 0

    def test_sensitive_keys_defined(self):
        """Sensitive keys set should be non-empty."""
        from tracing.config import SENSITIVE_KEYS
        assert len(SENSITIVE_KEYS) > 0
        assert "api_key" in SENSITIVE_KEYS
        assert "password" in SENSITIVE_KEYS


# ======================================================================
# Multicast Event Dispatch Tests
# 多播事件分发测试
# ======================================================================

class TestMulticastDispatch:
    """Test OrchestratorAgent._make_multicast."""

    def test_multicast_calls_all_callbacks(self):
        """Multicast should call all registered callbacks."""
        from agents.orchestrator import OrchestratorAgent

        calls = []

        def cb1(event, data):
            calls.append(("cb1", event, data))

        def cb2(event, data):
            calls.append(("cb2", event, data))

        multicast = OrchestratorAgent._make_multicast(cb1, cb2)
        multicast("test_event", {"key": "value"})

        assert len(calls) == 2
        assert calls[0] == ("cb1", "test_event", {"key": "value"})
        assert calls[1] == ("cb2", "test_event", {"key": "value"})

    def test_multicast_isolates_failures(self):
        """One failing callback should not prevent others from executing."""
        from agents.orchestrator import OrchestratorAgent

        calls = []

        def cb_fail(event, data):
            raise RuntimeError("intentional error")

        def cb_ok(event, data):
            calls.append(("ok", event))

        multicast = OrchestratorAgent._make_multicast(cb_fail, cb_ok)
        # Should not raise
        multicast("test_event", None)

        # cb_ok should still be called
        assert len(calls) == 1
        assert calls[0] == ("ok", "test_event")
