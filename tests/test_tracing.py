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
            AttrKey.GEN_AI_RESPONSE_CONTENT,
            AttrKey.GEN_AI_RESPONSE_TOOL_CALLS,
            AttrKey.GEN_AI_RESPONSE_FINISH_REASON,
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

    def test_end_span_records_response_data(self, setup_tracing):
        """_end_llm_span should set response attributes when response_data is provided."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from llm.client import LLMClient
            with patch("openai.AsyncOpenAI"):
                client = LLMClient(api_key="test", base_url="http://fake")

            exporter = setup_tracing

            ctx = client._start_llm_span("chat", [{"role": "user", "content": "hello"}], 0.7, 4096)
            response_data = {
                "response_content": "Hello! How can I help?",
                "tool_calls": None,
                "finish_reason": "stop",
            }
            client._end_llm_span(ctx, success=True, response_data=response_data)

            spans = exporter.get_finished_spans()
            llm_spans = [s for s in spans if s.name == "llm.chat"]
            assert len(llm_spans) == 1
            assert llm_spans[0].attributes.get("gen_ai.response.content") == "Hello! How can I help?"
            assert llm_spans[0].attributes.get("gen_ai.response.finish_reason") == "stop"
        finally:
            config.TRACING_ENABLED = original

    def test_end_span_records_tool_calls(self, setup_tracing):
        """_end_llm_span should set tool_calls attribute when present."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from llm.client import LLMClient
            with patch("openai.AsyncOpenAI"):
                client = LLMClient(api_key="test", base_url="http://fake")

            exporter = setup_tracing

            ctx = client._start_llm_span("chat_with_tools", [{"role": "user", "content": "search"}], 0.7, 4096)
            tool_calls_data = [
                {"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"test"}'}},
            ]
            response_data = {
                "response_content": "",
                "tool_calls": tool_calls_data,
                "finish_reason": "tool_calls",
            }
            client._end_llm_span(ctx, success=True, response_data=response_data)

            spans = exporter.get_finished_spans()
            llm_spans = [s for s in spans if s.name == "llm.chat_with_tools"]
            assert len(llm_spans) == 1
            tc_attr = llm_spans[0].attributes.get("gen_ai.response.tool_calls")
            assert tc_attr is not None
            parsed = json.loads(tc_attr)
            assert parsed[0]["function"]["name"] == "web_search"
            assert llm_spans[0].attributes.get("gen_ai.response.finish_reason") == "tool_calls"
        finally:
            config.TRACING_ENABLED = original


# ======================================================================
# Extract Response Data Tests
# 提取响应数据测试
# ======================================================================

class TestExtractResponseData:
    """Test LLMClient._extract_response_data method."""

    def test_extract_from_chat_response(self):
        """Should extract content and finish_reason from a chat response."""
        from llm.client import LLMClient
        with patch("openai.AsyncOpenAI"):
            client = LLMClient(api_key="test", base_url="http://fake")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Hello, I am an AI assistant."
        mock_resp.choices[0].message.tool_calls = None
        mock_resp.choices[0].finish_reason = "stop"

        data = client._extract_response_data(mock_resp, "chat")
        assert data["response_content"] == "Hello, I am an AI assistant."
        assert data["tool_calls"] is None
        assert data["finish_reason"] == "stop"

    def test_extract_from_chat_with_tools_response(self):
        """Should extract content, tool_calls, and finish_reason."""
        from llm.client import LLMClient
        with patch("openai.AsyncOpenAI"):
            client = LLMClient(api_key="test", base_url="http://fake")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = None
        mock_resp.choices[0].finish_reason = "tool_calls"

        tc = MagicMock()
        tc.id = "call_abc123"
        tc.type = "function"
        tc.function.name = "web_search"
        tc.function.arguments = '{"query": "python asyncio"}'
        mock_resp.choices[0].message.tool_calls = [tc]

        data = client._extract_response_data(mock_resp, "chat_with_tools")
        assert data["response_content"] == ""
        assert data["tool_calls"] is not None
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["function"]["name"] == "web_search"
        assert data["finish_reason"] == "tool_calls"

    def test_extract_handles_empty_response(self):
        """Should return empty data when response has no choices."""
        from llm.client import LLMClient
        with patch("openai.AsyncOpenAI"):
            client = LLMClient(api_key="test", base_url="http://fake")

        mock_resp = MagicMock()
        mock_resp.choices = []

        data = client._extract_response_data(mock_resp, "chat")
        assert data["response_content"] == ""
        assert data["tool_calls"] is None
        assert data["finish_reason"] == ""

    def test_extract_handles_none_resp(self):
        """Should return empty data when resp is None."""
        from llm.client import LLMClient
        with patch("openai.AsyncOpenAI"):
            client = LLMClient(api_key="test", base_url="http://fake")

        data = client._extract_response_data(None, "chat")
        assert data["response_content"] == ""
        assert data["tool_calls"] is None
        assert data["finish_reason"] == ""


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


# ======================================================================
# DAG / Emergent Bridge Event Tests
# DAG / 涌现模式 Bridge 事件测试
# ======================================================================

class TestBridgeDAGEvents:
    """Test TracingBridge handling of DAG execution events."""

    def test_dag_superstep_creates_spans(self, setup_tracing):
        """superstep + node_running/completed should create correct span hierarchy."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            exporter = setup_tracing
            bridge = TracingBridge()

            # Simulate DAG execution lifecycle
            bridge.on_event("task_start", {"task": "dag test"})
            bridge.on_event("phase", "Executing DAG (attempt 1)...")
            bridge.on_event("superstep", {"step": 0, "nodes": ["n1", "n2"], "total_ready": 2})

            # Node 1
            node1 = MagicMock()
            node1.id = "action_1"
            node1.node_type = MagicMock(value="ACTION")
            node1.description = "Search web"
            bridge.on_event("node_running", {"node": node1})
            bridge.on_event("node_completed", {"node": node1})

            # Node 2
            node2 = MagicMock()
            node2.id = "action_2"
            node2.node_type = MagicMock(value="ACTION")
            node2.description = "Execute code"
            bridge.on_event("node_running", {"node": node2})
            bridge.on_event("node_completed", {"node": node2})

            bridge.on_event("task_complete", {"answer": "done"})

            spans = exporter.get_finished_spans()
            span_names = [s.name for s in spans]

            assert "dag.super_step.0" in span_names
            assert "node.execute.action_1" in span_names
            assert "node.execute.action_2" in span_names

            # Verify parent-child: node spans should be children of superstep
            node_span = [s for s in spans if s.name == "node.execute.action_1"][0]
            assert node_span.parent is not None
        finally:
            config.TRACING_ENABLED = original

    def test_node_failed_records_error(self, setup_tracing):
        """node_failed should create a span with ERROR status."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge
            from opentelemetry.trace import StatusCode

            exporter = setup_tracing
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "test"})
            bridge.on_event("phase", "Executing DAG (attempt 1)...")
            bridge.on_event("superstep", {"step": 0, "nodes": ["n1"], "total_ready": 1})

            node = MagicMock()
            node.id = "action_1"
            node.node_type = MagicMock(value="ACTION")
            node.description = "Failing step"
            bridge.on_event("node_running", {"node": node})
            bridge.on_event("node_failed", {"node": node, "reason": "timeout exceeded"})

            bridge.on_event("task_complete", {"answer": "partial"})

            spans = exporter.get_finished_spans()
            node_spans = [s for s in spans if s.name == "node.execute.action_1"]
            assert len(node_spans) == 1
            assert node_spans[0].status.status_code == StatusCode.ERROR
        finally:
            config.TRACING_ENABLED = original


