"""
Shared tool execution logic for native tool-calling loops.
原生工具调用动作执行循环共享的工具执行逻辑。

Extracts structured tool-call behavior shared by:
  - tool_calling.loop.ActionToolLoop.execute
  - agent_loop.loop.AgentLoop.run

将多个结构化工具调用路径的重复执行行为收敛为共享模块。

These helpers implement the Action/tool-result Observation transport. They do
not parse the classic textual ``Thought:/Action:/Observation:`` protocol.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import validators
from jsonschema.exceptions import SchemaError, ValidationError

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

_VALIDATOR_CACHE: dict[str, Any] = {}


def _format_validation_path(error: ValidationError) -> str:
    """Return a compact JSONPath-like location for a schema error."""
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def validate_tool_arguments(tool: BaseTool, arguments: dict[str, Any]) -> str:
    """Validate one argument object against the tool's declared JSON Schema.

    The returned string is empty on success.  Validation failures are data the
    model can correct on its next turn, so callers transport the message as a
    normal tool error instead of raising out of the agent loop.
    """
    try:
        schema = tool.parameters_schema
        cache_key = json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        validator = _VALIDATOR_CACHE.get(cache_key)
        if validator is None:
            validator_type = validators.validator_for(schema)
            validator_type.check_schema(schema)
            validator = validator_type(schema)
            _VALIDATOR_CACHE[cache_key] = validator
        errors = sorted(
            validator.iter_errors(arguments),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except SchemaError as exc:
        return f"tool schema is invalid: {exc.message}"
    except Exception as exc:
        return f"tool schema validation failed: {type(exc).__name__}: {exc}"
    if not errors:
        return ""
    first = errors[0]
    suffix = f" (and {len(errors) - 1} more error(s))" if len(errors) > 1 else ""
    return f"arguments do not match schema at {_format_validation_path(first)}: {first.message}{suffix}"


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
    if isinstance(result, str) and result.lstrip().lower().startswith("error:"):
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
    if not isinstance(result, str) or len(result) <= limit:
        return result, result
    if is_error:
        head_size = max(1, limit // 2)
        tail_size = max(0, limit - head_size)
        marker = (
            f"\n\n[Tool error truncated to head/tail at {limit} characters; "
            f"original length={len(result)}]\n\n"
        )
        truncated = result[:head_size]
        if tail_size:
            truncated += marker + result[-tail_size:]
        else:
            truncated += marker
        return truncated, truncated
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
    turn: int | None = None,
) -> list[dict[str, Any]]:
    """Execute tool calls sequentially, classify results, account to router.

    Returns a list of tool-message dicts (``{"role": "tool", ...}``) in the
    same order as *tool_calls*, preserving the OpenAI protocol alignment.
    *tool_calls_log* is appended in-place (same semantics as the original
    inline code — callers that rebind the list variable on each execute()
    call still work correctly).

    When *policy* is provided it overrides *truncation_limit* and controls
    error message formatting. When *policy* is None (default), the function
    uses *truncation_limit* directly.

    顺序执行工具调用、分类结果、记账到 ToolRouter。
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
        # tool_calls 中的 arguments 是 JSON 字符串，需要先解析并按工具 schema 校验。
        # 例如 get_user_location 的 "{}" 合法，而传入数组 "[]" 会在执行前直接报错。
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
        turn_payload = {"turn": turn} if turn is not None else {}
        if on_event is not None:
            on_event(
                "tool_started",
                {
                    "tool": fn_name,
                    "parameters": event_args,
                    "action_id": node_id,
                    "call_id": str(tc.id),
                    **turn_payload,
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
                        **turn_payload,
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
        argument_error = validate_tool_arguments(t, fn_args)
        if argument_error:
            res = f"Error: {argument_error}"
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

    tool_messages: list[dict[str, Any]] = []

    # Deliberately dispatch in provider order.  Tool calls in one assistant
    # message are not guaranteed to be independent (writes and control-transfer
    # tools in particular are order-sensitive), and every call must receive
    # exactly one matching result before the loop advances.
    for requested_call in tool_calls:
        tool_call, func_name, func_args, result, is_error, is_rate_limited = (
            await _exec_one(requested_call)
        )
        if is_rate_limited:
            tool_router.record_rate_limited(node_id, func_name)
        elif is_error:
            tool_router.record_failure(node_id, func_name)
        else:
            tool_router.record_success(node_id, func_name)

        # 审计记录与回填模型的文本分别生成：例如超长天气 JSON 可在日志保留摘要，
        # 给下一轮 LLM 的内容则按 effort 对应上限截断，避免一次工具结果挤满上下文。
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

        # 每个 assistant tool_call 必须有同 call_id 的 role="tool" 响应；这才是实际的
        # Observation 传输方式，项目不会拼接字面量 "Observation: ..." 再交给模型。
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result_with_marker,
        })

    return tool_messages
