"""Build explicit experiment matrices without touching global configuration."""

from __future__ import annotations

from itertools import product

from core.models import Effort, EngineKind, ExecutorKind
from evaluation.models import ExperimentSpec


def build_experiments(
    engines: list[str] | None = None,
    executors: list[str] | None = None,
    efforts: list[str] | None = None,
    capability_sets: list[list[str]] | None = None,
) -> list[ExperimentSpec]:
    engine_values = [EngineKind(value) for value in (engines or ["auto"])]
    if EngineKind.WORKFLOW in engine_values:
        raise ValueError("workflow evaluation requires workflow specifications and is not part of task auto-routing")
    executor_values = [ExecutorKind(value) for value in (executors or ["auto"])]
    effort_values = [Effort(value) for value in (efforts or ["auto"])]
    capability_values = capability_sets or [[]]
    return [
        ExperimentSpec(
            engine=engine,
            executor=executor,
            effort=effort,
            capabilities=capabilities,
        )
        for engine, executor, effort, capabilities in product(
            engine_values, executor_values, effort_values, capability_values
        )
    ]
