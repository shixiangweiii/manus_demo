"""
Unit tests for Memory Tools (memory_search, memory_store, memory_consolidate, memory_revoke).
记忆工具单元测试。
"""

import json

import pytest

from memory.agentic_store import AgenticMemoryStore
from memory.models import AgenticMemoryRecord, MemoryKind, MemoryStatus
from memory.service import AgenticMemoryService
from tools.memory_tools import (
    MemoryConsolidateTool,
    MemoryRevokeTool,
    MemorySearchTool,
    MemoryStoreTool,
)


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def service(tmp_path):
    store = AgenticMemoryStore(memory_dir=str(tmp_path))
    return AgenticMemoryService(store)


@pytest.fixture
def search_tool(service):
    return MemorySearchTool(service)


@pytest.fixture
def store_tool(service):
    return MemoryStoreTool(service)


@pytest.fixture
def consolidate_tool(service):
    return MemoryConsolidateTool(service)


@pytest.fixture
def revoke_tool(service):
    return MemoryRevokeTool(service)


# ======================================================================
# Schema tests
# ======================================================================

class TestToolSchemas:
    def test_search_schema(self, search_tool):
        schema = search_tool.to_openai_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "memory_search"
        params = schema["function"]["parameters"]
        assert "query" in params["properties"]
        assert params["required"] == ["query"]

    def test_store_schema(self, store_tool):
        schema = store_tool.to_openai_tool()
        assert schema["function"]["name"] == "memory_store"
        params = schema["function"]["parameters"]
        assert "kind" in params["properties"]
        assert "content" in params["properties"]
        assert "kind" in params["required"]

    def test_consolidate_schema(self, consolidate_tool):
        schema = consolidate_tool.to_openai_tool()
        assert schema["function"]["name"] == "memory_consolidate"

    def test_revoke_schema(self, revoke_tool):
        schema = revoke_tool.to_openai_tool()
        assert schema["function"]["name"] == "memory_revoke"
        params = schema["function"]["parameters"]
        assert "memory_id" in params["required"]


# ======================================================================
# Search tool tests
# ======================================================================

class TestMemorySearchTool:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, search_tool, service):
        service._store.add(AgenticMemoryRecord(
            content="Python 3.12 偏好",
            summary="用户偏好 Python 3.12",
            confidence=0.9,
            tags=["python"],
        ))

        result = await search_tool.execute(query="Python")
        data = json.loads(result)
        assert data["count"] >= 1
        assert any("Python" in r["summary"] for r in data["results"])

    @pytest.mark.asyncio
    async def test_search_empty(self, search_tool):
        result = await search_tool.execute(query="nonexistent_xyz")
        data = json.loads(result)
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_search_emits_search_start(self, service):
        # P2-6: tool-initiated search must emit memory_search_start so the eval
        # probe (memory_search_count) counts it.
        events = []
        tool = MemorySearchTool(service, on_event=lambda e, d: events.append((e, d)))
        await tool.execute(query="anything")
        assert any(e == "memory_search_start" for e, _ in events)


# ======================================================================
# Store tool tests
# ======================================================================

class TestMemoryStoreTool:
    @pytest.mark.asyncio
    async def test_store_normal(self, store_tool, service):
        result = await store_tool.execute(
            kind="factual",
            content="用户偏好使用 uv 管理依赖",
            summary="偏好 uv",
            tags=["preference"],
        )
        data = json.loads(result)
        assert data["status"] == "stored"
        assert data["kind"] == "factual"

        # Verify persisted
        records = service._store.list_records(kind=MemoryKind.FACTUAL)
        assert len(records) == 1
        assert records[0].content == "用户偏好使用 uv 管理依赖"

    @pytest.mark.asyncio
    async def test_store_confidence_cap(self, store_tool, service):
        result = await store_tool.execute(
            kind="factual",
            content="test content",
        )
        data = json.loads(result)
        assert data["confidence"] <= 0.6

        records = service._store.list_records()
        assert records[0].confidence <= 0.6


# ======================================================================
# Revoke tool tests
# ======================================================================