class TestBridgeEmergentEvents:
    """Test TracingBridge handling of emergent planning TODO events."""

    def test_todo_lifecycle_spans(self, setup_tracing):
        """todo_start + todo_complete should create and end TODO spans."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            exporter = setup_tracing
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "emergent test"})
            bridge.on_event("phase", "Executing emergent plan...")
            bridge.on_event("todo_list_initialized", {"items": ["todo1", "todo2"]})

            # TODO 1: success
            todo1 = MagicMock()
            todo1.id = "todo_1"
            todo1.description = "Research topic"
            todo1.retry_count = 0
            bridge.on_event("todo_start", {"todo": todo1})
            bridge.on_event("todo_complete", {"todo": todo1})

            # TODO 2: failed then blocked
            todo2 = MagicMock()
            todo2.id = "todo_2"
            todo2.description = "Write report"
            todo2.retry_count = 3
            bridge.on_event("todo_start", {"todo": todo2})
            bridge.on_event("todo_failed", {"todo": todo2, "result": MagicMock(output="error detail")})

            bridge.on_event("task_complete", {"answer": "partial"})

            spans = exporter.get_finished_spans()
            span_names = [s.name for s in spans]

            assert "todo.execute.todo_1" in span_names
            assert "todo.execute.todo_2" in span_names
        finally:
            config.TRACING_ENABLED = original

    def test_todo_blocked_records_error(self, setup_tracing):
        """todo_blocked should mark the span as ERROR."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge
            from opentelemetry.trace import StatusCode

            exporter = setup_tracing
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "test"})
            bridge.on_event("phase", "Executing emergent plan...")

            todo = MagicMock()
            todo.id = "todo_1"
            todo.description = "Blocked task"
            todo.retry_count = 3
            bridge.on_event("todo_start", {"todo": todo})
            bridge.on_event("todo_blocked", {"todo": todo, "result": MagicMock(output="stuck")})

            bridge.on_event("task_complete", {"answer": "incomplete"})

            spans = exporter.get_finished_spans()
            todo_spans = [s for s in spans if "todo.execute" in s.name]
            assert len(todo_spans) == 1
            assert todo_spans[0].status.status_code == StatusCode.ERROR
        finally:
            config.TRACING_ENABLED = original


