"""
Skill Registry - In-memory store for discovered skills.
技能注册表——已发现技能的内存存储。

Populated by the runtime after SkillLoader discovery.
Provides formatted descriptions for system prompt injection (progressive
disclosure tier 1: name + description only, ~100 tokens per skill).

由运行时调用 SkillLoader.discover() 后填充。
提供格式化描述供系统提示词注入（渐进式披露第一层：仅名称+描述）。
"""

from __future__ import annotations

import logging

from skills.models import SkillDef

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    In-memory registry of discovered skills / 已发现技能的内存注册表。

    Thread-safety note: this class is built before task execution and then read
    from the runtime event loop, so no explicit locking is needed.

    线程安全说明：运行前完成构造，运行中只在事件循环内读取，无需显式加锁。
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillDef] = {}

    def register(self, skill: SkillDef) -> None:
        """Register a discovered skill. Overwrites if name already exists.
        注册已发现的技能。若名称已存在则覆盖（后发现的同名技能优先）。
        """
        existing = self._skills.get(skill.meta.name)
        if existing is not None:
            logger.debug(
                "[SkillRegistry] Overwriting skill '%s' (was from %s, now from %s)",
                skill.meta.name,
                existing.skill_dir,
                skill.skill_dir,
            )
        self._skills[skill.meta.name] = skill

    def get(self, name: str) -> SkillDef | None:
        """Look up a skill by name. Returns None if not found.
        按名称查找技能。未找到返回 None。
        """
        return self._skills.get(name)

    def list_all(self) -> list[SkillDef]:
        """Return all registered skills, sorted by name.
        返回所有已注册技能，按名称排序。
        """
        return sorted(self._skills.values(), key=lambda s: s.meta.name)

    def format_descriptions(self) -> str:
        """Format skill names and descriptions for system prompt injection.
        格式化技能名称和描述，供系统提示词注入。

        Output format (one line per skill):
          - {name}: {description} [tools: {allowed_tools}]

        Only includes the name, description, and allowed_tools (if non-empty)
        — this is progressive disclosure tier 1, keeping token cost low
        (~100 tokens per skill).

        输出格式（每技能一行）：
        仅包含 name、description 和 allowed_tools（非空时）——
        这是渐进式披露第一层，保持 token 开销低（约 100 tokens/技能）。
        """
        if not self._skills:
            return ""

        lines: list[str] = []
        for skill in self.list_all():
            line = f"- {skill.meta.name}: {skill.meta.description}"
            if skill.meta.allowed_tools:
                tools_str = ", ".join(skill.meta.allowed_tools)
                line += f" [tools: {tools_str}]"
            lines.append(line)

        return "\n".join(lines)

    def load_full_content(self, name: str, *, max_tokens: int) -> str | None:
        """Load the full SKILL.md content for a skill (progressive disclosure tier 2).
        加载技能的完整 SKILL.md 内容（渐进式披露第二层）。

        Returns None if skill not found. Content is truncated at the caller's
        per-runtime ``max_tokens`` limit using a conservative approximation.

        未找到技能时返回 None。内容在 SKILLS_MAX_CONTENT_TOKENS tokens 处截断
        （近似估算，4 chars/token）。

        Args:
            name: Skill name to load.
            max_tokens: Maximum approximate tokens returned to the caller.

        Returns:
            Full content string (possibly truncated), or None if not found.
        """
        skill = self._skills.get(name)
        if skill is None:
            return None

        content = skill.full_content
        # Approximate token counting for mixed English+CJK content.
        # English: ~4 chars/token; CJK: ~1.5 chars/token.
        # Use a conservative estimate of 2 chars/token to avoid context overflow
        # for CJK-heavy skill content.
        # 混合英文+中文内容的近似 token 计数。
        # 英文：约 4 字符/token；中文：约 1.5 字符/token。
        # 使用保守估算 2 字符/token，避免中文偏重的技能内容导致上下文溢出。
        max_chars = max_tokens * 2

        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n[Content truncated at skill token limit]"
            logger.debug(
                "[SkillRegistry] Skill '%s' content truncated from %d to %d chars (%d tokens est.)",
                name, len(skill.full_content), max_chars, max_tokens,
            )

        return content

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills
