"""
Prompt utilities - Shared system prompt components for agent tool selection guidance.
提示词工具 - 智能体工具选择引导的共享系统提示组件。
"""
from datetime import datetime

import config

# SubAgent tool usage guidance (appended to system prompts when SUBAGENT_ENABLED=true)
# 子智能体工具使用引导（SUBAGENT_ENABLED=true 时追加到系统提示词）
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


def get_subagent_guidance() -> str:
    """Return subagent guidance string if enabled, empty string otherwise.
    SUBAGENT_ENABLED=true 时返回引导文本，否则返回空字符串。"""
    if config.SUBAGENT_ENABLED:
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


# HITL tool usage guidance (injected when HITL_ENABLED=true)
# 人机交互工具使用引导（HITL_ENABLED=true 时追加到系统提示词）
_HITL_GUIDANCE = """

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
- Repeatedly asking the same question (max 5 calls per task)

When you do call ask_user, phrase your question clearly and include
the context of what you already know. For example:
"I found your location as Beijing via IP geolocation. Is this correct?
If not, please tell me your city."
"""


def get_hitl_guidance() -> str:
    """Return HITL guidance string if enabled, empty string otherwise.
    HITL_ENABLED=true 时返回引导文本，否则返回空字符串。"""
    if config.HITL_ENABLED:
        return _HITL_GUIDANCE
    return ""


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
) -> str:
    """Compose a system prompt with optional context / location / search / subagent / HITL guidance.
    组合系统提示词，按需注入运行时上下文、位置工具引导、搜索工具引导、子智能体引导和人机交互引导。

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
