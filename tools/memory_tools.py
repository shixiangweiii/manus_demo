"""
Memory Tools - Agent-callable tools for structured memory operations.
记忆工具 —— 供 Agent 调用的结构化记忆操作工具。

Four tools: memory_search, memory_store, memory_consolidate, memory_revoke.
All tools inject AgenticMemoryService via constructor.
Tools emit events through an optional on_event callback.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from tools.base import BaseTool
from memory.service import AgenticMemoryService

# Agent-initiated store confidence cap / Agent 写入的置信度上限
AGENT_CONFIDENCE_CAP = 0.6


class MemorySearchTool(BaseTool):
    """Search structured memories by query, kind, or tags."""

    def __init__(self, service: AgenticMemoryService, on_event: Callable | None = None):
        self._service = service
        self._on_event = on_event

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return (
            "Search structured memories for relevant information. "
            "Returns matching memory records with confidence scores. "
            "搜索结构化记忆，返回匹配的记忆记录和置信度评分。"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query / 搜索查询文本",
                },
                "kind": {
                    "type": "string",
                    "description": "Memory kind filter: factual|experiential|working|procedural",
                    "enum": ["factual", "experiential", "working", "procedural"],
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to boost relevance / 用于提升相关性的标签",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default 3) / 最大返回数量",
                    "default": 3,
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("query", "")
        kind_str = kwargs.get("kind")
        tags = kwargs.get("tags", [])
        top_k = kwargs.get("top_k", 3)

        # Emit search_start so eval probe counts LLM-initiated tool searches
        # (probe.memory_search_count increments on memory_search_start, NOT result).
        # 发出 search_start，让评测 probe 统计 LLM 主动发起的工具搜索。
        if self._on_event:
            self._on_event("memory_search_start", {"query": query, "source": "tool"})

        from memory.models import MemoryKind, MemorySearchQuery

        kind = MemoryKind(kind_str) if kind_str else None
        search_query = MemorySearchQuery(
            query=query, kind=kind, tags=tags, top_k=top_k,
        )
        results = self._service.search(search_query)

        if self._on_event:
            self._on_event("memory_search_result", {
                "query": query, "count": len(results), "source": "tool",
            })

        if not results:
            return json.dumps({"results": [], "count": 0}, ensure_ascii=False)

        output = []
        for r in results:
            output.append({
                "id": r.record.id,
                "short_id": r.record.id[:8],
                "kind": r.record.kind.value,
                "summary": r.record.summary,
                "confidence": r.record.confidence,
                "score": r.score,
                "tags": r.record.tags[:5],
            })
        return json.dumps({"results": output, "count": len(output)}, ensure_ascii=False)


class MemoryStoreTool(BaseTool):
    """Store a new memory record (agent-initiated, confidence capped at 0.6)."""

    def __init__(self, service: AgenticMemoryService, on_event: Callable | None = None):
        self._service = service
        self._on_event = on_event

    @property
    def name(self) -> str:
        return "memory_store"

    @property
    def description(self) -> str:
        return (
            "Store a new memory record. Confidence is capped at 0.6 for agent-initiated writes. "
            "存储新的记忆记录。Agent 发起的写入置信度上限为 0.6。"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Memory type: factual|experiential|working|procedural",
                    "enum": ["factual", "experiential", "working", "procedural"],
                },
                "content": {
                    "type": "string",
                    "description": "The memory content to store / 要存储的记忆内容",
                },
                "summary": {
                    "type": "string",
                    "description": "Brief summary (optional) / 简短摘要（可选）",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization / 分类标签",
                },
            },
            "required": ["kind", "content"],
        }

    async def execute(self, **kwargs: Any) -> str:
        kind_str = kwargs.get("kind", "experiential")
        content = kwargs.get("content", "")
        summary = kwargs.get("summary", content[:200])
        tags = kwargs.get("tags", [])

        from memory.models import AgenticMemoryRecord, MemoryKind

        kind = MemoryKind(kind_str)
        record = AgenticMemoryRecord(
            kind=kind,
            content=content,
            summary=summary,
            tags=tags,
            source="agent",
            confidence=AGENT_CONFIDENCE_CAP,
            importance=0.5,
        )
        self._service.add_record(record)

        if self._on_event:
            self._on_event("memory_store", {
                "id": record.id, "kind": kind.value, "source": "tool",
            })

        return json.dumps({
            "status": "stored",
            "id": record.id,
            "short_id": record.id[:8],
            "kind": kind.value,
            "confidence": record.confidence,
        }, ensure_ascii=False)


class MemoryConsolidateTool(BaseTool):
    """Consolidate task experience into structured memory."""

    def __init__(self, service: AgenticMemoryService, on_event: Callable | None = None):
        self._service = service
        self._on_event = on_event

    @property
    def name(self) -> str:
        return "memory_consolidate"

    @property
    def description(self) -> str:
        return (
            "Consolidate current task experience into long-term memory. "
            "将当前任务经验巩固为长期记忆。"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to consolidate / 要巩固的任务 ID",
                },
                "notes": {
                    "type": "string",
                    "description": "Additional notes or observations / 额外备注或观察",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs.get("task_id", "")
        notes = kwargs.get("notes", "")

        records = await self._service.consolidate_task(task_id=task_id, notes=notes)

        if self._on_event:
            self._on_event("memory_consolidate", {
                "task_id": task_id, "records_created": len(records),
            })

        return json.dumps({
            "status": "consolidated",
            "records_created": len(records),
            "record_ids": [r.id for r in records],
        }, ensure_ascii=False)


class MemoryRevokeTool(BaseTool):
    """Revoke a memory record (soft delete, no physical removal)."""

    def __init__(self, service: AgenticMemoryService, on_event: Callable | None = None):
        self._service = service
        self._on_event = on_event

    @property
    def name(self) -> str:
        return "memory_revoke"

    @property
    def description(self) -> str:
        return (
            "Revoke a memory record by ID. The record is soft-deleted (not physically removed). "
            "撤销一条记忆记录。记录将被软删除（不物理移除）。"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The memory record ID to revoke / 要撤销的记忆记录 ID",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for revocation / 撤销原因",
                },
            },
            "required": ["memory_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        memory_id = kwargs.get("memory_id", "")
        reason = kwargs.get("reason", "")

        success = self._service.revoke(memory_id, reason)

        if success:
            if self._on_event:
                self._on_event("memory_revoke", {"memory_id": memory_id, "reason": reason})
            return json.dumps({
                "status": "revoked",
                "memory_id": memory_id,
            }, ensure_ascii=False)
        else:
            # Return an Error:-prefixed string so ToolRouter/classify_tool_result treats
            # a failed revoke as a failure (a JSON {"status":"error"} reads as success).
            # 返回 Error: 前缀字符串，让 classify_tool_result 识别为失败（JSON 体会被误判成功）。
            return f"Error: Memory record '{memory_id}' not found"
