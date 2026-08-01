"""Goal anchoring, milestone, and reflection models."""

import time
from enum import Enum

from pydantic import BaseModel, Field


class Milestone(BaseModel):
    id: int
    description: str
    completion_criteria: str
    estimated_complexity: str = "medium"


class MilestonePlan(BaseModel):
    goal_description: str
    milestones: list[Milestone] = Field(default_factory=list)
    backward_reasoning: str = ""


class GoalDocument(BaseModel):
    original_task: str
    success_criteria: str
    target_state_description: str
    key_deliverables: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    progress_pct: float = 0.0
    completed_milestones_summary: str = ""
    current_focus: str = ""
    updated_at: float = Field(default_factory=time.time)


class GoalAction(str, Enum):
    EXECUTE_TODO = "execute_todo"
    REPLAN = "replan"
    COMPLETE = "complete"


class GoalReflection(BaseModel):
    current_state_summary: str
    gap_analysis: str
    next_milestone: str
    progress_pct: float = 0.0
    suggested_action: GoalAction = GoalAction.EXECUTE_TODO
    reasoning: str = ""


class GoalReanchorResult(BaseModel):
    updated_goal_doc: GoalDocument
    goal_drift_detected: bool = False
    correction_applied: str = ""
