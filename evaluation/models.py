"""Models shared by static cases, generated sets, runners, reports, and UI."""

from __future__ import annotations

import math
import re
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from core.models import Effort, EngineKind, EngineStopReason

_VERIFIER_TYPES = {
    "file_exists",
    "file_contains",
    "json_field",
    "numeric_range",
    "regex_match",
    "keyword_include",
    "keyword_exclude",
    "composite_and",
    "composite_or",
}


def _validate_verifier_specs(specs: list[dict[str, Any]], path: str = "verifiers") -> None:
    for index, spec in enumerate(specs):
        location = f"{path}[{index}]"
        if not isinstance(spec, dict):
            raise ValueError(f"{location} must be an object")
        verifier_type = spec.get("type")
        if verifier_type not in _VERIFIER_TYPES:
            raise ValueError(f"{location}.type is not a supported deterministic verifier")
        params = spec.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"{location}.params must be an object")

        def require_text(name: str) -> str:
            value = params.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{location}.params.{name} must be non-empty text")
            return value

        source = params.get("source", "output")
        if source not in {"output", "file"}:
            raise ValueError(f"{location}.params.source must be output or file")
        if source == "file":
            require_text("path")

        if verifier_type in {"composite_and", "composite_or"}:
            nested = params.get("verifiers")
            if not isinstance(nested, list) or not nested:
                raise ValueError(f"{location}.params.verifiers must be a non-empty list")
            _validate_verifier_specs(nested, f"{location}.params.verifiers")
        elif verifier_type in {"keyword_include", "keyword_exclude"}:
            keywords = params.get("keywords")
            if (
                not isinstance(keywords, list)
                or not keywords
                or any(not isinstance(item, str) or not item.strip() for item in keywords)
            ):
                raise ValueError(
                    f"{location}.params.keywords must be a non-empty text list"
                )
        elif verifier_type == "regex_match":
            pattern = require_text("pattern")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{location}.params.pattern is invalid: {exc}") from exc
        elif verifier_type == "json_field":
            require_text("field")
        elif verifier_type == "numeric_range":
            if params.get("min") is None and params.get("max") is None:
                raise ValueError(f"{location}.params requires min and/or max")
            try:
                minimum = float(params["min"]) if params.get("min") is not None else None
                maximum = float(params["max"]) if params.get("max") is not None else None
                tolerance = float(params.get("tolerance", 0.01))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{location}.params numeric bounds must be numbers"
                ) from exc
            if any(
                value is not None and not math.isfinite(value)
                for value in (minimum, maximum)
            ) or not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError(f"{location}.params numeric bounds must be finite")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{location}.params.min must not exceed max")
        elif verifier_type == "file_exists":
            require_text("path")
        elif verifier_type == "file_contains":
            require_text("path")
            require_text("content")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TaskDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class EvaluationCase(BaseModel):
    task_id: str
    task_description: str
    difficulty: TaskDifficulty = TaskDifficulty.MEDIUM
    tags: list[str] = Field(default_factory=list)
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

    @field_validator("verifiers")
    @classmethod
    def validate_verifiers(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _validate_verifier_specs(value)
        return value


class ExperimentSpec(BaseModel):
    engine: EngineKind = EngineKind.AGENT_LOOP
    effort: Effort = Effort.AUTO
    capabilities: list[str] = Field(default_factory=list)

    @property
    def id(self) -> str:
        capability_label = "+".join(sorted(self.capabilities)) or "base"
        return f"{self.engine.value}__{self.effort.value}__{capability_label}"


class CaseMetrics(BaseModel):
    """Independent engine, verifier, cost, and latency observations."""

    success: bool = False
    engine_success: bool = False
    verifier_passed: bool | None = None
    stop_reason: EngineStopReason | None = None
    llm_calls: int = 0
    agent_turns: int = 0
    context_compaction_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    tool_calls: int = 0
    reasoning_tokens: int = 0
    subagent_calls: int = 0


class CaseResult(BaseModel):
    case_id: str
    experiment: ExperimentSpec
    actual_engine: EngineKind | None = None
    actual_effort: Effort | None = None
    output: str = ""
    metrics: CaseMetrics = Field(default_factory=CaseMetrics)
    verifier_details: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    run_id: str = ""
    trial: int = 1


class DimensionSummary(BaseModel):
    experiment_id: str
    cases: int = 0
    success_rate: float = 0.0
    engine_success_rate: float = 0.0
    verifier_rate: float | None = None
    stop_reason_counts: dict[str, int] = Field(default_factory=dict)
    average_llm_calls: float = 0.0
    average_agent_turns: float = 0.0
    average_context_compaction_calls: float = 0.0
    average_prompt_tokens: float = 0.0
    average_completion_tokens: float = 0.0
    average_total_tokens: float = 0.0
    average_latency_ms: float = 0.0
    average_tool_calls: float = 0.0
    average_reasoning_tokens: float = 0.0
    average_subagent_calls: float = 0.0
    stability: float | None = None


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
