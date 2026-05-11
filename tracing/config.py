"""
Tracing Configuration - Centralized tracing settings.
追踪配置 —— 集中管理所有 Tracing 相关的配置常量。

All settings are read from environment variables via the root config module.
所有配置从根 config 模块读取（最终来源于环境变量或 .env 文件）。
"""

from __future__ import annotations

import config as root_config


# --- Core Settings ---
# --- 核心设置 ---

ENABLED: bool = root_config.TRACING_ENABLED
"""Master switch for tracing. When False, all tracing components are no-ops.
总开关。关闭时所有 tracing 组件为空操作。"""

BACKEND: str = root_config.TRACING_BACKEND
"""Export backend: 'console' | 'file' | 'rich' | 'otlp' | 'phoenix'.
导出后端选择。"""

ENDPOINT: str = root_config.TRACING_ENDPOINT
"""OTLP HTTP endpoint URL.
OTLP HTTP 端点地址。"""

SERVICE_NAME: str = root_config.TRACING_SERVICE_NAME
"""Service name for Resource identification.
服务标识名称。"""

SAMPLE_RATE: float = root_config.TRACING_SAMPLE_RATE
"""Sampling rate (0.0 to 1.0). 1.0 = trace everything.
采样率。1.0 = 全量追踪。"""

LOG_PROMPTS: bool = root_config.TRACING_LOG_PROMPTS
"""Whether to record full prompt/response content in spans.
是否在 Span 中记录完整的 prompt/response 内容。"""

MAX_ATTRIBUTE_LENGTH: int = root_config.TRACING_MAX_ATTRIBUTE_LENGTH
"""Maximum character length for attribute values (truncation protection).
属性值最大字符长度（截断保护）。"""


# --- Derived Settings ---
# --- 派生设置 ---

SERVICE_VERSION: str = "7.0.0"
"""Current service version, embedded in Resource.
当前服务版本，嵌入到 Resource 中。"""

TRACE_OUTPUT_DIR: str = "traces"
"""Directory for FileSpanExporter output (relative to project root).
FileSpanExporter 输出目录（相对于项目根目录）。"""

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
