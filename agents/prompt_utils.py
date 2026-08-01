"""
Prompt utilities - Shared system prompt components for agent tool selection guidance.
提示词工具 - 智能体工具选择引导的共享系统提示组件。
"""
from datetime import datetime

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

_SUBAGENT_RUNTIME_OVERRIDE: bool | None = None


def set_subagent_runtime_enabled(enabled: bool | None) -> None:
    global _SUBAGENT_RUNTIME_OVERRIDE
    _SUBAGENT_RUNTIME_OVERRIDE = enabled


def get_subagent_guidance() -> str:
    """Return subagent guidance when the current runtime enables it."""
    enabled = (
        _SUBAGENT_RUNTIME_OVERRIDE
        if _SUBAGENT_RUNTIME_OVERRIDE is not None
        else config.SUBAGENT_ENABLED
    )
    if enabled:
        return _SUBAGENT_GUIDANCE
    return ""


# TODO parallel dispatch guidance is appended only when both TODO parallelism
# and subagents are enabled. Unlike the
# generic _SUBAGENT_GUIDANCE (which ends with "when in doubt, use basic tools"),
# this actively encourages keeping independent subjects as separate dependency-free
# TODOs so the scheduler can fan them out to isolated sub-agents in parallel.
# TODO 并行派发引导由运行时 capability 配置注入。
_EMERGENT_PARALLEL_GUIDANCE = """

## Parallel Execution of Independent TODOs

This task may contain multiple INDEPENDENT subjects/subtasks. The scheduler can
execute mutually independent TODOs IN PARALLEL by dispatching each to an isolated
sub-agent. To enable this:
- Keep genuinely independent subjects as SEPARATE TODOs with EMPTY `dependencies`.
- Do NOT merge unrelated research subjects into one TODO.
- Do NOT add artificial dependencies between subjects that don't actually depend
  on each other.
- Only declare a dependency when a TODO truly needs another TODO's output.

Each independent TODO will be run by its own focused sub-agent and only its
summary is returned — so write each TODO description as a self-contained,
fully-specified unit of work.
"""


def get_emergent_parallel_guidance(
    parallel_todos: bool | None = None,
    subagent_enabled: bool | None = None,
) -> str:
    """Return emergent parallel-dispatch guidance, or empty string when disabled.
    EMERGENT_PARALLEL_TODOS 且 SUBAGENT_ENABLED 同时为 true 时返回引导，否则空串。"""
    parallel = config.EMERGENT_PARALLEL_TODOS if parallel_todos is None else parallel_todos
    subagent = config.SUBAGENT_ENABLED if subagent_enabled is None else subagent_enabled
    if parallel and subagent:
        return _EMERGENT_PARALLEL_GUIDANCE
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


def get_location_guidance() -> str:
    """Return the get_user_location tool guidance string (always on).
    返回 get_user_location 工具引导（始终启用）。"""
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


def get_search_guidance() -> str:
    """Return the search tool priority guidance string (always on).
    返回搜索工具优先级引导（始终启用）。"""
    return _SEARCH_TOOL_GUIDANCE


# HITL tool usage guidance (injected when HITL is active)
# 人机交互工具使用引导（HITL 激活时追加到系统提示词）
#
# Activation gating uses a runtime override based on
# interactive mode) with fallback to config.HITL_ENABLED. This avoids injecting
# the guidance in non-interactive single-task mode where ask_user would only
# return Error: anyway — preventing wasted LLM calls on a tool the LLM cannot
# usefully invoke.
# 通过运行时开关 + config 兜底门控；非交互模式下既不注册工具也不注入引导，
# 避免 LLM 调用一个注定返回 Error 的工具。
_HITL_RUNTIME_OVERRIDE: bool | None = None


def set_hitl_runtime_enabled(enabled: bool | None) -> None:
    """Runtime override for HITL guidance injection.

    Set by the runtime based on interactive mode and structured settings.
    Pass None (or never call) to fall back to config.HITL_ENABLED.

    由运行时根据 interactive 模式与结构化配置设置此开关。
    None 表示回退到 config.HITL_ENABLED。"""
    global _HITL_RUNTIME_OVERRIDE
    _HITL_RUNTIME_OVERRIDE = enabled


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


def get_hitl_guidance() -> str:
    """Return HITL guidance string if HITL is active, empty string otherwise.

    Active when runtime override is True, or (override unset AND config.HITL_ENABLED).
    The max-prompts limit is interpolated from config.HITL_MAX_PROMPTS_PER_TASK
    so the LLM always sees the actual configured value.

    HITL 激活时返回引导文本，否则返回空字符串。max-prompts 从 config 动态注入。"""
    enabled = (
        _HITL_RUNTIME_OVERRIDE
        if _HITL_RUNTIME_OVERRIDE is not None
        else config.HITL_ENABLED
    )
    if not enabled:
        return ""
    return _HITL_GUIDANCE_TEMPLATE.format(
        max_prompts=config.HITL_MAX_PROMPTS_PER_TASK
    )


