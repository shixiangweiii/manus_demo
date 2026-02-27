"""
Short-Term Memory - Sliding window buffer for recent conversation messages.
短期记忆 —— 最近对话消息的滑动窗口缓冲区。

Keeps the most recent N messages in memory to provide immediate context
to agents without exceeding token limits.
在内存中保留最近 N 条消息，为智能体提供即时上下文，同时避免超过 Token 限制。
"""

from __future__ import annotations

import logging
from typing import Any

import config

logger = logging.getLogger(__name__)


class ShortTermMemory:
    """
    In-memory sliding window that retains the last `window_size` messages.
    内存滑动窗口，保留最近 `window_size` 条消息。
    当消息数量超过窗口大小时，自动淘汰最旧的消息（FIFO）。
    """

    def __init__(self, window_size: int | None = None):
        self.window_size = window_size or config.SHORT_TERM_WINDOW  # 窗口大小，默认读取配置
        self._messages: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Core operations
    # 核心操作
    # ------------------------------------------------------------------

    def add(self, message: dict[str, Any]) -> None:
        """
        Append a message and evict the oldest if over the window size.
        追加一条消息，若超出窗口大小则淘汰最旧的消息。
        """
        self._messages.append(message)
        if len(self._messages) > self.window_size:
            evicted = len(self._messages) - self.window_size
            self._messages = self._messages[evicted:]  # 切片淘汰旧消息
            logger.debug("Short-term memory evicted %d old messages", evicted)

    def get_messages(self) -> list[dict[str, Any]]:
        """
        Return all messages currently in the window.
        返回当前窗口内的所有消息副本。
        """
        return list(self._messages)

    def get_recent(self, n: int = 5) -> list[dict[str, Any]]:
        """
        Return the most recent n messages.
        返回最近 n 条消息（用于快速获取最新上下文）。
        """
        return list(self._messages[-n:])

    def clear(self) -> None:
        """
        Clear all stored messages.
        清空所有存储的消息（通常在新会话开始时调用）。
        """
        self._messages.clear()
        logger.debug("Short-term memory cleared")

    # ------------------------------------------------------------------
    # Utilities
    # 工具方法
    # ------------------------------------------------------------------

    def to_text(self) -> str:
        """
        Serialize all messages into a readable text block.
        将所有消息序列化为可读文本块，用于摘要或调试输出。
        """
        lines = []
        for msg in self._messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"ShortTermMemory(size={len(self._messages)}, window={self.window_size})"
