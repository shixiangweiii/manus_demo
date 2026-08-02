"""
Prompt utilities - Shared system prompt components for agent tool selection guidance.
提示词工具 - 智能体工具选择引导的共享系统提示组件。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import config

# Subagent guidance is controlled by the current runtime capability settings.
# 子智能体引导由当前运行时的 capability 配置控制。
_SUBAGENT_GUIDANCE = """

## Tool Selection: When to Use the "subagent" Tool

You have access to a "subagent" tool that spawns an isolated sub-agent for focused subtasks.
Use this tool ONLY when the subtask meets ALL of these conditions:
- It requires 3+ tool calls to complete (multi-step work)
- It is a self-contained unit of work with clear boundaries
- Its result can be summarized without needing full intermediate context

DO NOT use the "subagent" tool for:
- Single operations (reading one file, running one command, one search query)
- Tasks where you need to see intermediate results to decide the next step
- Simple lookups or transformations that one tool call can handle

When in doubt, use basic tools directly. The subagent tool trades context visibility for isolation.
"""


@dataclass(frozen=True)
class PromptCapabilities:
    """Immutable, runtime-local facts used to compose system prompts.

    Prompt construction used to depend on mutable module globals populated by
    the last runtime that happened to be built.  Keeping the facts in an
    explicit value object makes two runtimes safe to construct and run in the
    same process, and lets child agents describe their restricted tool set.
    """

    tool_names: frozenset[str] = frozenset()
    python_command: str = "python3"
    hitl_configured: bool = False
    hitl_max_prompts: int = 3
    skill_descriptions: str = ""
    skills_max_activations: int = 1

    @classmethod
    def from_tools(
        cls,
        tools: Iterable[object],
        *,
        python_command: str = "python3",
        hitl_configured: bool = False,
        hitl_max_prompts: int = 3,
        skill_descriptions: str = "",
        skills_max_activations: int = 1,
    ) -> "PromptCapabilities":
        return cls(
            tool_names=frozenset(
                name
                for tool in tools
                if isinstance((name := getattr(tool, "name", None)), str)
            ),
            python_command=python_command,
            hitl_configured=hitl_configured,
            hitl_max_prompts=hitl_max_prompts,
            skill_descriptions=skill_descriptions,
            skills_max_activations=skills_max_activations,
        )

    def has_tool(self, name: str) -> bool:
        return name in self.tool_names


def get_subagent_guidance(
    capabilities: PromptCapabilities | None = None,
) -> str:
    """Return subagent guidance only when that tool is actually available."""
    enabled = (
        capabilities.has_tool("subagent")
        if capabilities is not None
        else config.SUBAGENT_ENABLED
    )
    if enabled:
        return _SUBAGENT_GUIDANCE
    return ""


# Location tool usage guidance (always injected when location tool is registered)
# 用户位置工具使用引导（始终注入，引导 LLM 在需要位置时主动调用工具而非臆造默认值）
_LOCATION_GUIDANCE = """

## Tool Selection: When to Use the "get_user_location" Tool

Some tasks (weather, local time, nearby restaurants, news, etc.) require
the user's current city. The user often does not state it explicitly.
In that case:
- Call get_user_location BEFORE making any assumption about the city.
- Use the returned city verbatim in subsequent steps.
- If the tool returns "Error: ...", ask the user for their city or
  state clearly that location is unknown — do NOT invent a default
  (no "默认北京", no "default to capital", no fabricated city).

DO NOT call get_user_location for tasks that do not depend on location
(coding help, math, general Q&A, file operations on local sandbox).
"""


def get_location_guidance(
    capabilities: PromptCapabilities | None = None,
) -> str:
    """Return location guidance when ``get_user_location`` is available."""
    if capabilities is not None and not capabilities.has_tool("get_user_location"):
        return ""
    return _LOCATION_GUIDANCE


# Search tool priority guidance (always injected for tool-calling agents)
# 搜索工具优先级引导（始终注入给工具调用类智能体）
_SEARCH_TOOL_GUIDANCE = """

## Tool Selection: Prefer Built-in Search Tools for Information Retrieval

For information retrieval tasks (weather, news, facts, stock prices,
translations, current events, etc.), follow this priority:

