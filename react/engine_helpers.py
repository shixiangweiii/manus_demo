"""
Shared tool execution logic for all ReAct-style loops.
所有 ReAct 风格循环共享的工具执行逻辑。

Batch 4.1 DRY refactor: extracts ~70 lines duplicated across four sites:
  - react.engine.ReActEngine.execute
  - react.reasoning_engine.ReasoningEngine.execute
  - agents.emergent_planner.EmergentPlannerAgent._execute_todo
  - agents.goal_driven_planner.GoalDrivenPlannerAgent._execute_todo_goal_guided

Batch 4.1 DRY 重构：从四处重复代码中提取的共享模块。
"""

from __future__ import annotations

import asyncio
import config as config_module
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from react.tool_call_helpers import (
    attribute_caller,
    classify_result,
    truncate_for_llm,
)
from schema import ReasoningEffort, ToolCallRecord
from tools.router import ToolRouter

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionPolicy:
    """Configurable behavior for tool result processing.
    工具结果处理的可配置策略。

    Controls truncation limits, error message formatting, and guidance
    injection — previously all hardcoded in the four duplicated blocks.
    """
    truncation_limit: int = 2000
    error_prefix: str = "[TOOL ERROR]"
    include_alternatives_hint: bool = True
    error_retry_guidance: str = (
        "IMPORTANT: The tool returned an error. Please analyze "
        "the error and decide whether to retry with different "
        "parameters or report the failure."
    )

    @staticmethod
    def default() -> ToolExecutionPolicy:
        return ToolExecutionPolicy(truncation_limit=config_module.TOOL_RESULT_TRUNCATION_LIMIT)

    @staticmethod
    def for_effort(effort: ReasoningEffort) -> ToolExecutionPolicy:
        base = config_module.TOOL_RESULT_TRUNCATION_LIMIT
        if effort == ReasoningEffort.LOW:
            return ToolExecutionPolicy(truncation_limit=max(500, base // 2))
        elif effort == ReasoningEffort.HIGH:
            return ToolExecutionPolicy(truncation_limit=base * 2)
        return ToolExecutionPolicy.default()


async def execute_tool_calls(
    tool_calls: list[Any],
    tools: dict[str, Any],
    tool_router: ToolRouter,
    *,
    node_id: str,
    agent_name: str,
    truncation_limit: int,
    tool_calls_log: list[ToolCallRecord],
    log_prefix: str = "",
    policy: ToolExecutionPolicy | None = None,
    parse_args: Callable[[str], dict] | None = None,
) -> list[dict[str, Any]]:
    """Execute tool calls concurrently, classify results, account to router.

    Returns a list of tool-message dicts (``{"role": "tool", ...}``) in the
    same order as *tool_calls*, preserving the OpenAI protocol alignment.
    *tool_calls_log* is appended in-place (same semantics as the original
    inline code — callers that rebind the list variable on each execute()
    call still work correctly).

    When *policy* is provided it overrides *truncation_limit* and controls
    error message formatting. When *policy* is None (default), the function
    uses *truncation_limit* directly — backward compatible with Batch 4.1
    call sites.

    并行执行工具调用、分类结果、记账到 ToolRouter。
    返回与 tool_calls 同序的 tool-message 列表。tool_calls_log 原地追加。
    """
    effective_policy = policy or ToolExecutionPolicy(truncation_limit=truncation_limit)
    effective_truncation = effective_policy.truncation_limit
    prefix = log_prefix or "ReAct"

    async def _exec_one(tc: Any) -> tuple[Any, str, dict, str, bool, bool]:
        fn_name = tc.function.name
        _parser = parse_args or json.loads
        try:
            fn_args = _parser(tc.function.arguments)
            if not isinstance(fn_args, dict):
                fn_args = {}
        except (json.JSONDecodeError, TypeError, ValueError):
            fn_args = {}
        logger.info("[%s] Tool call: %s(%s)", prefix, fn_name, fn_args)
        t = tools.get(fn_name)
        if t is None:
            res = f"Error: Unknown tool '{fn_name}'"
            is_err, rl = classify_result(res, None)
            return tc, fn_name, fn_args, res, is_err, rl
        attribute_caller(t, agent_name)
        try:
            res = await t.traced_execute(**fn_args)
            is_err, rl = classify_result(res, None)
            return tc, fn_name, fn_args, res, is_err, rl
        except Exception as exc:
            res = f"Error: Tool execution error: {exc}"
            is_err, rl = classify_result(None, exc)
            return tc, fn_name, fn_args, res, is_err, rl

    executions = await asyncio.gather(*(_exec_one(tc) for tc in tool_calls))

    tool_messages: list[dict[str, Any]] = []

    for tool_call, func_name, func_args, result, is_error, is_rate_limited in executions:
        if is_rate_limited:
            tool_router.record_rate_limited(node_id, func_name)
        elif is_error:
            tool_router.record_failure(node_id, func_name)
        else:
            tool_router.record_success(node_id, func_name)

        record_result, llm_result = truncate_for_llm(result, effective_truncation, is_error)

        tool_calls_log.append(ToolCallRecord(
            tool_name=func_name,
            parameters=func_args,
            result=record_result,
        ))

        if is_error:
            parts = [f"{effective_policy.error_prefix} {llm_result}"]
            if effective_policy.include_alternatives_hint:
                parts.append(f"\n\n{effective_policy.error_retry_guidance}")
            result_with_marker = "".join(parts)
        else:
            result_with_marker = llm_result

        tool_messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result_with_marker,
        })

    return tool_messages
