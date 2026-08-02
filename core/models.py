"""Small, stable models shared by engines, hosts, and evaluation."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EngineKind(str, Enum):
    SEQUENTIAL = "sequential"
    DAG = "dag"
    AGENT_LOOP = "agent_loop"


class EngineStopReason(str, Enum):
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    TIMEOUT = "timeout"
    MODEL_ERROR = "model_error"
    INVALID_RESPONSE = "invalid_response"
    ENGINE_ERROR = "engine_error"


class Effort(str, Enum):
    AUTO = "auto"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolInvocation(BaseModel):
    """One tool call made while executing an action."""

    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: str = ""


class Action(BaseModel):
    """An engine-independent unit of work."""

    id: str
    description: str
    success_criteria: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "description")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class EngineStats(BaseModel):
    """Small cross-engine counters suitable for comparison and tracing."""

    llm_calls: int = 0
    tool_calls: int = 0
    reasoning_tokens: int = 0
    subagent_calls: int = 0


class ActionResult(BaseModel):
    """Result returned by any action executor."""

    action_id: str
    success: bool
    output: str = ""
    tool_calls: list[ToolInvocation] = Field(default_factory=list)
    iterations: int = 0
    error: str | None = None
    failure_reason: EngineStopReason | None = None
    stats: EngineStats = Field(default_factory=EngineStats)

    def to_legacy(self):
        """Adapt to retained peripheral models at the runtime boundary."""
        from execution.models import ActionLoopStats, StepResult, ToolCallRecord

        return StepResult(
            step_id=self.action_id,
            success=self.success,
            output=self.output,
            tool_calls_log=[
                ToolCallRecord(
                    tool_name=call.tool_name,
                    parameters=call.parameters,
                    result=call.result,
                )
                for call in self.tool_calls
            ],
            iterations_completed=self.iterations,
            failure_reason=self.failure_reason,
            stats=ActionLoopStats(
                llm_calls=self.stats.llm_calls,
                tool_calls=self.stats.tool_calls,
                reasoning_tokens=self.stats.reasoning_tokens,
            ),
        )


class TaskRequest(BaseModel):
    """Stable input accepted by all task engines."""

    task: str
    context: str = ""
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=128)
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8], max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def validate_task(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task must not be empty")
        return value

    @field_validator("run_id", "task_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not value or any(
            not (char.isalnum() or char in "-_")
            for char in value
        ):
            raise ValueError("must contain only letters, numbers, '-' and '_'")
        return value


class EngineResult(BaseModel):
    """Stable output returned by all task engines."""

    output: str
    success: bool
    engine: EngineKind
    effort: Effort
    run_id: str
    task_id: str
    stop_reason: EngineStopReason
    stats: EngineStats = Field(default_factory=EngineStats)
    actions: list[ActionResult] = Field(default_factory=list)
    started_at: float = Field(default_factory=time.time)
    finished_at: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at) * 1000)
