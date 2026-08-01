"""Load built-in and generated cases through one model."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.models import EvaluationCase, TaskDifficulty

CASES_DIR = Path(__file__).resolve().parent / "cases"


def load_case_file(path: str | Path) -> list[EvaluationCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("cases", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"Evaluation case file must contain a list: {path}")
    return [EvaluationCase.model_validate(row) for row in rows]


def load_cases(
    paths: list[str | Path] | None = None,
    difficulty: TaskDifficulty | str | None = None,
    tags: list[str] | None = None,
    task_ids: list[str] | None = None,
) -> list[EvaluationCase]:
    files = [Path(path) for path in paths] if paths else sorted(CASES_DIR.glob("*.json"))
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for path in files:
        for case in load_case_file(path):
            if case.task_id in seen:
                raise ValueError(f"Duplicate evaluation case id: {case.task_id}")
            seen.add(case.task_id)
            cases.append(case)
    if difficulty:
        difficulty_value = TaskDifficulty(difficulty)
        cases = [case for case in cases if case.difficulty == difficulty_value]
    if tags:
        cases = [case for case in cases if any(tag in case.tags for tag in tags)]
    if task_ids:
        wanted = set(task_ids)
        cases = [case for case in cases if case.task_id in wanted]
    return cases
