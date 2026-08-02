"""Semantic checkpoint records owned by the unified runtime."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.models import Effort, EngineKind


class CheckpointError(Exception):
    """Base exception for runtime checkpoint operations."""


class CheckpointCorruptedError(CheckpointError):
    """A checkpoint file is not valid JSON or does not match the schema."""


class CheckpointIncompatibleError(CheckpointError):
    """A checkpoint belongs to an intentionally unsupported schema version."""


class CheckpointStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeCheckpoint(BaseModel):
    """Latest task boundary; resume restarts the task with the same choices."""

    schema_name: Literal["semantic-runtime-checkpoint"] = "semantic-runtime-checkpoint"
    schema_version: Literal[2] = 2
    task_id: str
    run_id: str
    task: str
    context: str = ""
    engine: EngineKind
    effort: Effort
    state: CheckpointStatus = CheckpointStatus.RUNNING
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class RuntimeCheckpointSummary(BaseModel):
    task_id: str
    task: str
    engine: EngineKind
    effort: Effort
    state: CheckpointStatus
    updated_at: float
    file_path: str
