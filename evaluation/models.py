"""Models shared by static cases, generated sets, runners, reports, and UI."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from core.models import Effort, EngineKind, ExecutorKind


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TaskDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class GroundTruth(BaseModel):
    expected_engine: str = ""
    expected_complexity: str = ""
    expected_step_count_range: tuple[int, int] = (1, 10)
    expected_tools: list[str] = Field(default_factory=list)
    expected_subtasks: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    must_include_keywords: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    reference_output: str = ""
    expected_hitl_calls: tuple[int, int] | None = None
    expected_subagent_calls: tuple[int, int] | None = None
    expected_handoff_calls: tuple[int, int] | None = None
    expected_skill_activations: tuple[int, int] | None = None
    is_attack: bool = False
    expected_goal_features: list[str] | None = None
    simulated_responses: list[str] | None = None


class EvaluationCase(BaseModel):
    task_id: str
    task_description: str
    difficulty: TaskDifficulty = TaskDifficulty.MEDIUM
    tags: list[str] = Field(default_factory=list)
    ground_truth: GroundTruth = Field(default_factory=GroundTruth)
    verifiers: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 128 or any(
            not (char.isalnum() or char in "-_") for char in value
        ):
            raise ValueError("task_id must contain only letters, numbers, '-' and '_'")
        return value

    @field_validator("task_description")
    @classmethod
    def validate_task_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task_description must not be empty")
        return value


class ExperimentSpec(BaseModel):
    engine: EngineKind = EngineKind.AUTO
    executor: ExecutorKind = ExecutorKind.AUTO
    effort: Effort = Effort.AUTO
    capabilities: list[str] = Field(default_factory=list)

    @property
    def id(self) -> str:
        capability_label = "+".join(sorted(self.capabilities)) or "base"
        return f"{self.engine.value}__{self.executor.value}__{self.effort.value}__{capability_label}"


class CaseMetrics(BaseModel):
    success: bool = False
    verifier_passed: bool | None = None
    tokens: int = 0
    latency_ms: float = 0.0
    tool_calls: int = 0
    iterations: int = 0
    replans: int = 0
    selector_correct: bool | None = None


class CaseResult(BaseModel):
    case_id: str
    experiment: ExperimentSpec
    actual_engine: EngineKind
    actual_executor: ExecutorKind
    actual_effort: Effort
    answer: str = ""
    metrics: CaseMetrics = Field(default_factory=CaseMetrics)
    verifier_details: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    run_id: str = ""
    trial: int = 1


class DimensionSummary(BaseModel):
    experiment_id: str
    cases: int = 0
    success_rate: float = 0.0
    verifier_rate: float | None = None
    average_tokens: float = 0.0
    average_latency_ms: float = 0.0
    average_tool_calls: float = 0.0
    average_iterations: float = 0.0
    average_replans: float = 0.0
    stability: float | None = None
    selector_accuracy: float | None = None


class EvaluationReport(BaseModel):
    report_id: str = Field(default_factory=lambda: new_id("report"))
    results: list[CaseResult] = Field(default_factory=list)
    summaries: list[DimensionSummary] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class DocumentRecord(BaseModel):
    doc_id: str = Field(default_factory=lambda: new_id("doc"))
    filename: str = ""
    title: str = ""
    content: str = ""
    char_count: int = 0
    content_hash: str = ""
    created_at: float = Field(default_factory=time.time)


class EvalSetStatus(str, Enum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class GeneratedEvalSet(BaseModel):
    evalset_id: str = Field(default_factory=lambda: new_id("es"))
    name: str = ""
    doc_id: str = ""
    doc_filename: str = ""
    target_goal: str = ""
    status: EvalSetStatus = EvalSetStatus.GENERATING
    generator: str = "heuristic"
    generation_model: str = ""
    generation_error: str = ""
    requested_num_tasks: int = 0
    tasks: list[EvaluationCase] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunProgress(BaseModel):
    total_units: int = 0
    completed_units: int = 0
    current_task_id: str = ""
    current_experiment: str = ""


class EvalRunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    evalset_id: str = ""
    evalset_name: str = ""
    doc_id: str = ""
    experiments: list[ExperimentSpec] = Field(default_factory=list)
    repeat: int = 1
    status: RunStatus = RunStatus.PENDING
    error: str = ""
    llm_model: str = ""
    progress: RunProgress = Field(default_factory=RunProgress)
    report: EvaluationReport | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    created_at: float = Field(default_factory=time.time)


class Suggestion(BaseModel):
    title: str
    severity: str = "info"
    evidence: str = ""
    action: str = ""


class AggregateAnalysis(BaseModel):
    analysis_id: str = Field(default_factory=lambda: new_id("analysis"))
    run_ids: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
