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
        result = await revoke_tool.execute(memory_id="nonexistent")
        data = json.loads(result)
        assert data["status"] == "error"

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
