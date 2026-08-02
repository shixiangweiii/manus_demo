"""Build explicit experiment matrices without touching global configuration."""

from __future__ import annotations

from itertools import product

from core.models import Effort, EngineKind
from evaluation.models import ExperimentSpec


def build_experiments(
    engines: list[str] | None = None,
    efforts: list[str] | None = None,
    capability_sets: list[list[str]] | None = None,
) -> list[ExperimentSpec]:
    engine_values = [EngineKind(value) for value in (engines or [kind.value for kind in EngineKind])]
    effort_values = [Effort(value) for value in (efforts or ["auto"])]
    capability_values = capability_sets or [[]]
    return [
        ExperimentSpec(
            engine=engine,
            effort=effort,
            capabilities=capabilities,
        )
        for engine, effort, capabilities in product(
            engine_values, effort_values, capability_values
        )
    ]