class TestMemoryRevokeTool:
    @pytest.mark.asyncio
    async def test_revoke_success(self, revoke_tool, service):
        record = AgenticMemoryRecord(content="to revoke", confidence=0.8)
        service._store.add(record)

        result = await revoke_tool.execute(memory_id=record.id, reason="outdated")
        data = json.loads(result)
        assert data["status"] == "revoked"

        # Verify revoked
        got = service._store.get(record.id)
        assert got.status == MemoryStatus.REVOKED

    @pytest.mark.asyncio
    async def test_revoke_not_found(self, revoke_tool):
        # P2-6: not-found must return an Error:-prefixed string (classify_result
        # treats a JSON {"status":"error"} body as success).
        result = await revoke_tool.execute(memory_id="nonexistent")
        assert result.startswith("Error:")
        assert "nonexistent" in result

    @pytest.mark.asyncio
    async def test_revoked_not_in_search(self, search_tool, service):
        record = AgenticMemoryRecord(
            content="python test unique content", confidence=0.9,
        )
        service._store.add(record)
        service._store.revoke(record.id)

        result = await search_tool.execute(query="python test unique")
        data = json.loads(result)
        assert data["count"] == 0


# ======================================================================
# Consolidate tool tests
# ======================================================================

class TestMemoryConsolidateTool:
    @pytest.mark.asyncio
    async def test_consolidate_with_notes(self, consolidate_tool, service):
        result = await consolidate_tool.execute(
            task_id="test_task_001",
            notes="Learned to use asyncio.gather for parallel execution",
        )
        data = json.loads(result)
        assert data["status"] == "consolidated"
        assert data["records_created"] >= 1

    @pytest.mark.asyncio
    async def test_consolidate_empty(self, consolidate_tool, service):
        result = await consolidate_tool.execute(task_id="nonexistent")
        data = json.loads(result)
        assert data["status"] == "consolidated"
        assert data["records_created"] == 0


# ======================================================================
# LLM-assisted consolidation (MEMORY_LLM_CONSOLIDATION_ENABLED)
# ======================================================================

class _FakeLLM:
    """Minimal LLMClient stub exposing chat_json."""

    def __init__(self, payload=None, raise_exc=False):
        self._payload = payload
        self._raise = raise_exc
        self.called = False

    async def chat_json(self, *args, **kwargs):
        self.called = True
        if self._raise:
            raise RuntimeError("boom")
        return self._payload


class TestLLMConsolidation:
    @pytest.mark.asyncio
    async def test_llm_branch_used_when_enabled(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "MEMORY_LLM_CONSOLIDATION_ENABLED", True)
        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        llm = _FakeLLM(payload={"summary": "LLM 合并摘要", "tags": ["python", "gil"]})
        svc = AgenticMemoryService(store, llm_client=llm)
        store.add(AgenticMemoryRecord(
            content="Python GIL", summary="GIL 原始摘要",
            task_id="t1", kind=MemoryKind.EXPERIENTIAL,
        ))

        records = await svc.consolidate_task(task_id="t1")
        assert llm.called is True
        proc = [r for r in records if r.kind == MemoryKind.PROCEDURAL]
        assert proc and proc[0].summary == "LLM 合并摘要"
        assert proc[0].metadata.get("llm_consolidated") is True
        assert "python" in proc[0].tags

    @pytest.mark.asyncio
    async def test_falls_back_on_llm_error(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "MEMORY_LLM_CONSOLIDATION_ENABLED", True)
        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        llm = _FakeLLM(raise_exc=True)
        svc = AgenticMemoryService(store, llm_client=llm)
        store.add(AgenticMemoryRecord(
            content="Python GIL", summary="确定性摘要",
            task_id="t1", kind=MemoryKind.EXPERIENTIAL,
        ))

        records = await svc.consolidate_task(task_id="t1")
        assert llm.called is True
        proc = [r for r in records if r.kind == MemoryKind.PROCEDURAL]
        # Deterministic fallback: summary derived from source record, flag False
        assert proc and proc[0].metadata.get("llm_consolidated") is False
        assert "确定性摘要" in proc[0].summary

    @pytest.mark.asyncio
    async def test_no_llm_when_disabled(self, tmp_path, monkeypatch):
        import config
        monkeypatch.setattr(config, "MEMORY_LLM_CONSOLIDATION_ENABLED", False)
        store = AgenticMemoryStore(memory_dir=str(tmp_path))
        llm = _FakeLLM(payload={"summary": "X", "tags": []})
        svc = AgenticMemoryService(store, llm_client=llm)
        store.add(AgenticMemoryRecord(
            content="Python GIL", summary="det", task_id="t1",
            kind=MemoryKind.EXPERIENTIAL,
        ))

        records = await svc.consolidate_task(task_id="t1")
        assert llm.called is False
        proc = [r for r in records if r.kind == MemoryKind.PROCEDURAL]
        assert proc and proc[0].metadata.get("llm_consolidated") is False
