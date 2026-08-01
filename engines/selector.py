"""Independent engine, executor, and effort selection policies."""

from __future__ import annotations

from core.models import Effort, EngineKind, ExecutorKind
from core.settings import AppSettings, RunSettings


class EngineSelector:
    """Select orchestration semantics; never chooses the action executor."""

    _GOAL_MARKERS = (
        "成功标准", "完成标准", "最终目标", "目标状态", "约束条件", "交付物",
        "success criteria", "definition of done", "target state", "deliverable",
    )
    _TODO_MARKERS = (
        "探索", "调研", "研究", "逐步尝试", "不确定", "边做边", "迭代调查",
        "explore", "research", "investigate", "figure out", "iteratively",
    )
    _DAG_MARKERS = (
        "并行", "分别", "同时", "依赖", "依赖图", "对比", "比较",
        "parallel", "independent", "depends on", "compare", "in parallel",
    )

    async def select(self, task: str, run: RunSettings) -> tuple[EngineKind, str]:
        if run.engine != EngineKind.AUTO:
            if run.engine == EngineKind.WORKFLOW:
                raise ValueError("workflow must be invoked with an explicit workflow specification")
            return run.engine, "explicit run setting"

        text = task.lower()
        goal_signals = sum(marker in text for marker in self._GOAL_MARKERS)
        if goal_signals >= 2 or (goal_signals == 1 and len(task) >= 80):
            return EngineKind.GOAL, "task states a goal, constraints, or completion criteria"
        if any(marker in text for marker in self._TODO_MARKERS):
            return EngineKind.TODO, "task requires exploratory, adaptive work"
        ordered_dependency = "先" in text and "再" in text
        if any(marker in text for marker in self._DAG_MARKERS) or ordered_dependency:
            return EngineKind.DAG, "task contains dependencies or parallelizable work"
        return EngineKind.SEQUENTIAL, "default for bounded linear work"


class EffortPolicy:
    DEFAULTS = {
        EngineKind.SEQUENTIAL: Effort.LOW,
        EngineKind.DAG: Effort.MEDIUM,
        EngineKind.TODO: Effort.HIGH,
        EngineKind.GOAL: Effort.HIGH,
        EngineKind.WORKFLOW: Effort.LOW,
    }

    @classmethod
    def select(cls, engine: EngineKind, requested: Effort) -> Effort:
        if requested != Effort.AUTO:
            return requested
        return cls.DEFAULTS[engine]


def select_executor(settings: AppSettings, requested: ExecutorKind) -> ExecutorKind:
    if requested != ExecutorKind.AUTO:
        return requested
    if settings.llm.supports_reasoning:
        return ExecutorKind.THINKING
    return ExecutorKind.REACT