class TestBridgeDispatchTable:
    """Test the dispatch table covers all known events."""

    def test_dispatch_table_covers_all_events(self):
        """All handled event names should have a handler in the dispatch table."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            bridge = TracingBridge()
            # Events that the bridge should handle
            expected_events = [
                # Top-level lifecycle
                "task_start", "task_complexity", "phase", "plan", "dag_created",
                "task_complete", "memory_stored", "token_usage_summary",
                # Emergent / TODO
                "todo_list_initialized", "todo_start", "todo_complete",
                "todo_failed", "todo_blocked",
                # DAG
                "superstep", "node_running", "node_completed", "node_failed",
                # Simple-path step
                "step_start", "step_complete", "step_failed",
                # Reflection / adaptation
                "reflection", "plan_adaptation",
                # v8 Goal-driven
                "goal_anchor", "goal_reflection", "goal_reanchor",
                "goal_drift_alert", "stagnation_detected",
                # v9 SubAgent
                "subagent_start", "subagent_complete", "subagent_failed",
                "subagent_timed_out", "subagent_limit_exceeded", "subagent_iteration",
                # v13 HITL
                "ask_user_prompt", "ask_user_response",
                "ask_user_timeout", "ask_user_cancelled",
            ]
            for event in expected_events:
                assert event in bridge._event_handlers, f"Event '{event}' missing from dispatch table"
                assert callable(bridge._event_handlers[event]), f"Handler for '{event}' is not callable"

            # Dead key removed in v13: adaptive_planning was never emitted by any
            # producer (all sites emit "plan_adaptation"). Ensure it stays gone.
            assert "adaptive_planning" not in bridge._event_handlers, \
                "adaptive_planning is dead — should be removed from dispatch table"
        finally:
            config.TRACING_ENABLED = original
        """Unknown events should be silently ignored."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.bridge import TracingBridge

            bridge = TracingBridge()
            # Should not raise for completely unknown events
            bridge.on_event("totally_unknown_event", {"data": 123})
            bridge.on_event("another_unknown", None)
        finally:
            config.TRACING_ENABLED = original


