"""
Reasoning Engine - ReActEngine variant for reasoning models.
推理引擎 —— 面向推理模型的 ReActEngine 变体。

Extends ReActEngine to properly handle reasoning models (DeepSeek R1,
OpenAI o-series, Claude extended thinking) where thinking tokens are
a separate concern from action iterations.

Key differences from ReActEngine:
1. Thinking rounds don't count toward MAX_REACT_ITERATIONS
2. Thinking token budget controlled by MAX_THINKING_TOKENS
3. assistant_msg separates thinking from response content
4. thinking_content written to message dict for context-aware splitting

Feature-flagged via config.ENABLE_REASONING_ENGINE (default: false).
通过 config.ENABLE_REASONING_ENGINE 灰度切换（默认关闭）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import config
from llm.client import _extract_thinking_content, _strip_thinking_from_content
from react.engine import ReActEngine
from schema import StepResult, ToolCallRecord

logger = logging.getLogger(__name__)


class ReasoningEngine(ReActEngine):
    """ReActEngine variant for reasoning models.

    推理模型适配的 ReAct 引擎变体。

    Inherits the standard ReAct loop but overrides:
    - Iteration counting: pure thinking rounds don't increment iteration
    - Message construction: separates thinking from response in assistant_msg
    - Thinking budget: tracks cumulative reasoning_tokens against MAX_THINKING_TOKENS
    """

    def __init__(self, *args: Any, max_thinking_tokens: int | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.max_thinking_tokens = max_thinking_tokens or config.MAX_THINKING_TOKENS

    async def execute(
        self,
        prompt: str,
        context: str = "",
        node_id: str | None = None,
        system_hint: str = "",
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,
    ) -> StepResult:
        """Execute a single task with reasoning-model-aware iteration counting.

        Core loop differences from ReActEngine:
        - LLM calls that produce only thinking (no tool_calls, no final answer)
          don't increment the iteration counter
        - Thinking content is separated from response in assistant messages
        - Cumulative thinking tokens are tracked against MAX_THINKING_TOKENS
        """
        step_id = node_id or "default"

        if context:
            prompt = f"{prompt}\n\nContext from previous steps:\n{context}"

        tool_calls_log: list[ToolCallRecord] = []
        self._current_log = tool_calls_log
        iteration = 0
        total_thinking_tokens = 0
        messages: list[dict[str, Any]] = []
        if system_hint:
            messages.append({"role": "system", "content": system_hint})

        logger.info("[ReasoningEngine] Starting execution for %s: %s", step_id, prompt[:100])

        while iteration < self.max_iterations:
            try:
                continue_msg = "Continue executing based on the tool results above."
                router_hint = self.tool_router.get_hint(str(step_id))

                if router_hint:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

                # Lazy import (same pattern as ReActEngine — breaks circular dep)
                from agents.prompt_utils import build_convergence_hint
                tool_call_counts: dict[str, int] = {}
                for tc in tool_calls_log:
                    tool_call_counts[tc.tool_name] = tool_call_counts.get(tc.tool_name, 0) + 1
                continue_msg += build_convergence_hint(tool_call_counts)

                non_system_msgs = [m for m in messages if m.get("role") != "system"]
                if not non_system_msgs:
                    user_input = prompt
                elif not tool_calls_log:
                    user_input = "Based on your previous thinking, continue with the task or call a tool."
                else:
                    user_input = continue_msg

                messages.append({"role": "user", "content": user_input})

                if self.context_manager is not None:
                    messages = await self.context_manager.compress_if_needed(
                        messages, self.llm_client
                    )

                records_before = len(self.llm_client.get_call_records())

                response_msg = await self.llm_client.chat_with_tools(
                    messages,
                    tools=self.tool_schemas,
                    temperature=config.REASONING_TEMPERATURE,
                    caller_tag=self.agent_name or "ReasoningEngine",
                )

                # Extract thinking from reasoning_content field or <think/> tags
                thinking = getattr(response_msg, "reasoning_content", None) or ""
                if not thinking:
                    thinking = _extract_thinking_content(response_msg.content or "")
                response_text = _strip_thinking_from_content(response_msg.content or "", thinking)

                # Track thinking token budget (differential: only count new records)
                new_records = self.llm_client.get_call_records()[records_before:]
                for rec in new_records:
                    total_thinking_tokens += rec.reasoning_tokens

                # Build assistant message with separated thinking
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": response_text,
                }
                if thinking:
                    assistant_msg["thinking_content"] = thinking
                if response_msg.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in response_msg.tool_calls
                    ]
                messages.append(assistant_msg)

            except Exception as exc:
                logger.error("[ReasoningEngine] LLM call failed: %s", exc)
                return StepResult(
                    step_id=step_id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                    iterations_completed=iteration,
                )

            # Determine if this was a thinking-only round
            has_tool_calls = bool(response_msg.tool_calls)
            has_final_answer = bool(response_text.strip())

            if has_tool_calls:
                # Tool calls always count as an iteration
                iteration += 1
                logger.debug("[ReasoningEngine] Iteration %d/%d (tool calls)", iteration, self.max_iterations)
            elif has_final_answer:
                # Final answer — count as an iteration and return
                iteration += 1
                logger.info("[ReasoningEngine] Completed in %d iterations (thinking tokens: %d)", iteration, total_thinking_tokens)
                if on_iteration:
                    on_iteration(iteration, tool_calls_log)
                return StepResult(
                    step_id=step_id,
                    success=True,
                    output=response_text,
                    tool_calls_log=tool_calls_log,
                    iterations_completed=iteration,
                )
            else:
                # Pure thinking round — don't increment iteration
                logger.debug("[ReasoningEngine] Thinking round (total thinking tokens: %d/%d)", total_thinking_tokens, self.max_thinking_tokens)

                # Check thinking budget
                if total_thinking_tokens > self.max_thinking_tokens:
                    logger.warning("[ReasoningEngine] Thinking budget exceeded (%d > %d), forcing iteration count", total_thinking_tokens, self.max_thinking_tokens)
                    iteration += 1
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    tool_summary = ", ".join(tc.tool_name for tc in tool_calls_log) if tool_calls_log else "no tools called"
                    partial_response = response_text.strip()[:200] if response_text else "(empty)"
                    output = (
                        f"Thinking budget exceeded ({total_thinking_tokens} > "
                        f"{self.max_thinking_tokens} tokens). "
                        f"Tools executed: [{tool_summary}]. "
                        f"Last partial response: {partial_response}"
                    )
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=output,
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )
                # Continue to next LLM call without incrementing iteration
                continue

            # --- Tool execution (same logic as ReActEngine) ---
            import asyncio
            import json
            from react.tool_call_helpers import attribute_caller, classify_result, truncate_for_llm

            async def _exec_one(tc: Any) -> tuple[Any, str, dict, str, bool, bool]:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                logger.info("[ReasoningEngine] Tool call: %s(%s)", fn_name, fn_args)
                t = self.tools.get(fn_name)
                if t is None:
                    res = f"Error: Unknown tool '{fn_name}'"
                    is_err, rl = classify_result(res, None)
                    return tc, fn_name, fn_args, res, is_err, rl
                attribute_caller(t, self.agent_name)
                try:
                    res = await t.traced_execute(**fn_args)
                    is_err, rl = classify_result(res, None)
                    return tc, fn_name, fn_args, res, is_err, rl
                except Exception as exc:
                    res = f"Error: Tool execution error: {exc}"
                    is_err, rl = classify_result(None, exc)
                    return tc, fn_name, fn_args, res, is_err, rl

            executions = await asyncio.gather(
                *(_exec_one(tc) for tc in response_msg.tool_calls)
            )

            tool_messages: list[dict[str, Any]] = []
            truncation_limit = config.TOOL_RESULT_TRUNCATION_LIMIT

            for tool_call, func_name, func_args, result, is_error, is_rate_limited in executions:
                if is_rate_limited:
                    self.tool_router.record_rate_limited(str(step_id), func_name)
                elif is_error:
                    self.tool_router.record_failure(str(step_id), func_name)
                else:
                    self.tool_router.record_success(str(step_id), func_name)

                record_result, llm_result = truncate_for_llm(
                    result, truncation_limit, is_error,
                )

                tool_calls_log.append(ToolCallRecord(
                    tool_name=func_name,
                    parameters=func_args,
                    result=record_result,
                ))

                if is_error:
                    result_with_marker = (
                        f"[TOOL ERROR] {llm_result}\n\n"
                        "IMPORTANT: The tool returned an error. Please analyze "
                        "the error and decide whether to retry with different "
                        "parameters or report the failure."
                    )
                else:
                    result_with_marker = llm_result

                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_with_marker,
                })

            messages.extend(tool_messages)

            if on_iteration:
                on_iteration(iteration, tool_calls_log)

        logger.warning("[ReasoningEngine] Hit max iterations (%d)", self.max_iterations)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Task did not complete within {self.max_iterations} iterations.",
            tool_calls_log=tool_calls_log,
            iterations_completed=iteration,
        )
