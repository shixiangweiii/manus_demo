"""Generate evaluation cases from a local document."""

from __future__ import annotations

import re
import time

from core.settings import AppSettings, get_settings
from evaluation.models import (
    EvalSetStatus,
    EvaluationCase,
    GeneratedEvalSet,
    GroundTruth,
    TaskDifficulty,
)
from llm.client import LLMClient


class EvalSetGenerator:
    def __init__(self, settings: AppSettings | None = None, llm_client: LLMClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client

    async def generate(
        self,
        document,
        *,
        name: str,
        target_goal: str = "",
        num_tasks: int | None = None,
    ) -> GeneratedEvalSet:
        count = max(1, min(20, num_tasks or self.settings.evaluation.default_num_tasks))
        result = GeneratedEvalSet(
            name=name or f"{document.title} evaluation",
            doc_id=document.doc_id,
            doc_filename=document.filename,
            target_goal=target_goal,
            requested_num_tasks=count,
        )
        try:
            if self.llm_client is not None and self.settings.llm.api_key:
                result.tasks = await self._generate_with_llm(document.content, target_goal, count)
                result.generator = "llm"
                result.generation_model = self.llm_client.model
            else:
                result.tasks = self._generate_heuristically(document.content, target_goal, count)
                result.generator = "heuristic"
            result.status = EvalSetStatus.READY
        except Exception as exc:
            result.status = EvalSetStatus.FAILED
            result.generation_error = str(exc)
        result.updated_at = time.time()
        return result

    async def _generate_with_llm(self, content: str, goal: str, count: int) -> list[EvaluationCase]:
        prompt = (
            f"Create {count} independent agent evaluation cases from the document. "
            "Cover factual lookup, synthesis, and multi-step work. Return JSON with a cases array. "
            "Each case needs task_id, task_description, difficulty, tags, ground_truth "
            "(expected_engine and success_criteria), and deterministic verifiers when possible.\n\n"
            f"Evaluation goal: {goal or 'general comprehension'}\n\nDocument:\n{content}"
        )
        data = await self.llm_client.chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=self.settings.evaluation.generation_max_tokens,
            caller_tag="evaluation.generator",
        )
        rows = data.get("cases", [])
        if not rows:
            raise ValueError("generator returned no cases")
        return [EvaluationCase.model_validate(row) for row in rows[:count]]

    @staticmethod
    def _generate_heuristically(content: str, goal: str, count: int) -> list[EvaluationCase]:
        fragments = [
            fragment.strip()
            for fragment in re.split(r"\n{2,}|(?<=[。！？.!?])\s+", content)
            if len(fragment.strip()) >= 30
        ]
        if not fragments:
            fragments = [content[:1000]]
        cases = []
        for index in range(count):
            fragment = fragments[index % len(fragments)][:600]
            task = (
                f"根据给定材料说明以下内容的核心含义，并给出材料中的依据：{fragment}"
                if not goal else f"围绕“{goal}”，分析材料片段并给出可验证结论：{fragment}"
            )
            cases.append(
                EvaluationCase(
                    task_id=f"generated_{index + 1:03d}",
                    task_description=task,
                    difficulty=TaskDifficulty.MEDIUM,
                    tags=["generated", "document"],
                    ground_truth=GroundTruth(
                        expected_engine="sequential",
                        success_criteria="结论必须基于给定材料",
                    ),
                )
            )
        return cases
