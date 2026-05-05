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
        **kwargs: Any,
    ) -> str:
        """
        Simple chat completion that returns the assistant's text.
        简单文本对话，返回 assistant 的文本响应。
        用于需要自由文本输出的场景（如 Reflector 的反馈、ContextManager 的摘要）。

        v6.0: Supports retry with exponential backoff if LLM_RETRY_ENABLED=true.
        """
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1 if self.retry_enabled else 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                return resp.choices[0].message.content or ""
            except RETRYABLE_ERRORS as exc:
                last_error = exc
                if self.retry_enabled and attempt < self.max_retries:
                    wait_time = self.backoff_factor ** attempt
                    logger.warning("[LLMClient] Retryable error on attempt %d: %s. Waiting %.1fs...", attempt + 1, exc, wait_time)
                    await asyncio.sleep(wait_time)
                else:
                    raise
        raise last_error or RuntimeError("LLM call failed")

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
        """
        last_error: Exception | None = None
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
                return resp.choices[0].message
            except RETRYABLE_ERRORS as exc:
                last_error = exc
                if self.retry_enabled and attempt < self.max_retries:
                    wait_time = self.backoff_factor ** attempt
                    logger.warning("[LLMClient] Retryable error on attempt %d: %s. Waiting %.1fs...", attempt + 1, exc, wait_time)
                    await asyncio.sleep(wait_time)
                else:
                    raise
        raise last_error or RuntimeError("LLM call failed")

    # ------------------------------------------------------------------
    # Convenience: structured JSON output
    # 结构化 JSON 输出（便捷方法）
    # ------------------------------------------------------------------

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Any:
        """
        Request a JSON response from the LLM.
        Falls back to extracting JSON from text if response_format is not supported.

        要求 LLM 返回 JSON 格式响应。
        若 API 不支持 response_format（如某些 Ollama 模型），
        则降级为从纯文本中提取 JSON。
        用于 Planner 生成计划、Reflector 生成评估结果等结构化输出场景。
        """
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
            text = resp.choices[0].message.content or "{}"
        except Exception:
            # 部分模型/服务不支持 response_format，降级为普通文本模式
            logger.warning("JSON mode not supported, falling back to plain text")
            text = await self.chat(messages, temperature=temperature, max_tokens=max_tokens)

        return self.parse_json(text)

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
