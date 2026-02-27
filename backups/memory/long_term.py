"""
Long-Term Memory - Persistent JSON-file storage for task summaries and learnings.
长期记忆 —— 基于 JSON 文件的持久化存储，保存任务摘要和学习成果。

Stores completed task summaries so the agent can recall past experiences
and avoid repeating mistakes. Uses simple keyword matching for retrieval.
存储已完成任务的摘要，让智能体能够回忆过往经验、避免重蹈覆辙。
使用简单的关键词匹配进行检索（无需向量数据库）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import config
from schema import MemoryEntry

logger = logging.getLogger(__name__)


class LongTermMemory:
    """
    JSON-file backed persistent memory with keyword-based retrieval.
    基于 JSON 文件的持久化记忆，使用关键词重叠度进行检索。
    每次调用 store() 后立即持久化到磁盘，重启后可恢复。
    """

    def __init__(self, memory_dir: str | None = None):
        self._dir = memory_dir or config.MEMORY_DIR  # 记忆文件存储目录
        os.makedirs(self._dir, exist_ok=True)
        self._file = os.path.join(self._dir, "memory.json")  # 唯一的 JSON 存储文件
        self._entries: list[MemoryEntry] = self._load()       # 启动时从磁盘加载

    # ------------------------------------------------------------------
    # Persistence
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> list[MemoryEntry]:
        """
        Load entries from disk.
        从磁盘加载所有记忆条目。若文件不存在或格式错误则返回空列表。
        """
        if not os.path.exists(self._file):
            return []
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [MemoryEntry(**e) for e in data]
        except Exception as exc:
            logger.warning("Failed to load long-term memory: %s", exc)
            return []

    def _save(self) -> None:
        """
        Persist entries to disk.
        将所有记忆条目持久化到磁盘（JSON 格式，ensure_ascii=False 支持中文）。
        """
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump([e.model_dump() for e in self._entries], f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Core operations
    # 核心操作
    # ------------------------------------------------------------------

    def store(self, entry: MemoryEntry) -> None:
        """
        Add a new memory entry and persist.
        添加一条新记忆并立即持久化到磁盘。
        """
        self._entries.append(entry)
        self._save()
        logger.info("Stored long-term memory: %s", entry.task[:60])

    def search(self, query: str, top_k: int = 3) -> list[MemoryEntry]:
        """
        Retrieve the most relevant memories using keyword overlap scoring.
        Simple but effective for a demo: counts how many query words appear
        in each entry's task + summary + learnings.

        使用关键词重叠度评分检索最相关的记忆条目。
        简单但有效的 demo 实现：统计查询词出现在每条记忆
        （task + summary + learnings）中的次数作为相关性分数。
        """
        query_words = set(query.lower().split())  # 将查询词拆分为词集合
        scored: list[tuple[float, MemoryEntry]] = []

        for entry in self._entries:
            # 将记忆条目的所有文本字段合并后与查询词做交集
            text = f"{entry.task} {entry.summary} {' '.join(entry.learnings)}".lower()
            entry_words = set(text.split())
            overlap = len(query_words & entry_words)  # 词汇重叠数量作为相关性分数
            if overlap > 0:
                scored.append((overlap, entry))

        scored.sort(key=lambda x: x[0], reverse=True)  # 按相关性降序排列
        return [e for _, e in scored[:top_k]]

    def get_all(self) -> list[MemoryEntry]:
        """
        Return all stored entries.
        返回所有存储的记忆条目。
        """
        return list(self._entries)

    def clear(self) -> None:
        """
        Remove all entries.
        清除所有记忆条目并更新磁盘文件。
        """
        self._entries.clear()
        self._save()

    # ------------------------------------------------------------------
    # Formatting
    # 格式化输出
    # ------------------------------------------------------------------

    def format_memories(self, entries: list[MemoryEntry]) -> str:
        """
        Format memory entries into a readable context string.
        将记忆条目格式化为可读的上下文字符串，注入到智能体 prompt 中。
        """
        if not entries:
            return "No relevant past experiences found."
        parts = []
        for i, e in enumerate(entries, 1):
            learnings = "; ".join(e.learnings) if e.learnings else "None"
            parts.append(
                f"[Memory {i}] Task: {e.task}\n"
                f"  Summary: {e.summary}\n"
                f"  Learnings: {learnings}"
            )
        return "\n".join(parts)

    def __len__(self) -> int:
        return len(self._entries)
