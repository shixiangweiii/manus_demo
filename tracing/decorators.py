"""
Tracing Decorators - Declarative instrumentation for methods.
追踪装饰器 —— 方法级别的声明式埋点。

Provides:
- @traced(span_name, attributes): General-purpose method tracing
- _truncate / _is_sensitive_key / _safe_set_attribute: Shared helpers

LLM and tool call tracing is handled inline by LLMClient._start_llm_span / _end_llm_span
and BaseTool.traced_execute respectively, using these shared helpers for attribute handling.
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
