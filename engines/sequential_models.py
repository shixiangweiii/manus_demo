"""Plan and reflection models for sequential orchestration."""

from enum import Enum

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Step(BaseModel):
    id: int
    description: str
    dependencies: list[int] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: str | None = None


class Plan(BaseModel):
    task: str
    steps: list[Step] = Field(default_factory=list)
    current_step_index: int = 0


class Reflection(BaseModel):
    passed: bool
    score: float = 0.0
    feedback: str = ""
    suggestions: list[str] = Field(default_factory=list)
