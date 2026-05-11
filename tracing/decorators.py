"""
Tracing Decorators - Declarative instrumentation for methods.
追踪装饰器 —— 方法级别的声明式埋点。

Provides:
- @traced(span_name, attributes): General-purpose method tracing
- @traced_llm_call: LLM call tracing (auto-records model, tokens, latency)
- @traced_tool_call: Tool call tracing (auto-records tool_name, params, result)

All decorators:
- Support both sync and async functions
- Degrade to no-op when TRACING_ENABLED=false (checked at import time in __init__.py)
- Properly propagate exceptions while recording them in the span
- Truncate attribute values per TRACING_MAX_ATTRIBUTE_LENGTH

所有装饰器：
- 支持同步和异步函数
- TRACING_ENABLED=false 时退化为空操作（在 __init__.py 导入时检查）
- 正确传播异常同时记录到 Span
- 根据 TRACING_MAX_ATTRIBUTE_LENGTH 截断属性值
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable

from opentelemetry import trace
from opentelemetry.trace import StatusCode, Span

from tracing import config as tracing_config
from tracing.spans import SpanName, AttrKey

logger = logging.getLogger(__name__)


def _truncate(value: Any, max_length: int | None = None) -> str:
    """
    Truncate a value to the configured max attribute length.
    将值截断到配置的最大属性长度。
    """
    max_len = max_length or tracing_config.MAX_ATTRIBUTE_LENGTH
    text = str(value)
    if len(text) > max_len:
        return text[:max_len] + "...[truncated]"
    return text


def _is_sensitive_key(key: str) -> bool:
    """
    Check if an attribute key contains sensitive information.
    检查属性键是否包含敏感信息。
    """
    key_lower = key.lower()
    return any(s in key_lower for s in tracing_config.SENSITIVE_KEYS)


def _safe_set_attribute(span: Span, key: str, value: Any) -> None:
    """
    Safely set a span attribute with truncation and sensitive data protection.
    安全地设置 Span 属性，带截断和敏感数据保护。
    """
    if _is_sensitive_key(key):
        span.set_attribute(key, "[REDACTED]")
        return

    if isinstance(value, (str,)):
        span.set_attribute(key, _truncate(value))
    elif isinstance(value, (int, float, bool)):
        span.set_attribute(key, value)
    elif value is None:
        pass  # Skip None values
    else:
        span.set_attribute(key, _truncate(value))


def traced(span_name: str = "", attributes: dict[str, Any] | None = None):
    """
    General-purpose tracing decorator. Supports both sync and async functions.
    通用追踪装饰器，支持同步和异步函数。

    Args:
        span_name: Name for the span. If empty, uses "{class_name}.{method_name}".
                   Span 名称。为空时使用 "{类名}.{方法名}"。
        attributes: Static attributes to add to the span.
                    要添加到 Span 的静态属性。

    Usage:
        @traced("planner.classify_task")
        async def classify_task(self, task: str) -> str: ...

        @traced()  # auto-derives span name
        def process(self, data): ...
    """
    def decorator(func: Callable) -> Callable:
        # Determine span name
        name = span_name or f"{func.__qualname__}"

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                tracer = trace.get_tracer("manus_demo.traced")
                with tracer.start_as_current_span(name) as span:
                    # Set static attributes
                    if attributes:
                        for key, value in attributes.items():
                            _safe_set_attribute(span, key, value)

                    # Record start time for latency
                    start = time.perf_counter()

                    try:
                        result = await func(*args, **kwargs)
                        span.set_status(StatusCode.OK)
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                        return result
                    except Exception as exc:
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                        span.set_status(StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                tracer = trace.get_tracer("manus_demo.traced")
                with tracer.start_as_current_span(name) as span:
                    if attributes:
                        for key, value in attributes.items():
                            _safe_set_attribute(span, key, value)

                    start = time.perf_counter()

                    try:
                        result = func(*args, **kwargs)
                        span.set_status(StatusCode.OK)
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                        return result
                    except Exception as exc:
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                        span.set_status(StatusCode.ERROR, str(exc))
                        span.record_exception(exc)
                        raise

            return sync_wrapper

    return decorator


def traced_llm_call(func: Callable) -> Callable:
    """
    LLM call tracing decorator. Auto-records model, tokens, latency, retries.
    LLM 调用追踪装饰器。自动记录 model、tokens、latency、重试次数。

    Designed for LLMClient methods (chat, chat_with_tools, chat_json).
    为 LLMClient 方法（chat, chat_with_tools, chat_json）设计。

    Extracts from the LLMClient instance (self):
    - self.model -> gen_ai.request.model
    - method name -> gen_ai.call_type

    Extracts from kwargs/args:
    - temperature -> gen_ai.request.temperature
    - max_tokens -> gen_ai.request.max_tokens
    - messages (first user content, if LOG_PROMPTS enabled) -> gen_ai.prompt.content

    The decorator inspects the OpenAI response to extract token usage.
    装饰器检查 OpenAI 响应以提取 token 用量。

    Usage:
        class LLMClient:
            @traced_llm_call
            async def chat(self, messages, temperature=0.7, ...): ...
    """
    # Determine span name from method name
    method_name = func.__name__
    span_name_map = {
        "chat": SpanName.LLM_CHAT,
        "chat_with_tools": SpanName.LLM_CHAT_WITH_TOOLS,
        "chat_json": SpanName.LLM_CHAT_JSON,
    }
    span_name = span_name_map.get(method_name, f"llm.{method_name}")

    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        tracer = trace.get_tracer("manus_demo.llm")

        with tracer.start_as_current_span(span_name) as span:
            start = time.perf_counter()

            # --- Pre-call attributes ---
            # Model from self (LLMClient instance)
            model = getattr(self, "model", "unknown")
            span.set_attribute(AttrKey.GEN_AI_SYSTEM, "openai")
            span.set_attribute(AttrKey.GEN_AI_REQUEST_MODEL, model)
            span.set_attribute(AttrKey.GEN_AI_CALL_TYPE, method_name)

            # Temperature and max_tokens from kwargs or args
            # Method signatures: chat(self, messages, temperature=0.7, max_tokens=4096)
            messages = args[0] if args else kwargs.get("messages", [])
            temperature = kwargs.get("temperature", args[1] if len(args) > 1 else None)
            max_tokens = kwargs.get("max_tokens", args[2] if len(args) > 2 else None)

            if temperature is not None:
                span.set_attribute(AttrKey.GEN_AI_REQUEST_TEMPERATURE, temperature)
            if max_tokens is not None:
                span.set_attribute(AttrKey.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)

            # Optionally record prompt content
            if tracing_config.LOG_PROMPTS and messages:
                for msg in messages:
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        _safe_set_attribute(span, AttrKey.GEN_AI_PROMPT_CONTENT, content)
                        break

            # Track retry count
            retry_count = 0

            try:
                result = await func(self, *args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                span.set_status(StatusCode.OK)

                # --- Post-call: extract token usage from result ---
                # For chat(): result is a string (content extracted already)
                # For chat_with_tools(): result is the message object
                # Token usage is recorded via LLMClient._record_call, but we
                # also check if the response is available
                # We access the last call record from the client
                if hasattr(self, "_call_records") and self._call_records:
                    last_record = self._call_records[-1]
                    if last_record.prompt_tokens > 0:
                        span.set_attribute(AttrKey.GEN_AI_USAGE_INPUT_TOKENS, last_record.prompt_tokens)
                    if last_record.completion_tokens > 0:
                        span.set_attribute(AttrKey.GEN_AI_USAGE_OUTPUT_TOKENS, last_record.completion_tokens)
                    if last_record.total_tokens > 0:
                        span.set_attribute(AttrKey.GEN_AI_USAGE_TOTAL_TOKENS, last_record.total_tokens)

                return result

            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                span.set_attribute(AttrKey.ERROR_TYPE, type(exc).__name__)
                span.set_attribute(AttrKey.ERROR_MESSAGE, _truncate(str(exc), 500))
                span.set_status(StatusCode.ERROR, str(exc)[:200])
                span.record_exception(exc)
                raise

    return wrapper


def traced_tool_call(func: Callable) -> Callable:
    """
    Tool call tracing decorator. Auto-records tool_name, parameters, result, latency.
    工具调用追踪装饰器。自动记录 tool_name、parameters、result、latency。

    Designed for BaseTool.execute() or wrapper functions that call tools.
    为 BaseTool.execute() 或调用工具的包装函数设计。

    Extracts from the tool instance (self):
    - self.name -> tool.name

    Extracts from kwargs:
    - All kwargs -> tool.parameters (JSON serialized)

    Usage:
        class WebSearchTool(BaseTool):
            @traced_tool_call
            async def execute(self, query: str, max_results: int = 5) -> str: ...
    """
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        tracer = trace.get_tracer("manus_demo.tool")

        # Get tool name from self
        tool_name = getattr(self, "name", func.__qualname__)
        full_span_name = f"{SpanName.TOOL_EXECUTE}.{tool_name}"

        with tracer.start_as_current_span(full_span_name) as span:
            start = time.perf_counter()

            # --- Pre-call attributes ---
            span.set_attribute(AttrKey.TOOL_NAME, tool_name)

            # Record parameters (kwargs as JSON)
            import json
            if kwargs:
                try:
                    params_str = json.dumps(kwargs, ensure_ascii=False, default=str)
                    _safe_set_attribute(span, AttrKey.TOOL_PARAMETERS, params_str)
                except (TypeError, ValueError):
                    span.set_attribute(AttrKey.TOOL_PARAMETERS, str(kwargs))

            try:
                result = await func(self, *args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000

                span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                span.set_attribute(AttrKey.TOOL_SUCCESS, True)
                span.set_status(StatusCode.OK)

                # Record result size
                if isinstance(result, str):
                    span.set_attribute(AttrKey.TOOL_RESULT_SIZE, len(result))
                    # Optionally record result content (truncated)
                    if tracing_config.LOG_PROMPTS:
                        _safe_set_attribute(span, AttrKey.TOOL_RESULT, result)

                return result

            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                span.set_attribute(AttrKey.LATENCY_MS, round(elapsed_ms, 2))
                span.set_attribute(AttrKey.TOOL_SUCCESS, False)
                span.set_attribute(AttrKey.TOOL_ERROR, _truncate(str(exc), 500))
                span.set_status(StatusCode.ERROR, str(exc)[:200])
                span.record_exception(exc)
                raise

    return wrapper