_HITL_UNAVAILABLE_GUIDANCE = """

## Runtime Constraint: ask_user Is Unavailable

HITL is configured globally, but this run is non-interactive. The "ask_user"
tool is NOT available in this run. Do not create plan steps that call ask_user,
do not simulate asking the user, and do not wait for user input. If the user's
task explicitly asks for ask_user, plan a best-effort response explaining that
ask_user is unavailable in non-interactive mode.
"""


def get_hitl_unavailable_guidance() -> str:
    """Return planning guidance when HITL is configured but runtime-disabled."""
    if config.HITL_ENABLED and _HITL_RUNTIME_OVERRIDE is False:
        return _HITL_UNAVAILABLE_GUIDANCE
    return ""


# Skill activation guidance (injected when skills are enabled and discovered)
# 技能激活引导（技能启用且已发现时追加到系统提示词）
#
# Uses the same module-level variable pattern as HITL's _HITL_RUNTIME_OVERRIDE:
# The runtime calls set_skill_descriptions() after discovery, and
# get_skill_guidance() reads it at system prompt build time. This avoids
# changing planner constructor signatures.
# 使用与 HITL _HITL_RUNTIME_OVERRIDE 相同的模块级变量模式：
# 运行时在发现技能后调用 set_skill_descriptions()，
# get_skill_guidance() 在系统提示词构建时读取。避免修改各 Agent 构造函数签名。
_SKILL_DESCRIPTIONS: str = ""
_SKILLS_RUNTIME_ENABLED: bool | None = None
_SKILLS_RUNTIME_MAX_ACTIVATIONS: int | None = None


def set_skill_descriptions(
    descriptions: str,
    *,
    enabled: bool | None = None,
    max_activations: int | None = None,
) -> None:
    """Set the formatted skill descriptions for guidance injection.
    设置格式化的技能描述供引导注入。

    Called by the runtime after SkillLoader.discover() completes.
    Only when SKILLS_ENABLED=true; otherwise should not be called.
    """
    global _SKILL_DESCRIPTIONS, _SKILLS_RUNTIME_ENABLED, _SKILLS_RUNTIME_MAX_ACTIVATIONS
    _SKILL_DESCRIPTIONS = descriptions
    _SKILLS_RUNTIME_ENABLED = enabled
    _SKILLS_RUNTIME_MAX_ACTIVATIONS = max_activations


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


def get_skill_guidance() -> str:
    """Return skill guidance string if Skills are enabled and skills exist, empty string otherwise.
    Skills 启用且存在技能时返回引导文本，否则返回空字符串。

    The guidance is only injected when BOTH conditions are met:
    1. config.SKILLS_ENABLED is True (master switch)
    2. _SKILL_DESCRIPTIONS is non-empty (skills were discovered)

    This prevents injecting the guidance when no skills are available,
    which would confuse the LLM into trying to activate non-existent skills.
    """
    enabled = (
        _SKILLS_RUNTIME_ENABLED
        if _SKILLS_RUNTIME_ENABLED is not None
        else config.SKILLS_ENABLED
    )
    if not enabled:
        return ""
    if not _SKILL_DESCRIPTIONS:
        return ""
    return _SKILL_GUIDANCE_TEMPLATE.format(
        max_activations=(
            _SKILLS_RUNTIME_MAX_ACTIVATIONS
            if _SKILLS_RUNTIME_MAX_ACTIVATIONS is not None
            else config.SKILLS_MAX_ACTIVATIONS_PER_TASK
        )
    )


def build_context_injection() -> str:
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
    return (
        "\n\n## Current Context (auto-injected, treat as ground truth)\n"
        f"- Today's date: {now.strftime('%Y-%m-%d')} ({weekday_en} / {weekday_zh})\n"
        f"- Current time: {now.strftime('%H:%M')} (local timezone)\n"
        f"- Python command for shell tasks: `{config.PYTHON_COMMAND}`. "
        "Use this command instead of bare `python` when invoking Python from shell.\n"
        "Use these values directly when composing search queries or reasoning "
        "about \"today\" / \"tomorrow\" / \"yesterday\". Do NOT ask tools for the "
        "date when it is already provided here.\n"
    )


def build_system_prompt(
    base_prompt: str,
    inject_context: bool = True,
    inject_subagent_guidance: bool = True,
    inject_location_guidance: bool = True,
    inject_search_guidance: bool = True,
    inject_hitl_guidance: bool = True,
    inject_hitl_unavailable_guidance: bool = False,
    inject_skill_guidance: bool = True,
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
    parts = [base_prompt]
    if inject_context:
        parts.append(build_context_injection())
    if inject_location_guidance:
        parts.append(get_location_guidance())
    if inject_search_guidance:
        parts.append(get_search_guidance())
    if inject_subagent_guidance:
        guidance = get_subagent_guidance()
        if guidance:
            parts.append(guidance)
    if inject_hitl_guidance:
        hitl_guidance = get_hitl_guidance()
        if hitl_guidance:
            parts.append(hitl_guidance)
    if inject_hitl_unavailable_guidance:
        unavailable_guidance = get_hitl_unavailable_guidance()
        if unavailable_guidance:
            parts.append(unavailable_guidance)
    if inject_skill_guidance:
        skill_guidance = get_skill_guidance()
        if skill_guidance:
            parts.append(skill_guidance)
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
