"""
LLM Client - Unified wrapper for OpenAI-compatible APIs.
LLM 客户端 —— OpenAI 兼容 API 的统一封装。

Supports any provider that exposes an OpenAI-compatible chat completions endpoint
(e.g., DeepSeek, Qwen/DashScope, Ollama, vLLM, etc.).
支持任何暴露 OpenAI 兼容 chat completions 接口的服务商
（如 DeepSeek、通义千问/DashScope、Ollama、vLLM 等）。

Includes optional retry with exponential backoff and structured tracing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from openai import (
    APIConnectionError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from core.redaction import redact_text, redact_value
from core.settings import AppSettings
from llm.models import LLMCallRecord
from tracing.spans import AttrKey

logger = logging.getLogger(__name__)

RETRYABLE_ERRORS = (APIConnectionError, RateLimitError, InternalServerError)

# Internal message keys that must NOT be sent to the OpenAI-compatible API.
# These are annotation fields used by loops, tracing, and context management.
_INTERNAL_MESSAGE_KEYS = frozenset({"reasoning_content"})


def _sanitize_messages_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip internal-only keys from messages before sending to the API provider.

    Returns a new list; the original messages list is not modified.
    Internal fields like reasoning_content are kept in the loop's local
    transcript for tracing/context purposes but must not leak into the
    OpenAI chat.completions.create() payload.
    """
    return [
        {k: v for k, v in msg.items() if k not in _INTERNAL_MESSAGE_KEYS}
        for msg in messages
    ]


