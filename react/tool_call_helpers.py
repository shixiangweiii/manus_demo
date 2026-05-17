"""
Shared helpers for ReAct-style tool execution.
ReAct 风格工具执行的共享辅助函数。

Three independent ReAct loops live in the codebase:
  - react.engine.ReActEngine._exec_one (canonical)
  - agents.goal_driven_planner.GoalDrivenPlannerAgent._execute_todo_goal_guided
  - agents.emergent_planner.EmergentPlannerAgent._execute_todo (legacy path)

Without these helpers, fixes that target the canonical loop tend to drift away
from the others. CLAUDE.md #21 documents the v12/v13 examples (set_caller,
TOOL_RESULT_TRUNCATION_LIMIT, rate_limited three-state semantics). This module
makes the shared behavior a single, importable contract.

三套独立 ReAct 循环若不共享 helper,后续修复极易"漏改"。本模块把"调用者归因 /
错误三态分类 / 输出截断"统一为纯函数,改一次三处生效。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel substring used to distinguish business rate-limiting from real tool
# failure. Tools returning this prefix are not failing — they are gating; they
# must NOT count toward ToolRouter's failure_threshold (which would suggest
# replacing the tool with an "alternative", but rate-limited tools have no
# alternative).
# 业务限流标记 —— 用于区分"工具坏了"和"工具被限流",后者不应进入 failure 桶。
RATE_LIMITED_MARKER = "SubAgent call limit reached"


def attribute_caller(tool: Any, agent_name: str) -> None:
    """Synchronously notify a tool of the agent calling it.

    Used by SubAgentTool / similar tools to attribute parent_agent in tracing.
    No-op when agent_name is empty or the tool lacks set_caller. Must be called
    immediately before `await tool.traced_execute(...)` with no await between —
    asyncio's single-threaded model then guarantees no Task interleaves before
    the tool's synchronous prologue captures the value.

    用于 SubAgentTool 等需要知道调用者身份的工具。空名或无 set_caller 时静默 no-op。
    调用时必须紧贴 await traced_execute,中间不能插入任何 await。
    """
    if not agent_name or not hasattr(tool, "set_caller"):
        return
    try:
        tool.set_caller(agent_name)
    except Exception:
        logger.debug(
            "[tool_call_helpers] set_caller failed for tool=%s",
            getattr(tool, "name", repr(tool)),
            exc_info=True,
        )


def classify_result(
    result: Any,
    exc: BaseException | None = None,
) -> tuple[bool, bool]:
    """Classify a tool's outcome into (is_error, is_rate_limited).

    Decision tree:
      - exc is not None        → (True, False)   hard failure
      - result startswith Error: with RATE_LIMITED_MARKER inside → (True, True) gating
      - result startswith Error:                                  → (True, False) soft failure
      - otherwise                                                 → (False, False)

    The three states map to ToolRouter's three accounting paths
    (record_rate_limited / record_failure / record_success). Without this
    classifier, tools that swallow exceptions and return Error: strings would be
    miscounted as success (defeating the failure threshold), and rate-limited
    returns would pollute the failure bucket (triggering misleading "use a
    different tool" hints).

    返回 (is_error, is_rate_limited)。is_rate_limited=True 蕴含 is_error=True。
    """
    if exc is not None:
        return True, False
    if isinstance(result, str) and result.startswith("Error:"):
        return True, RATE_LIMITED_MARKER in result
    return False, False


def truncate_for_llm(
    result: Any,
    limit: int,
    is_error: bool,
) -> tuple[Any, Any]:
    """Truncate a successful, oversized string result before it reaches the LLM.

    Returns (record_str, llm_str):
      - Errors                    → (result, result)   keep full text for debugging
      - Non-string                → (result, result)   defensive fallback
      - String within limit       → (result, result)
      - String over limit         → (truncated, truncated + marker)

    The marker tells the LLM there is more content available so it can choose to
    re-fetch or accept the truncation; without it, the LLM may silently treat
    the truncated payload as complete.

    返回 (写入 ToolCallRecord 的文本, 发给 LLM 的文本)。
    错误结果保留全文,成功结果截断 + 加 marker。
    """
    if is_error or not isinstance(result, str) or len(result) <= limit:
        return result, result
    truncated = result[:limit]
    marker = (
        f"\n\n[Tool output truncated at {limit} characters "
        f"to control context size; original length={len(result)}]"
    )
    return truncated, truncated + marker
