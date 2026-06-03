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

    def __init__(self, store: AgenticMemoryStore | None = None, llm_client: Any = None):
        self._store = store or AgenticMemoryStore()
        # Optional LLMClient for LLM-assisted consolidation (v15.x).
        # None → deterministic consolidation only. / 无 client 时仅走确定性巩固。
        self._llm = llm_client

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

    async def consolidate_task(
        self,
        task_id: str,
        notes: str = "",
    ) -> list[AgenticMemoryRecord]:
        """
        Consolidate a task's memories into a higher-level record.
        将某任务的记忆巩固为更高层的记录。

        When ``config.MEMORY_LLM_CONSOLIDATION_ENABLED`` is on AND an LLMClient was
        injected, the procedural summary/tags are distilled by the LLM; otherwise
        (or on any LLM failure) it falls back to deterministic concatenation.
        当 MEMORY_LLM_CONSOLIDATION_ENABLED 开启且注入了 LLMClient 时，
        由 LLM 提炼过程性摘要/标签；否则（或 LLM 失败时）降级为确定性拼接。
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
            # Default deterministic consolidation / 默认确定性巩固
            combined_summary = " | ".join(
                r.summary[:100] for r in task_records[:3]
            )
            tags = list({t for r in task_records for t in r.tags[:5]})[:8]
            llm_used = False

            # LLM-assisted distillation (opt-in) / LLM 辅助提炼（按需开启）
            if config.MEMORY_LLM_CONSOLIDATION_ENABLED and self._llm is not None:
                distilled = await self._llm_consolidate(task_records)
                if distilled is not None:
                    combined_summary = distilled.get("summary") or combined_summary
                    llm_tags = distilled.get("tags")
                    if isinstance(llm_tags, list) and llm_tags:
                        tags = [str(t) for t in llm_tags][:8]
                    llm_used = True
                    logger.info(
                        "LLM-assisted consolidation for task %s (%d source records)",
                        task_id[:8], len(task_records),
                    )

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
                metadata={
                    "consolidated_from": [r.id for r in task_records],
                    "llm_consolidated": llm_used,
                },
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

    async def _llm_consolidate(
        self,
        task_records: list[AgenticMemoryRecord],
    ) -> dict[str, Any] | None:
        """
        Use the LLM to distill a consolidated summary + tags from task records.
        用 LLM 从任务记录中提炼合并摘要与标签。
        Returns ``{"summary": str, "tags": list[str]}`` or None on failure.
        失败时返回 None，调用方降级为确定性巩固。
        """
        source = "\n".join(
            f"- [{r.kind.value}] {r.summary[:200]}" for r in task_records[:6]
        )
        prompt = (
            "你是记忆巩固助手。下面是同一任务产生的若干条记忆，请将它们合并为一条"
            "简洁、可复用的过程性知识。\n\n"
            f"记忆条目：\n{source}\n\n"
            "请只返回 JSON：{\"summary\": \"<不超过150字的合并摘要>\", "
            "\"tags\": [\"<最多5个关键词，中英文皆可>\"]}"
        )
        try:
            data = await self._llm.chat_json(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=400,
                caller_tag="MemoryConsolidation",
            )
            return data if isinstance(data, dict) else None
        except Exception:
            logger.debug("[AgenticMemory] LLM consolidation failed", exc_info=True)
            return None

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
                # re.ASCII makes \w/\b consider ASCII only, so an adjacent CJK char
                # (e.g. "python的") counts as a non-word boundary and DOES match.
                # 加 re.ASCII 让 \b 只认 ASCII，紧邻的中文字符视为非词字符 → "python的" 可命中。
                matched = _re.search(rf'\b{_re.escape(kw)}\b', text_lower, _re.ASCII) is not None
            else:
                # CJK keywords have no \b word boundary in continuous Chinese text
                # (CJK chars are \w under Unicode), so use plain substring matching.
                # 中文关键词在连续中文里没有 \b 词边界，改用子串匹配。
                matched = kw in text_lower
            if matched:
                tags.append(kw)
        return tags
