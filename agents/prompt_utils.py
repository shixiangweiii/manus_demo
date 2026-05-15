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
) -> str:
    """Compose a system prompt with optional context injection and subagent guidance.
    组合系统提示词，按需注入运行时上下文和子智能体引导文本。

    Args:
        base_prompt: The agent's base system prompt.
        inject_context: When True (default), append today's date/time so the LLM
            does not need to discover it via tools.
        inject_subagent_guidance: When True (default), append SubAgent tool
            usage guidance (only emitted if SUBAGENT_ENABLED=true). Set False
            for agents that do not call tools (e.g., Planner, Reflector).
    """
    parts = [base_prompt]
    if inject_context:
        parts.append(build_context_injection())
    if inject_subagent_guidance:
        guidance = get_subagent_guidance()
        if guidance:
            parts.append(guidance)
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
