"""Persistent task-level agent loop."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4

from context.manager import ContextManager
from core.models import Effort, EngineStats, EngineStopReason
from execution.models import ResolvedEffort, ToolCallRecord, resolve_effort
from llm.client import LLMClient
from tool_calling.loop import normalize_model_response
from tool_calling.tool_execution import ToolExecutionPolicy, execute_tool_calls
from tools.base import BaseTool
from tools.router import ToolRouter
from tools.todo_write import TodoWriteTool

from agent_loop.models import AgentLoopResult

logger = logging.getLogger(__name__)


class AgentLoop:
    """Own one task transcript and let the model plan through native tools.

    The canonical transcript contains at most one system message and exactly
    one user message.  Later turns append only exact assistant messages and one
    matching ``role=tool`` result for every tool call.  In particular, the loop
    never fabricates ``Continue`` user turns.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool] | dict[str, BaseTool],
        max_turns: int = 30,
        timeout_seconds: float = 600.0,
        context_manager: ContextManager | None = None,
        agent_name: str = "AgentLoop",
        guardrail: Any | None = None,
        temperature: float = 0.5,
        result_truncation_limit: int = 2000,
        on_event: Callable[[str, Any], None] | None = None,
        max_total_tokens: int | None = None,
        max_reasoning_tokens: int = 10_000,
    ) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be greater than zero")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_total_tokens is not None and max_total_tokens <= 0:
            raise ValueError("max_total_tokens must be greater than zero")
        if max_reasoning_tokens <= 0:
            raise ValueError("max_reasoning_tokens must be greater than zero")

        self.llm_client = llm_client
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.context_manager = context_manager
        self.agent_name = agent_name
        self.guardrail = guardrail
        self.temperature = temperature
        self.result_truncation_limit = result_truncation_limit
        self._on_event = on_event
        self.max_total_tokens = max_total_tokens
        self.max_reasoning_tokens = max_reasoning_tokens

        supplied = dict(tools) if isinstance(tools, dict) else {tool.name: tool for tool in tools}
        # The task engine always owns this state.  A caller-supplied tool with
        # the same name must not smuggle state across AgentLoop instances.
        self.todo_write = TodoWriteTool(on_event=self._emit)
        supplied[self.todo_write.name] = self.todo_write
        self.tools = supplied
        self._tools_full = dict(supplied)
        self._allowed_tool_names: list[str] | None = None
        self.tool_schemas = [tool.to_openai_tool() for tool in self.tools.values()]
        self.tool_router = ToolRouter(available_tools=list(self.tools))

        self._history: list[dict[str, Any]] = []
        self._current_log: list[ToolCallRecord] = []
        self._turns = 0
        self._stats = EngineStats()
        self._caller_tag = f"{self.agent_name}:idle:{uuid4().hex}"
        self._untracked_tokens_used = 0

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return a defensive copy of the exact canonical transcript."""
        return deepcopy(self._history)

    @property
    def todos(self) -> list[dict[str, str]]:
        return self.todo_write.snapshot

    @property
    def caller_tag(self) -> str:
        """Stable per-run tag for concurrency-safe shared-client accounting."""
        return self._caller_tag

    @property
    def turns(self) -> int:
        """Number of model calls consumed so far by the current run."""
        return self._turns

    @property
    def tokens_used(self) -> int:
        """Total recorded tokens attributed to the current run."""
        return self._tokens_used(self._caller_tag)

    @property
    def stats_snapshot(self) -> EngineStats:
        """Return work observed so far, including an interrupted tool round."""
        stats = self._stats.model_copy(deep=True)
        stats.tool_calls = len(self._current_log)
        stats.subagent_calls = sum(
            call.tool_name == "subagent" for call in self._current_log
        )
        return stats

    async def run(
        self,
        task: str,
        context: str = "",
        system_prompt: str = "",
        effort: Effort = Effort.MEDIUM,
    ) -> AgentLoopResult:
        """Run one complete task until a typed terminal condition is reached."""
        task = task.strip()
        if not task:
            return AgentLoopResult(
                success=False,
                output="Task must not be empty.",
                stop_reason=EngineStopReason.INVALID_RESPONSE,
            )

        initial_user = task
        if context:
            initial_user += f"\n\nContext:\n{context}"
        self._history = []
        if system_prompt:
            self._history.append({"role": "system", "content": system_prompt})
        self._history.append({"role": "user", "content": initial_user})
        self._current_log = []
        self._turns = 0
        self._stats = EngineStats()
        self._untracked_tokens_used = 0
        for tool in self._tools_full.values():
            if tool.name == self.todo_write.name:
                continue
            reset = getattr(tool, "reset_task_state", None)
            if callable(reset):
                reset()
        # A reused AgentLoop starts a new task with empty task-local TODO state.
        self.todo_write = TodoWriteTool(on_event=self._emit)
        self._tools_full[self.todo_write.name] = self.todo_write
        self._allowed_tool_names = None
        self._apply_tool_filter()
        self.tool_router.reset_node("agent-loop")

        resolved_effort = resolve_effort(effort)
        temperature, turn_limit = self._effort_policy(resolved_effort)
        policy = ToolExecutionPolicy.for_effort(
            resolved_effort,
            self.result_truncation_limit,
        )
        # A random per-run tag remains stable for every task turn and optional
        # context-summary call.  Shared LLM clients can then attribute parallel
        # child loops without relying on a racy global record slice.
        self._caller_tag = f"{self.agent_name}:{uuid4().hex}"
        caller_tag = self._caller_tag
        self._emit("agent_loop_started", {"task": task, "max_turns": turn_limit})

        try:
            async with asyncio.timeout(self.timeout_seconds):
                result = await self._run_turns(
                    turn_limit=turn_limit,
                    temperature=temperature,
                    caller_tag=caller_tag,
                    policy=policy,
                )
        except TimeoutError:
            result = self._result(
                output=f"Task timed out after {self.timeout_seconds:g} seconds.",
                success=False,
                stop_reason=EngineStopReason.TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[AgentLoop] Engine failure", exc_info=True)
            result = self._result(
                output=f"Agent loop failed: {type(exc).__name__}: {exc}",
                success=False,
                stop_reason=EngineStopReason.ENGINE_ERROR,
            )

        self._emit("agent_loop_completed", result.model_dump())
        return result

    async def _run_turns(
        self,
        *,
        turn_limit: int,
        temperature: float,
        caller_tag: str,
        policy: ToolExecutionPolicy,
    ) -> AgentLoopResult:
        last_text = ""
        model_calls = 0
        while model_calls < turn_limit:
            budget_failure = self._token_budget_failure()
            if budget_failure is not None:
                return budget_failure
            reasoning_failure = self._reasoning_budget_failure()
            if reasoning_failure is not None:
                return reasoning_failure

            request_messages = self._history
            if self.context_manager is not None:
                prepared = await self.context_manager.prepare_messages(
                    self._history,
                    self.llm_client,
                    caller_tag=caller_tag,
                )
                request_messages = prepared.messages
                self._stats.llm_calls += prepared.llm_calls
                self._stats.reasoning_tokens += prepared.reasoning_tokens
                self._untracked_tokens_used += prepared.untracked_tokens
                model_calls += prepared.llm_calls
                self._turns = model_calls

            # Summarization is part of this loop's budget and may consume the
            # remainder before the task-level model turn starts.
            budget_failure = self._token_budget_failure()
            if budget_failure is not None:
                return budget_failure
            reasoning_failure = self._reasoning_budget_failure()
            if reasoning_failure is not None:
                return reasoning_failure
            if model_calls >= turn_limit:
                output = last_text or (
                    f"Task did not complete within {turn_limit} model calls."
                )
                return self._result(
                    output=output,
                    success=False,
                    stop_reason=EngineStopReason.MAX_TURNS,
                )

            turn = model_calls + 1
            self._turns = turn
            self._emit("agent_turn_started", {"turn": turn})
            records_before = self._record_count()
            remaining_tokens = self._remaining_tokens()
            prompt_tokens_estimate = ContextManager.estimate_messages_tokens(
                request_messages
            )
            try:
                call_kwargs: dict[str, Any] = {}
                if remaining_tokens is not None:
                    completion_budget = remaining_tokens - prompt_tokens_estimate
                    if completion_budget <= 0:
                        return self._token_budget_result()
                    call_kwargs["max_tokens"] = min(4096, completion_budget)
                # Count the invocation directly.  Usage recording is optional
                # and must not decide whether an LLM call happened.
                self._stats.llm_calls += 1
                model_calls += 1
                response = await self.llm_client.chat_with_tools(
                    request_messages,
                    tools=self.tool_schemas,
                    temperature=temperature,
                    caller_tag=caller_tag,
                    **call_kwargs,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[AgentLoop] Model call failed: %s", exc)
                return self._result(
                    output=f"Model call failed: {type(exc).__name__}: {exc}",
                    success=False,
                    stop_reason=EngineStopReason.MODEL_ERROR,
                )

            recorded_reasoning_tokens = self._reasoning_tokens_since(
                records_before,
                caller_tag,
            )
            self._stats.reasoning_tokens += recorded_reasoning_tokens

            # Append the provider message before inspecting its terminal shape.
            try:
                normalized = normalize_model_response(response)
            except ValueError as exc:
                self._account_untracked_model_call(
                    records_before,
                    request_messages,
                    response,
                )
                return self._result(
                    output=f"Model returned an invalid response: {exc}",
                    success=False,
                    stop_reason=EngineStopReason.INVALID_RESPONSE,
                )
            self._account_untracked_model_call(
                records_before,
                request_messages,
                response,
                assistant_message=normalized.assistant_message,
            )
            self._history.append(normalized.assistant_message)
            if normalized.reasoning and recorded_reasoning_tokens <= 0:
                self._stats.reasoning_tokens += ContextManager.estimate_tokens(
                    normalized.reasoning
                )
            if (
                self.max_total_tokens is not None
                and self.tokens_used > self.max_total_tokens
            ):
                return self._token_budget_result()
            reasoning_failure = self._reasoning_budget_failure()
            if reasoning_failure is not None:
                return reasoning_failure
            tool_calls = normalized.tool_calls
            reasoning = normalized.reasoning
            text = normalized.output
            if text:
                last_text = text

            if not tool_calls:
                if text.strip():
                    self._emit("agent_turn_completed", {"turn": turn, "final": True})
                    return self._result(
                        output=text,
                        success=True,
                        stop_reason=EngineStopReason.COMPLETED,
                    )
                if not reasoning:
                    return self._result(
                        output="Model returned an empty response.",
                        success=False,
                        stop_reason=EngineStopReason.INVALID_RESPONSE,
                    )
                # A provider reasoning-only assistant message is a valid turn.
                # The next request uses that exact history without a fake user
                # instruction; max_turns remains the convergence guard.
                self._emit("agent_turn_completed", {"turn": turn, "reasoning_only": True})
                continue

            # Text accompanying tool calls is intentionally non-terminal.  The
            # exact assistant message remains in history, then each requested
            # tool receives one matching result in provider order.
            tool_messages = await execute_tool_calls(
                tool_calls,
                self.tools,
                self.tool_router,
                node_id="agent-loop",
                agent_name=self.agent_name,
                truncation_limit=policy.truncation_limit,
                tool_calls_log=self._current_log,
                log_prefix="AgentLoop",
                policy=policy,
                guardrail=self.guardrail,
                on_event=self._emit,
                turn=turn,
            )
            self._history.extend(tool_messages)
            self._stats.tool_calls = len(self._current_log)
            self._stats.subagent_calls = sum(
                call.tool_name == "subagent" for call in self._current_log
            )
            self._emit(
                "agent_turn_completed",
                {"turn": turn, "tool_calls": len(tool_calls), "final": False},
            )

        output = last_text or f"Task did not complete within {turn_limit} model calls."
        return self._result(
            output=output,
            success=False,
            stop_reason=EngineStopReason.MAX_TURNS,
        )

    def _effort_policy(self, effort: ResolvedEffort) -> tuple[float, int]:
        if effort == ResolvedEffort.LOW:
            return 0.3, max(1, self.max_turns // 2)
        if effort == ResolvedEffort.HIGH:
            return 0.7, self.max_turns
        return self.temperature, self.max_turns

    def _record_count(self) -> int:
        getter = getattr(self.llm_client, "get_call_records", None)
        return len(getter()) if callable(getter) else 0

    def _reasoning_tokens_since(self, start: int, caller_tag: str) -> int:
        getter = getattr(self.llm_client, "get_call_records", None)
        if not callable(getter):
            return 0
        return sum(
            int(getattr(record, "reasoning_tokens", 0) or 0)
            for record in getter()[start:]
            if getattr(record, "caller_tag", "") == caller_tag
        )

    def _tokens_used(self, caller_tag: str) -> int:
        getter = getattr(self.llm_client, "get_call_records", None)
        if not callable(getter):
            return self._untracked_tokens_used
        total = self._untracked_tokens_used
        for record in getter():
            if getattr(record, "caller_tag", "") != caller_tag:
                continue
            recorded_total = int(getattr(record, "total_tokens", 0) or 0)
            if not recorded_total:
                recorded_total = int(
                    (getattr(record, "prompt_tokens", 0) or 0)
                    + (getattr(record, "completion_tokens", 0) or 0)
                )
            total += recorded_total
        return total

    def _recorded_tokens_since(self, start: int, caller_tag: str) -> int:
        getter = getattr(self.llm_client, "get_call_records", None)
        if not callable(getter):
            return 0
        total = 0
        for record in getter()[start:]:
            if getattr(record, "caller_tag", "") != caller_tag:
                continue
            recorded_total = int(getattr(record, "total_tokens", 0) or 0)
            if not recorded_total:
                recorded_total = int(
                    (getattr(record, "prompt_tokens", 0) or 0)
                    + (getattr(record, "completion_tokens", 0) or 0)
                )
            total += recorded_total
        return total

    def _account_untracked_model_call(
        self,
        records_before: int,
        request_messages: list[dict[str, Any]],
        response: Any,
        *,
        assistant_message: dict[str, Any] | None = None,
    ) -> None:
        """Conservatively account for providers that expose no usage data."""
        if self.max_total_tokens is None:
            return
        if self._recorded_tokens_since(records_before, self._caller_tag):
            return
        response_tokens = (
            ContextManager.estimate_messages_tokens([assistant_message])
            if assistant_message is not None
            else self._estimate_raw_response_tokens(response)
        )
        self._untracked_tokens_used += (
            ContextManager.estimate_messages_tokens(request_messages)
            + response_tokens
        )

    @staticmethod
    def _estimate_raw_response_tokens(response: Any) -> int:
        """Best-effort response estimate used only for malformed responses."""
        parts = [str(getattr(response, "content", "") or "")]
        parts.append(str(getattr(response, "reasoning_content", "") or ""))
        try:
            for call in list(getattr(response, "tool_calls", None) or []):
                function = getattr(call, "function", None)
                parts.append(str(getattr(function, "name", "") or ""))
                parts.append(str(getattr(function, "arguments", "") or ""))
        except Exception:
            parts.append(str(response))
        return ContextManager.estimate_tokens("".join(parts)) + 4

    def _remaining_tokens(self) -> int | None:
        if self.max_total_tokens is None:
            return None
        return max(0, self.max_total_tokens - self.tokens_used)

    def _token_budget_failure(self) -> AgentLoopResult | None:
        remaining = self._remaining_tokens()
        if remaining is None or remaining > 0:
            return None
        return self._token_budget_result()

    def _token_budget_result(self) -> AgentLoopResult:
        return self._result(
            output=(
                "Token budget exhausted "
                f"({self.tokens_used}/{self.max_total_tokens} tokens used; "
                "remaining budget cannot fit the next model call)."
            ),
            success=False,
            stop_reason=EngineStopReason.ENGINE_ERROR,
        )

    def _reasoning_budget_failure(self) -> AgentLoopResult | None:
        if self._stats.reasoning_tokens <= self.max_reasoning_tokens:
            return None
        return self._result(
            output=(
                "Reasoning budget exhausted "
                f"({self._stats.reasoning_tokens}/{self.max_reasoning_tokens} "
                "reasoning tokens used)."
            ),
            success=False,
            stop_reason=EngineStopReason.ENGINE_ERROR,
        )

    def _result(
        self,
        *,
        output: str,
        success: bool,
        stop_reason: EngineStopReason,
    ) -> AgentLoopResult:
        self._stats.tool_calls = len(self._current_log)
        return AgentLoopResult(
            output=output,
            success=success,
            stop_reason=stop_reason,
            stats=self._stats.model_copy(deep=True),
            tool_calls_log=list(self._current_log),
            turns=self._turns,
        )

    def _emit(self, event: str, data: Any = None) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[AgentLoop] Event callback failed: %s", event, exc_info=True)

    def set_allowed_tools(self, tool_names: list[str] | None) -> None:
        """Apply an optional skill filter while retaining engine-local TODOs."""
        self._allowed_tool_names = list(tool_names) if tool_names else None
        self._apply_tool_filter()

    def _apply_tool_filter(self) -> None:
        if self._allowed_tool_names is None:
            self.tools = dict(self._tools_full)
        else:
            allowed = set(self._allowed_tool_names)
            allowed.add(self.todo_write.name)
            if "activate_skill" in self._tools_full:
                allowed.add("activate_skill")
            self.tools = {
                name: tool
                for name, tool in self._tools_full.items()
                if name in allowed
            }
        self.tool_schemas = [tool.to_openai_tool() for tool in self.tools.values()]
