"""Models used by action executors and retained ReAct implementations."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReasoningEffort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolCallRecord(BaseModel):
    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: str = ""


class StepResult(BaseModel):
    step_id: int | str
    success: bool
    output: str = ""
    tool_calls_log: list[ToolCallRecord] = Field(default_factory=list)
    iterations_completed: int = 0
