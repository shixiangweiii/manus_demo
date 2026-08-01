"""
Tracing Module - OpenTelemetry-based full-lifecycle tracing for Manus Demo.
全链路追踪模块 —— 基于 OpenTelemetry 标准的运行时可观察性。

Provides:
- TracingBridge: Event-to-Span bridge (subscribes to _emit events)
- Decorators: @traced (general-purpose method tracing)
- Provider: TracerProvider factory with multi-backend support
- Exporters: FileSpanExporter, RichConsoleExporter

Usage:
    # AgentRuntime 根据 settings.toml 初始化，亦可手动初始化：
    from tracing import init_tracing, get_tracer, TracingBridge

    init_tracing()
    bridge = TracingBridge()
    tracer = get_tracer("my_module")

"""

from __future__ import annotations

from tracing.provider import init_tracing, get_tracer, shutdown_tracing
from tracing.bridge import TracingBridge
from tracing.decorators import traced


__all__ = [
    "init_tracing",
    "get_tracer",
    "shutdown_tracing",
    "TracingBridge",
    "traced",
]
