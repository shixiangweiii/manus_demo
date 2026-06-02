"""
SkillAutoDistiller (v20.5) — 从成功轨迹中自动蒸馏 SKILL.md 格式的技能文件。

设计原则（对齐 roadmap v20.5 + v17 "禁止静默自改" 原则）：
  - 基于记忆的计数：通过 AgenticMemory 检索同类成功经验，计数 >= N 则触发蒸馏
  - 蒸馏结果写入 .agents/skills/auto-{name}/SKILL.md（用户级目录，半可信）
  - 不自动设置 allowed-tools — 蒸馏的 skill 只提供指令，不预授权工具
  - 蒸馏的 skill 不自动激活，需用户确认或手动移动到项目级目录
  - 蒸馏失败不影响正常任务执行（调用方包 try/except）
  - LLM 辅助蒸馏为 opt-in（SELF_EVOLUTION_LLM_EXTRACTION），默认走确定性蒸馏
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

import config
from llm.client import LLMClient
from memory.models import (
    AgenticMemoryRecord,
    MemoryKind,
    MemorySearchQuery,
)
from memory.service import AgenticMemoryService
from skills.models import SKILL_NAME_PATTERN, SkillDef, SkillMeta
from evolution.models import (
    EVOLUTION_SOURCE,
    EXPERIENCE_TAG,
    SKILL_DISTILL_TAG,
    TaskOutcome,
)

logger = logging.getLogger(__name__)

# 截断控制 / Truncation limits
_TASK_TRUNCATE = 200
_SUMMARY_TRUNCATE = 300
_TRAJECTORY_ITEMS = 8
_TRAJECTORY_ITEM_CHARS = 120

# SKILL.md 模板 / SKILL.md template
_SKILL_MD_TEMPLATE = """\
---
name: {name}
description: {description}
metadata:
  author: auto-distilled
  version: "1.0"
  source: self-evolution
  distilled_from: {task_pattern}
---

# {title}

## Workflow
{workflow}

