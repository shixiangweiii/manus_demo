"""
Unit tests for Agentic Memory models, store, and retrieval.
结构化记忆模型、存储和检索单元测试。
"""

import json
import os
import time

import pytest

from memory.models import (
    AgenticMemoryRecord,
    MemoryKind,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStatus,
)
from memory.agentic_store import AgenticMemoryStore, _tokenize


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def store(tmp_path):
    """Create an AgenticMemoryStore with a temp directory."""
    return AgenticMemoryStore(memory_dir=str(tmp_path))


@pytest.fixture
def sample_record():
    """Create a sample memory record."""
    return AgenticMemoryRecord(
        kind=MemoryKind.FACTUAL,
        content="用户偏好使用 Python 3.12 和 uv 作为包管理器",
        summary="用户偏好 Python 3.12 + uv",
        tags=["python", "preference"],
        source="agent",
        confidence=0.8,
        importance=0.7,
    )


@pytest.fixture
def populated_store(store):
    """Store with several records for search testing."""
    records = [
        AgenticMemoryRecord(
            kind=MemoryKind.FACTUAL,
            content="用户偏好使用 Python 3.12",
            summary="用户偏好 Python 3.12",
            tags=["python", "preference"],
            confidence=0.9,
            importance=0.7,
        ),
        AgenticMemoryRecord(
            kind=MemoryKind.EXPERIENTIAL,
            content="完成了一个 Flask web 应用的开发",
            summary="Flask 开发经验",
            tags=["flask", "web"],
            confidence=0.7,
            importance=0.5,
        ),
        AgenticMemoryRecord(
            kind=MemoryKind.PROCEDURAL,
            content="Deploy FastAPI to production: use gunicorn with uvicorn workers",
            summary="FastAPI 部署步骤",
            tags=["fastapi", "deploy"],
            confidence=0.85,
            importance=0.8,
        ),
    ]
    for r in records:
        store.add(r)
    return store


# ======================================================================
# Model tests
# ======================================================================

class TestModels:
    def test_record_defaults(self):
        r = AgenticMemoryRecord(content="test")
        assert r.id  # auto-generated
        assert r.kind == MemoryKind.EXPERIENTIAL
        assert r.status == MemoryStatus.ACTIVE
        assert r.confidence == 0.5
        assert r.source == "system"
        assert r.created_at > 0
        assert r.updated_at > 0
        assert r.access_count == 0
        assert r.links == []

    def test_record_kind_enum(self):
        assert MemoryKind.FACTUAL.value == "factual"
        assert MemoryKind.EXPERIENTIAL.value == "experiential"
        assert MemoryKind.WORKING.value == "working"
        assert MemoryKind.PROCEDURAL.value == "procedural"

    def test_record_status_enum(self):
        assert MemoryStatus.ACTIVE.value == "active"
        assert MemoryStatus.REVOKED.value == "revoked"

    def test_record_custom_fields(self):
        r = AgenticMemoryRecord(
            content="test content",
            kind=MemoryKind.FACTUAL,
            confidence=0.9,
            tags=["a", "b"],
            links=["other_id"],
        )
        assert r.kind == MemoryKind.FACTUAL
        assert r.confidence == 0.9
        assert r.tags == ["a", "b"]
        assert r.links == ["other_id"]

    def test_search_query_defaults(self):
        q = MemorySearchQuery(query="test")
        assert q.top_k == 3
        assert q.min_confidence == 0.35
        assert q.include_revoked is False
        assert q.kind is None
        assert q.tags == []

    def test_search_result(self):
        r = AgenticMemoryRecord(content="test")
        sr = MemorySearchResult(record=r, score=0.8, matched_terms=["test"])
        assert sr.score == 0.8
        assert sr.matched_terms == ["test"]
        assert sr.record.id == r.id


# ======================================================================
# Store core tests
# ======================================================================

