"""
Skill Activation Tool - Lets the LLM activate a skill at runtime.
技能激活工具——让 LLM 在运行时激活技能。

When the LLM calls this tool, the full SKILL.md body is returned,
providing the LLM with detailed instructions, constraints, and tool
guidance from the activated skill (progressive disclosure tier 2).

LLM 调用此工具时，返回完整 SKILL.md 内容，提供激活技能的详细指令、
约束和工具引导（渐进式披露第二层）。

After activation, the skill's allowed_tools list is
applied as a tool filter on the current structured tool-calling loop, and max activations
per task are enforced.

激活后，技能的 allowed_tools 列表作为工具过滤应用于当前
结构化工具调用循环，并强制执行每任务最大激活次数。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from skills.registry import SkillRegistry
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class SkillActivationTool(BaseTool):
    """
    Tool that activates a skill and returns its full content.
    激活技能并返回其完整内容的工具。

    Returns the SKILL.md body, applies its tool filter, and enforces limits.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        on_event: Callable[[str, Any], None] | None = None,
        tool_filter_callback: Callable[[list[str]], None] | None = None,
        max_activations: int = 3,
        max_content_tokens: int = 5000,
        guardrail: Any | None = None,
    ):
        """
        Args:
            registry: SkillRegistry to look up skills from.
            on_event: Event callback for emitting skill events.
            tool_filter_callback: callback to apply the allowed-tools filter.
                Called with the skill's allowed_tools list after activation.
            max_activations: Per-task activation limit.
            max_content_tokens: Max content tokens per activation.
        """
        self._registry = registry
        self._on_event = on_event or (lambda *_: None)
        self._tool_filter_callback = tool_filter_callback
        self._max_activations = max_activations
        self._max_content_tokens = max_content_tokens
        self._activation_count: int = 0
        self._active_skills: list[str] = []
        self._guardrail = guardrail

    @property
    def name(self) -> str:
        return "activate_skill"

    @property
    def description(self) -> str:
        # Dynamically list available skills in the description so the LLM
        # can see what's available without reading the system prompt section.
        # 动态列出可用技能，LLM 可直接在工具描述中看到。
        skill_names = [s.meta.name for s in self._registry.list_all()]
        available = ", ".join(skill_names) if skill_names else "(none)"
        return (
            "Activate a skill by name to get its detailed instructions and guidance. "
            "Use this when the available skills list suggests a skill relevant to the "
            "current task. After activation, the skill's instructions are loaded into "
            "your context and any tool pre-authorizations are applied. "
            f"Available skills: [{available}]. "
            "激活指定名称的技能，获取其详细指令和引导。当可用技能列表中某个技能"
            "与当前任务相关时调用此工具。激活后，技能指令加载到上下文中，"
            "相关工具预授权自动生效。"
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to activate / 要激活的技能名称",
                },
            },
            "required": ["skill_name"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Activate a skill: return full content and apply its tool filter.
        激活技能：返回完整内容并应用工具过滤。
        """
        skill_name = kwargs.get("skill_name", "").strip()

        if not skill_name:
            return "Error: skill_name parameter is required"

        # Check activation limit / 检查激活次数上限
        if self._activation_count >= self._max_activations:
            self._on_event("skill_activation_failed", {
                "name": skill_name,
                "error": f"Max activations reached ({self._max_activations} per task)",
            })
            return (
                f"Error: Maximum skill activations reached ({self._max_activations} per task). "
                f"Cannot activate '{skill_name}'."
            )

        # Check if skill is already active — return cached result without wasting slot
        # 检查技能是否已激活——返回缓存结果，不浪费激活槽位
        if skill_name in self._active_skills:
            content = self._registry.load_full_content(
                skill_name,
                max_tokens=self._max_content_tokens,
            )
            if content is None:
                return f"Error: Failed to load content for skill '{skill_name}'"
            logger.debug(
                "[SkillActivationTool] Skill '%s' already active, returning cached content",
                skill_name,
            )
            return f"[Skill Already Active: {skill_name}]\n\n{content}"

        # Look up skill in registry / 在注册表中查找技能
        skill = self._registry.get(skill_name)
        if skill is None:
            available = [s.meta.name for s in self._registry.list_all()]
            self._on_event("skill_activation_failed", {
                "name": skill_name,
                "error": f"Skill not found. Available: {available}",
            })
            return (
                f"Error: Skill '{skill_name}' not found. "
                f"Available skills: {', '.join(available) if available else 'none'}"
            )

        # Increment activation count (before any await — budget reservation pattern)
        # 递增激活计数（在任何 await 之前——预算预留模式）
        self._activation_count += 1

        # Load full content with truncation / 加载完整内容（含截断）
        content = self._registry.load_full_content(
            skill_name,
            max_tokens=self._max_content_tokens,
        )
        if content is None:
            # Should not happen since we just checked get(), but be defensive
            # 不应发生（刚检查过 get()），但保持防御性
            return f"Error: Failed to load content for skill '{skill_name}'"

        # Apply the skill-content guardrail based on trust level.
        # 根据信任等级应用技能内容护栏。
        if self._guardrail is not None:
            try:
                decision = self._guardrail.scan_skill_content(content, skill.meta.license)
                if decision.transformed_text is not None:
                    content = decision.transformed_text
                    self._on_event("skill_content_guarded", {
                        "name": skill_name,
                        "trust_level": skill.meta.license,
                        "action": decision.action.value,
                        "reason": decision.reason,
                    })
            except Exception as exc:
                logger.error(
                    "[SkillActivationTool] Guardrail failed for skill '%s'",
                    skill_name,
                    exc_info=True,
                )
                self._on_event("skill_activation_failed", {
                    "name": skill_name,
                    "error": f"guardrail check failed: {exc}",
                })
                return f"Error: Skill guardrail check failed for '{skill_name}': {exc}"

        # Only expose the skill as active after its content passes the guardrail.
        self._active_skills.append(skill_name)
        self._on_event("skill_activated", {
            "name": skill_name,
            "activation_count": self._activation_count,
            "content_length": len(content),
            "allowed_tools": skill.meta.allowed_tools,
            "trust_level": skill.meta.license,
        })

        # Apply every skill's policy. An empty allowed_tools list explicitly
        # means "all tools", so it must also clear a previous restrictive
        # skill filter when multiple activations are allowed in one task.
        if self._tool_filter_callback is not None:
            self._tool_filter_callback(skill.meta.allowed_tools)

        logger.info(
            "[SkillActivationTool] Activated skill '%s' (%d/%d activations, %d chars)",
            skill_name, self._activation_count, self._max_activations, len(content),
        )

        # Return the skill content with a header / 返回带标题的技能内容
        header = f"[Skill Activated: {skill_name}]"
        if skill.meta.allowed_tools:
            header += f" [Pre-authorized tools: {', '.join(skill.meta.allowed_tools)}]"
        return f"{header}\n\n{content}"

    def reset_task_state(self) -> None:
        """Reset activation state before a new runtime task.
        在新的运行时任务前重置激活状态。
        """
        self._activation_count = 0
        self._active_skills = []

    def set_tool_filter_callback(self, callback: Callable[[list[str]], None]) -> None:
        """Set the callback that filters available tools after skill activation.
        设置技能激活后过滤可用工具的回调。
        """
        self._tool_filter_callback = callback

    def clone(
        self,
        *,
        tool_filter_callback: Callable[[list[str]], None] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> "SkillActivationTool":
        """Return an independent task-local activation tool.

        The registry is immutable during execution and can be shared, while
        activation counters and the tool-filter callback must belong to one
        concrete loop.  In particular, a child AgentLoop must never mutate the
        parent loop's filter callback.
        """
        return SkillActivationTool(
            registry=self._registry,
            on_event=on_event if on_event is not None else self._on_event,
            tool_filter_callback=tool_filter_callback,
            max_activations=self._max_activations,
            max_content_tokens=self._max_content_tokens,
            guardrail=self._guardrail,
        )

    @property
    def skill_descriptions(self) -> str:
        """Formatted progressive-disclosure summary for this tool's registry."""
        return self._registry.format_descriptions()

    @property
    def max_activations(self) -> int:
        return self._max_activations

    @property
    def active_skills(self) -> list[str]:
        """Return list of skill names activated in the current task.
        返回当前任务中已激活的技能名称列表。
        """
        return list(self._active_skills)

    @property
    def activation_count(self) -> int:
        """Return the number of activations in the current task.
        返回当前任务的激活次数。
        """
        return self._activation_count
