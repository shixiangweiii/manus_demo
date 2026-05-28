"""
Evaluation variants for reasoning-engine matrix runs.

Variants are named bundles of config overrides. They let evaluation scripts run
ReAct, ReasoningEngine, goal-driven, SubAgent, and DAG-parallel comparisons
without relying on ad-hoc shell environment changes.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from pydantic import BaseModel, Field

from evaluation.metrics import PlanMode


class EvaluationVariant(BaseModel):
    """A reproducible evaluation configuration bundle."""

    id: str
    description: str = ""
    env_overrides: dict[str, Any] = Field(default_factory=dict)
    modes: list[PlanMode] = Field(default_factory=list)
    expected_focus: str = ""
    baseline_variant: str = "react_auto_baseline"


ALL_MODES = [PlanMode.SIMPLE, PlanMode.COMPLEX, PlanMode.EMERGENT]


EVALUATION_VARIANTS: dict[str, EvaluationVariant] = {
    "react_auto_baseline": EvaluationVariant(
        id="react_auto_baseline",
        description="Baseline ReActEngine with automatic effort routing.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": False,
            "REASONING_EFFORT": "auto",
            "ENABLE_GOAL_DRIVEN_PLANNER": False,
            "SUBAGENT_ENABLED": False,
            "DAG_SERIAL_EXECUTION": True,
        },
        modes=ALL_MODES,
        expected_focus="baseline",
    ),
    "reasoning_auto": EvaluationVariant(
        id="reasoning_auto",
        description="ReasoningEngine enabled with planner-selected effort.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": True,
            "REASONING_EFFORT": "auto",
            "ENABLE_GOAL_DRIVEN_PLANNER": False,
            "SUBAGENT_ENABLED": False,
            "DAG_SERIAL_EXECUTION": True,
        },
        modes=ALL_MODES,
        expected_focus="default reasoning candidate",
    ),
    "reasoning_low": EvaluationVariant(
        id="reasoning_low",
        description="ReasoningEngine with low effort.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": True,
            "REASONING_EFFORT": "low",
            "ENABLE_GOAL_DRIVEN_PLANNER": False,
            "SUBAGENT_ENABLED": False,
            "DAG_SERIAL_EXECUTION": True,
        },
        modes=ALL_MODES,
        expected_focus="cost-sensitive reasoning",
    ),
    "reasoning_medium": EvaluationVariant(
        id="reasoning_medium",
        description="ReasoningEngine with medium effort.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": True,
            "REASONING_EFFORT": "medium",
            "ENABLE_GOAL_DRIVEN_PLANNER": False,
            "SUBAGENT_ENABLED": False,
            "DAG_SERIAL_EXECUTION": True,
        },
        modes=ALL_MODES,
        expected_focus="balanced reasoning",
    ),
    "reasoning_high": EvaluationVariant(
        id="reasoning_high",
        description="ReasoningEngine with high effort.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": True,
            "REASONING_EFFORT": "high",
            "ENABLE_GOAL_DRIVEN_PLANNER": False,
            "SUBAGENT_ENABLED": False,
            "DAG_SERIAL_EXECUTION": True,
        },
        modes=ALL_MODES,
        expected_focus="hard/emergent reasoning ceiling",
    ),
    "goal_driven_auto": EvaluationVariant(
        id="goal_driven_auto",
        description="Goal-driven emergent planner with ReasoningEngine auto effort.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": True,
            "REASONING_EFFORT": "auto",
            "ENABLE_GOAL_DRIVEN_PLANNER": True,
            "SUBAGENT_ENABLED": False,
            "DAG_SERIAL_EXECUTION": True,
        },
        modes=[PlanMode.EMERGENT],
        expected_focus="goal-driven planning",
    ),
    "subagent_on": EvaluationVariant(
        id="subagent_on",
        description="SubAgent-enabled emergent runs with ReasoningEngine auto effort.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": True,
            "REASONING_EFFORT": "auto",
            "ENABLE_GOAL_DRIVEN_PLANNER": False,
            "SUBAGENT_ENABLED": True,
            "DAG_SERIAL_EXECUTION": True,
        },
        modes=[PlanMode.EMERGENT],
        expected_focus="delegation ROI",
    ),
    "dag_parallel": EvaluationVariant(
        id="dag_parallel",
        description="DAG parallel execution with ReasoningEngine auto effort.",
        env_overrides={
            "ENABLE_REASONING_ENGINE": True,
            "REASONING_EFFORT": "auto",
            "ENABLE_GOAL_DRIVEN_PLANNER": False,
            "SUBAGENT_ENABLED": False,
            "DAG_SERIAL_EXECUTION": False,
        },
        modes=[PlanMode.COMPLEX],
        expected_focus="parallel DAG throughput",
    ),
    "agentic_memory_on": EvaluationVariant(
        id="agentic_memory_on",
        description="Agentic Memory enabled with memory tools for recall/store/correction.",
        env_overrides={
            "AGENTIC_MEMORY_ENABLED": True,
            "MEMORY_TOOLS_ENABLED": True,
        },
        modes=[PlanMode.SIMPLE, PlanMode.EMERGENT],
        expected_focus="memory recall and storage",
    ),
}


def list_variants() -> list[EvaluationVariant]:
    """Return all registered variants in stable order."""
    return list(EVALUATION_VARIANTS.values())


def get_variant(variant_id: str) -> EvaluationVariant:
    """Return one variant by id."""
    try:
        return EVALUATION_VARIANTS[variant_id]
    except KeyError as exc:
        known = ", ".join(sorted(EVALUATION_VARIANTS))
        raise ValueError(f"Unknown evaluation variant '{variant_id}'. Known variants: {known}") from exc


def get_variants(variant_ids: list[str] | None) -> list[EvaluationVariant]:
    """Return selected variants, or all variants when ids is None/empty."""
    if not variant_ids:
        return list_variants()
    return [get_variant(v) for v in variant_ids]


@contextmanager
def apply_variant(variant: EvaluationVariant) -> Iterator[None]:
    """
    Temporarily apply a variant's config overrides to the live config module.

    EvaluationRunner already restores task-scoped feature toggles. This context
    handles variant-scoped toggles around the whole run and restores them even
    when a task crashes.
    """
    import config

    originals: dict[str, Any] = {}
    for key, value in variant.env_overrides.items():
        if not hasattr(config, key):
            raise AttributeError(f"config has no attribute '{key}' for variant '{variant.id}'")
        originals[key] = getattr(config, key)
        setattr(config, key, value)
    try:
        yield
    finally:
        for key, value in originals.items():
            setattr(config, key, value)