class TestStoreCore:
    def test_add_and_get(self, store, sample_record):
        store.add(sample_record)
        got = store.get(sample_record.id)
        assert got is not None
        assert got.content == sample_record.content
        assert got.kind == MemoryKind.FACTUAL

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None

    def test_list_records(self, store):
        store.add(AgenticMemoryRecord(kind=MemoryKind.FACTUAL, content="factual"))
        store.add(AgenticMemoryRecord(kind=MemoryKind.EXPERIENTIAL, content="exp"))
        store.add(AgenticMemoryRecord(kind=MemoryKind.PROCEDURAL, content="proc"))

        all_records = store.list_records()
        assert len(all_records) == 3

        factual = store.list_records(kind=MemoryKind.FACTUAL)
        assert len(factual) == 1
        assert factual[0].kind == MemoryKind.FACTUAL

    def test_list_records_by_status(self, store):
        r1 = AgenticMemoryRecord(content="active")
        r2 = AgenticMemoryRecord(content="revoked", status=MemoryStatus.REVOKED)
        store.add(r1)
        store.add(r2)

        active = store.list_records(status=MemoryStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].status == MemoryStatus.ACTIVE

    def test_clear(self, store, sample_record):
        store.add(sample_record)
        assert len(store.list_records()) == 1
        store.clear()
        assert len(store.list_records()) == 0

    def test_persistence(self, tmp_path, sample_record):
        """Records survive store recreation."""
        store1 = AgenticMemoryStore(memory_dir=str(tmp_path))
        store1.add(sample_record)

        store2 = AgenticMemoryStore(memory_dir=str(tmp_path))
        got = store2.get(sample_record.id)
        assert got is not None
        assert got.content == sample_record.content

    def test_atomic_write_no_tmp_residue(self, store):
        """After save, no .tmp file should remain."""
        store.add(AgenticMemoryRecord(content="test"))
        assert not os.path.exists(store._file + ".tmp")

    def test_save_persists(self, store):
        r = AgenticMemoryRecord(content="test")
        store.add(r)
        # Read raw JSON to verify
        with open(store._file, "r") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["id"] == r.id


# ======================================================================
# Revoke tests
# ======================================================================

class TestRevoke:
    def test_revoke_sets_status(self, store, sample_record):
        store.add(sample_record)
        assert store.revoke(sample_record.id) is True
        got = store.get(sample_record.id)
        assert got.status == MemoryStatus.REVOKED

    def test_revoke_nonexistent(self, store):
        assert store.revoke("nonexistent") is False

    def test_revoke_persists(self, tmp_path):
        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        r = AgenticMemoryRecord(content="to revoke")
        store.add(r)
        store.revoke(r.id)

        store2 = AgenticMemoryStore(memory_dir=str(tmp_path))
        assert store2.get(r.id).status == MemoryStatus.REVOKED

    def test_revoked_not_in_default_list(self, store):
        r = AgenticMemoryRecord(content="active")
        r2 = AgenticMemoryRecord(content="revoked")
        store.add(r)
        store.add(r2)
        store.revoke(r2.id)

        active = store.list_records(status=MemoryStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].id == r.id


# ======================================================================
# Access stats tests
# ======================================================================

class TestAccessStats:
    def test_update_access_stats(self, store, sample_record):
        store.add(sample_record)
        assert sample_record.access_count == 0

        store.update_access_stats(sample_record.id)
        assert sample_record.access_count == 1
        assert sample_record.last_accessed_at > 0

        store.update_access_stats(sample_record.id)
        assert sample_record.access_count == 2

    def test_update_nonexistent(self, store):
        # Should not raise
        store.update_access_stats("nonexistent")


# ======================================================================
# Tokenizer tests
# ======================================================================

class TestTokenizer:
    def test_english_tokens(self):
        tokens = _tokenize("Python web framework")
        assert "python" in tokens
        assert "web" in tokens
        assert "framework" in tokens

    def test_chinese_bigrams(self):
        tokens = _tokenize("用户偏好")
        assert "用户" in tokens or "户偏" in tokens or "偏好" in tokens
        # Only bigrams, no single chars (removed to reduce noise)
        assert "用" not in tokens and "偏" not in tokens

    def test_mixed_tokens(self):
        tokens = _tokenize("使用 Python 3.12 进行开发")
        assert "python" in tokens
        assert "3" in tokens or "12" in tokens

    def test_empty_string(self):
        tokens = _tokenize("")
        assert len(tokens) == 0


# ======================================================================
# Search tests
# ======================================================================

