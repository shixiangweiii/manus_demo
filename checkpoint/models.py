"""
Checkpoint data models for Task Resume (v14.5).

Each execution path (simple / DAG / emergent / goal-driven) has its own
state model that captures the minimal data needed to resume from the
last execution boundary.

任务恢复的 Checkpoint 数据模型。
每条执行路径有独立的状态模型，仅捕获恢复到最近可执行边界所需的最小数据。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from schema import TaskRunState


# ======================================================================
# Exceptions
# ======================================================================

class CheckpointError(Exception):
    """Base exception for checkpoint operations."""


class CheckpointCorruptedError(CheckpointError):
    """Checkpoint file contains invalid JSON."""


class CheckpointValidationError(CheckpointError):
    """Checkpoint data fails Pydantic validation."""


class CheckpointVersionMismatchError(CheckpointError):
    """Checkpoint schema version is incompatible with current code."""


# ======================================================================
# Path-Specific State Models
# ======================================================================

class SimplePathState(BaseModel):
    """Checkpoint state for the simple (flat plan) execution path.
    Simple 路径的 checkpoint 状态。"""
    plan: dict = Field(description="Plan.model_dump() — step statuses preserved")
    all_results: list[dict] = Field(
        default_factory=list,
        description="[StepResult.model_dump(), ...] — completed step results",
    )
    attempt: int = Field(default=0, description="Replan attempt counter")
    reflection: dict | None = Field(default=None, description="Reflection.model_dump() or None")
    current_step_index: int = Field(default=0, description="Index of the next step to execute")
    # v20.4: Skill activation state at checkpoint time
    active_skills: list[str] = Field(default_factory=list, description="Skill names active at checkpoint time")
    skill_activation_count: int = Field(default=0, description="Skill activation count at checkpoint time")


class DAGPathState(BaseModel):
    """Checkpoint state for the DAG (complex) execution path.
    DAG 路径的 checkpoint 状态。"""
    dag: dict = Field(description="TaskDAG.to_dict() — node statuses and results preserved")
    results: list[dict] = Field(
        default_factory=list,
        description="[StepResult.model_dump(), ...] — ACTION node results",
    )
    attempt: int = Field(default=0, description="Replan attempt counter")
    reflection: dict | None = Field(default=None, description="Reflection.model_dump() or None")
    # v20.4: Skill activation state at checkpoint time
    active_skills: list[str] = Field(default_factory=list, description="Skill names active at checkpoint time")
    skill_activation_count: int = Field(default=0, description="Skill activation count at checkpoint time")


class EmergentPathState(BaseModel):
    """Checkpoint state for the emergent (TODO-list) execution path.
    Emergent 路径的 checkpoint 状态。"""
    todo_list: dict = Field(description="TodoList.model_dump() — TODO statuses and results preserved")
    all_results: list[dict] = Field(
        default_factory=list,
        description="[StepResult.model_dump(), ...]",
    )
    iteration: int = Field(default=0, description="Outer loop iteration counter")
    stagnation_state: dict = Field(
        default_factory=dict,
        description='{"prev_completed": int, "stagnation_rounds": int}',
    )
    # v20.4: Skill activation state at checkpoint time
    active_skills: list[str] = Field(default_factory=list, description="Skill names active at checkpoint time")
    skill_activation_count: int = Field(default=0, description="Skill activation count at checkpoint time")


class GoalDrivenPathState(BaseModel):
    """Checkpoint state for the goal-driven execution path.
    Goal-Driven 路径的 checkpoint 状态。"""
    todo_list: dict = Field(description="TodoList.model_dump()")
    all_results: list[dict] = Field(default_factory=list)
    iteration: int = Field(default=0)
    stagnation_state: dict = Field(default_factory=dict)
    goal_doc: dict = Field(description="GoalDocument.model_dump()")
    milestone_plan: dict | None = Field(default=None, description="MilestonePlan.model_dump()")
    last_reflection: dict | None = Field(default=None, description="GoalReflection.model_dump()")
    reanchor_counter: int = Field(default=0)
    # v20.4: Skill activation state at checkpoint time
    active_skills: list[str] = Field(default_factory=list, description="Skill names active at checkpoint time")
    skill_activation_count: int = Field(default=0, description="Skill activation count at checkpoint time")


# ======================================================================
# Top-Level Checkpoint Model
# ======================================================================

CHECKPOINT_VERSION = 2  # v20.4: adds active_skills + skill_activation_count to PathState


class TaskCheckpoint(BaseModel):
    """Top-level checkpoint capturing all state needed to resume a task.
    顶层 checkpoint，捕获恢复任务所需的全部状态。"""
    task_id: str
    task: str
    context: str
    complexity: str = Field(description='"simple" | "complex" | "emergent" | "goal_driven"')
    effort: str = Field(description="ReasoningEffort value: low / medium / high")
    state: TaskRunState = TaskRunState.RUNNING

    created_at: float
    updated_at: float
    checkpoint_version: int = CHECKPOINT_VERSION

    # Only one of these is populated per checkpoint
    simple_state: SimplePathState | None = None
    dag_state: DAGPathState | None = None
    emergent_state: EmergentPathState | None = None
    goal_driven_state: GoalDrivenPathState | None = None

    # Cross-path metadata
    token_usage_snapshot: dict | None = None
    hitl_prompt_count: int = 0
    resume_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Diagnostic resume boundary metadata; not required for schema compatibility",
    )


class TaskCheckpointSummary(BaseModel):
    """Lightweight summary for listing checkpointed tasks.
    checkpointed 任务的轻量摘要，用于列表展示。"""
    task_id: str
    task: str
    complexity: str
    state: TaskRunState
    updated_at: float
    file_path: str