## Gotchas
{gotchas}
"""


class SkillAutoDistiller:
    """
    从成功轨迹中蒸馏 SKILL.md / Distill SKILL.md from successful trajectories.

    遵循 "经验 → 知识 → 可复用技能" 的升级路径：
    1. 通过 AgenticMemory 检索同类成功经验计数
    2. 计数 >= SKILL_AUTO_DISTILL_MIN_SUCCESSES 时触发蒸馏
    3. 确定性或 LLM 辅助提取共性步骤 → SKILL.md 正文
    4. 写入 .agents/skills/auto-{name}/SKILL.md（半可信级别）
    5. 在 AgenticMemory 中记录蒸馏标记（防重复蒸馏）
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
            logger.debug("[SkillAutoDistiller] event callback failed for '%s'", event, exc_info=True)

    # ------------------------------------------------------------------
    # Public: check if distillation is warranted / 判断是否值得蒸馏
    # ------------------------------------------------------------------

    def should_distill(self, task: str) -> bool:
        """
        检查同类任务成功次数是否达到蒸馏阈值。
        Check if accumulated success count for similar tasks reaches the distillation threshold.

        通过 AgenticMemory 检索 EXPERIENCE_TAG 标记的成功经验来计数。
        不使用内存计数器（与 ExperienceLearner 的无状态设计一致）。
        """
        if not task:
            return False

        min_successes = getattr(config, "SKILL_AUTO_DISTILL_MIN_SUCCESSES", 3)
        query = MemorySearchQuery(
            query=task,
            tags=[EXPERIENCE_TAG],
            top_k=min_successes + 2,  # 多取几条确保计数准确
            min_confidence=0.0,
        )
        try:
            results = self._memory.search(query)
        except Exception:
            logger.debug("[SkillAutoDistiller] memory search failed in should_distill", exc_info=True)
            return False

        # 计算有效成功经验数量（排除已被蒸馏标记的）
        success_count = 0
        for r in results:
            if EXPERIENCE_TAG in r.record.tags and SKILL_DISTILL_TAG not in r.record.tags:
                success_count += 1

        if success_count < min_successes:
            logger.debug(
                "[SkillAutoDistiller] success count %d < threshold %d for task pattern",
                success_count, min_successes,
            )
            return False

        # 检查是否已经蒸馏过类似 skill（防重复蒸馏）
        if self._is_already_distilled(task):
            logger.debug("[SkillAutoDistiller] similar skill already distilled, skipping")
            return False

        logger.info(
            "[SkillAutoDistiller] distillation threshold reached: %d successes for '%s'",
            success_count, task[:_TASK_TRUNCATE],
        )
        return True

    # ------------------------------------------------------------------
    # Public: distill a skill from accumulated experience / 蒸馏技能
    # ------------------------------------------------------------------

    async def distill(self, outcome: TaskOutcome) -> SkillDef | None:
        """
        从任务结果和积累的成功经验中蒸馏出 SKILL.md。
        Distill a SKILL.md from the task outcome and accumulated success experiences.

        使用 LLM 辅助提取（SELF_EVOLUTION_LLM_EXTRACTION=true）或
        确定性提取（默认，从成功经验摘要中提取高频模式）。
        """
        task = outcome.task or ""
        if not task:
            return None

        try:
            # 收集同类成功经验
            experiences = self._gather_success_experiences(task)

            if config.SELF_EVOLUTION_LLM_EXTRACTION:
                skill_data = await self._llm_distill(task, experiences)
            else:
                skill_data = self._deterministic_distill(task, experiences)

            if not skill_data:
                logger.debug("[SkillAutoDistiller] distillation produced no result")
                return None

            # 写入 SKILL.md 文件
            skill_def = self._write_skill_file(skill_data, task)
            if skill_def:
                # 标记已蒸馏（防重复）
                self._mark_distilled(task, skill_data["name"])
                self._emit("skill_auto_created", {
                    "name": skill_data["name"],
                    "path": skill_def.skill_dir,
                })
                logger.info(
                    "[SkillAutoDistiller] auto-distilled skill '%s' to %s",
                    skill_data["name"], skill_def.skill_dir,
                )

            return skill_def
        except Exception:
            logger.debug("[SkillAutoDistiller] distill failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Gather success experiences / 收集同类成功经验
    # ------------------------------------------------------------------

    def _gather_success_experiences(self, task: str) -> list[dict[str, Any]]:
        """从 AgenticMemory 中检索同类成功经验。"""
        query = MemorySearchQuery(
            query=task,
            tags=[EXPERIENCE_TAG],
            top_k=10,
            min_confidence=0.0,
        )
        try:
            results = self._memory.search(query)
        except Exception:
            return []

        experiences = []
        for r in results:
            if EXPERIENCE_TAG in r.record.tags:
                experiences.append({
                    "summary": r.record.summary,
                    "content": r.record.content,
                    "tags": r.record.tags,
                    "confidence": r.record.confidence,
                })
        return experiences

    # ------------------------------------------------------------------
    # Deterministic distillation / 确定性蒸馏
    # ------------------------------------------------------------------

    def _deterministic_distill(
        self,
        task: str,
        experiences: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """
        从成功经验摘要中提取高频模式，生成 SKILL.md 数据。
        不调用 LLM，纯确定性逻辑。
        """
        if not experiences:
            return None

        # 生成 skill name：从任务描述中提取关键词
        name = self._derive_skill_name(task)
        if not name:
            return None

        # 提取共性步骤
        summaries = [e.get("summary", "") for e in experiences if e.get("summary")]
        workflow = self._extract_workflow(summaries)
        description = self._derive_description(task, summaries)
        gotchas = self._derive_gotchas(experiences)
        title = name.replace("-", " ").title()

        return {
            "name": name,
            "description": description,
            "title": title,
            "workflow": workflow,
            "gotchas": gotchas,
            "task_pattern": task[:_TASK_TRUNCATE],
        }

    # ------------------------------------------------------------------
    # LLM-assisted distillation / LLM 辅助蒸馏
    # ------------------------------------------------------------------

    async def _llm_distill(
        self,
        task: str,
        experiences: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """
        使用 LLM 从成功经验中蒸馏出结构化 SKILL.md 内容。
        """
        if not experiences:
            return None

        exp_text = "\n".join(
            f"- {e.get('summary', '(no summary)')}"
            for e in experiences[:6]
        )
        prompt = (
            "Distill a reusable Agent Skill from these successful task experiences.\n"
            "从这些成功任务经验中蒸馏一个可复用的 Agent Skill。\n"
            "The skill should capture the common workflow pattern that made these tasks succeed.\n\n"
            f"Task pattern: {task[:_TASK_TRUNCATE]}\n"
            f"Successful experiences:\n{exp_text}\n\n"
            'Return ONLY JSON with these fields:\n'
            '{\n'
            '  "name": "kebab-case-skill-name (1-64 chars, lowercase+hyphens)",\n'
            '  "description": "When to use this skill, what it does (1-200 chars)",\n'
            '  "workflow": "Step-by-step workflow (numbered list)",\n'
            '  "gotchas": "Important caveats and tips"\n'
            '}'
        )
        try:
            data = await self._llm.chat_json(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=800,
                caller_tag="SkillAutoDistiller",
            )
        except Exception:
            logger.debug("[SkillAutoDistiller] LLM distillation failed", exc_info=True)
            return None

        if not isinstance(data, dict):
            return None

        name = (data.get("name") or "").strip()
        description = (data.get("description") or "").strip()
        workflow = (data.get("workflow") or "").strip()
        gotchas = (data.get("gotchas") or "").strip()

        if not name or not description:
            return None

        # 验证 name 格式
        if not SKILL_NAME_PATTERN.match(name):
            logger.debug("[SkillAutoDistiller] LLM generated invalid skill name: %s", name)
            return None

        # 确保以 auto- 前缀
        if not name.startswith("auto-"):
            name = f"auto-{name}"

        title = name.replace("-", " ").title()

        return {
            "name": name,
            "description": description[:1024],
            "title": title,
            "workflow": workflow or "1. Follow the standard approach for this task type",
            "gotchas": gotchas or "None identified",
            "task_pattern": task[:_TASK_TRUNCATE],
        }

    # ------------------------------------------------------------------
    # Write SKILL.md to disk / 写入 SKILL.md 文件
    # ------------------------------------------------------------------

    def _write_skill_file(self, skill_data: dict[str, Any], task: str) -> SkillDef | None:
        """
        将蒸馏结果写入 .agents/skills/auto-{name}/SKILL.md。
        路径使用 SKILLS_USER_DIR（用户级目录，半可信）。
        """
        name = skill_data["name"]
        # 写入用户级目录（半可信）
        skill_base_dir = getattr(config, "SKILLS_USER_DIR", os.path.expanduser("~/.manus_demo/skills"))
        skill_dir = os.path.join(skill_base_dir, name)

        try:
            os.makedirs(skill_dir, exist_ok=True)
        except OSError as e:
            logger.warning("[SkillAutoDistiller] cannot create skill directory %s: %s", skill_dir, e)
            return None

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        content = _SKILL_MD_TEMPLATE.format(
            name=name,
            description=skill_data["description"],
            task_pattern=skill_data.get("task_pattern", task[:_TASK_TRUNCATE]),
            title=skill_data.get("title", name.replace("-", " ").title()),
            workflow=skill_data.get("workflow", "1. Follow the standard approach"),
            gotchas=skill_data.get("gotchas", "None identified"),
        )

        try:
            with open(skill_md_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            logger.warning("[SkillAutoDistiller] cannot write SKILL.md %s: %s", skill_md_path, e)
            return None

        # 构造 SkillDef 返回
        meta = SkillMeta(
            name=name,
            description=skill_data["description"],
            license="user",  # 半可信级别 / semi-trusted
            compatibility=">=20.0",
            metadata={
                "author": "auto-distilled",
                "version": "1.0",
                "source": "self-evolution",
                "distilled_from": skill_data.get("task_pattern", ""),
            },
            allowed_tools=[],  # 蒸馏的 skill 不预授权工具 / no pre-authorized tools
        )
        return SkillDef(
            meta=meta,
            skill_dir=skill_dir,
            full_content=content,
        )

    # ------------------------------------------------------------------
    # Mark distilled in memory / 标记已蒸馏
    # ------------------------------------------------------------------

    def _mark_distilled(self, task: str, skill_name: str) -> None:
        """
        在 AgenticMemory 中记录蒸馏标记，防止重复蒸馏同类任务模式。
        Record a distillation marker in AgenticMemory to prevent re-distillation.
        """
        confidence_cap = getattr(config, "SKILL_AUTO_DISTILL_CONFIDENCE_CAP", 0.55)
        record = AgenticMemoryRecord(
            kind=MemoryKind.PROCEDURAL,
            content=f"已蒸馏为技能: {skill_name}\n任务模式: {task[:_TASK_TRUNCATE]}",
            summary=f"Skill auto-distilled: {skill_name}",
            tags=[SKILL_DISTILL_TAG],
            source=EVOLUTION_SOURCE,
            confidence=min(confidence_cap, 0.55),
            importance=0.5,
            metadata={
                "skill_name": skill_name,
                "task_pattern": task[:_TASK_TRUNCATE],
                "distilled_at": "v20.5",
            },
        )
        try:
            self._memory.add_record(record)
        except Exception:
            logger.debug("[SkillAutoDistiller] failed to mark distilled in memory", exc_info=True)

    def _is_already_distilled(self, task: str) -> bool:
        """检查是否已蒸馏过类似 skill。Check if a similar skill has already been distilled."""
        query = MemorySearchQuery(
            query=task,
            tags=[SKILL_DISTILL_TAG],
            top_k=1,
            min_confidence=0.0,
        )
        try:
            results = self._memory.search(query)
        except Exception:
            return False
        for r in results:
            if SKILL_DISTILL_TAG in r.record.tags:
                return True
        return False

    # ------------------------------------------------------------------
    # Name / description derivation helpers / 名称和描述推导辅助
    # ------------------------------------------------------------------

    def _derive_skill_name(self, task: str) -> str | None:
        """
        从任务描述中提取关键词，生成 auto- 前缀的 kebab-case skill name。
        Derive a skill name from the task description with auto- prefix.
        """
        # 简单策略：提取主要动词+名词组合
        # Simple strategy: extract key verb+noun from task description
        task_lower = task.lower().strip()

        # 移除常见前缀 / Remove common prefixes
        for prefix in ("please ", "can you ", "i need to ", "i want to ", "help me "):
            if task_lower.startswith(prefix):
                task_lower = task_lower[len(prefix):]

        # 提取前几个有意义的词 / Extract first few meaningful words
        words = re.findall(r"[a-z]+", task_lower)
        # 过滤掉停用词 / Filter stop words
        stop_words = {"the", "a", "an", "and", "or", "to", "for", "in", "on", "at", "of", "with", "by", "is", "are", "it", "this", "that"}
        meaningful = [w for w in words if w not in stop_words][:3]

        if not meaningful:
            return None

        name = "auto-" + "-".join(meaningful)

        # 验证 name 格式 / Validate name format
        if not SKILL_NAME_PATTERN.match(name):
            return None

        # 长度限制 / Length limit
        if len(name) > 64:
            name = name[:64]
            # 确保不以连字符结尾 / Ensure doesn't end with hyphen
            name = name.rstrip("-")

        return name

    def _derive_description(self, task: str, summaries: list[str]) -> str:
        """从任务描述和成功经验摘要中推导 skill description。"""
        # 取最相关的摘要作为 description 基础
        if summaries:
            best = max(summaries, key=len)
            desc = best[:200]
        else:
            desc = f"Auto-distilled skill for: {task[:100]}"
        return f"Automatically distilled from repeated success. {desc}"[:1024]

    def _extract_workflow(self, summaries: list[str]) -> str:
        """从成功经验摘要中提取共性步骤。"""
        if not summaries:
            return "1. Follow the standard approach for this task type"

        # 收集所有出现的步骤模式 / Collect step patterns
        steps: list[str] = []
        for s in summaries:
            # 按换行或分隔符拆分 / Split by newlines or separators
            parts = re.split(r"[|\n,;]", s)
            for p in parts:
                p = p.strip()
                if p and len(p) > 5:
                    steps.append(p)

        if not steps:
            return "1. Follow the standard approach for this task type"

        # 取前 5 个独特步骤 / Take first 5 unique steps
        seen = set()
        unique_steps = []
        for step in steps:
            if step.lower() not in seen:
                seen.add(step.lower())
                unique_steps.append(step)
            if len(unique_steps) >= 5:
                break

        return "\n".join(f"{i+1}. {s}" for i, s in enumerate(unique_steps))

    def _derive_gotchas(self, experiences: list[dict[str, Any]]) -> str:
        """从经验中推导注意事项。"""
        gotchas = []
        for e in experiences:
            content = e.get("content", "")
            # 查找经验中的关键提示 / Find key tips in experience content
            for line in content.split("\n"):
                line = line.strip()
                if any(kw in line.lower() for kw in ("注意", "caution", "avoid", "ensure", "always", "never", "不要", "避免")):
                    if line and len(line) > 5:
                        gotchas.append(line[:120])
                        if len(gotchas) >= 3:
                            break
            if len(gotchas) >= 3:
                break

        if not gotchas:
            return "None identified from accumulated experience"
        return "\n".join(f"- {g}" for g in gotchas)
