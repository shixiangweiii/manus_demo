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
from react.engine_helpers import ToolExecutionPolicy, execute_tool_calls
from schema import ReasoningEffort, StepResult, ToolCallRecord

logger = logging.getLogger(__name__)


class ReasoningEngine(ReActEngine):
    """ReActEngine variant for reasoning models.

    推理模型适配的 ReAct 引擎变体。

    Inherits the standard ReAct loop but overrides:
    - Iteration counting: pure thinking rounds don't increment iteration
    - Message construction: separates thinking from response in assistant_msg
    - Thinking budget: tracks cumulative reasoning_tokens against MAX_THINKING_TOKENS
    - Temperature: MEDIUM uses REASONING_TEMPERATURE instead of REACT_TEMPERATURE
    """

    def __init__(self, *args: Any, max_thinking_tokens: int | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.max_thinking_tokens = max_thinking_tokens or config.MAX_THINKING_TOKENS

    def _apply_effort(self, effort: ReasoningEffort) -> tuple[float, int]:
        """Override: MEDIUM uses REASONING_TEMPERATURE instead of REACT_TEMPERATURE."""
        if effort == ReasoningEffort.LOW:
            return 0.3, max(3, self.max_iterations // 2)
        elif effort == ReasoningEffort.HIGH:
            return 0.7, self.max_iterations
        return config.REASONING_TEMPERATURE, self.max_iterations

    async def execute(
        self,
        prompt: str,
        context: str = "",
        node_id: str | None = None,
        system_hint: str = "",
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,
        effort: ReasoningEffort | None = None,
    ) -> StepResult:
        """Execute a single task with reasoning-model-aware iteration counting.

        Core loop differences from ReActEngine:
        - LLM calls that produce only thinking (no tool_calls, no final answer)
          don't increment the iteration counter
        - Thinking content is separated from response in assistant messages
        - Cumulative thinking tokens are tracked against MAX_THINKING_TOKENS
        """
        step_id = node_id or "default"
        effective_effort = effort or ReasoningEffort.MEDIUM
        effective_temp, effective_max_iter = self._apply_effort(effective_effort)
        effective_policy = ToolExecutionPolicy.for_effort(effective_effort)

        # ReasoningEngine: LOW effort caps thinking budget
        effective_thinking_budget = self.max_thinking_tokens
        if effective_effort == ReasoningEffort.LOW:
            effective_thinking_budget = min(self.max_thinking_tokens, 2000)

        if context:
            prompt = f"{prompt}\n\nContext from previous steps:\n{context}"

        tool_calls_log: list[ToolCallRecord] = []
        self._current_log = tool_calls_log
        iteration = 0
        total_thinking_tokens = 0
        thinking_rounds = 0
        messages: list[dict[str, Any]] = []
        if system_hint:
            messages.append({"role": "system", "content": system_hint})

        logger.info("[ReasoningEngine] Starting execution for %s: %s", step_id, prompt[:100])

        while iteration < effective_max_iter:
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
                        messages, self.llm_client, caller_tag=self.agent_name or "ReasoningEngine"
                    )

                records_before = len(self.llm_client.get_call_records())

                response_msg = await self.llm_client.chat_with_tools(
                    messages,
                    tools=self.tool_schemas,
                    temperature=effective_temp,
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

            # Budget guard: applies to ALL branches, not just pure-thinking
            budget_exceeded = total_thinking_tokens > effective_thinking_budget
            if budget_exceeded:
                logger.warning("[ReasoningEngine] Thinking budget exceeded (%d > %d) on %s branch",
                               total_thinking_tokens, effective_thinking_budget,
                               "tool-call" if has_tool_calls else "final-answer" if has_final_answer else "thinking")

            if has_tool_calls:
                # Tool call breaks the consecutive thinking streak
                thinking_rounds = 0
                # Tool calls always count as an iteration
                iteration += 1
                if budget_exceeded:
                    # Budget exceeded: skip tool execution, force model to summarize
                    logger.warning("[ReasoningEngine] Budget exceeded on tool-call round, skipping tool execution")
                    tool_summary = ", ".join(tc.tool_name for tc in tool_calls_log) if tool_calls_log else "none"
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=f"Thinking budget exceeded ({total_thinking_tokens} > {effective_thinking_budget} tokens) "
                               f"during tool-call round. Tools executed so far: [{tool_summary}].",
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )
                logger.debug("[ReasoningEngine] Iteration %d/%d (tool calls)", iteration, effective_max_iter)
            elif has_final_answer:
                # Final answer breaks the consecutive thinking streak
                thinking_rounds = 0
                # Final answer — count as an iteration and return
                # Allow return even if budget exceeded (final answer is valuable data)
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
                thinking_rounds += 1
                logger.debug("[ReasoningEngine] Thinking round %d/%d (tokens: %d/%d)", thinking_rounds, config.MAX_THINKING_ROUNDS, total_thinking_tokens, effective_thinking_budget)

                # Hard stop: consecutive thinking rounds cap (independent of token tracking)
                if thinking_rounds > config.MAX_THINKING_ROUNDS:
                    logger.warning("[ReasoningEngine] Max thinking rounds exceeded (%d > %d), forcing exit", thinking_rounds, config.MAX_THINKING_ROUNDS)
                    iteration += 1
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=f"Max consecutive thinking rounds exceeded ({thinking_rounds} > {config.MAX_THINKING_ROUNDS}). "
                               f"Tools executed: [{', '.join(tc.tool_name for tc in tool_calls_log) if tool_calls_log else 'none'}].",
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )

                # Check thinking budget (second guard, depends on token tracking)
                if total_thinking_tokens > effective_thinking_budget:
                    logger.warning("[ReasoningEngine] Thinking budget exceeded (%d > %d), forcing iteration count", total_thinking_tokens, effective_thinking_budget)
                    iteration += 1
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    tool_summary = ", ".join(tc.tool_name for tc in tool_calls_log) if tool_calls_log else "no tools called"
                    partial_response = response_text.strip()[:200] if response_text else "(empty)"
                    output = (
                        f"Thinking budget exceeded ({total_thinking_tokens} > "
                        f"{effective_thinking_budget} tokens). "
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

            # --- Tool execution (same logic as ReActEngine, Batch 4.1 DRY) ---
            tool_messages = await execute_tool_calls(
                response_msg.tool_calls,
                self.tools,
                self.tool_router,
                node_id=str(step_id),
                agent_name=self.agent_name,
                truncation_limit=effective_policy.truncation_limit,
                tool_calls_log=tool_calls_log,
                log_prefix="ReasoningEngine",
                policy=effective_policy,
            )
            messages.extend(tool_messages)

            if on_iteration:
                on_iteration(iteration, tool_calls_log)

        logger.warning("[ReasoningEngine] Hit max iterations (%d)", effective_max_iter)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Task did not complete within {effective_max_iter} iterations.",
            tool_calls_log=tool_calls_log,
            iterations_completed=iteration,
        )
