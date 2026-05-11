"""
Tracing Module - OpenTelemetry-based full-lifecycle tracing for Manus Demo.
全链路追踪模块 —— 基于 OpenTelemetry 标准的运行时可观察性。

Provides:
- TracingBridge: Event-to-Span bridge (subscribes to _emit events)
- Decorators: @traced, @traced_llm_call, @traced_tool_call
- Provider: TracerProvider factory with multi-backend support
- Exporters: FileSpanExporter, RichConsoleExporter

Usage:
    # 在 OrchestratorAgent 中自动初始化（通过 config.TRACING_ENABLED）
    # 或手动初始化：
    from tracing import init_tracing, get_tracer, TracingBridge

    init_tracing()
    bridge = TracingBridge()
    tracer = get_tracer("my_module")

v7.0: Initial implementation.
"""

from __future__ import annotations

import config as _config

# Lazy imports to avoid loading OpenTelemetry when tracing is disabled
# 延迟导入：TRACING_ENABLED=false 时不加载 OpenTelemetry，零开销
if _config.TRACING_ENABLED:
    from tracing.provider import init_tracing, get_tracer, shutdown_tracing
    from tracing.bridge import TracingBridge
    from tracing.decorators import traced, traced_llm_call, traced_tool_call
else:
    # No-op stubs when tracing is disabled
    # Tracing 关闭时的空实现桩
    def init_tracing() -> None:
        """No-op when tracing is disabled."""
        pass

    def get_tracer(name: str = ""):
        """Returns None when tracing is disabled."""
        return None

    def shutdown_tracing() -> None:
        """No-op when tracing is disabled."""
        pass

    class TracingBridge:
        """No-op bridge when tracing is disabled."""
        def on_event(self, event: str, data=None) -> None:
            pass

    def traced(span_name: str = "", attributes: dict = None):
        """No-op decorator when tracing is disabled."""
        def decorator(func):
            return func
        return decorator

    def traced_llm_call(func):
        """No-op decorator when tracing is disabled."""
        return func

    def traced_tool_call(func):
        """No-op decorator when tracing is disabled."""
        return func


__all__ = [
    "init_tracing",
    "get_tracer",
    "shutdown_tracing",
    "TracingBridge",
    "traced",
    "traced_llm_call",
    "traced_tool_call",
]
