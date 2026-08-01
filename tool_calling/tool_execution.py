"""
Shared tool execution logic for native tool-calling action loops.
原生工具调用动作执行循环共享的工具执行逻辑。

Extracts structured tool-call behavior shared across action-loop executors:
  - tool_calling.loop.ToolCallingLoop.execute
  - tool_calling.reasoning_aware_loop.ReasoningAwareToolCallingLoop.execute
  - agents.emergent_planner.EmergentPlannerAgent._execute_todo
  - agents.goal_driven_planner.GoalDrivenPlannerAgent._execute_todo_goal_guided

将多个结构化工具调用路径的重复执行行为收敛为共享模块。

These helpers implement the Action/tool-result Observation transport. They do
not parse the classic textual ``Thought:/Action:/Observation:`` protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from execution.models import ResolvedEffort, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)

RATE_LIMITED_MARKER = "SubAgent call limit reached"
RATE_LIMITED_RESULT_MARKERS = (
    RATE_LIMITED_MARKER,
    "429",
    "Too Many Requests",
    "rate-limited",
    "rate limited",
)


def set_tool_caller(tool: Any, agent_name: str) -> None:
    """Notify a tool which agent is calling it immediately before execution.

    Call this directly before ``await tool.traced_execute(...)`` without an
    intervening await so concurrent tasks cannot overwrite caller attribution.
    """
    if not agent_name or not hasattr(tool, "set_caller"):
        return
    try:
        tool.set_caller(agent_name)
    except Exception:
        logger.debug(
            "[tool_execution] set_caller failed for tool=%s",
            getattr(tool, "name", repr(tool)),
            exc_info=True,
        )


def classify_tool_result(
    result: Any,
    exc: BaseException | None = None,
) -> tuple[bool, bool]:
    """Classify a tool outcome as ``(is_error, is_rate_limited)``.

    Exceptions and ``Error:`` results are failures; recognized rate-limit
    markers use the router's rate-limited bucket instead of its failure bucket.
    """
    if exc is not None:
        return True, False
    if isinstance(result, str) and result.lstrip().lower().startswith("error"):
        return True, any(
            marker.lower() in result.lower()
            for marker in RATE_LIMITED_RESULT_MARKERS
        )
    return False, False


def truncate_tool_result_for_llm(
    result: Any,
    limit: int,
    is_error: bool,
) -> tuple[Any, Any]:
    """Return the recorded and LLM-facing forms of one tool result."""
    if is_error or not isinstance(result, str) or len(result) <= limit:
        return result, result
    truncated = result[:limit]
    marker = (
        f"\n\n[Tool output truncated at {limit} characters "
        f"to control context size; original length={len(result)}]"
    )
    return truncated, truncated + marker


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
        return ToolExecutionPolicy()

    @staticmethod
    def for_effort(effort: ResolvedEffort, base: int = 2000) -> ToolExecutionPolicy:
        if effort == ResolvedEffort.LOW:
            return ToolExecutionPolicy(truncation_limit=max(500, base // 2))
        elif effort == ResolvedEffort.HIGH:
            return ToolExecutionPolicy(truncation_limit=base * 2)
        return ToolExecutionPolicy(truncation_limit=base)


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
    guardrail: Any | None = None,
    on_event: Callable[[str, Any], None] | None = None,
) -> list[dict[str, Any]]:
    """Execute tool calls concurrently, classify results, account to router.

    Returns a list of tool-message dicts (``{"role": "tool", ...}``) in the
    same order as *tool_calls*, preserving the OpenAI protocol alignment.
    *tool_calls_log* is appended in-place (same semantics as the original
    inline code — callers that rebind the list variable on each execute()
    call still work correctly).

    When *policy* is provided it overrides *truncation_limit* and controls
    error message formatting. When *policy* is None (default), the function
    uses *truncation_limit* directly.

    并行执行工具调用、分类结果、记账到 ToolRouter。
    返回与 tool_calls 同序的 tool-message 列表。tool_calls_log 原地追加。
    """
    effective_policy = policy or ToolExecutionPolicy(truncation_limit=truncation_limit)
    effective_truncation = effective_policy.truncation_limit
    prefix = log_prefix or "ToolCalling"

    _GAction = None
    if guardrail is not None:
        try:
            from guardrails.models import GuardrailAction as _GAction
        except Exception as exc:
            raise RuntimeError("Guardrail is configured but unavailable") from exc

    async def _exec_one(tc: Any) -> tuple[Any, str, dict, str, bool, bool]:
        fn_name = tc.function.name
        _parser = parse_args or json.loads
        argument_error = ""
        try:
            fn_args = _parser(tc.function.arguments)
            if not isinstance(fn_args, dict):
                argument_error = "tool arguments must be a JSON object"
                fn_args = {}
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            argument_error = f"invalid tool arguments: {exc}"
            fn_args = {}
        logger.info("[%s] Tool call: %s(%s)", prefix, fn_name, fn_args)
        event_args = BaseTool._sanitize_params(fn_args)
        if on_event is not None:
            on_event(
                "tool_started",
                {
                    "tool": fn_name,
                    "parameters": event_args,
                    "action_id": node_id,
                    "call_id": str(tc.id),
                },
            )

        def finish(res: str, is_err: bool, rate_limited: bool):
            if on_event is not None:
                on_event(
                    "tool_completed",
                    {
                        "tool": fn_name,
                        "success": not is_err,
                        "result": str(res)[:1000],
                        "action_id": node_id,
                        "call_id": str(tc.id),
                    },
                )
            return tc, fn_name, fn_args, res, is_err, rate_limited

        if argument_error:
            res = f"Error: {argument_error}"
            is_err, rl = classify_tool_result(res, None)
            return finish(res, is_err, rl)

        t = tools.get(fn_name)
        if t is None:
            res = f"Error: Unknown tool '{fn_name}'"
            is_err, rl = classify_tool_result(res, None)
            return finish(res, is_err, rl)
        # Tool-input guardrail: block dangerous calls and gated writes.
        # BEFORE execution. The tool-execution layer resolves CONFIRM internally.
        if guardrail is not None:
            try:
                decision = await guardrail.check_tool_input(fn_name, fn_args)
                if decision.action == _GAction.BLOCK:
                    res = f"Error: [GUARDRAIL BLOCKED] {decision.reason}"
                    is_err, rl = classify_tool_result(res, None)
                    return finish(res, is_err, rl)
            except Exception as exc:
                logger.error("[%s] tool-input guardrail failed", prefix, exc_info=True)
                res = f"Error: guardrail input check failed: {exc}"
                is_err, rl = classify_tool_result(res, None)
                return finish(res, is_err, rl)
        set_tool_caller(t, agent_name)
        try:
            res = await t.traced_execute(**fn_args)
            is_err, rl = classify_tool_result(res, None)
            # Tool-output guardrail: neutralize injection in untrusted output.
            if guardrail is not None and not is_err:
                try:
                    out = guardrail.scan_tool_output(fn_name, res)
                    if out.transformed_text is not None:
                        res = out.transformed_text
                except Exception as exc:
                    logger.error("[%s] tool-output guardrail failed", prefix, exc_info=True)
                    res = f"Error: guardrail output check failed: {exc}"
                    is_err, rl = classify_tool_result(res, None)
            return finish(res, is_err, rl)
        except Exception as exc:
            res = f"Error: Tool execution error: {exc}"
            is_err, rl = classify_tool_result(None, exc)
            return finish(res, is_err, rl)

    executions = await asyncio.gather(*(_exec_one(tc) for tc in tool_calls))

    tool_messages: list[dict[str, Any]] = []

    for tool_call, func_name, func_args, result, is_error, is_rate_limited in executions:
        if is_rate_limited:
            tool_router.record_rate_limited(node_id, func_name)
        elif is_error:
            tool_router.record_failure(node_id, func_name)
        else:
            tool_router.record_success(node_id, func_name)

        record_result, llm_result = truncate_tool_result_for_llm(
            result,
            effective_truncation,
            is_error,
        )

        tool_calls_log.append(ToolCallRecord(
            tool_name=func_name,
            parameters=BaseTool._sanitize_params(func_args),
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
