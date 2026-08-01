"""Platform adapter around the unified evaluation runner."""

from __future__ import annotations

import time
from typing import Callable

from core.settings import AppSettings, get_settings
from evaluation.models import EvalRunRecord, GeneratedEvalSet, RunStatus
from evaluation.runner import EvaluationRunner

MAX_REPEAT = 5


def validate_repeat(value) -> int:
    try:
        repeat = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("repeat must be an integer") from exc
    if not 1 <= repeat <= MAX_REPEAT:
        raise ValueError(f"repeat must be between 1 and {MAX_REPEAT}")
    return repeat


class EvalSetExecutor:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()

    async def execute(
        self,
        evalset: GeneratedEvalSet,
        run: EvalRunRecord,
        on_progress: Callable[[EvalRunRecord], None] | None = None,
    ) -> EvalRunRecord:
        if not evalset.tasks:
            run.status = RunStatus.FAILED
            run.error = "评测集中没有任务"
            return run
        run.repeat = validate_repeat(run.repeat)
        run.status = RunStatus.RUNNING
        run.started_at = time.time()
        run.progress.total_units = len(evalset.tasks) * len(run.experiments) * run.repeat

        def progress(completed, total, case, experiment):
            run.progress.completed_units = completed
            run.progress.total_units = total
            run.progress.current_task_id = case.task_id
            run.progress.current_experiment = experiment.id
            if on_progress:
                on_progress(run)

        try:
            run.report = await EvaluationRunner(self.settings).evaluate_matrix(
                evalset.tasks,
                run.experiments,
                repeat=run.repeat,
                on_progress=progress,
            )
            run.status = RunStatus.COMPLETED
            run.llm_model = self.settings.llm.model
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = time.time()
        run.progress.current_task_id = ""
        run.progress.current_experiment = ""
        if on_progress:
            on_progress(run)
        return run
