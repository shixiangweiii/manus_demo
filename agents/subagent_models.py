"""Subagent lifecycle and summary contracts."""

from enum import Enum

from pydantic import BaseModel, Field

from execution.models import ToolCallRecord


class SubAgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class SubAgentSummary(BaseModel):
    accomplished: str = ""
    findings: str = ""
    issues: str = ""
    artifacts: list[str] = Field(default_factory=list)
    tool_calls_summary: str = ""


class SubAgentResult(BaseModel):
    subagent_id: str
    task_description: str
    status: SubAgentStatus
    summary: SubAgentSummary = Field(default_factory=SubAgentSummary)
    summary_text: str = ""
    tool_calls_count: int = 0
    iterations_used: int = 0
    duration_ms: float = 0.0
    tokens_used: int = 0
    tool_calls_log: list[ToolCallRecord] = Field(default_factory=list)
