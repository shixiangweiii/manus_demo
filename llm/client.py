"""
LLM Client - Unified wrapper for OpenAI-compatible APIs.
LLM 客户端 —— OpenAI 兼容 API 的统一封装。

Supports any provider that exposes an OpenAI-compatible chat completions endpoint
(e.g., DeepSeek, Qwen/DashScope, Ollama, vLLM, etc.).
支持任何暴露 OpenAI 兼容 chat completions 接口的服务商
（如 DeepSeek、通义千问/DashScope、Ollama、vLLM 等）。

v6.0: Optional retry mechanism with exponential backoff.
      Set LLM_RETRY_ENABLED=true to enable retries on transient errors.
      Default: false (backward compatible).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI, RateLimitError, APIError, APITimeoutError

import config
from schema import LLMCallRecord

logger = logging.getLogger(__name__)

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIError)


class LLMClient:
    """
    Thin async wrapper around an OpenAI-compatible chat completions API.
    OpenAI 兼容 chat completions API 的轻量异步封装。
    所有智能体共享同一个 LLMClient 实例，统一管理 API 凭证和模型配置。

    v6.0: Optional retry mechanism with exponential backoff.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        retry_enabled: bool | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
    ):
        self.model = model or config.LLM_MODEL
        self._client = AsyncOpenAI(
            base_url=base_url or config.LLM_BASE_URL,
            api_key=api_key or config.LLM_API_KEY,
        )

        self.retry_enabled = retry_enabled if retry_enabled is not None else config.LLM_RETRY_ENABLED
        self.max_retries = max_retries if max_retries is not None else config.LLM_RETRY_MAX_ATTEMPTS
        self.backoff_factor = backoff_factor if backoff_factor is not None else config.LLM_RETRY_BACKOFF_FACTOR

        # Per-call token 消耗记录列表
        self._call_records: list[LLMCallRecord] = []

        if self.retry_enabled:
            logger.info("[LLMClient] Retry enabled (max_attempts=%d, backoff=%.1f)", self.max_retries, self.backoff_factor)
        else:
            logger.info("[LLMClient] Retry disabled (backward compatible mode)")

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

        v6.0: Supports retry with exponential backoff if LLM_RETRY_ENABLED=true.
        v7.0: Tracing integration — creates llm.chat span when TRACING_ENABLED=true.
        Wave-6: caller_tag is recorded on the LLMCallRecord and the LLM span so
                token usage can be attributed per-agent (SubAgent / Executor / ...).
        """
        # Allow internal callers (e.g. chat_json fallback) to suppress duplicate span creation
        skip_tracing = kwargs.pop("_skip_tracing", False)
        span_ctx = None if skip_tracing else self._start_llm_span(
            "chat", messages, temperature, max_tokens, caller_tag=caller_tag,
        )
        last_error: Exception | None = None
        try:
            for attempt in range(self.max_retries + 1 if self.retry_enabled else 1):
                try:
                    resp = await self._client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    if config.TOKEN_TRACKING_ENABLED:
                        self._record_call(resp.usage, "chat", messages, caller_tag=caller_tag)
                    result = resp.choices[0].message.content or ""
                    response_data = self._extract_response_data(resp, "chat")
                    self._end_llm_span(span_ctx, success=True, response_data=response_data)
                    return result
                except RETRYABLE_ERRORS as exc:
                    last_error = exc
                    if self.retry_enabled and attempt < self.max_retries:
                        wait_time = self.backoff_factor ** attempt
                        logger.warning("[LLMClient] Retryable error on attempt %d: %s. Waiting %.1fs...", attempt + 1, exc, wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        raise
            raise last_error or RuntimeError("LLM call failed")
        except Exception as exc:
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
        这是 ReAct 循环的核心：让 LLM 自主决策调用哪个工具。
        tool_choice="auto" 让 LLM 自行决定是否调用工具（也可能直接给出文本答案）。

        v6.0: Supports retry with exponential backoff if LLM_RETRY_ENABLED=true.
        v7.0: Tracing integration — creates llm.chat_with_tools span when TRACING_ENABLED=true.
        Wave-6: caller_tag is recorded on the LLMCallRecord and the LLM span.
        """
        span_ctx = self._start_llm_span(
            "chat_with_tools", messages, temperature, max_tokens, caller_tag=caller_tag,
        )
        last_error: Exception | None = None
        try:
            for attempt in range(self.max_retries + 1 if self.retry_enabled else 1):
                try:
                    resp = await self._client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    if config.TOKEN_TRACKING_ENABLED:
                        self._record_call(resp.usage, "chat_with_tools", messages, caller_tag=caller_tag)
                    result = resp.choices[0].message
                    response_data = self._extract_response_data(resp, "chat_with_tools")
                    self._end_llm_span(span_ctx, success=True, response_data=response_data)
                    return result
                except RETRYABLE_ERRORS as exc:
                    last_error = exc
                    if self.retry_enabled and attempt < self.max_retries:
                        wait_time = self.backoff_factor ** attempt
                        logger.warning("[LLMClient] Retryable error on attempt %d: %s. Waiting %.1fs...", attempt + 1, exc, wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        raise
            raise last_error or RuntimeError("LLM call failed")
        except Exception as exc:
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

        v7.0: Tracing integration — creates llm.chat_json span when TRACING_ENABLED=true.
        Wave-6: caller_tag is recorded on the LLMCallRecord and the LLM span.
                The fallback path forwards caller_tag to chat() so attribution
                survives JSON-mode-not-supported degradation.
        """
        span_ctx = self._start_llm_span(
            "chat_json", messages, temperature, max_tokens, caller_tag=caller_tag,
        )
        response_data: dict[str, Any] | None = None
        try:
            try:
                # 优先使用 JSON mode（强制 LLM 输出合法 JSON）
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},  # OpenAI JSON mode
                    **kwargs,
                )
                if config.TOKEN_TRACKING_ENABLED:
                    self._record_call(resp.usage, "chat_json", messages, caller_tag=caller_tag)
                text = resp.choices[0].message.content or "{}"
                logger.debug("[chat_json] Raw response: %.500s", text)
                response_data = self._extract_response_data(resp, "chat_json")
            except Exception:
                # 部分模型/服务不支持 response_format，降级为普通文本模式
                logger.warning("JSON mode not supported, falling back to plain text")
                text = await self.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    caller_tag=caller_tag,
                    _skip_tracing=True,
                )
                logger.debug("[chat_json] Fallback response: %.500s", text)
                response_data = {"response_content": text, "tool_calls": None, "finish_reason": "fallback"}

            result = self.parse_json(text)
            self._end_llm_span(span_ctx, success=True, response_data=response_data)
            return result
        except Exception as exc:
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
        import re
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
    ) -> None:
        """Record token usage for a single LLM API call.

        Wave-6: caller_tag identifies which agent issued the call so the
        Orchestrator can build a by_caller token view (SubAgent gets its
        own bucket separate from the parent).
        """
        if not config.TOKEN_TRACKING_ENABLED:
            return

        prompt_summary = ""
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                prompt_summary = content[:200] if len(content) > 200 else content
                break
        if not prompt_summary:
            prompt_summary = call_type

        if usage is None:
            logger.warning("[LLMClient] API response missing usage data (model=%s)", self.model)
            return

        prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
        completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
        total_tokens = getattr(usage, 'total_tokens', 0) or 0

        self._call_records.append(LLMCallRecord(
            call_type=call_type,
            prompt_summary=prompt_summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            engine=self.model,
            caller_tag=caller_tag,
        ))

    def get_call_records(self) -> list[LLMCallRecord]:
        """Return a copy of the call records list."""
        return list(self._call_records)

    def reset_usage(self) -> None:
        """Clear call records for a new task."""
        self._call_records.clear()

    # ------------------------------------------------------------------
    # Tracing Helpers (v7)
    # 追踪辅助方法（v7 新增）
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

        Wave-6: caller_tag is set as `gen_ai.caller` so trace consumers can
        filter/group LLM spans by issuing agent (SubAgent / Executor / ...).

        Returns an opaque context object (span + token) or None.
        返回一个不透明的上下文对象（span + token）或 None。
        """
        if not config.TRACING_ENABLED:
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
                # Wave-6: per-agent attribution
                span.set_attribute("gen_ai.caller", caller_tag)
            if temperature is not None:
                span.set_attribute("gen_ai.request.temperature", temperature)
            if max_tokens is not None:
                span.set_attribute("gen_ai.request.max_tokens", max_tokens)

            # Record prompt content (full, unconditional, no truncation — demo/tutorial use)
            # v13: include assistant.tool_calls and tool message correlation fields
            # (tool_call_id / name) so the trace preserves the v12 ReAct chain.
            # 把 assistant.tool_calls 与 tool 消息关联字段也带上，否则 v12 ReAct 链路在 trace 里看不见。
            if messages:
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
                            tc_repr = json.dumps(tool_calls, ensure_ascii=False, default=str)
                        except (TypeError, ValueError):
                            tc_repr = str(tool_calls)
                        body_lines.append(f"tool_calls={tc_repr}")
                    if body_lines:
                        parts.append(f"{header}\n" + "\n".join(body_lines))
                if parts:
                    span.set_attribute("gen_ai.prompt.content", "\n\n".join(parts))

            import time
            return {"span": span, "token": token, "start_time": time.perf_counter()}
        except Exception:
            logger.debug("[LLMClient] Failed to start tracing span", exc_info=True)
            return None

    def _end_llm_span(self, span_ctx: Any, success: bool = True, error: Exception | None = None, response_data: dict[str, Any] | None = None) -> None:
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

            # Record token usage from last call record.
            # Depends on _record_call being called before _end_llm_span
            # (safe in single-threaded asyncio event loop — no await between them).
            if self._call_records:
                last_record = self._call_records[-1]
                if last_record.prompt_tokens > 0:
                    span.set_attribute("gen_ai.usage.input_tokens", last_record.prompt_tokens)
                if last_record.completion_tokens > 0:
                    span.set_attribute("gen_ai.usage.output_tokens", last_record.completion_tokens)
                if last_record.total_tokens > 0:
                    span.set_attribute("gen_ai.usage.total_tokens", last_record.total_tokens)

            # Record full response data (unconditional, no truncation, no sanitization — demo/tutorial use)
            if response_data:
                content = response_data.get("response_content", "")
                if content:
                    span.set_attribute("gen_ai.response.content", content)
                tool_calls = response_data.get("tool_calls")
                if tool_calls:
                    span.set_attribute("gen_ai.response.tool_calls", json.dumps(tool_calls, ensure_ascii=False))
                finish_reason = response_data.get("finish_reason", "")
                if finish_reason:
                    span.set_attribute("gen_ai.response.finish_reason", finish_reason)

            if success:
                span.set_status(StatusCode.OK)
            else:
                if error:
                    span.set_attribute("error.type", type(error).__name__)
                    span.set_attribute("error.message", str(error)[:500])
                    span.record_exception(error)
                span.set_status(StatusCode.ERROR, str(error)[:200] if error else "unknown error")

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
                return {"response_content": "", "tool_calls": None, "finish_reason": ""}

            choice = resp.choices[0]
            message = choice.message

            # Response content (text the LLM returned)
            content = getattr(message, "content", None) or ""

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
            }
        except Exception:
            logger.debug("[LLMClient] Failed to extract response data for tracing", exc_info=True)
            return {"response_content": "", "tool_calls": None, "finish_reason": ""}
