"""
Agentic Memory Service - High-level API for memory retrieval, storage, and consolidation.
结构化记忆服务 —— 提供检索、存储、巩固的高层 API。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import config
from schema import MemoryEntry

from memory.agentic_store import AgenticMemoryStore
from memory.models import (
    AgenticMemoryRecord,
    MemoryKind,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStatus,
)

logger = logging.getLogger(__name__)


class AgenticMemoryService:
    """
    High-level service for agentic memory operations.
    结构化记忆服务，封装检索、存储、巩固等操作。
    """

    def __init__(self, store: AgenticMemoryStore | None = None):
        self._store = store or AgenticMemoryStore()

    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        """Public search API for Memory Tools / 供记忆工具调用的公共搜索接口。"""
        results = self._store.search(query)
        for r in results:
            self._store.update_access_stats(r.record.id)
        if results:
            self._store.save()
        return results

    def add_record(self, record: AgenticMemoryRecord) -> AgenticMemoryRecord:
        """Public add API for Memory Tools / 供记忆工具调用的公共写入接口。"""
        return self._store.add(record)

    def search_for_task(self, task: str, top_k: int | None = None) -> list[MemorySearchResult]:
        """
        Search memories relevant to a task description.
        检索与任务描述相关的记忆。
        """
        top_k = top_k or config.MEMORY_SEARCH_TOP_K
        query = MemorySearchQuery(
            query=task,
            top_k=top_k,
            min_confidence=config.MEMORY_MIN_CONFIDENCE,
        )
        results = self._store.search(query)

        # Update access stats / 更新访问统计
        for r in results:
            self._store.update_access_stats(r.record.id)
        if results:
            self._store.save()

        logger.info("Memory search for '%s': %d results", task[:50], len(results))
        return results

    def format_context(self, results: list[MemorySearchResult]) -> str:
        """
        Format search results into a context string for LLM injection.
        将检索结果格式化为可注入 LLM 上下文的字符串。
        """
        if not results:
            return ""
        parts = []
        for r in results:
            rec = r.record
            short_id = rec.id[:8]
            tag_str = ",".join(rec.tags[:3]) if rec.tags else "none"
            parts.append(
                f"[{short_id}] ({rec.kind.value}) {rec.summary}\n"
                f"  confidence={rec.confidence:.2f} score={r.score:.2f} "
                f"tags={tag_str}"
            )
        return "\n".join(parts)

    def store_task_result(
        self,
        task: str,
        answer: str,
        task_id: str,
        success: bool,
        metadata: dict[str, Any] | None = None,
    ) -> AgenticMemoryRecord | None:
        """
        Store a completed task result as experiential memory.
        将完成的任务结果存储为经验记忆。
        """
        now = time.time()
        record = AgenticMemoryRecord(
            kind=MemoryKind.EXPERIENTIAL,
            content=task,
            summary=answer[:500],
            task_id=task_id,
            source="system",
            confidence=0.7 if success else 0.4,
            importance=0.6 if success else 0.3,
            tags=self._extract_tags(task),
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._store.add(record)
        logger.info("Stored task result memory: %s", task[:60])
        return record

    def consolidate_task(
        self,
        task_id: str,
        notes: str = "",
    ) -> list[AgenticMemoryRecord]:
        # TODO: v16 启用 LLM 辅助巩固时使用 config.MEMORY_LLM_CONSOLIDATION_ENABLED
        """
        Deterministic consolidation: create procedural/experiential records from task.
        确定性巩固：从任务信息创建过程性/经验性记录。
        """
        now = time.time()
        records: list[AgenticMemoryRecord] = []

        # Guard: empty task_id with no notes → nothing to consolidate
        if not task_id:
            if notes:
                record = AgenticMemoryRecord(
                    kind=MemoryKind.EXPERIENTIAL,
                    content=notes,
                    summary=notes[:200],
                    source="agent",
                    confidence=0.6,
                    importance=0.5,
                    created_at=now,
                    updated_at=now,
                )
                self._store.add(record)
                records.append(record)
            return records

        # Find existing experiential records for this task
        # 查找该任务的已有经验记录
        task_records = [
            r for r in self._store.list_records()
            if r.task_id == task_id and r.status == MemoryStatus.ACTIVE
        ]

        if task_records:
            # Create a consolidated procedural record
            # 创建合并后的过程性记录
            combined_summary = " | ".join(
                r.summary[:100] for r in task_records[:3]
            )
            tags = list({t for r in task_records for t in r.tags[:5]})[:8]

            record = AgenticMemoryRecord(
                kind=MemoryKind.PROCEDURAL,
                content=f"Consolidated from task {task_id}: {combined_summary}",
                summary=combined_summary[:200],
                task_id=task_id,
                source="system",
                confidence=0.75,
                importance=0.7,
                tags=tags,
                created_at=now,
                updated_at=now,
                metadata={"consolidated_from": [r.id for r in task_records]},
            )
            self._store.add(record)
            records.append(record)

        if notes:
            record = AgenticMemoryRecord(
                kind=MemoryKind.EXPERIENTIAL,
                content=notes,
                summary=notes[:200],
                task_id=task_id,
                source="agent",
                confidence=0.6,
                importance=0.5,
                created_at=now,
                updated_at=now,
            )
            self._store.add(record)
            records.append(record)

        logger.info("Consolidated %d records for task %s", len(records), task_id[:8])
        return records

    def revoke(self, memory_id: str, reason: str = "") -> bool:
        """Revoke a memory record / 撤销一条记忆记录"""
        success = self._store.revoke(memory_id)
        if success:
            logger.info("Revoked memory %s: %s", memory_id[:8], reason)
        return success

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tags(text: str, max_tags: int = 5) -> list[str]:
        """Extract simple tags from text for basic categorization."""
        import re as _re
        tags: list[str] = []
        # Common tech keywords / 常见技术关键词
        keywords = [
            "python", "flask", "fastapi", "django", "react", "vue",
            "docker", "kubernetes", "api", "database", "sql", "nosql",
            "web", "cli", "test", "deploy", "debug", "refactor",
            "数据分析", "机器学习", "深度学习", "前端", "后端", "微服务",
        ]
        text_lower = text.lower()
        for kw in keywords:
            if len(tags) >= max_tags:
                break
            if kw.isascii():
                # Word boundary for ASCII to avoid false positives (e.g. "web" in "webhook").
                matched = _re.search(rf'\b{_re.escape(kw)}\b', text_lower) is not None
            else:
                # CJK keywords have no \b word boundary in continuous Chinese text
                # (CJK chars are \w under Unicode), so use plain substring matching.
                # 中文关键词在连续中文里没有 \b 词边界，改用子串匹配。
                matched = kw in text_lower
            if matched:
                tags.append(kw)
        return tags
