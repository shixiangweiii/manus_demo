"""
Context Manager - Token-aware context window management with LLM-based compression.
上下文管理器 —— 带 Token 感知的上下文窗口管理，使用 LLM 进行摘要压缩。

Monitors the total token usage of conversation messages and automatically
compresses older messages into a concise summary when the context exceeds
the configured limit, preserving the system prompt and recent messages.
监控对话消息的总 Token 使用量，当上下文超过配置限制时，
自动将较旧的消息压缩为简洁摘要，同时保留 system prompt 和最近消息。
"""

from __future__ import annotations

import logging
from typing import Any

import config

logger = logging.getLogger(__name__)


class ContextManager:
    """
    Manages conversation context to stay within token limits.
    管理对话上下文，使其保持在 Token 限制内。

    Strategy:
      1. Estimate token count for all messages.
      2. If over the limit, split into [system_prompt, old_messages, recent_messages].
      3. Summarize old_messages using the LLM.
      4. Replace old messages with a single summary message.

    压缩策略：
      1. 估算所有消息的 Token 总量。
      2. 若超限，将消息分为 [system_prompt, 旧消息, 最近消息] 三部分。
      3. 使用 LLM 将旧消息压缩为简洁摘要。
      4. 用单条摘要消息替换旧消息，缩减上下文长度。
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        reserve_recent: int = 6,  # 保留的最近消息条数（不参与压缩）
    ):
        self.max_tokens = max_tokens or config.MAX_CONTEXT_TOKENS
        self.reserve_recent = reserve_recent

    # ------------------------------------------------------------------
    # Token estimation
    # Token 估算
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        Rough token estimation: ~1 token per 3 characters for English,
        ~1 token per 2 characters for CJK-heavy text.
        Avoids the need for tiktoken dependency.

        粗略 Token 估算：英文约每 3 个字符 1 个 Token，
        中日韩字符较多时约每 2 个字符 1 个 Token。
        避免依赖 tiktoken 库，保持依赖最小化。
        """
        return max(1, len(text) // 3)

    def estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """
        Estimate total tokens for a list of messages.
        估算消息列表的总 Token 数（包含每条消息的 overhead）。
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "") or ""
            total += self.estimate_tokens(content) + 4  # 每条消息约 4 个 Token 的固定开销
        return total

    # ------------------------------------------------------------------
    # Context compression
    # 上下文压缩
    # ------------------------------------------------------------------

    async def compress_if_needed(
        self,
        messages: list[dict[str, Any]],
        llm_client: Any,
    ) -> list[dict[str, Any]]:
        """
        Check if messages exceed token limit; if so, compress older messages
        via LLM summarization while keeping the system prompt and recent messages.
        检查消息是否超过 Token 限制；若超限，通过 LLM 摘要压缩旧消息，
        同时保留 system prompt 和最近消息。

        Returns a (possibly shorter) list of messages.
        返回（可能更短的）消息列表。
        """
        total = self.estimate_messages_tokens(messages)
        if total <= self.max_tokens:
            return messages  # 未超限，直接返回

        logger.info(
            "Context too long (~%d tokens, limit %d). Compressing...",
            total, self.max_tokens,
        )

        # Separate system prompt from conversation
        # 将 system prompt 与对话历史分离（system prompt 始终保留）
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # Keep the most recent messages as-is
        # 保留最近 reserve_recent 条消息原文（提供最新上下文）
        if len(non_system) <= self.reserve_recent:
            return messages  # 消息数量不够多，无法压缩

        old_msgs = non_system[:-self.reserve_recent]     # 需要压缩的旧消息
        recent_msgs = non_system[-self.reserve_recent:]  # 保留的最近消息

        # Build text to summarize（构建待摘要的文本）
        old_text = self._messages_to_text(old_msgs)

        summary = await self._summarize(old_text, llm_client)

        # Construct compressed context（构建压缩后的上下文）
        summary_message = {
            "role": "system",
            "content": (
                f"[Context Summary - The following is a compressed summary of "
                f"earlier conversation]\n{summary}"
                # [上下文摘要 - 以下是早期对话的压缩摘要]
            ),
        }

        compressed = system_msgs + [summary_message] + recent_msgs
        new_total = self.estimate_messages_tokens(compressed)
        logger.info("Compressed context: %d tokens -> ~%d tokens", total, new_total)
        return compressed

    # ------------------------------------------------------------------
    # Internal helpers
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _messages_to_text(messages: list[dict[str, Any]]) -> str:
        """
        Convert messages to a readable text block for summarization.
        将消息列表转换为可读文本块，供 LLM 进行摘要。
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "") or ""
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    @staticmethod
    async def _summarize(text: str, llm_client: Any) -> str:
        """
        Use the LLM to produce a concise summary of conversation history.
        使用 LLM 对对话历史进行简洁摘要。
        若摘要失败（网络异常等），降级为截断原文末尾 2000 字符。
        """
        summary_prompt = [
            {
                "role": "system",
                "content": (
                    "You are a summarization assistant. Condense the following "
                    "conversation history into a brief, information-dense summary. "
                    "Preserve key facts, decisions, tool results, and action items. "
                    "Be concise but thorough."
                    # 你是一个摘要助手。将以下对话历史压缩为简洁、信息密集的摘要。
                    # 保留关键事实、决策、工具结果和行动项。简洁但全面。
                ),
            },
            {
                "role": "user",
                "content": f"Summarize this conversation:\n\n{text}",
            },
        ]
        try:
            summary = await llm_client.chat(summary_prompt, temperature=0.2, max_tokens=1024)
            return summary
        except Exception as exc:
            logger.error("Summarization failed: %s", exc)
            # Fallback: truncate to last N characters（降级方案：截断为最后 2000 字符）
            return text[-2000:] + "\n[... earlier context truncated ...]"
