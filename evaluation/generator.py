"""Generate evaluation cases from a local document."""

from __future__ import annotations

import re
import time
from typing import Any

from core.settings import AppSettings, get_settings
from evaluation.models import (
    EvalSetStatus,
    EvaluationCase,
    GeneratedEvalSet,
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
            "Each case MUST contain task_id, task_description, difficulty, tags, source_excerpt, and a non-empty "
            "verifiers array. Do not return an engine preference or prose-only success criteria: "
            "only deterministic verifiers affect evaluation success. Supported verifier "
            "shapes are keyword_include/keyword_exclude with params.keywords, regex_match with "
            "params.pattern, numeric_range with params.min and/or params.max, json_field with "
            "params.field and optional params.expected, file_exists with params.path, file_contains "
            "with params.path and params.content, and composite_and/composite_or with a non-empty "
            "params.verifiers array. Choose assertions that can be checked from the final output or "
            "the evaluation sandbox, and never emit an empty verifier list. source_excerpt MUST be a "
            "non-empty verbatim excerpt from the supplied document containing the evidence needed to "
            "solve that case; the task runner receives no hidden copy of the document.\n\n"
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
        cases: list[EvaluationCase] = []
        for row in rows[:count]:
            if not isinstance(row, dict):
                raise ValueError("generator returned a non-object case")
            source_excerpt = str(row.get("source_excerpt", "")).strip()
            if not source_excerpt or source_excerpt not in content:
                raise ValueError(
                    "generator returned a case without a verbatim source_excerpt"
                )
            case_data = dict(row)
            case_data.pop("source_excerpt", None)
            description = str(case_data.get("task_description", "")).strip()
            case_data["task_description"] = (
                f"{description}\n\nSource material:\n{source_excerpt}"
            )
            cases.append(EvaluationCase.model_validate(case_data))
        empty_verifier_ids = [case.task_id for case in cases if not case.verifiers]
        if empty_verifier_ids:
            raise ValueError(
                "generator returned cases without deterministic verifiers: "
                + ", ".join(empty_verifier_ids)
            )
        return cases

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
                    verifiers=[EvalSetGenerator._heuristic_verifier(fragment)],
                )
            )
        return cases

    @staticmethod
    def _heuristic_verifier(fragment: str) -> dict[str, Any]:
        """Build a minimal deterministic grounding check for a generated case."""
        candidates = re.findall(
            r"[A-Za-z][A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{2,8}",
            fragment,
        )
        ignored = {
            "about",
            "agent",
            "given",
            "material",
            "内容",
            "以下内容",
            "材料",
        }
        anchor = next(
            (candidate for candidate in candidates if candidate.lower() not in ignored),
            "",
        )
        if anchor:
            return {
                "type": "keyword_include",
                "params": {"keywords": [anchor]},
            }
        return {"type": "regex_match", "params": {"pattern": r"\S"}}