class TestSearch:
    def test_basic_keyword_search(self, populated_store):
        results = populated_store.search(MemorySearchQuery(query="Python 偏好"))
        assert len(results) > 0
        # Factual record about Python should rank high
        top = results[0]
        assert "Python" in top.record.content or "python" in top.record.content.lower()

    def test_english_keyword_search(self, populated_store):
        results = populated_store.search(MemorySearchQuery(query="FastAPI deploy"))
        assert len(results) > 0
        assert any("FastAPI" in r.record.content or "fastapi" in r.record.content.lower()
                    for r in results)

    def test_chinese_keyword_search(self, populated_store):
        results = populated_store.search(MemorySearchQuery(query="Flask 开发"))
        assert len(results) > 0

    def test_search_respects_top_k(self, populated_store):
        results = populated_store.search(MemorySearchQuery(query="python", top_k=1))
        assert len(results) <= 1

    def test_search_filters_by_confidence(self, store):
        store.add(AgenticMemoryRecord(content="low confidence", confidence=0.2))
        store.add(AgenticMemoryRecord(content="high confidence python", confidence=0.9))

        results = store.search(MemorySearchQuery(query="python", min_confidence=0.5))
        assert len(results) == 1
        assert "high" in results[0].record.content

    def test_search_filters_revoked(self, store):
        r1 = AgenticMemoryRecord(content="active python record", confidence=0.9)
        r2 = AgenticMemoryRecord(content="revoked python record", confidence=0.9)
        store.add(r1)
        store.add(r2)
        store.revoke(r2.id)

        results = store.search(MemorySearchQuery(query="python"))
        assert len(results) == 1
        assert results[0].record.id == r1.id

    def test_search_includes_revoked_when_requested(self, store):
        r = AgenticMemoryRecord(content="revoked python record", confidence=0.9)
        store.add(r)
        store.revoke(r.id)

        results = store.search(MemorySearchQuery(query="python", include_revoked=True))
        assert len(results) == 1

    def test_search_filters_by_kind(self, populated_store):
        results = populated_store.search(
            MemorySearchQuery(query="python", kind=MemoryKind.FACTUAL)
        )
        assert all(r.record.kind == MemoryKind.FACTUAL for r in results)

    def test_search_result_has_breakdown(self, populated_store):
        results = populated_store.search(MemorySearchQuery(query="python"))
        assert len(results) > 0
        r = results[0]
        assert "keyword" in r.score_breakdown
        assert "confidence" in r.score_breakdown
        assert "recency" in r.score_breakdown

    def test_search_no_results(self, populated_store):
        results = populated_store.search(MemorySearchQuery(query="量子计算xyz"))
        assert len(results) == 0

    def test_cjk_stopword_bigrams_no_false_positive(self, store):
        """P3: unrelated Chinese tasks must not recall each other on
        connective bigrams alone (e.g. '基本'/'用法')."""
        store.add(AgenticMemoryRecord(
            content="Python的GIL是什么？简要解释",
            summary="GIL 全局解释器锁",
            confidence=0.9,
        ))
        # Different topic, overlaps only on stopword bigrams like 用法/基本
        results = store.search(MemorySearchQuery(query="Java的基本用法是什么"))
        assert len(results) == 0

    def test_meaningful_cjk_overlap_still_recalls(self, store):
        """Genuine topical overlap (装饰器) must still be recalled."""
        store.add(AgenticMemoryRecord(
            content="Python装饰器的实现原理",
            summary="装饰器语法糖",
            confidence=0.9,
        ))
        results = store.search(MemorySearchQuery(query="Python装饰器怎么用"))
        assert len(results) > 0


# ======================================================================
# Scoring detail tests
# ======================================================================

class TestScoring:
    def test_tag_match_boosts_score(self, store):
        r1 = AgenticMemoryRecord(
            content="web framework", summary="web", tags=["flask"], confidence=0.8
        )
        r2 = AgenticMemoryRecord(
            content="web framework", summary="web", tags=["fastapi"], confidence=0.8
        )
        store.add(r1)
        store.add(r2)

        results = store.search(
            MemorySearchQuery(query="web framework", tags=["flask"])
        )
        assert len(results) == 2
        # r1 should score higher due to tag match
        assert results[0].record.id == r1.id

    def test_recency_newer_ranks_higher(self, tmp_path):
        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        now = time.time()

        old = AgenticMemoryRecord(
            content="python test", summary="old record", confidence=0.8,
            created_at=now - 86400 * 30,  # 30 days ago
            updated_at=now - 86400 * 30,
        )
        new = AgenticMemoryRecord(
            content="python test", summary="new record", confidence=0.8,
            created_at=now,
            updated_at=now,
        )
        store.add(old)
        store.add(new)

        results = store.search(MemorySearchQuery(query="python"))
        assert len(results) == 2
        # Newer record should rank first
        assert results[0].record.id == new.id

    def test_link_bonus(self, store):
        r1 = AgenticMemoryRecord(
            content="python test", summary="no links", confidence=0.8,
        )
        r2 = AgenticMemoryRecord(
            content="python test", summary="has links", confidence=0.8,
            links=["id1", "id2", "id3"],
        )
        store.add(r1)
        store.add(r2)

        results = store.search(MemorySearchQuery(query="python"))
        assert len(results) == 2
        # r2 should score higher due to link bonus
        assert results[0].record.id == r2.id