1. **web_search** — search the web for relevant information
2. **fetch_url** — extract content from specific URLs found via search
3. Only use **execute_python** for HTTP requests if the built-in tools
   cannot provide the needed data (e.g., a specific REST API with
   structured JSON output that search cannot find)

**execute_python is best reserved for**: computation, data processing,
file manipulation, algorithm implementation, and tasks that cannot be
accomplished with the other tools.

Do NOT use execute_python to call public APIs (weather APIs, news APIs,
etc.) when web_search + fetch_url can obtain the same information.
"""


def get_search_guidance(
    capabilities: PromptCapabilities | None = None,
) -> str:
    """Return search guidance mentioning only tools available to this loop."""
    if capabilities is None:
        return _SEARCH_TOOL_GUIDANCE
    search_tools = [
        name for name in ("web_search", "fetch_url") if capabilities.has_tool(name)
    ]
    if not search_tools:
        return ""
    lines = [
        "\n\n## Tool Selection: Prefer Built-in Search Tools for Information Retrieval\n",
        "For current-information and retrieval tasks, prefer the available "
        "built-in tools in this order:",
    ]
    for index, name in enumerate(search_tools, start=1):
        purpose = (
            "search the web for relevant information"
            if name == "web_search"
            else "extract content from a specific URL"
        )
        lines.append(f"{index}. **{name}** — {purpose}")
    if capabilities.has_tool("execute_python"):
        lines.extend(
            [
                "Use **execute_python** for computation, data processing, file "
                "manipulation, or a structured API that the search tools cannot serve.",
                "Do not use execute_python for public information that the built-in "
                "search tools can obtain.",
            ]
        )
    return "\n".join(lines) + "\n"


# HITL tool usage guidance (injected when HITL is active)
# 人机交互工具使用引导（HITL 激活时追加到系统提示词）
#
# Availability comes from the immutable runtime-local prompt capabilities.
_HITL_GUIDANCE_TEMPLATE = """

## Tool Selection: When to Use the "ask_user" Tool

You have access to an "ask_user" tool that lets you ask the user
a question during execution. Use this tool ONLY when:
- You have APPROXIMATE or ambiguous information that could lead to
  wrong results (e.g., IP-based location that may be incorrect)
- You need a user preference or confirmation that no tool can provide
- The task is unclear and proceeding with assumptions would be risky

DO NOT use the "ask_user" tool for:
- Questions you can answer with other tools (web_search, etc.)
- Routine task execution where the user's original instruction is clear
- Repeatedly asking the same question (max {max_prompts} calls per task)

When you do call ask_user, phrase your question clearly and include
the context of what you already know. For example:
"I found your location as Beijing via IP geolocation. Is this correct?
If not, please tell me your city."
"""


def get_hitl_guidance(
    capabilities: PromptCapabilities | None = None,
) -> str:
    """Return HITL guidance string if HITL is active, empty string otherwise.

    Runtime callers pass explicit capabilities; the config fallback exists for
    standalone callers that do not have a runtime tool bundle.

    HITL 激活时返回引导文本，否则返回空字符串。max-prompts 从 config 动态注入。"""
    enabled = (
        capabilities.has_tool("ask_user")
        if capabilities is not None
        else config.HITL_ENABLED
    )
    if not enabled:
        return ""
    return _HITL_GUIDANCE_TEMPLATE.format(
        max_prompts=(
            capabilities.hitl_max_prompts
            if capabilities is not None
            else config.HITL_MAX_PROMPTS_PER_TASK
        )
    )


_HITL_UNAVAILABLE_GUIDANCE = """

## Runtime Constraint: ask_user Is Unavailable

HITL is configured globally, but this run is non-interactive. The "ask_user"
tool is NOT available in this run. Do not create plan steps that call ask_user,
do not simulate asking the user, and do not wait for user input. If the user's
task explicitly asks for ask_user, plan a best-effort response explaining that
ask_user is unavailable in non-interactive mode.
"""


def get_hitl_unavailable_guidance(
    capabilities: PromptCapabilities | None = None,
) -> str:
    """Return planning guidance when HITL is configured but runtime-disabled."""
    if capabilities is not None:
        if capabilities.hitl_configured and not capabilities.has_tool("ask_user"):
            return _HITL_UNAVAILABLE_GUIDANCE
        return ""
    if config.HITL_ENABLED:
        return _HITL_UNAVAILABLE_GUIDANCE
    return ""


# Skill activation guidance (injected when skills are enabled and discovered)
# 技能激活引导（技能启用且已发现时追加到系统提示词）
#
# Skill descriptions are carried by ``PromptCapabilities`` instead of process
# globals so concurrent runtimes cannot overwrite one another.
_SKILL_GUIDANCE_TEMPLATE = """

