"""
Tracing Configuration - Centralized tracing settings.
追踪配置 —— 集中管理所有 Tracing 相关的配置常量。

Settings are initialized from the structured settings snapshot.
"""

from __future__ import annotations

from core.settings import TracingSettings, get_settings

root_settings = get_settings()


# --- Core Settings ---
# --- 核心设置 ---

ENABLED: bool = root_settings.tracing.enabled
"""Master switch for tracing. When False, all tracing components are no-ops.
总开关。关闭时所有 tracing 组件为空操作。"""

BACKEND: str = root_settings.tracing.backend
"""Export backend: 'console' | 'file' | 'rich' | 'otlp' | 'phoenix'.
导出后端选择。"""

ENDPOINT: str = root_settings.tracing.endpoint
"""OTLP HTTP endpoint URL.
OTLP HTTP 端点地址。"""

SERVICE_NAME: str = root_settings.tracing.service_name
"""Service name for Resource identification.
服务标识名称。"""

SAMPLE_RATE: float = root_settings.tracing.sample_rate
"""Sampling rate (0.0 to 1.0). 1.0 = trace everything.
采样率。1.0 = 全量追踪。"""

LOG_PROMPTS: bool = root_settings.tracing.log_prompts
"""Whether to record full prompt/response content in spans.
是否在 Span 中记录完整的 prompt/response 内容。"""

MAX_ATTRIBUTE_LENGTH: int = root_settings.tracing.max_attribute_length
"""Maximum character length for attribute values (truncation protection).
属性值最大字符长度（截断保护）。"""


# --- Derived Settings ---
# --- 派生设置 ---

SERVICE_VERSION: str = "local"
"""Local service label embedded in Resource metadata."""

TRACE_OUTPUT_DIR: str = root_settings.tracing.output_dir
"""Expanded directory for FileSpanExporter output.
FileSpanExporter 的已展开输出目录。"""

BATCH_MAX_QUEUE_SIZE: int = 2048
"""Maximum queue size for BatchSpanProcessor.
BatchSpanProcessor 最大队列大小。"""

BATCH_MAX_EXPORT_SIZE: int = 256
"""Maximum batch size for export.
单次导出的最大批量大小。"""

BATCH_SCHEDULE_DELAY_MS: int = 5000
"""Delay between exports in milliseconds.
两次导出之间的延迟（毫秒）。"""


# --- Sensitive Data Patterns ---
# --- 敏感数据模式 ---

SENSITIVE_KEYS: set[str] = {
    "api_key", "api_secret", "token", "password",
    "credential", "secret", "authorization",
}
"""Attribute keys that should be redacted.
需要脱敏的属性键名。"""


def configure(settings: TracingSettings) -> None:
    """Apply one validated structured configuration before initialization."""
    global ENABLED, BACKEND, ENDPOINT, SERVICE_NAME, SAMPLE_RATE
    global LOG_PROMPTS, MAX_ATTRIBUTE_LENGTH, TRACE_OUTPUT_DIR
    ENABLED = settings.enabled
    BACKEND = settings.backend
    ENDPOINT = settings.endpoint
    SERVICE_NAME = settings.service_name
    SAMPLE_RATE = settings.sample_rate
    LOG_PROMPTS = settings.log_prompts
    MAX_ATTRIBUTE_LENGTH = settings.max_attribute_length
    TRACE_OUTPUT_DIR = settings.output_dir
