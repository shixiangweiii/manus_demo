"""Run evaluation cases through isolated runtime instances."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from core.events import EventBus, RuntimeEvent
from core.models import EngineKind
from core.settings import AppSettings, RunSettings, get_settings, validate_settings
from evaluation.metrics import aggregate_results
from evaluation.models import (
    CaseMetrics,
    CaseResult,
    EvaluationCase,
    EvaluationReport,
    ExperimentSpec,
)
from evaluation.verifiers import run_verifiers
from runtime.factory import build_runtime

ProgressCallback = Callable[[int, int, EvaluationCase, ExperimentSpec], None]

_OPTIONAL_CAPABILITIES = (
    "subagent",
    "parallel_todos",
    "agentic_memory",
    "memory_tools",
    "knowledge",
    "skills",
    "self_evolution",
    "skill_auto_distill",
    "handoff",
    "remote_subagent",
    "guardrails",
    "mcp_bridge",
    "agentbay",
)


class _Collector:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def __call__(self, event: RuntimeEvent) -> None:
        self.events.append(event)

    def count(self, *names: str) -> int:
        wanted = set(names)
        return sum(event.name in wanted for event in self.events)


class EvaluationRunner:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()

    async def evaluate_case(
        self,
        case: EvaluationCase,
        experiment: ExperimentSpec,
        *,
        trial: int = 1,
    ) -> CaseResult:
        settings = self._settings_for_experiment(experiment)
        sandbox = Path(tempfile.mkdtemp(prefix="manus_evaluation_"))
        settings.paths.state_dir = str(sandbox / "state")
        settings.paths.sandbox_dir = str(sandbox)
        settings.paths.checkpoint_dir = str(sandbox / "checkpoints")
        collector = _Collector()
        events = EventBus()
        events.subscribe(collector)
        started_at = time.perf_counter()
        try:
            runtime = await build_runtime(settings, events, interactive=False)
            run = RunSettings(
                engine=experiment.engine,
                executor=experiment.executor,
                effort=experiment.effort,
                capabilities=tuple(experiment.capabilities),
            )
            engine_result = await runtime.run(case.task_description, run)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            verifier = run_verifiers(case.verifiers, engine_result.answer, str(sandbox))
            verifier_passed = verifier.all_passed
            success = engine_result.success and verifier_passed is not False
            expected = self._expected_engine(case)
            records = runtime.context.llm_client.get_call_records()
            return CaseResult(
                case_id=case.task_id,
                experiment=experiment,
                actual_engine=engine_result.engine,
                actual_executor=engine_result.executor,
                actual_effort=engine_result.effort,
                answer=engine_result.answer,
                metrics=CaseMetrics(
                    success=success,
                    verifier_passed=verifier_passed,
                    tokens=sum(record.total_tokens for record in records),
                    latency_ms=elapsed_ms,
                    tool_calls=collector.count("tool_completed"),
                    iterations=(
                        sum(action.iterations for action in engine_result.actions)
                        + collector.count("subagent_iteration")
                    ),
                    replans=collector.count("plan_adaptation", "replan_started"),
                    selector_correct=(
                        engine_result.engine.value == expected
                        if expected and experiment.engine == EngineKind.AUTO
                        else None
                    ),
                ),
                verifier_details=[asdict(detail) for detail in verifier.details],
                run_id=engine_result.run_id,
                trial=trial,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            return CaseResult(
                case_id=case.task_id,
                experiment=experiment,
                actual_engine=experiment.engine,
                actual_executor=experiment.executor,
                actual_effort=experiment.effort,
                metrics=CaseMetrics(success=False, latency_ms=elapsed_ms),
                error=f"{type(exc).__name__}: {exc}",
                trial=trial,
            )
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    async def evaluate_matrix(
        self,
        cases: list[EvaluationCase],
        experiments: list[ExperimentSpec],
        *,
        repeat: int = 1,
        on_progress: ProgressCallback | None = None,
    ) -> EvaluationReport:
        if not cases:
            raise ValueError("evaluation requires at least one case")
        if not experiments:
            raise ValueError("evaluation requires at least one experiment")
        if not 1 <= repeat <= 5:
            raise ValueError("repeat must be between 1 and 5")
        for experiment in experiments:
            self._settings_for_experiment(experiment)
        total = len(cases) * len(experiments) * repeat
        completed = 0
        results: list[CaseResult] = []
        for experiment in experiments:
            for case in cases:
                for trial in range(1, repeat + 1):
                    result = await self.evaluate_case(case, experiment, trial=trial)
                    results.append(result)
                    completed += 1
                    if on_progress:
                        on_progress(completed, total, case, experiment)
        return EvaluationReport(results=results, summaries=aggregate_results(results))

    def _settings_for_experiment(self, experiment: ExperimentSpec) -> AppSettings:
        settings = self.settings.clone()
        for name in _OPTIONAL_CAPABILITIES:
            setattr(settings.capabilities, name, False)
        unknown = set(experiment.capabilities) - set(_OPTIONAL_CAPABILITIES)
        if unknown:
            raise ValueError(f"Unknown evaluation capabilities: {', '.join(sorted(unknown))}")
        for name in experiment.capabilities:
            setattr(settings.capabilities, name, True)
        validate_settings(settings)
        return settings

    @staticmethod
    def _expected_engine(case: EvaluationCase) -> str:
        expected = case.ground_truth.expected_engine or case.ground_truth.expected_complexity
        return {
            "simple": "sequential",
            "complex": "dag",
            "emergent": "todo",
        }.get(expected, expected)
