"""
Tracer Provider Factory - Initializes OpenTelemetry SDK.
TracerProvider 工厂 —— 初始化 OpenTelemetry SDK。

Responsibilities:
- Configure Resource (service.name, service.version, environment)
- Create SpanExporter based on TRACING_BACKEND config
- Configure sampling strategy
- Provide global get_tracer() convenience method

职责：
- 配置 Resource（服务名、版本、环境）
- 根据 TRACING_BACKEND 配置创建对应的 SpanExporter
- 配置采样策略
- 提供全局 get_tracer() 便捷方法
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Tracer

from tracing import config as tracing_config

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter

logger = logging.getLogger(__name__)

# Module-level state
_initialized: bool = False
_provider: TracerProvider | None = None


def init_tracing() -> None:
    """
    Initialize the OpenTelemetry TracerProvider (idempotent).
    初始化 OpenTelemetry TracerProvider（幂等调用）。

    Creates the TracerProvider with:
    - Resource identification (service name, version)
    - Sampling strategy based on TRACING_SAMPLE_RATE
    - SpanExporter based on TRACING_BACKEND
    - BatchSpanProcessor for async export

    创建 TracerProvider：
    - Resource 标识（服务名、版本）
    - 基于 TRACING_SAMPLE_RATE 的采样策略
    - 基于 TRACING_BACKEND 的 SpanExporter
    - BatchSpanProcessor 异步导出
    """
    global _initialized, _provider

    if _initialized:
        return

    if not tracing_config.ENABLED:
        logger.debug("[Tracing] Tracing is disabled, skipping initialization")
        _initialized = True
        return

    # --- Resource ---
    resource = Resource.create({
        "service.name": tracing_config.SERVICE_NAME,
        "service.version": tracing_config.SERVICE_VERSION,
        "deployment.environment": "development",
    })

    # --- Sampler ---
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased, ALWAYS_ON

    if tracing_config.SAMPLE_RATE >= 1.0:
        sampler = ALWAYS_ON
    else:
        sampler = TraceIdRatioBased(tracing_config.SAMPLE_RATE)

    # --- TracerProvider ---
    _provider = TracerProvider(resource=resource, sampler=sampler)

    # --- Exporter & Processor ---
    exporter = _create_exporter(tracing_config.BACKEND)

    if tracing_config.BACKEND in ("console", "rich"):
        # Console/Rich: use SimpleSpanProcessor for immediate output
        # 控制台/Rich：使用 SimpleSpanProcessor 立即输出
        processor = SimpleSpanProcessor(exporter)
    else:
        # File/OTLP: use BatchSpanProcessor for async export
        # 文件/OTLP：使用 BatchSpanProcessor 异步导出
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=tracing_config.BATCH_MAX_QUEUE_SIZE,
            max_export_batch_size=tracing_config.BATCH_MAX_EXPORT_SIZE,
            schedule_delay_millis=tracing_config.BATCH_SCHEDULE_DELAY_MS,
        )

    _provider.add_span_processor(processor)

    # Set as global provider
    trace.set_tracer_provider(_provider)

    logger.info(
        "[Tracing] Initialized: backend=%s, sample_rate=%.2f, service=%s",
        tracing_config.BACKEND,
        tracing_config.SAMPLE_RATE,
        tracing_config.SERVICE_NAME,
    )
    _initialized = True


def get_tracer(name: str = "manus_demo") -> Tracer:
    """
    Get a named Tracer instance.
    获取命名的 Tracer 实例。

    Args:
        name: Tracer name (typically module path)
              Tracer 名称（通常为模块路径）

    Returns:
        A Tracer instance from the global TracerProvider.
        来自全局 TracerProvider 的 Tracer 实例。
    """
    if not _initialized:
        init_tracing()
    return trace.get_tracer(name, tracing_config.SERVICE_VERSION)


def shutdown_tracing() -> None:
    """
    Gracefully shutdown the TracerProvider, flushing pending spans.
    优雅关闭 TracerProvider，刷新待导出的 Span。
    """
    global _initialized, _provider

    if _provider is not None:
        _provider.shutdown()
        logger.info("[Tracing] TracerProvider shutdown complete")

    _initialized = False
    _provider = None


def _create_exporter(backend: str) -> "SpanExporter":
    """
    Create a SpanExporter based on the backend configuration.
    根据后端配置创建 SpanExporter。

    Args:
        backend: One of 'console', 'file', 'rich', 'otlp', 'phoenix'

    Returns:
        Configured SpanExporter instance.
    """
    if backend == "console":
        return ConsoleSpanExporter()

    elif backend == "file":
        from tracing.exporters import FileSpanExporter
        return FileSpanExporter(output_dir=tracing_config.TRACE_OUTPUT_DIR)

    elif backend == "rich":
        from tracing.exporters import RichConsoleExporter
        return RichConsoleExporter()

    elif backend in ("otlp", "phoenix"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            endpoint = tracing_config.ENDPOINT
            if backend == "phoenix" and not endpoint.endswith("/v1/traces"):
                endpoint = endpoint.rstrip("/") + "/v1/traces"
            return OTLPSpanExporter(endpoint=endpoint)
        except ImportError:
            logger.warning(
                "[Tracing] OTLP exporter not available, falling back to console. "
                "Install: pip install opentelemetry-exporter-otlp"
            )
            return ConsoleSpanExporter()

    else:
        logger.warning(
            "[Tracing] Unknown backend '%s', falling back to console", backend
        )
        return ConsoleSpanExporter()
