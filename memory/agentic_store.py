"""
Agentic Memory Store - JSON-file backed structured memory with bilingual retrieval.
结构化记忆存储 —— 基于 JSON 文件、支持中英双语检索的 Agentic Memory。
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any

import config
from schema import MemoryEntry

from memory.models import (
    AgenticMemoryRecord,
    MemoryKind,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStatus,
)
# Shared bilingual tokenizer (reused by LongTermMemory too) / 共享双语分词器
# Kept exported as `_tokenize` for backward-compatible imports & tests.
# 保留 `_tokenize` 名以兼容既有 import 与单测。
from memory.text_utils import bilingual_tokenize as _tokenize

logger = logging.getLogger(__name__)

# Scoring weights / 排序权重
W_KEYWORD = 0.50
W_TAG = 0.15
W_CONFIDENCE = 0.15
W_RECENCY = 0.10
W_IMPORTANCE = 0.05
W_LINK = 0.05

# Recency decay lambda (per day) / 时间衰减系数（每天）
RECENCY_LAMBDA = 0.1

# Common CJK connective/function bigrams that carry little semantic weight.
# Subtracting these before keyword scoring reduces false-positive recall
# (e.g. unrelated tasks overlapping only on "基本"/"用法").
# 常见无实义的中文连接/功能 bigram，匹配前减去以降低假阳性召回。
_CJK_STOPWORD_BIGRAMS = frozenset({
    "什么", "简要", "用法", "基本", "进行", "方法", "如何", "怎么",
    "是什", "于多", "有什", "影响", "区别", "说明", "解释", "实现",
    "使用", "可以", "需要", "一个", "这个", "那个", "的基", "的语",
})


class AgenticMemoryStore:
    """
    JSON-file backed agentic memory store with bilingual keyword retrieval.
    基于 JSON 文件的结构化记忆存储，支持中英双语关键词检索。
    """

    def __init__(self, memory_dir: str | None = None):
        self._dir = os.path.join(memory_dir or config.MEMORY_DIR, "agentic_memory")
        os.makedirs(self._dir, exist_ok=True)
        self._file = os.path.join(self._dir, "memories.json")
        self._records: dict[str, AgenticMemoryRecord] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, AgenticMemoryRecord]:
        """Load records from disk / 从磁盘加载"""
        if not os.path.exists(self._file):
            return {}
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                logger.error("Agentic memory file is not a list, resetting")
                return {}
            records: dict[str, AgenticMemoryRecord] = {}
            for r in data:
                try:
                    record = AgenticMemoryRecord(**r)
                    records[record.id] = record
                except Exception as e:
                    logger.warning("Skipping corrupt memory record: %s", e)
            return records
        except json.JSONDecodeError as exc:
            logger.error("Corrupt agentic memory file (JSON parse error): %s", exc)
            return {}
        except Exception as exc:
            logger.warning("Failed to load agentic memory: %s", exc)
            return {}

    def _save(self) -> None:
        """Atomic save: .tmp + os.rename / 原子写入"""
        records = [r.model_dump(mode="json") for r in self._records.values()]
        tmp_path = self._file + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._file)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def add(self, record: AgenticMemoryRecord) -> AgenticMemoryRecord:
        """Add a record and persist / 添加记录并持久化"""
        self._records[record.id] = record
        self._save()
        logger.info("Added agentic memory [%s] %s", record.kind.value, record.summary[:60])
        return record

    def get(self, memory_id: str) -> AgenticMemoryRecord | None:
        """Get a record by ID / 按 ID 获取记录"""
        return self._records.get(memory_id)

    def list_records(
        self,
        kind: MemoryKind | None = None,
        status: MemoryStatus | None = None,
    ) -> list[AgenticMemoryRecord]:
        """List records with optional filters / 列出记录（可按类型/状态过滤）"""
        results = list(self._records.values())
        if kind is not None:
            results = [r for r in results if r.kind == kind]
        if status is not None:
            results = [r for r in results if r.status == status]
        return results

    def revoke(self, memory_id: str) -> bool:
        """Revoke a record (set status=REVOKED, no physical delete) / 撤销记录"""
        record = self._records.get(memory_id)
        if record is None:
            return False
        record.status = MemoryStatus.REVOKED
        record.updated_at = time.time()
        self._save()
        logger.info("Revoked memory %s", memory_id)
        return True

    def update_access_stats(self, memory_id: str) -> None:
        """Update access count and last access time / 更新访问统计"""
        record = self._records.get(memory_id)
        if record is None:
            return
        record.access_count += 1
        record.last_accessed_at = time.time()
        # Don't persist on every access — caller should batch-save if needed
        # 不在每次访问时持久化，调用方可在需要时批量保存

    def save(self) -> None:
        """Explicit persist / 显式持久化"""
        self._save()

    def clear(self) -> None:
        """Clear all records (testing only) / 清空所有记录（仅测试用）"""
        self._records.clear()
        self._save()

    # ------------------------------------------------------------------
    # Search / 检索
    # ------------------------------------------------------------------

    def search(self, query: MemorySearchQuery) -> list[MemorySearchResult]:
        """
        Bilingual keyword search with weighted scoring.
        双语关键词检索，加权评分排序。

        Weights: keyword=0.50, tag=0.15, confidence=0.15, recency=0.10, importance=0.05, link=0.05
        """
        now = time.time()
        # Drop low-signal CJK function bigrams so unrelated tasks don't recall
        # each other on connective words alone. / 去掉无实义中文连接 bigram，避免靠连接词互相召回。
        query_tokens = _tokenize(query.query) - _CJK_STOPWORD_BIGRAMS
        query_tags = {t.lower() for t in query.tags}

        candidates: list[MemorySearchResult] = []

        for record in self._records.values():
            # Status filter / 状态过滤
            if record.status == MemoryStatus.REVOKED and not query.include_revoked:
                continue

            # Confidence filter / 置信度过滤
            if record.confidence < query.min_confidence:
                continue

            # Kind filter / 类型过滤
            if query.kind is not None and record.kind != query.kind:
                continue

            # Tags boost score but don't hard-filter / 标签只影响评分，不做硬过滤

            # --- Scoring / 评分 ---
            # Keyword match / 关键词匹配
            record_text = f"{record.content} {record.summary} {' '.join(record.tags)}"
            record_tokens = _tokenize(record_text) - _CJK_STOPWORD_BIGRAMS
            matched = query_tokens & record_tokens
            keyword_score = len(matched) / max(len(query_tokens), 1)

            # Tag match / 标签匹配
            record_tags = {t.lower() for t in record.tags}
            tag_overlap = query_tags & record_tags if query_tags else set()
            tag_score = len(tag_overlap) / max(len(query_tags), 1) if query_tags else 0.0

            # Confidence (normalized) / 置信度
            confidence_score = record.confidence

            # Recency (exponential decay) / 时间衰减
            age_days = (now - record.created_at) / 86400.0
            recency_score = math.exp(-RECENCY_LAMBDA * age_days)

            # Importance / 重要性
            importance_score = record.importance

            # Link bonus / 关联加分
            link_score = min(len(record.links) * 0.1, 1.0)

            total = (
                W_KEYWORD * keyword_score
                + W_TAG * tag_score
                + W_CONFIDENCE * confidence_score
                + W_RECENCY * recency_score
                + W_IMPORTANCE * importance_score
                + W_LINK * link_score
            )

            if total > 0 and keyword_score > 0:
                candidates.append(MemorySearchResult(
                    record=record,
                    score=round(total, 4),
                    score_breakdown={
                        "keyword": round(keyword_score, 4),
                        "tag": round(tag_score, 4),
                        "confidence": round(confidence_score, 4),
                        "recency": round(recency_score, 4),
                        "importance": round(importance_score, 4),
                        "link": round(link_score, 4),
                    },
                    matched_terms=sorted(matched),
                ))

        candidates.sort(key=lambda r: r.score, reverse=True)
        return candidates[:query.top_k]

    # ------------------------------------------------------------------
    # Legacy Migration / 旧记忆迁移
    # ------------------------------------------------------------------

    def migrate_from_legacy(self, memory_dir: str | None = None) -> int:
        """
        Migrate legacy LongTermMemory entries into agentic store.
        Reads old memory.json, converts to experiential records, does NOT delete old file.
        迁移旧版 LongTermMemory 记录到 agentic store。
        读取旧 memory.json，转为 experiential 记录，不删除旧文件。
        Returns number of migrated entries.
        """
        legacy_dir = memory_dir or config.MEMORY_DIR
        legacy_file = os.path.join(legacy_dir, "memory.json")
        if not os.path.exists(legacy_file):
            return 0

        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Failed to read legacy memory for migration: %s", exc)
            return 0

        # Per-entry idempotency: track already-migrated legacy entries by a stable
        # key so a partial migration (or entries appended to memory.json after a
        # prior pass) can still be picked up — instead of skipping ALL legacy when
        # any single legacy record already exists.
        # 逐条幂等：按稳定 key 记录已迁移条目，避免「存在任一 legacy 记录即整体跳过」
        # 漏迁部分失败或后续新增的条目。
        existing_keys = {
            r.metadata.get("legacy_key")
            for r in self._records.values()
            if r.source == "legacy" and r.metadata.get("legacy_key")
        }

        migrated = 0
        for entry_data in data:
            try:
                entry = MemoryEntry(**entry_data)
            except Exception as e:
                logger.warning("Skipping unparseable legacy entry: %s", e)
                continue

            legacy_key = self._legacy_entry_key(entry)
            if legacy_key in existing_keys:
                continue  # already migrated in a prior pass

            record = AgenticMemoryRecord(
                kind=MemoryKind.EXPERIENTIAL,
                content=entry.task,
                summary=entry.summary[:500],
                tags=entry.learnings[:5] if entry.learnings else [],
                source="legacy",
                confidence=0.5,
                importance=0.5,
                created_at=entry.timestamp,
                updated_at=entry.timestamp,
                metadata={"legacy_key": legacy_key},
            )
            self._records[record.id] = record
            existing_keys.add(legacy_key)
            migrated += 1

        if migrated > 0:
            self._save()
            logger.info("Migrated %d legacy memory entries", migrated)
        return migrated

    @staticmethod
    def _legacy_entry_key(entry: "MemoryEntry") -> str:
        """Stable per-entry key for legacy-migration dedup (recomputable).
        legacy 迁移去重用的稳定 key（可复算，基于 task+summary+timestamp）。"""
        import hashlib
        raw = f"{entry.task}\x00{entry.summary}\x00{entry.timestamp}"
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]
