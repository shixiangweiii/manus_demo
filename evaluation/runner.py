"""Run evaluation cases through isolated runtime instances."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from core.events import EventBus, RuntimeEvent
from core.models import Effort, EngineKind
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
    "agentic_memory",
    "memory_tools",
    "knowledge",
    "skills",
    "self_evolution",
    "skill_auto_distill",
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

    def selected_runtime(
        self,
    ) -> tuple[EngineKind | None, Effort | None]:
        engine: EngineKind | None = None
        effort: Effort | None = None
        for event in reversed(self.events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if engine is None and event.engine:
                try:
                    engine = EngineKind(event.engine)
                except ValueError:
                    pass
            if effort is None and payload.get("effort"):
                try:
                    effort = Effort(payload["effort"])
                except ValueError:
                    pass
            if engine is not None and effort is not None:
                break
        return engine, effort


class EvaluationRunner:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()

    def validate_experiments(
        self,
        experiments: list[ExperimentSpec],
    ) -> None:
        """Validate a matrix before a host displays or persists it."""
        if not experiments:
            raise ValueError("evaluation requires at least one experiment")
        for experiment in experiments:
            self._settings_for_experiment(experiment)

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
        settings.capabilities.skills_user_dir = str(sandbox / "skills")
        collector = _Collector()
        events = EventBus()
        events.subscribe(collector)
        started_at = time.perf_counter()
        runtime = None
        try:
            runtime = await build_runtime(settings, events, interactive=False)
            run = RunSettings(
                engine=experiment.engine,
                effort=experiment.effort,
                capabilities=tuple(experiment.capabilities),
            )
            engine_result = await runtime.run(case.task_description, run)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            verifier = run_verifiers(case.verifiers, engine_result.output, str(sandbox))
            verifier_passed = verifier.all_passed
            success = engine_result.success and verifier_passed is not False
            return CaseResult(
                case_id=case.task_id,
                experiment=experiment,
                actual_engine=engine_result.engine,
                actual_effort=engine_result.effort,
                output=engine_result.output,
                metrics=CaseMetrics(
                    success=success,
                    engine_success=engine_result.success,
                    verifier_passed=verifier_passed,
                    stop_reason=engine_result.stop_reason,
                    llm_calls=engine_result.stats.llm_calls,
                    agent_turns=engine_result.stats.agent_turns,
                    context_compaction_calls=(
                        engine_result.stats.context_compaction_calls
                    ),
                    prompt_tokens=engine_result.stats.prompt_tokens,
                    completion_tokens=engine_result.stats.completion_tokens,
                    total_tokens=engine_result.stats.total_tokens,
                    latency_ms=elapsed_ms,
                    tool_calls=engine_result.stats.tool_calls,
                    reasoning_tokens=engine_result.stats.reasoning_tokens,
                    subagent_calls=engine_result.stats.subagent_calls,
                ),
                verifier_details=[asdict(detail) for detail in verifier.details],
                run_id=engine_result.run_id,
                trial=trial,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            actual_engine, actual_effort = collector.selected_runtime()
            return CaseResult(
                case_id=case.task_id,
                experiment=experiment,
                actual_engine=actual_engine,
                actual_effort=actual_effort,
                metrics=CaseMetrics(success=False, latency_ms=elapsed_ms),
                error=f"{type(exc).__name__}: {exc}",
                trial=trial,
            )
        finally:
            try:
                if runtime is not None:
                    await runtime.aclose()
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
        if not 1 <= repeat <= 5:
            raise ValueError("repeat must be between 1 and 5")
        self.validate_experiments(experiments)
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
