"""
ClassifierCalibrator (v17.3) - Offline grid-search for task-complexity rule thresholds.
分类器校准（v17.3）—— 离线网格搜索任务复杂度规则阈值。

设计原则（对齐路线图 §9 "只允许配置化调整，禁止静默自改代码"）：
  - 纯离线：只用 benchmark ground truth + PlannerAgent 的规则评分，无需 API key。
  - 只产出建议：写建议 JSON、打印应用方式，绝不改 live config / 源码。
  - 只校准 2 个决策阈值（simple/complex），评分权重保持硬编码。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

import config
from agents.planner import PlannerAgent
from evaluation.benchmark import BENCHMARK_TASKS

logger = logging.getLogger(__name__)

# 网格搜索默认范围（含端点）/ default grid-search ranges (inclusive)
DEFAULT_SIMPLE_RANGE = range(-4, 1)   # -4..0
DEFAULT_COMPLEX_RANGE = range(1, 6)   # 1..5


@dataclass
class CalibrationRow:
    """单个 benchmark 任务的规则评分快照。"""
    task_id: str
    expected: str
    score: int
    is_emergent: bool


@dataclass
class ThresholdEval:
    """一组阈值在 benchmark 上的表现。"""
    simple_threshold: int
    complex_threshold: int
    accuracy: float
    ambiguous_rate: float
    correct: int
    total: int


@dataclass
class CalibrationSuggestion:
    """校准建议结果。"""
    current: ThresholdEval
    suggested: ThresholdEval
    improved: bool
    rows: list[CalibrationRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _te(te: ThresholdEval) -> dict[str, Any]:
            return {
                "simple_threshold": te.simple_threshold,
                "complex_threshold": te.complex_threshold,
                "accuracy": round(te.accuracy, 4),
                "ambiguous_rate": round(te.ambiguous_rate, 4),
                "correct": te.correct,
                "total": te.total,
            }
        return {
            "version": "v17.3",
            "current": _te(self.current),
            "suggested": _te(self.suggested),
            "improved": self.improved,
            "per_task": [
                {"task_id": r.task_id, "expected": r.expected,
                 "score": r.score, "is_emergent": r.is_emergent}
                for r in self.rows
            ],
        }


class ClassifierCalibrator:
    """
    Offline grid-search calibrator for the two complexity decision thresholds.
    针对两个复杂度决策阈值的离线网格搜索校准器。
    """

    def __init__(self, tasks: list | None = None):
        # 只取带 expected_complexity ground truth 的任务参与校准
        # Only tasks with expected_complexity ground truth participate.
        source = tasks if tasks is not None else BENCHMARK_TASKS
        self._rows: list[CalibrationRow] = []
        for t in source:
            expected = (t.ground_truth.expected_complexity or "").strip()
            if not expected:
                continue
            self._rows.append(CalibrationRow(
                task_id=t.task_id,
                expected=expected,
                score=PlannerAgent._rule_score(t.task_description),
                is_emergent=PlannerAgent._is_emergent_by_rule(t.task_description),
            ))

    @property
    def rows(self) -> list[CalibrationRow]:
        return self._rows

    def _predict(self, row: CalibrationRow, simple_t: int, complex_t: int) -> str:
        """Replicate classify_by_rule using the precomputed score (no recompute)."""
        if row.is_emergent:
            return "emergent"
        if row.score <= simple_t:
            return "simple"
        if row.score >= complex_t:
            return "complex"
        return "ambiguous"

    def evaluate_thresholds(self, simple_t: int, complex_t: int) -> ThresholdEval:
        """Evaluate one (simple, complex) threshold pair over the benchmark.
        在 benchmark 上评估一组阈值；ambiguous 记为未命中（含 LLM 成本/不确定性）。"""
        total = len(self._rows)
        correct = 0
        ambiguous = 0
        for row in self._rows:
            pred = self._predict(row, simple_t, complex_t)
            if pred == "ambiguous":
                ambiguous += 1
            if pred == row.expected:
                correct += 1
        return ThresholdEval(
            simple_threshold=simple_t,
            complex_threshold=complex_t,
            accuracy=(correct / total) if total else 0.0,
            ambiguous_rate=(ambiguous / total) if total else 0.0,
            correct=correct,
            total=total,
        )

    def grid_search(
        self,
        simple_range: range = DEFAULT_SIMPLE_RANGE,
        complex_range: range = DEFAULT_COMPLEX_RANGE,
    ) -> ThresholdEval:
        """Grid-search the best threshold pair.
        网格搜索最优阈值组合。

        排序目标：accuracy 最高 → ambiguous_rate 最低 → 最接近默认阈值（稳定性）。
        """
        default_s = config.CLASSIFIER_SIMPLE_THRESHOLD
        default_c = config.CLASSIFIER_COMPLEX_THRESHOLD
        best: ThresholdEval | None = None

        def _is_better(cand: ThresholdEval) -> bool:
            if best is None:
                return True
            if cand.accuracy != best.accuracy:
                return cand.accuracy > best.accuracy
            if cand.ambiguous_rate != best.ambiguous_rate:
                return cand.ambiguous_rate < best.ambiguous_rate
            # tie-break: prefer thresholds closest to current defaults (least churn)
            cand_dist = abs(cand.simple_threshold - default_s) + abs(cand.complex_threshold - default_c)
            best_dist = abs(best.simple_threshold - default_s) + abs(best.complex_threshold - default_c)
            return cand_dist < best_dist

        for simple_t in simple_range:
            for complex_t in complex_range:
                if simple_t >= complex_t:
                    continue  # 阈值必须 simple < complex，否则区间无效
                cand = self.evaluate_thresholds(simple_t, complex_t)
                if _is_better(cand):
                    best = cand

        if best is None:
            # 退化：范围为空时返回当前阈值评估
            best = self.evaluate_thresholds(default_s, default_c)
        return best

    def suggest(
        self,
        simple_range: range = DEFAULT_SIMPLE_RANGE,
        complex_range: range = DEFAULT_COMPLEX_RANGE,
    ) -> CalibrationSuggestion:
        """Produce a calibration suggestion (current vs best)."""
        current = self.evaluate_thresholds(
            config.CLASSIFIER_SIMPLE_THRESHOLD,
            config.CLASSIFIER_COMPLEX_THRESHOLD,
        )
        suggested = self.grid_search(simple_range, complex_range)
        improved = (
            suggested.accuracy > current.accuracy
            or (suggested.accuracy == current.accuracy
                and suggested.ambiguous_rate < current.ambiguous_rate)
        ) and (
            suggested.simple_threshold != current.simple_threshold
            or suggested.complex_threshold != current.complex_threshold
        )
        return CalibrationSuggestion(
            current=current,
            suggested=suggested,
            improved=improved,
            rows=self._rows,
        )

    @staticmethod
    def write_suggestion(suggestion: CalibrationSuggestion, path: str) -> None:
        """Atomically write the suggestion JSON. NEVER touches live config.
        原子写建议 JSON；绝不改 live config / 源码。"""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = suggestion.to_dict()
        dir_name = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        logger.info("[ClassifierCalibrator] suggestion written to %s", path)
