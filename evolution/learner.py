"""
ExperienceLearner (v17.1 + v17.2) - Distill experience / failure lessons and
inject avoidance hints into future tasks.
经验学习器（v17.1 + v17.2）—— 从任务结果提炼经验/失败教训，并为后续相似任务注入避坑提示。

设计原则（对齐路线图 §9 风险表）：
  - 只写记忆，不改源码/不改路由；写入 v15 Agentic Memory。
  - 每条记忆 source="evolution"、带 task_id、confidence 受控、可 revoke（防 memory poisoning）。
  - LLM 提炼为 opt-in（SELF_EVOLUTION_LLM_EXTRACTION），默认走确定性提炼。
  - 学习失败不影响主流程（调用方包 try/except）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import config
from llm.client import LLMClient
from memory.models import (
    AgenticMemoryRecord,
    MemoryKind,
    MemorySearchQuery,
    MemoryStatus,
)
from memory.service import AgenticMemoryService
from evolution.models import (
    EVOLUTION_SOURCE,
    EVOLUTION_VERSION,
    EXPERIENCE_TAG,
    FAILURE_LESSON_TAG,
    USER_PREFERENCE_TAG,
    TaskOutcome,
)

logger = logging.getLogger(__name__)

# dedup 阈值：检索到的近重复记忆分数 >= 此值则跳过写入，避免同类教训刷屏
# Skip writing a near-duplicate failure lesson if an existing one scores >= this.
_DEDUP_SCORE_THRESHOLD = 0.6

# 提炼时截断，控制 prompt / 记忆体积
_FEEDBACK_TRUNCATE = 300
_TRAJECTORY_ITEMS = 8
_TRAJECTORY_ITEM_CHARS = 120


class ExperienceLearner:
    """
    Distills experience (success) or failure lessons (failure) from a TaskOutcome
    and retrieves them as avoidance hints for similar future tasks.
    从 TaskOutcome 提炼经验（成功）或失败教训（失败），并在相似任务中作为避坑提示召回。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        memory_service: AgenticMemoryService,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        self._llm = llm_client
        self._memory = memory_service
        self._on_event = on_event or (lambda *_: None)

    def _emit(self, event: str, data: Any = None) -> None:
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[ExperienceLearner] event callback failed for '%s'", event, exc_info=True)

    # ------------------------------------------------------------------
    # Public: learn after a task / 任务结束后学习
    # ------------------------------------------------------------------

    async def learn_from_task(self, outcome: TaskOutcome) -> list[AgenticMemoryRecord]:
        """
        Extract and persist experience or failure lessons from a completed task.
        从已完成任务提炼并持久化经验或失败教训。返回新建的记忆记录列表。
        """
        if not (outcome.task or outcome.task_id):
            return []
        try:
            if outcome.success:
                return await self._learn_success(outcome)
            return await self._learn_failure(outcome)
        except Exception:
            logger.debug("[ExperienceLearner] learn_from_task failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Public: retrieve avoidance hints / 召回避坑提示
    # ------------------------------------------------------------------

    def build_avoidance_hints(self, task: str) -> str:
        """
        Retrieve relevant failure lessons and format them as avoidance hints.
        检索相关失败教训并格式化为避坑提示；无相关教训返回空串（不污染 context）。
        """
        if not task:
            return ""
        query = MemorySearchQuery(
            query=task,
            tags=[FAILURE_LESSON_TAG],
            top_k=max(1, config.SELF_EVOLUTION_MAX_HINTS),
            min_confidence=config.MEMORY_MIN_CONFIDENCE,
        )
        try:
            results = self._memory.search(query)
        except Exception:
            logger.debug("[ExperienceLearner] avoidance search failed", exc_info=True)
            return ""

        # tags 在 store 中仅作评分加权而非硬过滤，这里显式过滤出失败教训记录
        lessons = [r for r in results if FAILURE_LESSON_TAG in r.record.tags]
        if not lessons:
            return ""

        lines = ["## 过往失败教训（请主动规避 / Past failures to avoid）"]
        for r in lessons[: config.SELF_EVOLUTION_MAX_HINTS]:
            md = r.record.metadata or {}
            reason = md.get("failure_reason") or r.record.summary
            correction = md.get("correction") or ""
            task_type = md.get("task_type") or ""
            line = f"- [{task_type}] 失败原因: {reason}" if task_type else f"- 失败原因: {reason}"
            if correction:
                line += f" → 建议做法: {correction}"
            lines.append(line)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Success path / 成功路径
    # ------------------------------------------------------------------

    async def _learn_success(self, outcome: TaskOutcome) -> list[AgenticMemoryRecord]:
        summary = ""
        tags: list[str] = []
        if config.SELF_EVOLUTION_LLM_EXTRACTION:
            data = await self._llm_extract_success(outcome)
            if data:
                summary = (data.get("summary") or "").strip()
                tags = [t for t in (data.get("tags") or []) if isinstance(t, str)][:3]

        if not summary:
            summary = self._deterministic_success_summary(outcome)
        if not summary:
            return []

        task_type = outcome.complexity or "unknown"
        content = f"任务: {outcome.task}\n有效做法: {summary}"
        record = AgenticMemoryRecord(
            kind=MemoryKind.PROCEDURAL,
            content=content,
            summary=summary[:200],
            tags=list(dict.fromkeys([EXPERIENCE_TAG, task_type, *tags])),
            task_id=outcome.task_id,
            source=EVOLUTION_SOURCE,
            confidence=min(0.6, config.SELF_EVOLUTION_CONFIDENCE_CAP),
            importance=0.6,
            metadata={
                "task_type": task_type,
                "evolution_version": EVOLUTION_VERSION,
            },
        )
        self._memory.add_record(record)
        self._emit("experience_learned", {
            "task_id": outcome.task_id,
            "kind": record.kind.value,
            "summary": record.summary,
        })
        logger.info("[ExperienceLearner] stored success experience for task %s", outcome.task_id[:8])
        return [record]

    def _deterministic_success_summary(self, outcome: TaskOutcome) -> str:
        """Build a procedural note without an LLM call. / 不调用 LLM 的确定性经验摘要。"""
        if outcome.reflection_feedback:
            base = outcome.reflection_feedback[:_FEEDBACK_TRUNCATE]
        else:
            base = (outcome.final_answer or "")[:_FEEDBACK_TRUNCATE]
        traj = self._trajectory_summary(outcome)
        if traj:
            return f"{base}\n步骤: {traj}" if base else f"步骤: {traj}"
        return base

    # ------------------------------------------------------------------
    # Failure path / 失败路径
    # ------------------------------------------------------------------

    async def _learn_failure(self, outcome: TaskOutcome) -> list[AgenticMemoryRecord]:
        task_type = outcome.complexity or "unknown"
        failure_reason = ""
        correction = ""

        if config.SELF_EVOLUTION_LLM_EXTRACTION:
            data = await self._llm_extract_failure(outcome)
            if data:
                failure_reason = (data.get("failure_reason") or "").strip()
                correction = (data.get("correction") or "").strip()
                task_type = (data.get("task_type") or task_type).strip() or task_type

        if not failure_reason:
            failure_reason = (
                outcome.reflection_feedback[:_FEEDBACK_TRUNCATE]
                if outcome.reflection_feedback
                else "任务未通过反思 / task did not pass reflection"
            )
        if not correction and outcome.suggestions:
            correction = outcome.suggestions[0][:_FEEDBACK_TRUNCATE]

        # dedup：相同失败原因已存在则跳过
        if self._is_duplicate(failure_reason, FAILURE_LESSON_TAG):
            logger.debug("[ExperienceLearner] skip duplicate failure lesson")
            return []

        content = f"任务: {outcome.task}\n失败原因: {failure_reason}"
        if correction:
            content += f"\n纠正建议: {correction}"
        record = AgenticMemoryRecord(
            kind=MemoryKind.EXPERIENTIAL,
            content=content,
            summary=failure_reason[:200],
            tags=[FAILURE_LESSON_TAG, task_type],
            task_id=outcome.task_id,
            source=EVOLUTION_SOURCE,
            confidence=min(0.5, config.SELF_EVOLUTION_CONFIDENCE_CAP),
            importance=0.5,
            metadata={
                "task_type": task_type,
                "failure_reason": failure_reason,
                "correction": correction,
                "evolution_version": EVOLUTION_VERSION,
            },
        )
        self._memory.add_record(record)
        self._emit("failure_lesson_stored", {
            "task_id": outcome.task_id,
            "task_type": task_type,
            "failure_reason": failure_reason,
            "correction": correction,
        })
        logger.info("[ExperienceLearner] stored failure lesson for task %s", outcome.task_id[:8])
        return [record]

    def _is_duplicate(self, query_text: str, tag: str) -> bool:
        """Return True if a near-duplicate record with the given tag already exists.
        若已存在同 tag 的近重复记忆则返回 True（dedup 防刷屏）。"""
        if not query_text:
            return False
        query = MemorySearchQuery(
            query=query_text,
            tags=[tag],
            top_k=1,
            min_confidence=0.0,
        )
        try:
            results = self._memory.search(query)
        except Exception:
            return False
        for r in results:
            if tag in r.record.tags and r.score >= _DEDUP_SCORE_THRESHOLD:
                return True
        return False

    # ------------------------------------------------------------------
    # Preference learning (v17.4) / 偏好学习
    # ------------------------------------------------------------------

    async def learn_preferences(
        self,
        task: str,
        hitl_pairs: list[dict],
    ) -> list[AgenticMemoryRecord]:
        """
        Extract durable user preferences from HITL question/answer pairs.
        从 HITL 问答对提炼持久用户偏好，写入 FACTUAL 记忆（tag=user_preference，可回滚）。
        """
        if not hitl_pairs:
            return []
        try:
            if config.SELF_EVOLUTION_LLM_EXTRACTION:
                prefs = await self._llm_extract_preferences(task, hitl_pairs)
                if prefs:
                    return self._store_preferences(prefs)
            # 确定性回退：每条问答存一条偏好
            deterministic = [
                {"preference": p.get("question", ""), "value": p.get("answer", "")}
                for p in hitl_pairs
                if p.get("answer")
            ]
            return self._store_preferences(deterministic)
        except Exception:
            logger.debug("[ExperienceLearner] learn_preferences failed", exc_info=True)
            return []

    def _store_preferences(self, prefs: list[dict]) -> list[AgenticMemoryRecord]:
        records: list[AgenticMemoryRecord] = []
        for p in prefs:
            preference = (p.get("preference") or "").strip()
            value = (p.get("value") or "").strip()
            if not value:
                continue
            summary = f"用户偏好: {value}" if not preference else f"{preference}: {value}"
            # dedup：相同偏好已存在则跳过
            if self._is_duplicate(summary, USER_PREFERENCE_TAG):
                continue
            content = f"问题: {preference}\n回答: {value}" if preference else f"偏好: {value}"
            record = AgenticMemoryRecord(
                kind=MemoryKind.FACTUAL,
                content=content,
                summary=summary[:200],
                tags=[USER_PREFERENCE_TAG],
                source=EVOLUTION_SOURCE,
                confidence=min(0.6, config.SELF_EVOLUTION_CONFIDENCE_CAP),
                importance=0.6,
                metadata={
                    "question": preference,
                    "answer": value,
                    "evolution_version": EVOLUTION_VERSION,
                },
            )
            self._memory.add_record(record)
            self._emit("preference_learned", {
                "preference": preference,
                "value": value,
            })
            records.append(record)
        if records:
            logger.info("[ExperienceLearner] stored %d user preference(s)", len(records))
        return records

    def build_preference_hints(self, task: str = "") -> str:
        """
        List known user preferences as hints. Preferences are mostly global
        (e.g. default city), so list by tag rather than keyword-gated search.
        列出已知用户偏好；偏好多为全局性，按 tag 列举而非关键词检索，避免漏召回。
        """
        try:
            records = self._memory._store.list_records(
                kind=MemoryKind.FACTUAL,
                status=MemoryStatus.ACTIVE,
            )
        except Exception:
            logger.debug("[ExperienceLearner] preference list failed", exc_info=True)
            return ""
        prefs = [r for r in records if USER_PREFERENCE_TAG in r.tags]
        if not prefs:
            return ""
        # 按重要度 + 最近更新排序，取前 N 条
        prefs.sort(key=lambda r: (r.importance, r.updated_at), reverse=True)
        prefs = prefs[: max(1, config.SELF_EVOLUTION_MAX_HINTS)]

        lines = ["## 已知用户偏好（请遵循 / Known user preferences）"]
        for r in prefs:
            md = r.metadata or {}
            q = md.get("question") or ""
            a = md.get("answer") or r.summary
            lines.append(f"- {q}: {a}" if q else f"- {a}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trajectory_summary(self, outcome: TaskOutcome) -> str:
        parts: list[str] = []
        for r in outcome.trajectory[:_TRAJECTORY_ITEMS]:
            status = "OK" if r.success else "FAIL"
            output = str(r.output or "")[:_TRAJECTORY_ITEM_CHARS]
            parts.append(f"[{status}] {output}")
        return " | ".join(parts)

    async def _llm_extract_success(self, outcome: TaskOutcome) -> dict[str, Any] | None:
        prompt = (
            "Distill a concise, reusable procedural note from this SUCCESSFUL task.\n"
            "从这次成功任务中提炼一条简洁、可复用的过程性经验。\n\n"
            f"Task: {outcome.task}\n"
            f"Reflection feedback: {outcome.reflection_feedback[:_FEEDBACK_TRUNCATE]}\n"
            f"Trajectory: {self._trajectory_summary(outcome)}\n\n"
            'Return ONLY JSON: {"summary": "what worked, actionable", "tags": ["..."]}'
        )
        return await self._safe_chat_json(prompt)

    async def _llm_extract_failure(self, outcome: TaskOutcome) -> dict[str, Any] | None:
        prompt = (
            "Distill a structured failure lesson from this FAILED task.\n"
            "从这次失败任务中提炼结构化失败教训。\n\n"
            f"Task: {outcome.task}\n"
            f"Task type: {outcome.complexity}\n"
            f"Reflection feedback: {outcome.reflection_feedback[:_FEEDBACK_TRUNCATE]}\n"
            f"Trajectory: {self._trajectory_summary(outcome)}\n\n"
            'Return ONLY JSON: {"task_type": "...", "failure_reason": "why it failed", '
            '"correction": "what to do differently next time"}'
        )
        return await self._safe_chat_json(prompt)

    async def _llm_extract_preferences(
        self,
        task: str,
        hitl_pairs: list[dict],
    ) -> list[dict] | None:
        qa = "\n".join(
            f"Q: {p.get('question', '')}\nA: {p.get('answer', '')}"
            for p in hitl_pairs
        )
        prompt = (
            "Extract DURABLE, reusable user preferences from these clarification Q&A.\n"
            "从以下澄清问答中提炼持久、可复用的用户偏好（如默认城市、输出格式、代码风格）。\n"
            "Only keep preferences likely to apply to future tasks; skip one-off answers.\n"
            "只保留可能适用于未来任务的偏好，忽略一次性的回答。\n\n"
            f"Task: {task}\n{qa}\n\n"
            'Return ONLY JSON: {"preferences": [{"preference": "...", "value": "..."}]}'
        )
        data = await self._safe_chat_json(prompt)
        if isinstance(data, dict):
            prefs = data.get("preferences")
            if isinstance(prefs, list):
                return [p for p in prefs if isinstance(p, dict)]
        return None

    async def _safe_chat_json(self, prompt: str) -> dict[str, Any] | None:
        try:
            data = await self._llm.chat_json(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=600,
                caller_tag="ExperienceLearner",
            )
            return data if isinstance(data, dict) else None
        except Exception:
            logger.debug("[ExperienceLearner] LLM extraction failed", exc_info=True)
            return None