# ======================================================================
# Legacy Migration tests
# ======================================================================

class TestLegacyMigration:
    def test_migrate_from_legacy(self, tmp_path):
        # Create legacy memory.json
        legacy_data = [
            {"task": "完成数据分析", "summary": "使用 pandas 处理 CSV",
             "learnings": ["pandas 好用"], "timestamp": time.time()},
            {"task": "Build REST API", "summary": "Used FastAPI",
             "learnings": [], "timestamp": time.time()},
        ]
        legacy_file = os.path.join(str(tmp_path), "memory.json")
        with open(legacy_file, "w") as f:
            json.dump(legacy_data, f)

        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        count = store.migrate_from_legacy(str(tmp_path))
        assert count == 2

        records = store.list_records(kind=MemoryKind.EXPERIENTIAL)
        assert len(records) == 2
        for r in records:
            assert r.source == "legacy"
            assert r.confidence == 0.5

    def test_migrate_preserves_old_file(self, tmp_path):
        legacy_data = [{"task": "test", "summary": "test", "learnings": [], "timestamp": time.time()}]
        legacy_file = os.path.join(str(tmp_path), "memory.json")
        with open(legacy_file, "w") as f:
            json.dump(legacy_data, f)

        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        store.migrate_from_legacy(str(tmp_path))

        # Old file still exists
        assert os.path.exists(legacy_file)

    def test_migrate_no_legacy_file(self, store):
        count = store.migrate_from_legacy("/nonexistent/path")
        assert count == 0

    def test_migrate_idempotent(self, tmp_path):
        legacy_data = [{"task": "test", "summary": "test", "learnings": [], "timestamp": time.time()}]
        legacy_file = os.path.join(str(tmp_path), "memory.json")
        with open(legacy_file, "w") as f:
            json.dump(legacy_data, f)

        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        store.migrate_from_legacy(str(tmp_path))
        count1 = len(store.list_records())

        # Second migration is idempotent — no duplicates
        store.migrate_from_legacy(str(tmp_path))
        count2 = len(store.list_records())
        assert count2 == count1  # no duplicates

    def test_migrate_picks_up_new_entries_after_partial(self, tmp_path):
        # P2-7: appending a new legacy entry after a prior migration must be
        # picked up — not skipped wholesale just because some legacy record exists.
        legacy_file = os.path.join(str(tmp_path), "memory.json")
        first = [{"task": "t1", "summary": "s1", "learnings": [], "timestamp": 111.0}]
        with open(legacy_file, "w") as f:
            json.dump(first, f)

        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        assert store.migrate_from_legacy(str(tmp_path)) == 1

        # Append a brand-new entry and re-run migration.
        both = first + [{"task": "t2", "summary": "s2", "learnings": [], "timestamp": 222.0}]
        with open(legacy_file, "w") as f:
            json.dump(both, f)

        migrated_again = store.migrate_from_legacy(str(tmp_path))
        assert migrated_again == 1  # only the new entry
        assert len(store.list_records(kind=MemoryKind.EXPERIENTIAL)) == 2


class TestExtractTagsChinese:
    """P2-7: CJK keywords must be extractable from continuous Chinese text
    (no \\b word boundary between CJK chars)."""

    def test_chinese_keyword_in_continuous_text(self):
        from memory.service import AgenticMemoryService
        tags = AgenticMemoryService._extract_tags("这是一个数据分析与机器学习任务")
        assert "数据分析" in tags
        assert "机器学习" in tags

    def test_ascii_keyword_still_word_bounded(self):
        from memory.service import AgenticMemoryService
        # "web" must NOT match inside "webhook" (word-boundary preserved for ASCII).
        tags = AgenticMemoryService._extract_tags("configure a webhook endpoint")
        assert "web" not in tags

    def test_ascii_keyword_adjacent_to_cjk(self):
        """P3: ASCII keyword immediately followed by a CJK char must still match.
        re.ASCII makes the CJK char a non-word boundary so \\bpython\\b hits."""
        from memory.service import AgenticMemoryService
        tags = AgenticMemoryService._extract_tags("Python的GIL是什么？简要解释")
        assert "python" in tags

    def test_ascii_keyword_cjk_on_both_sides(self):
        from memory.service import AgenticMemoryService
        tags = AgenticMemoryService._extract_tags("用python写一个脚本")
        assert "python" in tags