## Tool Selection: When to Use the "activate_skill" Tool

You have access to an "activate_skill" tool that loads detailed instructions
for a capability (a "skill"). Skills are listed in the "=== Available Skills ==="
section of your context.

Use activate_skill ONLY when:
- A skill's description matches the current task's needs
- You need specialized instructions or constraints for a domain
- The skill's pre-authorized tools would be useful for the task

DO NOT use activate_skill for:
- Tasks you can complete with built-in tools without specialized guidance
- Activating multiple skills at once (max {max_activations} per task)
- Activating a skill "just in case" — only activate when genuinely needed

After activation, the skill's full instructions appear in your context and
any tool pre-authorizations take effect.
"""


def get_skill_guidance(
    capabilities: PromptCapabilities | None = None,
) -> str:
    """Return skill guidance string if Skills are enabled and skills exist, empty string otherwise.
    Skills 启用且存在技能时返回引导文本，否则返回空字符串。

    Runtime guidance is injected only when ``activate_skill`` is in the loop's
    actual tool set and that tool exposes at least one discovered skill.

    This prevents injecting the guidance when no skills are available,
    which would confuse the LLM into trying to activate non-existent skills.
    """
    if capabilities is None:
        return ""
    enabled = capabilities.has_tool("activate_skill")
    if not enabled:
        return ""
    descriptions = capabilities.skill_descriptions
    if not descriptions:
        return ""
    return _SKILL_GUIDANCE_TEMPLATE.format(
        max_activations=(
            capabilities.skills_max_activations
        )
    )


def build_context_injection(
    capabilities: PromptCapabilities | None = None,
) -> str:
    """
    Build runtime context to inject into system prompts: today's date, weekday, etc.
    构建注入到系统提示词的运行时上下文：当前日期、星期几等。

    This eliminates two recurring failure modes:
    - LLM guesses the wrong year in search queries (training-data drift)
    - Planner over-splits "today/tomorrow" tasks because it can't tell the LLM
      already knows the date
    消除两类常见失败：
    - LLM 在搜索查询中猜错年份（训练数据漂移导致）
    - Planner 把"今天/明天"类任务过度拆分，因为它不知道 LLM 已经知道日期
    """
    now = datetime.now()
    weekday_en = now.strftime("%A")
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    lines = [
        "\n\n## Current Context (auto-injected, treat as ground truth)",
        f"- Today's date: {now.strftime('%Y-%m-%d')} ({weekday_en} / {weekday_zh})",
        f"- Current time: {now.strftime('%H:%M')} (local timezone)",
    ]
    if capabilities is None or capabilities.has_tool("execute_shell"):
        command = (
            capabilities.python_command
            if capabilities is not None
            else config.PYTHON_COMMAND
        )
        lines.append(
            f"- Python command for shell tasks: `{command}`. Use this command "
            "instead of bare `python` when invoking Python from shell."
        )
    lines.append(
        "Use these values directly when composing search queries or reasoning "
        "about \"today\" / \"tomorrow\" / \"yesterday\". Do NOT ask tools for the "
        "date when it is already provided here."
    )
    return "\n".join(lines) + "\n"


def build_system_prompt(
    base_prompt: str,
    inject_context: bool = True,
    inject_subagent_guidance: bool = True,
    inject_location_guidance: bool = True,
    inject_search_guidance: bool = True,
    inject_hitl_guidance: bool = True,
    inject_hitl_unavailable_guidance: bool = False,
    inject_skill_guidance: bool = True,
    capabilities: PromptCapabilities | None = None,
) -> str:
    """Compose a system prompt with optional context / location / search / subagent / HITL / skill guidance.
    组合系统提示词，按需注入运行时上下文、位置工具引导、搜索工具引导、子智能体引导、人机交互引导和技能引导。

    Args:
        base_prompt: The agent's base system prompt.
        inject_context: When True (default), append today's date/time so the LLM
            does not need to discover it via tools.
        inject_location_guidance: When True (default), append get_user_location
            tool usage guidance. Set False for agents that do not call tools
            (e.g., Reflector).
        inject_search_guidance: When True (default), append search tool priority
            guidance (prefer web_search/fetch_url over execute_python for info
            retrieval).
        inject_subagent_guidance: When True (default), append SubAgent tool
            usage guidance (only emitted if SUBAGENT_ENABLED=true). Set False
            for agents that do not call tools (e.g., Planner, Reflector).
        inject_hitl_guidance: When True (default), append HITL (ask_user) tool
            usage guidance (only emitted if HITL_ENABLED=true). Set False for
            agents that do not call tools (e.g., Planner, Reflector).
        inject_hitl_unavailable_guidance: When True, append a planning constraint
            when HITL is configured but disabled by runtime mode.
        inject_skill_guidance: When True (default), append Skill activation
            tool usage guidance (only emitted if SKILLS_ENABLED=true and skills
            were discovered). Set False for agents that do not call tools.
    """
    capabilities = capabilities or PromptCapabilities(
        python_command=config.PYTHON_COMMAND,
        hitl_configured=config.HITL_ENABLED,
        hitl_max_prompts=config.HITL_MAX_PROMPTS_PER_TASK,
        skills_max_activations=config.SKILLS_MAX_ACTIVATIONS_PER_TASK,
    )
    parts = [base_prompt]
    if inject_context:
        parts.append(build_context_injection(capabilities))
    if inject_location_guidance:
        guidance = get_location_guidance(capabilities)
        if guidance:
            parts.append(guidance)
    if inject_search_guidance:
        guidance = get_search_guidance(capabilities)
        if guidance:
            parts.append(guidance)
    if inject_subagent_guidance:
        guidance = get_subagent_guidance(capabilities)
        if guidance:
            parts.append(guidance)
    if inject_hitl_guidance:
        hitl_guidance = get_hitl_guidance(capabilities)
        if hitl_guidance:
            parts.append(hitl_guidance)
    if inject_hitl_unavailable_guidance:
        unavailable_guidance = get_hitl_unavailable_guidance(capabilities)
        if unavailable_guidance:
            parts.append(unavailable_guidance)
    if inject_skill_guidance:
        skill_guidance = get_skill_guidance(capabilities)
        if skill_guidance:
            parts.append(skill_guidance)
    if (
        inject_skill_guidance
        and capabilities is not None
        and capabilities.has_tool("activate_skill")
        and capabilities.skill_descriptions
    ):
        parts.append(
            "\n\n=== Available Skills ===\n"
            f"{capabilities.skill_descriptions}\n"
            "=== End Available Skills ===\n"
        )
    return "".join(parts)


def build_convergence_hint(tool_call_counts: dict[str, int]) -> str:
    """
    Build dynamic convergence guidance based on tool call frequency.
    根据工具调用频率构建动态收敛提示。

    Args:
        tool_call_counts: Mapping of tool_name → call count from tool_calls_log.

    Returns:
        Hint string to append to continue_msg, or empty string if no hint needed.
    """
    threshold = config.SEARCH_CONVERGENCE_THRESHOLD
    hint_parts: list[str] = []

    search_count = tool_call_counts.get("web_search", 0)
    if search_count >= threshold:
        if search_count >= threshold * 2:
            hint_parts.append(
                f"\n\nCRITICAL: You have called web_search {search_count} times. "
                "Either use fetch_url to access specific pages from results, "
                "or synthesize your final answer from accumulated data. "
                "Do NOT call web_search again."
            )
        else:
            hint_parts.append(
                f"\n\nNOTE: You have called web_search {search_count} times. "
                "Consider using fetch_url for specific URLs from your "
                "search results, or synthesize an answer from accumulated data."
            )

    fetch_count = tool_call_counts.get("fetch_url", 0)
    if fetch_count >= threshold:
        hint_parts.append(
            f"\n\nNOTE: You have called fetch_url {fetch_count} times. "
            "If you have enough information, provide your final answer now."
        )

    return "".join(hint_parts)
