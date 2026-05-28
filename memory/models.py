"""
Agentic Memory Models - Structured memory records, queries, and results.
结构化记忆模型 —— 支持多类型、置信度、撤销、关联的记忆记录与检索。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ======================================================================
# Enums
# ======================================================================

class MemoryKind(str, Enum):
    """Memory record type / 记忆类型"""
    FACTUAL = "factual"            # 事实性知识（用户偏好、项目约定）
    EXPERIENTIAL = "experiential"  # 经验性记忆（任务执行的经验总结）
    WORKING = "working"            # 工作记忆（当前会话的临时上下文）
    PROCEDURAL = "procedural"      # 过程性知识（操作步骤、最佳实践）


class MemoryStatus(str, Enum):
    """Memory record status / 记忆状态"""
    ACTIVE = "active"    # 正常可用
    REVOKED = "revoked"  # 已撤销（不物理删除）


# ======================================================================
# Core Models
# ======================================================================

class AgenticMemoryRecord(BaseModel):
    """
    A single structured memory record with metadata for retrieval and governance.
    单条结构化记忆记录，包含检索和治理所需的元数据。
    """
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    kind: MemoryKind = MemoryKind.EXPERIENTIAL
    content: str = ""                          # 原始内容
    summary: str = ""                          # 摘要（用于检索和上下文注入）
    tags: list[str] = Field(default_factory=list)
    task_id: str = ""
    session_id: str = ""
    source: str = "system"                     # system | agent | user | legacy
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_accessed_at: float = 0.0
    access_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: list[str] = Field(default_factory=list)  # 关联的其他 memory id


class MemorySearchQuery(BaseModel):
    """Search query for agentic memory / 记忆检索查询"""
    query: str
    kind: MemoryKind | None = None
    tags: list[str] = Field(default_factory=list)
    top_k: int = Field(default=3, ge=1)
    min_confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    include_revoked: bool = False


class MemorySearchResult(BaseModel):
    """A single search result with scoring breakdown / 带评分的检索结果"""
    record: AgenticMemoryRecord
    score: float
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    matched_terms: list[str] = Field(default_factory=list)