class TestBridgeHITLEvents:
    """Test TracingBridge handling of v13 HITL ask_user events."""

    def _make_bridge(self):
        from tracing.bridge import TracingBridge
        return TracingBridge()

    def test_ask_user_prompt_creates_span(self, setup_tracing):
        """ask_user_prompt should create a hitl.ask_user.<id> span as child of phase."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            import asyncio
            exporter = setup_tracing
            bridge = self._make_bridge()

            bridge.on_event("task_start", {"task": "hitl test"})
            bridge.on_event("phase", "Executing simple plan (attempt 1)...")

            fut = asyncio.get_event_loop().create_future() if asyncio.get_event_loop().is_running() else asyncio.new_event_loop().create_future()
            bridge.on_event("ask_user_prompt", {
                "question": "What's your favorite color?",
                "prompt_id": "abc12345",
                "response_future": fut,  # MUST NOT crash bridge
            })
            bridge.on_event("ask_user_response", {
                "prompt_id": "abc12345",
                "response": "blue",
                "prompt_count": 1,
            })
            bridge.on_event("task_complete", {"answer": "done"})

            spans = exporter.get_finished_spans()
            hitl_spans = [s for s in spans if "hitl.ask_user" in s.name]
            assert len(hitl_spans) == 1, f"expected 1 HITL span, got {[s.name for s in spans]}"

            from opentelemetry.trace import StatusCode
            assert hitl_spans[0].status.status_code == StatusCode.OK
            assert hitl_spans[0].attributes.get("hitl.prompt_id") == "abc12345"
            assert hitl_spans[0].attributes.get("hitl.question") == "What's your favorite color?"
            assert hitl_spans[0].attributes.get("hitl.response") == "blue"
            assert hitl_spans[0].attributes.get("hitl.prompt_count") == 1

            # response_future MUST NOT appear in attributes (non-serializable)
            assert "response_future" not in (hitl_spans[0].attributes or {})
        finally:
            config.TRACING_ENABLED = original

    def test_ask_user_timeout_marks_span_error(self, setup_tracing):
        """ask_user_timeout should close the HITL span with ERROR status."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from opentelemetry.trace import StatusCode
            exporter = setup_tracing
            bridge = self._make_bridge()

            bridge.on_event("task_start", {"task": "hitl timeout"})
            bridge.on_event("phase", "Executing simple plan (attempt 1)...")

            bridge.on_event("ask_user_prompt", {
                "question": "Q?",
                "prompt_id": "to-1",
                "response_future": object(),  # any unserializable obj
            })
            bridge.on_event("ask_user_timeout", {
                "prompt_id": "to-1",
                "timeout": 60,
                "prompt_count": 1,
            })
            bridge.on_event("task_complete", {"answer": "x"})

            spans = exporter.get_finished_spans()
            hitl = [s for s in spans if "hitl.ask_user" in s.name]
            assert len(hitl) == 1
            assert hitl[0].status.status_code == StatusCode.ERROR
            assert hitl[0].attributes.get("hitl.timeout_seconds") == 60
        finally:
            config.TRACING_ENABLED = original

    def test_ask_user_cancelled_marks_span_error(self, setup_tracing):
        """ask_user_cancelled (Ctrl+C/EOF) should close the HITL span with ERROR."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from opentelemetry.trace import StatusCode
            exporter = setup_tracing
            bridge = self._make_bridge()

            bridge.on_event("task_start", {"task": "hitl cancel"})
            bridge.on_event("phase", "Executing simple plan (attempt 1)...")

            bridge.on_event("ask_user_prompt", {
                "question": "Q?",
                "prompt_id": "cx-1",
                "response_future": object(),
            })
            bridge.on_event("ask_user_cancelled", {
                "prompt_id": "cx-1",
                "prompt_count": 1,
            })
            bridge.on_event("task_complete", {"answer": "x"})

            spans = exporter.get_finished_spans()
            hitl = [s for s in spans if "hitl.ask_user" in s.name]
            assert len(hitl) == 1
            assert hitl[0].status.status_code == StatusCode.ERROR
        finally:
            config.TRACING_ENABLED = original

    def test_multiple_concurrent_prompts(self, setup_tracing):
        """Multiple HITL prompts in a row should each get their own span keyed by prompt_id."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            exporter = setup_tracing
            bridge = self._make_bridge()

            bridge.on_event("task_start", {"task": "multi-hitl"})
            bridge.on_event("phase", "Executing simple plan (attempt 1)...")

            for pid in ("a1", "b2", "c3"):
                bridge.on_event("ask_user_prompt", {
                    "question": f"Q-{pid}",
                    "prompt_id": pid,
                    "response_future": object(),
                })
            # Resolve out of order
            bridge.on_event("ask_user_response", {"prompt_id": "b2", "response": "B", "prompt_count": 2})
            bridge.on_event("ask_user_response", {"prompt_id": "a1", "response": "A", "prompt_count": 1})
            bridge.on_event("ask_user_cancelled", {"prompt_id": "c3", "prompt_count": 3})
            bridge.on_event("task_complete", {"answer": "x"})

            spans = exporter.get_finished_spans()
            hitl = [s for s in spans if "hitl.ask_user" in s.name]
            assert len(hitl) == 3
            names = sorted(s.name for s in hitl)
            assert names == ["hitl.ask_user.a1", "hitl.ask_user.b2", "hitl.ask_user.c3"]
        finally:
            config.TRACING_ENABLED = original

    def test_unknown_prompt_id_is_silent(self, setup_tracing):
        """response/timeout/cancelled with unknown prompt_id should not raise."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            bridge = self._make_bridge()
            # Should not raise even with no matching prompt span
            bridge.on_event("ask_user_response", {"prompt_id": "ghost", "response": "x"})
            bridge.on_event("ask_user_timeout", {"prompt_id": "ghost", "timeout": 30})
            bridge.on_event("ask_user_cancelled", {"prompt_id": "ghost"})
        finally:
            config.TRACING_ENABLED = original


class TestBridgePhaseNesting:
    """Test that sub-component phase events do not destroy the parent phase span."""

    def test_emergent_sub_phase_keeps_parent(self, setup_tracing):
        """When emergent_planner emits 'Initializing emergent planning...' inside an
        existing 'Executing with emergent planning (TODO list)...' phase, the parent
        phase span must NOT be ended; the sub_phase should land as a span event.
        """
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            exporter = setup_tracing
            from tracing.bridge import TracingBridge
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "emergent"})
            bridge.on_event("phase", "Executing with emergent planning (TODO list)...")
            # emergent_planner inside emit:
            bridge.on_event("phase", "Initializing emergent planning...")
            bridge.on_event("phase", "Emergent planning iteration 1...")

            # TODO span emitted while we are STILL inside the emergent execution phase
            todo = MagicMock()
            todo.id = "t1"
            todo.description = "x"
            todo.retry_count = 0
            bridge.on_event("todo_start", {"todo": todo})
            bridge.on_event("todo_complete", {"todo": todo})

            bridge.on_event("phase", "Emergent planning completed.")
            bridge.on_event("task_complete", {"answer": "done"})

            spans = exporter.get_finished_spans()
            # There should be exactly ONE execution.emergent span (sub_phases must not split it)
            exec_spans = [s for s in spans if s.name == "execution.emergent"]
            assert len(exec_spans) == 1, f"expected 1 execution.emergent span, got {[s.name for s in spans]}"

            # The TODO span must be a child of execution.emergent, not of root
            todo_spans = [s for s in spans if "todo.execute" in s.name]
            assert len(todo_spans) == 1
            todo_parent = todo_spans[0].parent.span_id if todo_spans[0].parent else None
            exec_id = exec_spans[0].context.span_id
            assert todo_parent == exec_id, "TODO span lost parent — phase nesting broken"

            # Sub-phase events should be recorded on the execution.emergent span
            event_phase_texts = [
                e.attributes.get("phase", "") for e in exec_spans[0].events
                if e.name == "sub_phase"
            ]
            assert any("Initializing emergent" in t for t in event_phase_texts)
            assert any("iteration 1" in t for t in event_phase_texts)
            assert any("completed" in t for t in event_phase_texts)
        finally:
            config.TRACING_ENABLED = original

    def test_unknown_phase_keeps_current_phase(self, setup_tracing):
        """An unrecognized phase string must not close the active phase span."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            exporter = setup_tracing
            from tracing.bridge import TracingBridge
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "unknown phase test"})
            bridge.on_event("phase", "Executing simple plan (attempt 1)...")
            # Some weird phase that no rule maps:
            bridge.on_event("phase", "Doing something completely unrelated...")

            # Step span emitted while we should STILL be in execution.simple
            step = MagicMock()
            step.id = 1
            step.description = "step"
            bridge.on_event("step_start", {"step": step})
            bridge.on_event("step_complete", {"step": step})

            bridge.on_event("task_complete", {"answer": "done"})

            spans = exporter.get_finished_spans()
            simple_spans = [s for s in spans if s.name == "execution.simple"]
            assert len(simple_spans) == 1, "execution.simple span was closed by unknown phase"

            step_spans = [s for s in spans if "step.execute" in s.name]
            assert len(step_spans) == 1
            assert step_spans[0].parent.span_id == simple_spans[0].context.span_id
        finally:
            config.TRACING_ENABLED = original

    def test_goal_driven_sub_phase_keeps_parent(self, setup_tracing):
        """Goal-driven internal phases ('Building goal...', 'Compiling final answer...',
        'Goal-driven planning completed.') must NOT replace the outer execution.goal_driven span.
        """
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            exporter = setup_tracing
            from tracing.bridge import TracingBridge
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "goal-driven"})
            bridge.on_event("phase", "Executing with goal-driven planning (v8)...")
            bridge.on_event("phase", "Building goal document...")
            bridge.on_event("phase", "Planning backward from goal state...")
            bridge.on_event("phase", "Compiling final answer against goal...")
            bridge.on_event("phase", "Goal-driven planning completed.")
            bridge.on_event("task_complete", {"answer": "g"})

            spans = exporter.get_finished_spans()
            gd_spans = [s for s in spans if s.name == "execution.goal_driven"]
            assert len(gd_spans) == 1
            sub_phase_count = sum(1 for e in gd_spans[0].events if e.name == "sub_phase")
            assert sub_phase_count >= 4, "expected >=4 sub_phase events (building/planning/compiling/completed)"
        finally:
            config.TRACING_ENABLED = original

    def test_dag_adaptive_planning_is_sub_phase(self, setup_tracing):
        """dag/executor.py's 'Adaptive planning check (super-step N)...' must be
        a sub_phase event, not a real phase swap that wipes execution.dag."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            exporter = setup_tracing
            from tracing.bridge import TracingBridge
            bridge = TracingBridge()

            bridge.on_event("task_start", {"task": "dag adaptive"})
            bridge.on_event("phase", "Executing DAG (attempt 1)...")
            bridge.on_event("phase", "Adaptive planning check (super-step 2)...")
            bridge.on_event("task_complete", {"answer": "g"})

            spans = exporter.get_finished_spans()
            dag_spans = [s for s in spans if s.name == "execution.dag"]
            assert len(dag_spans) == 1
            sub_phase_events = [e for e in dag_spans[0].events if e.name == "sub_phase"]
            assert any("Adaptive planning check" in (e.attributes.get("phase", "")) for e in sub_phase_events)
        finally:
            config.TRACING_ENABLED = original


class TestFileSpanExporterDedup:
    """Test that FileSpanExporter merges multi-batch exports without duplicating spans."""

    def test_multi_batch_dedup_by_span_id(self, tmp_path):
        """Re-exporting the same span (defensive scenario) must not produce duplicates."""
        from tracing.exporters import FileSpanExporter
        import json

        out_dir = tmp_path / "traces"
        exporter = FileSpanExporter(output_dir=str(out_dir))

        # Build two fake spans sharing trace_id; second batch repeats the first one
        # plus introduces a new one (out-of-order start_time to verify sorting).
        class _FakeContext:
            def __init__(self, trace_id, span_id):
                self.trace_id = trace_id
                self.span_id = span_id

        class _FakeStatus:
            def __init__(self, name):
                from opentelemetry.trace import StatusCode
                self.status_code = StatusCode[name]

        class _FakeSpan:
            def __init__(self, span_id, name, start_ns, end_ns, status="OK"):
                self.context = _FakeContext(0xdeadbeef, span_id)
                self.parent = None
                self.name = name
                self.start_time = start_ns
                self.end_time = end_ns
                self.attributes = {}
                self.events = []
                self.status = _FakeStatus(status)

        # Batch 1: span A (start later) + span B (start earlier)
        a = _FakeSpan(0x1, "a", 2_000_000_000, 3_000_000_000)
        b = _FakeSpan(0x2, "b", 1_000_000_000, 1_500_000_000)
        result1 = exporter.export([a, b])

        # Batch 2: span A repeated + span C (latest)
        a_again = _FakeSpan(0x1, "a", 2_000_000_000, 3_000_000_000)
        c = _FakeSpan(0x3, "c", 4_000_000_000, 5_000_000_000)
        result2 = exporter.export([a_again, c])

        # Read back the file
        from opentelemetry.sdk.trace.export import SpanExportResult
        assert result1 == SpanExportResult.SUCCESS
        assert result2 == SpanExportResult.SUCCESS

        files = list(out_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))

        # 3 unique spans, not 4 (a is deduped)
        assert data["span_count"] == 3
        names = [s["name"] for s in data["spans"]]
        assert names.count("a") == 1
        assert names.count("b") == 1
        assert names.count("c") == 1

        # Sorted by start_time → b, a, c
        assert names == ["b", "a", "c"], f"spans not sorted by start_time: {names}"


class TestProviderShutdown:
    """Test provider shutdown and cleanup."""

    def test_shutdown_tracing(self):
        """shutdown_tracing should not raise when called."""
        import config
        original = config.TRACING_ENABLED
        config.TRACING_ENABLED = True
        try:
            from tracing.provider import init_tracing, shutdown_tracing, _initialized, _provider

            init_tracing()
            # Should not raise
            shutdown_tracing()

            # After shutdown, provider should be cleaned up
            from tracing.provider import _initialized as post_init, _provider as post_provider
            assert post_init is False
            assert post_provider is None
        finally:
            config.TRACING_ENABLED = original

    def test_shutdown_when_not_initialized(self):
        """shutdown_tracing should be safe to call when not initialized."""
        from tracing.provider import shutdown_tracing
        # Should not raise even if never initialized
        shutdown_tracing()
        shutdown_tracing()


class TestSharedSpanIcons:
    """Test that SPAN_ICONS is properly shared."""

    def test_span_icons_in_spans_module(self):
        """SPAN_ICONS should be defined in spans.py."""
        from tracing.spans import SPAN_ICONS, DEFAULT_SPAN_ICON
        assert isinstance(SPAN_ICONS, dict)
        assert len(SPAN_ICONS) > 0
        assert isinstance(DEFAULT_SPAN_ICON, str)

    def test_span_icons_coverage(self):
        """SPAN_ICONS should cover key span types."""
        from tracing.spans import SPAN_ICONS
        required_keys = ["task_execution", "llm", "tool", "execution", "reflector", "memory"]
        for key in required_keys:
            assert key in SPAN_ICONS, f"Missing icon key: {key}"
