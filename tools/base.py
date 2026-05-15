"""
Base Tool - Abstract interface for all tools in the Manus Demo.
BaseTool —— Manus Demo 中所有工具的抽象接口。

Each tool exposes:
  - name / description for the LLM to understand its purpose
  - parameters_schema (JSON Schema) for OpenAI function-calling
  - execute() to actually run the tool

每个工具暴露：
  - name / description：供 LLM 理解工具用途（写入 system prompt 或 function calling schema）
  - parameters_schema（JSON Schema）：OpenAI function calling 的参数描述
  - execute()：实际执行工具逻辑
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """
    Abstract base class for all agent tools.
    所有 Agent 工具的抽象基类。
    所有具体工具（web_search、execute_python、file_ops、execute_shell 等）都继承自此类。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique tool name used in function calls.
        工具唯一名称，用于 function calling 中的函数名标识。
        """

    @property
    @abstractmethod
    def description(self) -> str:
        """
        Human-readable description of what the tool does.
        工具功能的人类可读描述，LLM 根据此描述判断何时调用该工具。
        """

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """
        JSON Schema describing the tool's parameters.
        描述工具参数的 JSON Schema，供 LLM 生成正确的函数调用参数。
        """

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        Run the tool and return a string result.
        执行工具并返回字符串结果（所有工具均返回字符串，便于 LLM 处理）。
        """

    async def traced_execute(self, **kwargs: Any) -> str:
        """
        Execute the tool with tracing instrumentation (v7).
        带追踪埋点的工具执行方法（v7 新增）。

        This is the preferred entry point when tracing is enabled.
        When TRACING_ENABLED=false, delegates directly to execute() with zero overhead.

        Callers (ExecutorAgent, ReActEngine) should call traced_execute()
        instead of execute() to get automatic tracing.

        当 TRACING_ENABLED=true 时，创建 tool.execute.{name} Span 包装执行。
        当 TRACING_ENABLED=false 时，直接委托给 execute()，零开销。
        """
        import config as _config
        if not _config.TRACING_ENABLED:
            return await self.execute(**kwargs)

        # Tracing-enabled path
        import time
        try:
            from opentelemetry import trace
            from opentelemetry.trace import StatusCode

            tracer = trace.get_tracer("manus_demo.tool")
            span_name = f"tool.execute.{self.name}"

            with tracer.start_as_current_span(span_name) as span:
                start = time.perf_counter()
                span.set_attribute("tool.name", self.name)

                # Record parameters (sanitized and truncated)
                import json
                if kwargs:
                    try:
                        sanitized = self._sanitize_params(kwargs)
                        params_str = json.dumps(sanitized, ensure_ascii=False, default=str)
                        max_len = _config.TRACING_MAX_ATTRIBUTE_LENGTH
                        if len(params_str) > max_len:
                            params_str = params_str[:max_len] + "...[truncated]"
                        span.set_attribute("tool.parameters", params_str)
                    except (TypeError, ValueError):
                        span.set_attribute("tool.parameters", str(kwargs)[:500])

                try:
                    result = await self.execute(**kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    span.set_attribute("latency_ms", round(elapsed_ms, 2))

                    # Detect Error:-prefixed string returns (tools that swallow
                    # exceptions and return error strings). Reflect failure in
                    # the span so dashboards/traces are not misled.
                    is_error_str = isinstance(result, str) and result.startswith("Error:")

                    if isinstance(result, str):
                        span.set_attribute("tool.result_size", len(result))

                    if is_error_str:
                        span.set_attribute("tool.success", False)
                        span.set_attribute("tool.error", str(result)[:500])
                        span.set_status(StatusCode.ERROR, str(result)[:200])
                    else:
                        span.set_attribute("tool.success", True)
                        span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    span.set_attribute("latency_ms", round(elapsed_ms, 2))
                    span.set_attribute("tool.success", False)
                    span.set_attribute("tool.error", str(exc)[:500])
                    span.set_status(StatusCode.ERROR, str(exc)[:200])
                    span.record_exception(exc)
                    raise
        except ImportError:
            # OpenTelemetry not installed, fallback to direct execution
            return await self.execute(**kwargs)

    @staticmethod
    def _sanitize_params(params: dict) -> dict:
        """
        Recursively redact sensitive fields in tool parameters before recording.
        递归清洗工具参数中的敏感字段，避免在 Span 属性中泄露。
        """
        from tracing.config import SENSITIVE_KEYS

        sanitized = {}
        for key, value in params.items():
            if any(s in key.lower() for s in SENSITIVE_KEYS):
                sanitized[key] = "[REDACTED]"
            elif isinstance(value, dict):
                sanitized[key] = BaseTool._sanitize_params(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    BaseTool._sanitize_params(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                sanitized[key] = value
        return sanitized

    # ------------------------------------------------------------------
    # OpenAI function-calling schema
    # OpenAI function calling 格式转换
    # ------------------------------------------------------------------

    def to_openai_tool(self) -> dict[str, Any]:
        """
        Convert to the OpenAI tools format:
        转换为 OpenAI tools 格式，直接传入 chat completions API 的 tools 参数：

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": { ... JSON Schema ... }
            }
        }
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
