"""
SkillOptimizer — 轻量版 skill 优化闭环。

参考 SkillOpt 思路，但保持本项目的安全原则：
  - 默认只生成 diff 报告，不写入文件（禁止静默自改）
  - 使用 train/validation split，避免只针对单个失败样本过拟合
  - 可选 LLM 辅助优化；默认确定性优化 description
  - 只优化 SKILL.md frontmatter description 与正文提示，不修改 allowed-tools
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import config
from llm.client import LLMClient
from skills.loader import SkillLoader
from skills.models import SkillDef

logger = logging.getLogger(__name__)


@dataclass
class SkillEvalCase:
    """One evaluation case used by the optimizer. / 优化器使用的一条评测案例。"""
    task_id: str
    task_description: str
    expected_activation: bool
    actual_activation: bool
    task_success: bool = False
    activated_skills: list[str] = field(default_factory=list)
    final_answer: str = ""


@dataclass
class SkillOptimizationReport:
    """Optimization report with diagnosis and proposed diff. / 优化报告。"""
    skill_name: str
    skill_dir: str
    train_cases: list[SkillEvalCase]
    validation_cases: list[SkillEvalCase]
    diagnosis: str
    proposed_content: str
    diff: str
    applied: bool = False


class SkillOptimizer:
    """
    Optimize a skill's description/content from evaluation failures.
    从评测失败样本优化 skill 的 description/content。

    The optimizer is deliberately conservative:
    - does not change allowed-tools
    - does not write unless apply=True
    - emits a unified diff for review
    - deterministic by default; LLM path is opt-in
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        self._llm = llm_client
        self._on_event = on_event or (lambda *_: None)

    def _emit(self, event: str, data: Any = None) -> None:
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[SkillOptimizer] event callback failed for '%s'", event, exc_info=True)

    # ------------------------------------------------------------------
    # Public API / 公共接口
    # ------------------------------------------------------------------

    async def optimize(
        self,
        skill_dir: str,
        eval_results: list[dict[str, Any]] | None = None,
        *,
        apply: bool = False,
    ) -> SkillOptimizationReport | None:
        """
        Run the lightweight optimize loop for one skill directory.
        对单个 skill 目录运行轻量优化闭环。

        Args:
            skill_dir: Directory containing SKILL.md.
            eval_results: Optional evaluation result dicts (from report JSON).
            apply: If True, write proposed SKILL.md. Default False.
        """
        skill = SkillLoader._scan_skill_dir(skill_dir)
        if skill is None:
            logger.warning("[SkillOptimizer] invalid skill directory: %s", skill_dir)
            return None

        cases = self._cases_from_results(eval_results or [], skill.meta.name)
        train_cases, validation_cases = self._split_cases(cases)
        diagnosis = self.diagnose(train_cases, skill.meta.name)

        old_content = self._read_skill_md(skill_dir)
        if not old_content:
            return None

        if config.SKILL_OPTIMIZE_LLM_ENABLED and self._llm is not None:
            proposed = await self._llm_revise(skill, old_content, train_cases, diagnosis)
        else:
            proposed = self._deterministic_revise(skill, old_content, train_cases, diagnosis)

        diff = self.generate_diff(old_content, proposed, skill.meta.name)
        report = SkillOptimizationReport(
            skill_name=skill.meta.name,
            skill_dir=skill_dir,
            train_cases=train_cases,
            validation_cases=validation_cases,
            diagnosis=diagnosis,
            proposed_content=proposed,
            diff=diff,
            applied=False,
        )

        self._emit("skill_optimization_report", {
            "name": skill.meta.name,
            "train_cases": len(train_cases),
            "validation_cases": len(validation_cases),
            "has_diff": bool(diff.strip()),
        })

        if apply and diff.strip():
            self.apply_report(report)

        return report

    def diagnose(self, cases: list[SkillEvalCase], skill_name: str) -> str:
        """Diagnose activation failures. / 诊断激活失败模式。"""
        if not cases:
            return "No evaluation cases provided; using generic description strengthening."

        false_negatives = [c for c in cases if c.expected_activation and not c.actual_activation]
        false_positives = [c for c in cases if not c.expected_activation and c.actual_activation]
        failed_after_activation = [c for c in cases if c.actual_activation and not c.task_success]

        lines = [f"Skill: {skill_name}"]
        lines.append(f"False negatives: {len(false_negatives)}")
        lines.append(f"False positives: {len(false_positives)}")
        lines.append(f"Failed after activation: {len(failed_after_activation)}")

        if false_negatives:
            examples = "; ".join(c.task_description[:80] for c in false_negatives[:3])
            lines.append(f"Need broader trigger wording for: {examples}")
        if false_positives:
            examples = "; ".join(c.task_description[:80] for c in false_positives[:3])
            lines.append(f"Need narrower trigger exclusions for: {examples}")
        if failed_after_activation:
            lines.append("Need clearer workflow/gotchas for execution quality.")

        return "\n".join(lines)

    def generate_diff(self, old_content: str, new_content: str, skill_name: str) -> str:
        """Generate unified diff for review. / 生成统一 diff 供审核。"""
        if old_content == new_content:
            return ""
        return "".join(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{skill_name}/SKILL.md (current)",
            tofile=f"{skill_name}/SKILL.md (proposed)",
        ))

    def apply_report(self, report: SkillOptimizationReport) -> None:
        """Write proposed content to SKILL.md. Explicit opt-in only. / 显式 opt-in 写入。"""
        skill_md = os.path.join(report.skill_dir, "SKILL.md")
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(report.proposed_content)
        report.applied = True
        self._emit("skill_optimization_applied", {
            "name": report.skill_name,
            "path": skill_md,
        })

    # ------------------------------------------------------------------
    # Parsing evaluation results / 解析评测结果
    # ------------------------------------------------------------------

    def _cases_from_results(
        self,
        results: list[dict[str, Any]],
        skill_name: str,
    ) -> list[SkillEvalCase]:
        """Convert TaskEvaluationResult-like dicts into SkillEvalCase objects."""
        cases: list[SkillEvalCase] = []
        for r in results:
            skill_metrics = r.get("skill_metrics") or {}
            execution = r.get("execution") or {}
            expected = skill_metrics.get("expected_activation")
            if expected is None:
                continue
            activated_skills = skill_metrics.get("activated_skills") or []
            actual = bool(execution.get("skill_activations", 0) > 0)
            # If activated_skills is populated, require this skill's name for actual=true.
            if activated_skills:
                actual = skill_name in activated_skills
            cases.append(SkillEvalCase(
                task_id=r.get("task_id", ""),
                task_description=r.get("task_description", ""),
                expected_activation=bool(expected),
                actual_activation=actual,
                task_success=bool(execution.get("task_success", False)),
                activated_skills=list(activated_skills),
                final_answer=r.get("final_answer", ""),
            ))
        return cases

    def _split_cases(self, cases: list[SkillEvalCase]) -> tuple[list[SkillEvalCase], list[SkillEvalCase]]:
        """Deterministic train/validation split. / 确定性训练/验证拆分。"""
        if len(cases) < 2:
            return cases, []
        validation_size = max(1, int(len(cases) * config.SKILL_OPTIMIZE_VALIDATION_RATIO))
        # Deterministic: every Nth case goes to validation, no randomness.
        step = max(2, len(cases) // validation_size)
        validation = [c for i, c in enumerate(cases) if i % step == 0][:validation_size]
        validation_ids = {c.task_id for c in validation}
        train = [c for c in cases if c.task_id not in validation_ids]
        return train, validation

    # ------------------------------------------------------------------
    # Revision strategies / 修订策略
    # ------------------------------------------------------------------

    def _deterministic_revise(
        self,
        skill: SkillDef,
        old_content: str,
        cases: list[SkillEvalCase],
        diagnosis: str,
    ) -> str:
        """Deterministically revise description and add optimization notes."""
        if not cases:
            return old_content
        description = skill.meta.description
        false_negatives = [c for c in cases if c.expected_activation and not c.actual_activation]
        false_positives = [c for c in cases if not c.expected_activation and c.actual_activation]

        additions: list[str] = []
        if false_negatives:
            triggers = self._extract_trigger_terms([c.task_description for c in false_negatives])
            if triggers:
                additions.append("Use when the user mentions: " + ", ".join(triggers[:8]) + ".")
        if false_positives:
            exclusions = self._extract_trigger_terms([c.task_description for c in false_positives])
            if exclusions:
                additions.append("Do not use for unrelated tasks such as: " + ", ".join(exclusions[:6]) + ".")

        new_description = description
        if additions:
            new_description = (description.rstrip(".") + ". " + " ".join(additions))[:1024]

        content = self._replace_frontmatter_description(old_content, new_description)

        if "## Optimization Notes" not in content:
            content += "\n\n## Optimization Notes\n"
        content += f"\n<!-- optimization diagnosis:\n{diagnosis}\n-->\n"
        return content

    async def _llm_revise(
        self,
        skill: SkillDef,
        old_content: str,
        cases: list[SkillEvalCase],
        diagnosis: str,
    ) -> str:
        """LLM-assisted revision; falls back to deterministic on failure."""
        if self._llm is None:
            return self._deterministic_revise(skill, old_content, cases, diagnosis)

        case_text = "\n".join(
            f"- {c.task_id}: expected={c.expected_activation}, actual={c.actual_activation}, task={c.task_description[:120]}"
            for c in cases[:10]
        )
        prompt = (
            "Improve this Agent Skill's SKILL.md to reduce activation false negatives/false positives.\n"
            "Only revise description and guidance text. Do NOT add or change allowed-tools.\n"
            "Return ONLY the complete revised SKILL.md content.\n\n"
            f"Diagnosis:\n{diagnosis}\n\n"
            f"Cases:\n{case_text}\n\n"
            f"Current SKILL.md:\n{old_content}"
        )
        try:
            text = await self._llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=config.SKILL_OPTIMIZE_MAX_TOKENS,
                caller_tag="SkillOptimizer",
            )
            if isinstance(text, str) and text.strip().startswith("---"):
                return text.strip() + "\n"
        except Exception:
            logger.debug("[SkillOptimizer] LLM revision failed", exc_info=True)
        return self._deterministic_revise(skill, old_content, cases, diagnosis)

    # ------------------------------------------------------------------
    # Helpers / 辅助
    # ------------------------------------------------------------------

    def _read_skill_md(self, skill_dir: str) -> str:
        path = os.path.join(skill_dir, "SKILL.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            logger.warning("[SkillOptimizer] cannot read %s", path)
            return ""

    def _replace_frontmatter_description(self, content: str, new_description: str) -> str:
        """Replace single-line frontmatter description, or insert one after name."""
        lines = content.splitlines()
        if len(lines) < 2 or lines[0].strip() != "---":
            return content
        end_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break
        if end_idx is None:
            return content

        replaced = False
        for i in range(1, end_idx):
            if lines[i].startswith("description:"):
                lines[i] = f"description: {new_description}"
                replaced = True
                break
        if not replaced:
            insert_idx = 1
            for i in range(1, end_idx):
                if lines[i].startswith("name:"):
                    insert_idx = i + 1
                    break
            lines.insert(insert_idx, f"description: {new_description}")
        return "\n".join(lines) + "\n"

    def _extract_trigger_terms(self, texts: list[str]) -> list[str]:
        """Extract deterministic trigger terms from task texts."""
        stop_words = {
            "the", "a", "an", "and", "or", "to", "for", "in", "on", "at", "of", "with",
            "by", "is", "are", "it", "this", "that", "please", "use", "using", "skill",
        }
        counts: dict[str, int] = {}
        for text in texts:
            for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower()):
                if word not in stop_words:
                    counts[word] = counts.get(word, 0) + 1
        return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    @staticmethod
    def load_eval_results(path: str) -> list[dict[str, Any]]:
        """Load evaluation results from a JSON report file."""
        if not path:
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            # Common report shapes: {"results": [...]}, {"modes": {...}}
            if isinstance(data.get("results"), list):
                return [x for x in data["results"] if isinstance(x, dict)]
            flattened: list[dict[str, Any]] = []
            for value in data.values():
                if isinstance(value, dict) and isinstance(value.get("results"), list):
                    flattened.extend(x for x in value["results"] if isinstance(x, dict))
            return flattened
        return []
