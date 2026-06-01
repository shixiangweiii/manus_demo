"""
Named benchmark suites for evaluation matrix runs.

Suites keep the task selection policy close to the evaluation code, so repeated
runs compare the same workloads across reasoning variants.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from evaluation.benchmark import get_benchmark_tasks
from evaluation.metrics import PlanMode


class EvaluationSuite(BaseModel):
    """A reproducible subset of benchmark tasks."""

    id: str
    description: str = ""
    task_ids: list[str] = Field(default_factory=list)
    default_modes: list[PlanMode] = Field(default_factory=list)
    recommended_variants: list[str] = Field(default_factory=list)
    default_repeat: int = 1


ALL_MODES = [PlanMode.SIMPLE, PlanMode.COMPLEX, PlanMode.EMERGENT]


EVALUATION_SUITES: dict[str, EvaluationSuite] = {
    "smoke_reasoning": EvaluationSuite(
        id="smoke_reasoning",
        description="Low-cost verifier-heavy smoke suite for matrix plumbing.",
        task_ids=[
            "easy_002",
            "easy_003",
            "easy_006",
            "goal_easy_001",
            "medium_006",
            "file_multi_001",
            "resume_001",
            "safety_004",
        ],
        default_modes=ALL_MODES,
        recommended_variants=[
            "react_auto_baseline",
            "reasoning_auto",
            "reasoning_low",
            "reasoning_high",
        ],
        default_repeat=2,
    ),
    "core_reasoning": EvaluationSuite(
        id="core_reasoning",
        description="Main deterministic-ish reasoning suite across simple, complex, emergent, resume, and safety tasks.",
        task_ids=[
            "easy_001",
            "easy_002",
            "easy_003",
            "easy_004",
            "easy_006",
            "medium_001",
            "medium_002",
            "medium_003",
            "medium_006",
            "hard_002",
            "hard_003",
            "hard_005",
            "file_multi_001",
            "file_multi_002",
            "resume_001",
            "resume_002",
            "safety_001",
            "safety_002",
            "safety_003",
            "safety_004",
        ],
        default_modes=ALL_MODES,
        recommended_variants=[
            "react_auto_baseline",
            "reasoning_auto",
            "reasoning_low",
            "reasoning_medium",
            "reasoning_high",
        ],
        default_repeat=3,
    ),
    "live_web": EvaluationSuite(
        id="live_web",
        description="Noisy live web/fetch tasks, reported separately from core correctness.",
        task_ids=[
            "easy_005",
            "medium_005",
            "webfetch_001",
            "webfetch_002",
        ],
        default_modes=ALL_MODES,
        recommended_variants=[
            "react_auto_baseline",
            "reasoning_auto",
            "reasoning_high",
        ],
        default_repeat=2,
    ),
    "agentic_capabilities": EvaluationSuite(
        id="agentic_capabilities",
        description="Focused GoalDriven, SubAgent, HITL, and resume capability suite.",
        task_ids=[
            "subagent_easy_001",
            "subagent_medium_001",
            "subagent_medium_002",
            "subagent_hard_001",
            "hitl_easy_001",
            "hitl_easy_002",
            "hitl_medium_001",
            "hitl_hard_001",
            "goal_easy_001",
            "goal_medium_001",
            "goal_hard_001",
            "resume_001",
            "resume_002",
        ],
        default_modes=ALL_MODES,
        recommended_variants=[
            "react_auto_baseline",
            "reasoning_auto",
            "goal_driven_auto",
            "subagent_on",
        ],
        default_repeat=3,
    ),
    "full": EvaluationSuite(
        id="full",
        description="All benchmark tasks; use for milestone regression runs.",
        task_ids=[],
        default_modes=ALL_MODES,
        recommended_variants=[
            "react_auto_baseline",
            "reasoning_auto",
            "reasoning_medium",
            "reasoning_high",
        ],
        default_repeat=3,
    ),
    "memory_agentic": EvaluationSuite(
        id="memory_agentic",
        description="Agentic Memory recall/store/correction/poisoning prevention benchmarks.",
        task_ids=[
            "memory_fact_write_001",
            "memory_fact_recall_001",
            "memory_experience_write_001",
            "memory_experience_recall_001",
            "memory_correction_001",
            "memory_poisoning_001",
        ],
        default_modes=[PlanMode.SIMPLE, PlanMode.EMERGENT],
        recommended_variants=["react_auto_baseline", "agentic_memory_on"],
        default_repeat=3,
    ),
    "mcp_bridge": EvaluationSuite(
        id="mcp_bridge",
        description="MCP Bridge tool discovery, schema adaptation, execution, and error handling benchmarks.",
        task_ids=[
            "mcp_stdio_001",
            "mcp_http_001",
            "mcp_schema_001",
            "mcp_failure_001",
        ],
        default_modes=[PlanMode.SIMPLE],
        recommended_variants=["mcp_bridge_on"],
        default_repeat=2,
    ),
    "multi_agent": EvaluationSuite(
        id="multi_agent",
        description="v18.5 multi-agent collaboration: Handoff/SubAgent delegation ROI vs single-agent baseline.",
        task_ids=[
            "multi_agent_001",
            "multi_agent_002",
            "multi_agent_003",
            "subagent_easy_001",
            "subagent_medium_001",
        ],
        default_modes=[PlanMode.EMERGENT],
        recommended_variants=["react_auto_baseline", "handoff_on", "subagent_on"],
        default_repeat=2,
    ),
    "red_team": EvaluationSuite(
        id="red_team",
        description="v19.4 AgentDojo-style security red-team: attack cases + benign controls; guardrails_on vs baseline.",
        task_ids=[
            "safety_001",
            "safety_002",
            "safety_003",
            "safety_004",
            "redteam_tool_injection_001",
            "redteam_exfil_001",
            "redteam_memory_poison_001",
            "redteam_benign_write_001",
            "redteam_benign_search_001",
        ],
        default_modes=[PlanMode.SIMPLE],
        recommended_variants=["react_auto_baseline", "guardrails_on"],
        default_repeat=2,
    ),
    "skill": EvaluationSuite(
        id="skill",
        description="v20.4 Skill activation quality: activation rate, false activation, output quality, token overhead.",
        task_ids=[
            "skill_activate_001", "skill_activate_002", "skill_activate_003",
            "skill_activate_004", "skill_activate_005", "skill_activate_006", "skill_activate_007",
            "skill_noactivate_001", "skill_noactivate_002", "skill_noactivate_003", "skill_noactivate_004",
            "skill_security_001", "skill_security_002", "skill_security_003",
        ],
        default_modes=[PlanMode.SIMPLE, PlanMode.EMERGENT],
        recommended_variants=["react_auto_baseline", "skills_on"],
        default_repeat=2,
    ),
}


def list_suites() -> list[EvaluationSuite]:
    """Return all registered suites in stable order."""
    return list(EVALUATION_SUITES.values())


def get_suite(suite_id: str) -> EvaluationSuite:
    """Return one suite by id."""
    try:
        return EVALUATION_SUITES[suite_id]
    except KeyError as exc:
        known = ", ".join(sorted(EVALUATION_SUITES))
        raise ValueError(f"Unknown evaluation suite '{suite_id}'. Known suites: {known}") from exc


def resolve_suite_task_ids(suite: EvaluationSuite, override_task_ids: list[str] | None = None) -> list[str]:
    """Resolve task ids for a suite, with optional CLI override."""
    if override_task_ids:
        return override_task_ids
    if suite.task_ids:
        return suite.task_ids
    return [task.task_id for task in get_benchmark_tasks()]


def validate_suite_tasks(suite: EvaluationSuite) -> None:
    """Raise ValueError if a suite references unknown benchmark tasks."""
    known = {task.task_id for task in get_benchmark_tasks()}
    task_ids = resolve_suite_task_ids(suite)
    missing = [task_id for task_id in task_ids if task_id not in known]
    if missing:
        raise ValueError(f"Suite '{suite.id}' references unknown task ids: {', '.join(missing)}")
