"""
Prompt utilities - Shared system prompt components for agent tool selection guidance.
提示词工具 - 智能体工具选择引导的共享系统提示组件。
"""
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


def build_system_prompt(base_prompt: str) -> str:
    """Compose a system prompt with optional subagent guidance.
    组合系统提示词，根据 SUBAGENT_ENABLED 条件性附加引导文本。"""
    guidance = get_subagent_guidance()
    if guidance:
        return base_prompt + guidance
    return base_prompt
