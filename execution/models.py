"""Models shared by action executors and tool-calling loops."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from core.models import Effort, EngineStopReason


class ResolvedEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def resolve_effort(effort: Effort) -> ResolvedEffort:
    """Resolve the public effort policy to an internal non-auto level."""
    if effort == Effort.LOW:
        return ResolvedEffort.LOW
    if effort == Effort.HIGH:
        return ResolvedEffort.HIGH
    return ResolvedEffort.MEDIUM


class ToolCallRecord(BaseModel):
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: str = ""


class ActionLoopStats(BaseModel):
    """Usage observed dynamically while one action loop is running."""

    llm_calls: int = 0
    tool_calls: int = 0
    reasoning_tokens: int = 0


class StepResult(BaseModel):
    step_id: int | str
    success: bool
    output: str = ""
    tool_calls_log: list[ToolCallRecord] = Field(default_factory=list)
    iterations_completed: int = 0
    stats: ActionLoopStats = Field(default_factory=ActionLoopStats)
    failure_reason: EngineStopReason | None = None
