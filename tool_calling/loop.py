"""One action-scoped native tool-calling loop.

``ActionToolLoop`` executes exactly one action for Sequential and DAG engines.
It owns no task-level planning state.  The separate :mod:`agent_loop` package
implements the persistent task-level loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from context.manager import ContextManager
from core.models import EngineStopReason
from execution.models import (
    ActionLoopStats,
    ResolvedEffort,
    StepResult,
    ToolCallRecord,
)
from llm.client import (
    LLMClient,
    _extract_reasoning_content_from_tags,
    _strip_reasoning_from_content,
)
from tool_calling.tool_execution import ToolExecutionPolicy, execute_tool_calls
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedModelResponse:
    """Provider-neutral fields consumed by either native loop."""

    assistant_message: dict[str, Any]
    tool_calls: list[Any]
    reasoning: str
    output: str


def response_reasoning(response: Any) -> str:
    """Return provider reasoning metadata without changing assistant content."""
    reasoning = getattr(response, "reasoning_content", None) or ""
    if reasoning:
        return reasoning
    return _extract_reasoning_content_from_tags(getattr(response, "content", None))


def response_output(response: Any, reasoning: str) -> str:
    """Return user-facing text while transcript serialization stays exact."""
    return _strip_reasoning_from_content(
        getattr(response, "content", None) or "",
        reasoning,
    )


def serialize_assistant_message(response: Any) -> dict[str, Any]:
    """Serialize the provider assistant message without rewriting its content.

    OpenAI response models expose ``model_dump``; lightweight test doubles do
    not, so the fallback copies the same protocol fields explicitly.
    ``reasoning_content`` is retained as internal transcript metadata and is
    stripped by ``LLMClient`` before the next provider request.
    """
    if hasattr(response, "model_dump"):
        dumped = response.model_dump(mode="json", exclude_none=True)
        raw = dict(dumped) if isinstance(dumped, dict) else {}
        # Keep exact values for fields accepted again as an assistant input;
        # omit response-only metadata such as annotations that providers may
        # reject when the transcript is sent back.
        message = {
            key: raw[key]
            for key in (
                "role",
                "content",
                "name",
                "refusal",
                "audio",
                "function_call",
                "tool_calls",
            )
            if key in raw
        }
    else:
        message = {
            "role": getattr(response, "role", "assistant") or "assistant",
            "content": getattr(response, "content", None),
        }
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": getattr(call, "type", "function") or "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ]

    message["role"] = "assistant"
    message.setdefault("content", getattr(response, "content", None))
    reasoning = getattr(response, "reasoning_content", None)
    if reasoning:
        message["reasoning_content"] = reasoning
    return message


def normalize_model_response(response: Any) -> NormalizedModelResponse:
    """Validate and normalize one structured model response.

    A malformed provider object is a model protocol failure, not an exception
    that should escape an action loop as an engine failure.
    """
    if response is None:
        raise ValueError("response is null")
    try:
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        seen_call_ids: set[str] = set()
        for call in tool_calls:
            function = getattr(call, "function", None)
            call_id = getattr(call, "id", None)
            name = getattr(function, "name", None) if function is not None else None
            arguments = (
                getattr(function, "arguments", None)
                if function is not None
                else None
            )
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(name, str)
                or not name
                or not isinstance(arguments, str)
            ):
                raise ValueError("malformed tool call")
            if call_id in seen_call_ids:
                raise ValueError(f"duplicate tool call id: {call_id}")
            seen_call_ids.add(call_id)

        reasoning = response_reasoning(response)
        output = response_output(response, reasoning)
        if not isinstance(reasoning, str) or not isinstance(output, str):
            raise ValueError("response content must be text")
        assistant_message = serialize_assistant_message(response)
    except Exception as exc:
        raise ValueError(str(exc) or "malformed response") from exc

    return NormalizedModelResponse(
        assistant_message=assistant_message,
        tool_calls=tool_calls,
        reasoning=reasoning,
        output=output,
    )


def _call_records(llm_client: Any) -> list[Any]:
    getter = getattr(llm_client, "get_call_records", None)
    return list(getter()) if callable(getter) else []


class ActionToolLoop:
    """Execute one action through a structured assistant/tool-result cycle."""

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool] | dict[str, BaseTool],
        max_iterations: int | None = None,
        tool_router: ToolRouter | None = None,
        context_manager: ContextManager | None = None,
        agent_name: str = "",
        guardrail: Any | None = None,
        temperature: float = 0.5,
        result_truncation_limit: int = 2000,
        on_event: Callable[[str, Any], None] | None = None,
        max_reasoning_tokens: int | None = None,
        max_reasoning_rounds: int = 5,
    ) -> None:
        self.llm_client = llm_client
        self.context_manager = context_manager
        self.max_iterations = max_iterations or 10
        self.temperature = temperature
        self.result_truncation_limit = result_truncation_limit
        self.agent_name = agent_name
        self.guardrail = guardrail
        self._on_event = on_event
        self.max_reasoning_tokens = max_reasoning_tokens or 10_000
        self.max_reasoning_rounds = max_reasoning_rounds

        self.tools = dict(tools) if isinstance(tools, dict) else {t.name: t for t in tools}
        self._tools_full = dict(self.tools)
        self.tool_schemas = [tool.to_openai_tool() for tool in self.tools.values()]
        self.tool_router = tool_router or ToolRouter(available_tools=list(self.tools))
        self._current_log: list[ToolCallRecord] = []
        self._current_messages: list[dict[str, Any]] = []

    def _apply_effort(self, effort: ResolvedEffort) -> tuple[float, int, int]:
        """Return temperature, turn cap, and reasoning-token cap."""
        if effort == ResolvedEffort.LOW:
            return (
                0.3,
                max(1, self.max_iterations // 2),
                min(self.max_reasoning_tokens, 2_000),
            )
        if effort == ResolvedEffort.HIGH:
            return 0.7, self.max_iterations, self.max_reasoning_tokens
        return self.temperature, self.max_iterations, self.max_reasoning_tokens

    async def execute(
        self,
        prompt: str,
        context: str = "",
        node_id: str | None = None,
        system_hint: str = "",
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,
        effort: ResolvedEffort | None = None,
    ) -> StepResult:
        """Execute one action with one initial user message and no synthetic turns."""
        step_id = node_id or "default"
        effective_effort = effort or ResolvedEffort.MEDIUM
        temperature, max_turns, reasoning_budget = self._apply_effort(effective_effort)
        policy = ToolExecutionPolicy.for_effort(
            effective_effort,
            self.result_truncation_limit,
        )

        user_content = prompt
        if context:
            user_content += f"\n\nContext from previous steps:\n{context}"
        messages: list[dict[str, Any]] = []
        if system_hint:
            messages.append({"role": "system", "content": system_hint})
        messages.append({"role": "user", "content": user_content})
        self._current_messages = messages

        tool_calls_log: list[ToolCallRecord] = []
        self._current_log = tool_calls_log
        stats = ActionLoopStats()
        consecutive_reasoning_rounds = 0
        caller_tag = f"{self.agent_name or 'ActionToolLoop'}:{step_id}:{id(messages):x}"

        logger.info("[ActionToolLoop] Starting action %s: %s", step_id, prompt[:100])
        for turn in range(1, max_turns + 1):
            turn_identity = {"action_id": str(step_id), "turn": turn}
            turn_completion: dict[str, Any] = {
                "success": False,
                "outcome": EngineStopReason.ENGINE_ERROR.value,
            }
            self._emit("action_turn_started", turn_identity)
            try:
                request_messages = messages
                try:
                    if self.context_manager is not None:
                        prepared = await self.context_manager.prepare_messages(
                            messages,
                            self.llm_client,
                            caller_tag=caller_tag,
                        )
                        request_messages = prepared.messages
                        stats.llm_calls += prepared.llm_calls
                        stats.reasoning_tokens += prepared.reasoning_tokens
                        if stats.reasoning_tokens > reasoning_budget:
                            turn_completion = {
                                "success": False,
                                "outcome": "reasoning_budget",
                                "stop_reason": EngineStopReason.ENGINE_ERROR.value,
                            }
                            return StepResult(
                                step_id=step_id,
                                success=False,
                                output=(
                                    f"Reasoning budget exceeded ({stats.reasoning_tokens} > "
                                    f"{reasoning_budget} tokens) during context preparation."
                                ),
                                tool_calls_log=tool_calls_log,
                                iterations_completed=turn,
                                stats=stats,
                                failure_reason=EngineStopReason.ENGINE_ERROR,
                            )
                    records_before = len(_call_records(self.llm_client))
                    # Count the model invocation independently of optional token
                    # records, and include failed calls in the observed work.
                    stats.llm_calls += 1
                    response = await self.llm_client.chat_with_tools(
                        request_messages,
                        tools=self.tool_schemas,
                        temperature=temperature,
                        caller_tag=caller_tag,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("[ActionToolLoop] Model call failed", exc_info=True)
                    turn_completion = {
                        "success": False,
                        "outcome": EngineStopReason.MODEL_ERROR.value,
                        "stop_reason": EngineStopReason.MODEL_ERROR.value,
                    }
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=f"LLM call failed: {exc}",
                        tool_calls_log=tool_calls_log,
                        iterations_completed=turn,
                        stats=stats,
                        failure_reason=EngineStopReason.MODEL_ERROR,
                    )

                new_records = _call_records(self.llm_client)[records_before:]
                recorded_reasoning_tokens = sum(
                    int(getattr(record, "reasoning_tokens", 0) or 0)
                    for record in new_records
                    if getattr(record, "caller_tag", "") == caller_tag
                )
                stats.reasoning_tokens += recorded_reasoning_tokens
                try:
                    normalized = normalize_model_response(response)
                except ValueError as exc:
                    turn_completion = {
                        "success": False,
                        "outcome": EngineStopReason.INVALID_RESPONSE.value,
                        "stop_reason": EngineStopReason.INVALID_RESPONSE.value,
                    }
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=f"Model returned an invalid response: {exc}",
                        tool_calls_log=tool_calls_log,
                        iterations_completed=turn,
                        stats=stats,
                        failure_reason=EngineStopReason.INVALID_RESPONSE,
                    )

                messages.append(normalized.assistant_message)
                self._current_messages = messages
                tool_calls = normalized.tool_calls
                reasoning = normalized.reasoning
                output = normalized.output
                if reasoning and recorded_reasoning_tokens <= 0:
                    stats.reasoning_tokens += ContextManager.estimate_tokens(reasoning)

                if stats.reasoning_tokens > reasoning_budget:
                    turn_completion = {
                        "success": False,
                        "outcome": "reasoning_budget",
                        "stop_reason": EngineStopReason.ENGINE_ERROR.value,
                    }
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=(
                            f"Reasoning budget exceeded ({stats.reasoning_tokens} > "
                            f"{reasoning_budget} tokens) before accepting the model response."
                        ),
                        tool_calls_log=tool_calls_log,
                        iterations_completed=turn,
                        stats=stats,
                        failure_reason=EngineStopReason.ENGINE_ERROR,
                    )

                if not tool_calls:
                    if output.strip():
                        if on_iteration:
                            on_iteration(turn, tool_calls_log)
                        turn_completion = {
                            "success": True,
                            "outcome": "final",
                            "final": True,
                        }
                        return StepResult(
                            step_id=step_id,
                            success=True,
                            output=output,
                            tool_calls_log=tool_calls_log,
                            iterations_completed=turn,
                            stats=stats,
                        )
                    if not reasoning:
                        turn_completion = {
                            "success": False,
                            "outcome": EngineStopReason.INVALID_RESPONSE.value,
                            "stop_reason": EngineStopReason.INVALID_RESPONSE.value,
                        }
                        return StepResult(
                            step_id=step_id,
                            success=False,
                            output="Model returned an empty response.",
                            tool_calls_log=tool_calls_log,
                            iterations_completed=turn,
                            stats=stats,
                            failure_reason=EngineStopReason.INVALID_RESPONSE,
                        )
                    consecutive_reasoning_rounds += 1
                    if consecutive_reasoning_rounds > self.max_reasoning_rounds:
                        turn_completion = {
                            "success": False,
                            "outcome": "reasoning_limit",
                            "stop_reason": EngineStopReason.ENGINE_ERROR.value,
                        }
                        return StepResult(
                            step_id=step_id,
                            success=False,
                            output=(
                                "Reasoning limit exceeded before the action produced "
                                "a tool call or final answer."
                            ),
                            tool_calls_log=tool_calls_log,
                            iterations_completed=turn,
                            stats=stats,
                            failure_reason=EngineStopReason.ENGINE_ERROR,
                        )
                    turn_completion = {
                        "success": True,
                        "outcome": "reasoning_only",
                        "reasoning_only": True,
                    }
                    continue

                consecutive_reasoning_rounds = 0
                tool_messages = await execute_tool_calls(
                    tool_calls,
                    self.tools,
                    self.tool_router,
                    node_id=str(step_id),
                    agent_name=self.agent_name,
                    truncation_limit=policy.truncation_limit,
                    tool_calls_log=tool_calls_log,
                    log_prefix="ActionToolLoop",
                    policy=policy,
                    guardrail=self.guardrail,
                    on_event=self._on_event,
                    turn=turn,
                )
                messages.extend(tool_messages)
                self._current_messages = messages
                stats.tool_calls = len(tool_calls_log)

                if on_iteration:
                    on_iteration(turn, tool_calls_log)
                turn_completion = {
                    "success": True,
                    "outcome": "tool_calls",
                    "tool_calls": len(tool_calls),
                    "final": False,
                }
            except asyncio.CancelledError:
                turn_completion = {
                    "success": False,
                    "outcome": "cancelled",
                }
                raise
            finally:
                self._emit(
                    "action_turn_completed",
                    {**turn_identity, **turn_completion},
                )

        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Task did not complete within {max_turns} iterations.",
            tool_calls_log=tool_calls_log,
            iterations_completed=max_turns,
            stats=stats,
            failure_reason=EngineStopReason.MAX_TURNS,
        )

    def get_node_summary(self, node_id: str) -> dict[str, Any]:
        return self.tool_router.get_node_summary(str(node_id))

    def _emit(self, event: str, data: Any = None) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug(
                "[ActionToolLoop] Event callback failed: %s",
                event,
                exc_info=True,
            )

    def set_allowed_tools(self, tool_names: list[str] | None) -> None:
        if tool_names:
            allowed = set(tool_names)
            if "activate_skill" in self._tools_full:
                allowed.add("activate_skill")
            self.tools = {
                name: tool
                for name, tool in self._tools_full.items()
                if name in allowed
            }
        else:
            self.tools = dict(self._tools_full)
        self.tool_schemas = [tool.to_openai_tool() for tool in self.tools.values()]