def _extract_reasoning_content_from_tags(content: str | None) -> str:
    """从 DeepSeek R1 风格的 <think/> 标签中提取推理内容。
    Extract reasoning content from DeepSeek R1 style <think/> tags.

    DeepSeek R1 format: <think\\n...reasoning...\\n</think\\n>actual response
    Opening tag has no closing > — the newline IS the delimiter.
    Closing tag is </think\\n>.

    Returns the reasoning portion, or "" if no supported tags are found.
    """
    if not content or "<think" not in content:
        return ""
    # DeepSeek R1: <think\n...content...\n</think\n>
    match = re.search(r"<think\n(.*?)\n</think\n>", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: standard <think ...>...</think ...> with > closing
    match = re.search(r"<think[^>]*>(.*?)</think[^>]*>", content, re.DOTALL)
    return match.group(1).strip() if match else ""


def _strip_reasoning_from_content(
    content: str,
    reasoning_content: str,
) -> str:
    """Remove tagged reasoning content, returning only the response.

    For DeepSeek official API, content never contains reasoning (it's in
    reasoning_content), so content is returned as-is. For self-hosted R1
    models where <think/> tags are in content, we strip them out.
    """
    if not reasoning_content or not content:
        return content
    if "<think" in content:
        stripped = re.sub(r"<think\n.*?\n</think\n>", "", content, count=1, flags=re.DOTALL)
        if stripped == content:
            stripped = re.sub(r"<think[^>]*>.*?</think[^>]*>", "", content, count=1, flags=re.DOTALL)
        return stripped.strip()
    return content


class LLMClient:
    """
    Thin async wrapper around an OpenAI-compatible chat completions API.
    OpenAI 兼容 chat completions API 的轻量异步封装。
    所有智能体共享同一个 LLMClient 实例，统一管理 API 凭证和模型配置。

    Supports optional retry with exponential backoff.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        retry_enabled: bool | None = None,
        max_attempts: int | None = None,
        backoff_factor: float | None = None,
        timeout: float | None = None,
        token_tracking: bool | None = None,
        reasoning_token_tracking: bool | None = None,
        tracing_enabled: bool | None = None,
        tracing_log_prompts: bool | None = None,
        tracing_max_attribute_length: int | None = None,
        settings_snapshot: AppSettings | None = None,
    ):
        if settings_snapshot is None:
            raise ValueError(
                "LLMClient requires an explicit AppSettings snapshot; "
                "use LLMClient.from_settings(settings)"
            )
        defaults = settings_snapshot
        self._closed = False
        self._close_lock = asyncio.Lock()
        self.model = defaults.llm.model if model is None else model
        self._client = AsyncOpenAI(
            base_url=defaults.llm.base_url if base_url is None else base_url,
            api_key=defaults.llm.api_key if api_key is None else api_key,
            timeout=defaults.llm.timeout_seconds if timeout is None else timeout,
        )

        self.retry_enabled = retry_enabled if retry_enabled is not None else defaults.llm.retry_enabled
        self.max_attempts = (
            max_attempts
            if max_attempts is not None
            else defaults.llm.retry_max_attempts
        )
        self.backoff_factor = backoff_factor if backoff_factor is not None else defaults.llm.retry_backoff_factor
        self.token_tracking = token_tracking if token_tracking is not None else defaults.llm.token_tracking
        self.reasoning_token_tracking = (
            reasoning_token_tracking
            if reasoning_token_tracking is not None
            else defaults.llm.reasoning_token_tracking
        )
        self.tracing_enabled = tracing_enabled if tracing_enabled is not None else defaults.tracing.enabled
        self.tracing_log_prompts = (
            tracing_log_prompts
            if tracing_log_prompts is not None
            else defaults.tracing.log_prompts
        )
        self.tracing_max_attribute_length = (
            tracing_max_attribute_length
            if tracing_max_attribute_length is not None
            else defaults.tracing.max_attribute_length
        )

        # Per-call token 消耗记录列表
        self._call_records: list[LLMCallRecord] = []

        if self.retry_enabled:
            logger.info("[LLMClient] Retry enabled (max_attempts=%d, backoff=%.1f)", self.max_attempts, self.backoff_factor)
        else:
            logger.info("[LLMClient] Retry disabled")

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "LLMClient":
        """Create a client whose behavior comes from one AppSettings snapshot."""
        return cls(
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key,
            model=settings.llm.model,
            retry_enabled=settings.llm.retry_enabled,
            max_attempts=settings.llm.retry_max_attempts,
            backoff_factor=settings.llm.retry_backoff_factor,
            timeout=settings.llm.timeout_seconds,
            token_tracking=settings.llm.token_tracking,
            reasoning_token_tracking=settings.llm.reasoning_token_tracking,
            tracing_enabled=settings.tracing.enabled,
            tracing_log_prompts=settings.tracing.log_prompts,
            tracing_max_attribute_length=settings.tracing.max_attribute_length,
            settings_snapshot=settings,
        )

    async def aclose(self) -> None:
        """Close the owned async HTTP client once, with retry on close failure."""
        async with self._close_lock:
            if self._closed:
                return
            await self._client.close()
            self._closed = True

    # ------------------------------------------------------------------
    # Core chat completion
    # 基础文本对话
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller_tag: str = "",
        **kwargs: Any,
    ) -> str:
        """
        Simple chat completion that returns the assistant's text.
        简单文本对话，返回 assistant 的文本响应。
        用于需要自由文本输出的场景（如 Reflector 的反馈、ContextManager 的摘要）。

        Supports configured retry and creates an llm.chat span when tracing is enabled.
        caller_tag is recorded on the LLMCallRecord and the LLM span so
                token usage can be attributed per-agent (SubAgent / Executor / ...).
        """
        # Allow internal callers (e.g. chat_json fallback) to suppress duplicate span creation
        skip_tracing = kwargs.pop("_skip_tracing", False)
        span_ctx = None if skip_tracing else self._start_llm_span(
            "chat", messages, temperature, max_tokens, caller_tag=caller_tag,
        )
        last_error: Exception | None = None
        call_record: LLMCallRecord | None = None
        api_messages = _sanitize_messages_for_api(messages)
        try:
            attempts = self.max_attempts if self.retry_enabled else 1
            for attempt in range(attempts):
                try:
                    resp = await self._client.chat.completions.create(
                        model=self.model,
                        messages=api_messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    call_record = self._record_call(
                        resp.usage, "chat", messages, caller_tag=caller_tag
                    )
                    result = resp.choices[0].message.content or ""
                    response_data = self._extract_response_data(resp, "chat")
                    self._end_llm_span(
                        span_ctx,
                        success=True,
                        response_data=response_data,
                        call_record=call_record,
                    )
                    return result
                except RETRYABLE_ERRORS as exc:
                    last_error = exc
                    if self.retry_enabled and attempt + 1 < attempts:
                        wait_time = self.backoff_factor ** attempt
                        logger.warning("[LLMClient] Retryable error on attempt %d: %s. Waiting %.1fs...", attempt + 1, exc, wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        raise
            raise last_error or RuntimeError("LLM call failed")
        except Exception as exc:
            if call_record is None:
                call_record = self._record_call(
                    None,
                    "chat",
                    messages,
                    caller_tag=caller_tag,
                    warn_missing_usage=False,
                )
            self._end_llm_span(span_ctx, success=False, error=exc)
            raise

    # ------------------------------------------------------------------
    # Chat with function-calling / tools
    # 带工具调用的对话
    # ------------------------------------------------------------------

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller_tag: str = "",
        **kwargs: Any,
    ) -> Any:
        """
        Chat completion with OpenAI-style function calling.
        Returns the raw response message object so the caller can inspect
        tool_calls and content.

        OpenAI 风格的 function calling 对话。
        返回原始响应消息对象，调用方可检查 tool_calls 和 content。
        这是结构化工具调用循环的模型决策入口：LLM 自主选择是否调用工具，
        不依赖对字面 ``Action:`` 文本的解析。
        tool_choice="auto" 让 LLM 自行决定是否调用工具（也可能直接给出文本答案）。

        Supports configured retry and traces llm.chat_with_tools calls.
        caller_tag is recorded on the LLMCallRecord and the LLM span.
        """
        span_ctx = self._start_llm_span(
            "chat_with_tools", messages, temperature, max_tokens, caller_tag=caller_tag,
        )
        last_error: Exception | None = None
        call_record: LLMCallRecord | None = None
        api_messages = _sanitize_messages_for_api(messages)
        try:
            attempts = self.max_attempts if self.retry_enabled else 1
            for attempt in range(attempts):
                try:
                    resp = await self._client.chat.completions.create(
                        model=self.model,
                        messages=api_messages,
                        tools=tools,
                        tool_choice="auto",
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    call_record = self._record_call(
                        resp.usage,
                        "chat_with_tools",
                        messages,
                        caller_tag=caller_tag,
                    )
                    result = resp.choices[0].message
                    response_data = self._extract_response_data(resp, "chat_with_tools")
                    self._end_llm_span(
                        span_ctx,
                        success=True,
                        response_data=response_data,
                        call_record=call_record,
                    )
                    return result
                except RETRYABLE_ERRORS as exc:
                    last_error = exc
                    if self.retry_enabled and attempt + 1 < attempts:
                        wait_time = self.backoff_factor ** attempt
                        logger.warning("[LLMClient] Retryable error on attempt %d: %s. Waiting %.1fs...", attempt + 1, exc, wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        raise
            raise last_error or RuntimeError("LLM call failed")
        except Exception as exc:
            if call_record is None:
                call_record = self._record_call(
                    None,
                    "chat_with_tools",
                    messages,
                    caller_tag=caller_tag,
                    warn_missing_usage=False,
                )
            self._end_llm_span(span_ctx, success=False, error=exc)
            raise

    # ------------------------------------------------------------------
    # Convenience: structured JSON output
    # 结构化 JSON 输出（便捷方法）
    # ------------------------------------------------------------------

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        caller_tag: str = "",
        **kwargs: Any,
    ) -> Any:
        """
        Request a JSON response from the LLM.
        Falls back to extracting JSON from text if response_format is not supported.

        要求 LLM 返回 JSON 格式响应。
        若 API 不支持 response_format（如某些 Ollama 模型），
        则降级为从纯文本中提取 JSON。
        用于 Planner 生成计划、Reflector 生成评估结果等结构化输出场景。

        Creates an llm.chat_json span when tracing is enabled.
        caller_tag is recorded on the LLMCallRecord and the LLM span.
                The fallback path forwards caller_tag to chat() so attribution
                survives JSON-mode-not-supported degradation.
        """
        span_ctx = self._start_llm_span(
            "chat_json", messages, temperature, max_tokens, caller_tag=caller_tag,
        )
        response_data: dict[str, Any] | None = None
        call_record: LLMCallRecord | None = None
        records_at_start = len(self._call_records)
        api_messages = _sanitize_messages_for_api(messages)
        try:
            last_error: Exception | None = None
            attempts = self.max_attempts if self.retry_enabled else 1
            for attempt in range(attempts):
                try:
                    resp = await self._client.chat.completions.create(
                        model=self.model,
                        messages=api_messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                        **kwargs,
                    )
                    call_record = self._record_call(
                        resp.usage, "chat_json", messages, caller_tag=caller_tag
                    )
                    text = resp.choices[0].message.content or "{}"
                    logger.debug("[chat_json] Raw response: %.500s", text)
                    response_data = self._extract_response_data(resp, "chat_json")
                    break
                except BadRequestError as exc:
                    if "response_format" not in str(exc).lower():
                        raise
                    logger.warning("JSON mode not supported, falling back to plain text: %s", exc)
                    records_before = len(self._call_records)
                    text = await self.chat(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        caller_tag=caller_tag,
                        _skip_tracing=True,
                    )
                    if len(self._call_records) > records_before:
                        call_record = self._call_records[-1]
                    logger.debug("[chat_json] Fallback response: %.500s", text)
                    response_data = {
                        "response_content": text,
                        "tool_calls": None,
                        "finish_reason": "fallback",
                    }
                    break
                except RETRYABLE_ERRORS as exc:
                    last_error = exc
                    if self.retry_enabled and attempt + 1 < attempts:
                        wait_time = self.backoff_factor ** attempt
                        logger.warning(
                            "[LLMClient] Retryable JSON error on attempt %d: %s. Waiting %.1fs...",
                            attempt + 1,
                            exc,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise
            else:
                raise last_error or RuntimeError("LLM JSON call failed")

            result = self.parse_json(text)
            self._end_llm_span(
                span_ctx,
                success=True,
                response_data=response_data,
                call_record=call_record,
            )
            return result
        except Exception as exc:
            if call_record is None and len(self._call_records) == records_at_start:
                call_record = self._record_call(
                    None,
                    "chat_json",
                    messages,
                    caller_tag=caller_tag,
                    warn_missing_usage=False,
                )
            self._end_llm_span(span_ctx, success=False, error=exc)
            raise

    # ------------------------------------------------------------------
    # Helpers
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def parse_json(text: str) -> Any:
        """
        Best-effort JSON extraction from LLM output.
        从 LLM 输出中尽力提取 JSON，处理两种常见格式：
        1. 纯 JSON 字符串
        2. Markdown 代码块（```json ... ``` 或 ``` ... ```）
        无法解析时抛出 ValueError。
        """
        text = text.strip()
        # Try direct parse first（先尝试直接解析）
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try to find JSON block in markdown fences（尝试从 Markdown 代码块中提取）
        # 匹配带可选语言标签的围栏代码块（```json 或 ```）
        patterns = [
            r'```json\s*\n(.*?)\n```',  # ```json ... ```
            r'```\s*\n(.*?)\n```',      # ``` ... ```
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    continue
        raise ValueError(f"Could not parse JSON from LLM output:\n{text[:300]}")

    _parse_json = parse_json  # backward compat: agents may call the old private name

    # ------------------------------------------------------------------
    # Token Usage Tracking
    # Token 消耗追踪
    # ------------------------------------------------------------------

    def _record_call(
        self,
        usage: Any,
        call_type: str,
        messages: list[dict[str, Any]],
        caller_tag: str = "",
        warn_missing_usage: bool = True,
    ) -> LLMCallRecord:
        """Record every successful LLM call and optionally its token usage.

        caller_tag identifies which agent issued the call so the
        runtime can build a by-caller view without relying on a shared-list
        slice.  ``token_tracking`` controls token fields, not whether the call
        itself exists in ``EngineStats.llm_calls``.
        """
        prompt_summary = ""
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                text = str(content)
                prompt_summary = redact_text(text[:200] if len(text) > 200 else text)
                break
        if not prompt_summary:
            prompt_summary = call_type

        if self.token_tracking and usage is None and warn_missing_usage:
            logger.warning("[LLMClient] API response missing usage data (model=%s)", self.model)

        tracked_usage = usage if self.token_tracking else None
        prompt_tokens = getattr(tracked_usage, 'prompt_tokens', 0) or 0
        completion_tokens = getattr(tracked_usage, 'completion_tokens', 0) or 0
        total_tokens = getattr(tracked_usage, 'total_tokens', 0) or 0

        # 提取 reasoning tokens（如 OpenAI o 系列 / DeepSeek R1）。
        reasoning_tokens = 0
        if self.token_tracking and self.reasoning_token_tracking:
            # OpenAI: usage.completion_tokens_details.reasoning_tokens
            details = getattr(tracked_usage, 'completion_tokens_details', None)
            if details:
                reasoning_tokens = getattr(details, 'reasoning_tokens', 0) or 0
            # DeepSeek: usage.reasoning_tokens（部分版本直接暴露）
            if not reasoning_tokens:
                reasoning_tokens = getattr(tracked_usage, 'reasoning_tokens', 0) or 0

        record = LLMCallRecord(
            call_type=call_type,
            prompt_summary=prompt_summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reasoning_tokens=reasoning_tokens,
            model=self.model,
            caller_tag=caller_tag,
        )
        self._call_records.append(record)
        return record

    def get_call_records(self) -> list[LLMCallRecord]:
        """Return a copy of the call records list."""
        return list(self._call_records)

    def reset_usage(self) -> None:
        """Clear call records for a new task."""
        self._call_records.clear()

    # ------------------------------------------------------------------
    # Tracing helpers
    # 追踪辅助方法
    # ------------------------------------------------------------------

    def _start_llm_span(
        self,
        call_type: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        caller_tag: str = "",
    ) -> Any:
        """
        Start a tracing span for an LLM call (if tracing is enabled).
        为 LLM 调用创建追踪 Span（如果 tracing 已启用）。

        caller_tag is set as `gen_ai.caller` so trace consumers can
        filter/group LLM spans by issuing agent (SubAgent / Executor / ...).

        Returns an opaque context object (span + token) or None.
        返回一个不透明的上下文对象（span + token）或 None。
        """
        if not self.tracing_enabled:
            return None

        try:
            from opentelemetry import trace, context as otel_context
            from opentelemetry.trace import StatusCode

            span_name_map = {
                "chat": "llm.chat",
                "chat_with_tools": "llm.chat_with_tools",
                "chat_json": "llm.chat_json",
            }
            span_name = span_name_map.get(call_type, f"llm.{call_type}")

            tracer = trace.get_tracer("manus_demo.llm")
            # Use current context so the LLM span becomes a child of the active phase/task span
            span = tracer.start_span(span_name, context=otel_context.get_current())
            # Attach so that any nested spans (e.g. tool calls) become children of this LLM span
            token = otel_context.attach(trace.set_span_in_context(span))

            # Set pre-call attributes
            span.set_attribute("gen_ai.system", "openai")
            span.set_attribute("gen_ai.request.model", self.model)
            span.set_attribute("gen_ai.call_type", call_type)
            if caller_tag:
                # Per-agent attribution
                span.set_attribute("gen_ai.caller", caller_tag)
            if temperature is not None:
                span.set_attribute("gen_ai.request.temperature", temperature)
            if max_tokens is not None:
                span.set_attribute("gen_ai.request.max_tokens", max_tokens)

            # Record prompt content only when explicitly enabled, with the
            # configured attribute-length limit.
            if messages and self.tracing_log_prompts:
                parts = []
                for msg in messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    tool_calls = msg.get("tool_calls")
                    tool_call_id = msg.get("tool_call_id")
                    name = msg.get("name")
                    header = f"[{role}]"
                    if name:
                        header += f" name={name}"
                    if tool_call_id:
                        header += f" tool_call_id={tool_call_id}"
                    body_lines: list[str] = []
                    if content:
                        body_lines.append(str(content))
                    if tool_calls:
                        try:
                            tc_repr = json.dumps(
                                redact_value(tool_calls),
                                ensure_ascii=False,
                                default=str,
                            )
                        except (TypeError, ValueError):
                            tc_repr = str(tool_calls)
                        body_lines.append(f"tool_calls={tc_repr}")
                    if body_lines:
                        parts.append(f"{header}\n" + "\n".join(body_lines))
                if parts:
                    span.set_attribute(
                        "gen_ai.prompt.content",
                        redact_text("\n\n".join(parts))[
                            :self.tracing_max_attribute_length
                        ],
                    )

            import time
            return {"span": span, "token": token, "start_time": time.perf_counter()}
        except Exception:
            logger.debug("[LLMClient] Failed to start tracing span", exc_info=True)
            return None

    def _end_llm_span(
        self,
        span_ctx: Any,
        success: bool = True,
        error: Exception | None = None,
        response_data: dict[str, Any] | None = None,
        call_record: LLMCallRecord | None = None,
    ) -> None:
        """
        End a tracing span for an LLM call.
        结束 LLM 调用的追踪 Span。
        """
        if span_ctx is None:
            return

        try:
            import time
            from opentelemetry import context as otel_context
            from opentelemetry.trace import StatusCode

            span = span_ctx["span"]
            token = span_ctx.get("token")
            start_time = span_ctx["start_time"]

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            span.set_attribute("latency_ms", round(elapsed_ms, 2))

            if call_record is not None:
                if call_record.prompt_tokens > 0:
                    span.set_attribute("gen_ai.usage.input_tokens", call_record.prompt_tokens)
                if call_record.completion_tokens > 0:
                    span.set_attribute("gen_ai.usage.output_tokens", call_record.completion_tokens)
                if call_record.total_tokens > 0:
                    span.set_attribute("gen_ai.usage.total_tokens", call_record.total_tokens)
                if call_record.reasoning_tokens > 0:
                    span.set_attribute(
                        AttrKey.GEN_AI_USAGE_REASONING_TOKENS,
                        call_record.reasoning_tokens,
                    )

            # Record response content only when prompt logging is enabled.
            if response_data:
                content = response_data.get("response_content", "")
                if content and self.tracing_log_prompts:
                    span.set_attribute(
                        "gen_ai.response.content",
                        redact_text(str(content))[:self.tracing_max_attribute_length],
                    )
                tool_calls = response_data.get("tool_calls")
                if tool_calls and self.tracing_log_prompts:
                    span.set_attribute(
                        "gen_ai.response.tool_calls",
                        json.dumps(
                            redact_value(tool_calls), ensure_ascii=False, default=str
                        )[:self.tracing_max_attribute_length],
                    )
                finish_reason = response_data.get("finish_reason", "")
                if finish_reason:
                    span.set_attribute("gen_ai.response.finish_reason", finish_reason)
                reasoning_content = response_data.get("reasoning_content", "")
                if reasoning_content and self.tracing_log_prompts:
                    span.set_attribute(
                        AttrKey.GEN_AI_RESPONSE_REASONING_CONTENT,
                        redact_text(str(reasoning_content))[
                            :self.tracing_max_attribute_length
                        ],
                    )

            if success:
                span.set_status(StatusCode.OK)
            else:
                if error:
                    span.set_attribute("error.type", type(error).__name__)
                    if self.tracing_log_prompts:
                        safe_error = redact_text(str(error))
                        span.set_attribute("error.message", safe_error[:500])
                        span.record_exception(
                            RuntimeError(f"{type(error).__name__}: {safe_error}")
                        )
                        status_message = safe_error[:200]
                    else:
                        status_message = "LLM call failed"
                else:
                    status_message = "unknown error"
                span.set_status(StatusCode.ERROR, status_message)

            # End span before detaching context (OTel lifecycle convention).
            span.end()

            # Detach after span.end() to restore parent context.
            if token:
                otel_context.detach(token)
        except Exception:
            logger.debug("[LLMClient] Failed to end tracing span", exc_info=True)

    def _extract_response_data(self, resp: Any, call_type: str) -> dict[str, Any]:
        """
        Extract response content, tool_calls, and finish_reason from an LLM API response.
        从 LLM API 响应中提取 response content、tool_calls 和 finish_reason。

        Returns a dict suitable for passing to _end_llm_span as response_data.
        此提取在 _end_llm_span 之前完成，以便 resp 在 span 结束后可释放。
        """
        try:
            if not resp or not resp.choices:
                return {"response_content": "", "tool_calls": None, "finish_reason": "", "reasoning_content": ""}

            choice = resp.choices[0]
            message = choice.message

            # Response content (text the LLM returned)
            content = getattr(message, "content", None) or ""

            # DeepSeek exposes reasoning in message.reasoning_content.
            # DeepSeek 官方 API 将推理内容放在 message.reasoning_content 字段。
            reasoning_content = getattr(message, "reasoning_content", None) or ""

            # Finish reason (e.g., "stop", "tool_calls", "length")
            finish_reason = getattr(choice, "finish_reason", "") or ""

            # Tool calls (only present in chat_with_tools responses)
            tool_calls = None
            if hasattr(message, "tool_calls") and message.tool_calls:
                tool_calls = []
                for tc in message.tool_calls:
                    tool_calls.append({
                        "id": getattr(tc, "id", ""),
                        "type": getattr(tc, "type", "function"),
                        "function": {
                            "name": getattr(tc.function, "name", ""),
                            "arguments": getattr(tc.function, "arguments", ""),
                        },
                    })

            return {
                "response_content": content,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
                "reasoning_content": (
                    reasoning_content
                    if reasoning_content
                    else _extract_reasoning_content_from_tags(content)
                ),
            }
        except Exception:
            logger.debug("[LLMClient] Failed to extract response data for tracing", exc_info=True)
            return {"response_content": "", "tool_calls": None, "finish_reason": "", "reasoning_content": ""}
