"""Typed results owned by the task-level agent loop."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.models import EngineStats, EngineStopReason
from execution.models import ToolCallRecord


class AgentLoopResult(BaseModel):
    output: str = ""
    success: bool
    stop_reason: EngineStopReason
    stats: EngineStats = Field(default_factory=EngineStats)
    tool_calls_log: list[ToolCallRecord] = Field(default_factory=list)
    turns: int = 0

