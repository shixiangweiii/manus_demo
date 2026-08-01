"""
Reasoning-model-aware native tool-calling action loop.
面向推理模型的原生工具调用动作执行循环。

Extends ToolCallingLoop to properly handle reasoning models (DeepSeek R1,
OpenAI o-series, Claude extended thinking) where reasoning tokens are
a separate concern from action iterations.

Both this class and its base class use structured ``tool_calls`` and
``role="tool"`` results. ``ReasoningAwareToolCallingLoop`` adds reasoning-token and
reasoning-only-round handling; it does not switch to, prompt for, or parse a
literal ``Thought:/Action:/Observation:`` text protocol.

Key differences from ToolCallingLoop:
1. Reasoning-only rounds don't count toward the action-iteration limit
2. Reasoning token budget is configured independently
3. assistant_msg separates reasoning metadata from response content
4. reasoning_content written to message dict for context-aware splitting

Selection is explicit through the unified executor policy.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from llm.client import _extract_reasoning_content_from_tags, _strip_reasoning_from_content
from tool_calling.loop import ToolCallingLoop
from tool_calling.tool_execution import ToolExecutionPolicy, execute_tool_calls
from execution.models import ResolvedEffort, StepResult, ToolCallRecord

logger = logging.getLogger(__name__)


class ReasoningAwareToolCallingLoop(ToolCallingLoop):
    """Reasoning-model-aware variant of the structured tool-calling loop.

    面向推理模型的结构化工具调用循环变体。

    Inherits the native tool-calling loop but overrides:
    - Iteration counting: reasoning-only rounds don't increment iteration
    - Message construction: separates reasoning metadata from the answer
    - Reasoning budget: tracks cumulative ``reasoning_tokens``
    - Temperature: uses the reasoning-aware tool-calling executor temperature
    """

    def __init__(
        self,
        *args: Any,
        max_reasoning_tokens: int | None = None,
        max_reasoning_rounds: int = 5,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.max_reasoning_tokens = max_reasoning_tokens or 10000
        self.max_reasoning_rounds = max_reasoning_rounds

    def _apply_effort(self, effort: ResolvedEffort) -> tuple[float, int]:
        """Apply effort to reasoning-aware tool-calling execution."""
        if effort == ResolvedEffort.LOW:
            return 0.3, max(3, self.max_iterations // 2)
        elif effort == ResolvedEffort.HIGH:
            return 0.7, self.max_iterations
        return self.temperature, self.max_iterations

    async def execute(
        self,
        prompt: str,
        context: str = "",
        node_id: str | None = None,
        system_hint: str = "",
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,
        effort: ResolvedEffort | None = None,
    ) -> StepResult:
        """Execute a single task with reasoning-model-aware iteration counting.

        Core action-loop differences from ToolCallingLoop:
        - LLM calls that produce only reasoning (no tool_calls, no final answer)
          don't increment the iteration counter
        - Reasoning content is separated from response in assistant messages
        - Cumulative reasoning tokens are tracked against the configured budget
        """
        step_id = node_id or "default"
        effective_effort = effort or ResolvedEffort.MEDIUM
        effective_temp, effective_max_iter = self._apply_effort(effective_effort)
        effective_policy = ToolExecutionPolicy.for_effort(
            effective_effort,
            self.result_truncation_limit,
        )

        # LOW effort caps the reasoning-token budget.
        effective_reasoning_budget = self.max_reasoning_tokens
        if effective_effort == ResolvedEffort.LOW:
            effective_reasoning_budget = min(self.max_reasoning_tokens, 2000)

        if context:
            prompt = f"{prompt}\n\nContext from previous steps:\n{context}"

        tool_calls_log: list[ToolCallRecord] = []
        self._current_log = tool_calls_log
        iteration = 0
        total_reasoning_tokens = 0
        reasoning_rounds = 0
        messages: list[dict[str, Any]] = []
        if system_hint:
            messages.append({"role": "system", "content": system_hint})

        logger.info("[ReasoningAwareToolCallingLoop] Starting execution for %s: %s", step_id, prompt[:100])

        while iteration < effective_max_iter:
            try:
                continue_msg = "Continue executing based on the tool results above."
                router_hint = self.tool_router.get_hint(str(step_id))

                if router_hint:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

                # Lazy import (same pattern as ToolCallingLoop — breaks circular dep)
                from agents.prompt_utils import build_convergence_hint
                tool_call_counts: dict[str, int] = {}
                for tc in tool_calls_log:
                    tool_call_counts[tc.tool_name] = tool_call_counts.get(tc.tool_name, 0) + 1
                continue_msg += build_convergence_hint(tool_call_counts)

                non_system_msgs = [m for m in messages if m.get("role") != "system"]
                if not non_system_msgs:
                    user_input = prompt
                elif not tool_calls_log:
                    user_input = "Based on your previous reasoning, continue with the task or call a tool."
                else:
                    user_input = continue_msg

                messages.append({"role": "user", "content": user_input})

                if self.context_manager is not None:
                    messages = await self.context_manager.compress_if_needed(
                        messages, self.llm_client, caller_tag=self.agent_name or "ReasoningAwareToolCallingLoop"
                    )

                records_before = len(self.llm_client.get_call_records())

                response_msg = await self.llm_client.chat_with_tools(
                    messages,
                    tools=self.tool_schemas,
                    temperature=effective_temp,
                    caller_tag=self.agent_name or "ReasoningAwareToolCallingLoop",
                )

                # Extract reasoning from reasoning_content or provider <think/> tags.
                reasoning_content = getattr(response_msg, "reasoning_content", None) or ""
                if not reasoning_content:
                    reasoning_content = _extract_reasoning_content_from_tags(response_msg.content or "")
                response_text = _strip_reasoning_from_content(
                    response_msg.content or "",
                    reasoning_content,
                )

                # Track reasoning tokens differentially from only new call records.
                new_records = self.llm_client.get_call_records()[records_before:]
                for rec in new_records:
                    total_reasoning_tokens += rec.reasoning_tokens

                # Keep provider reasoning in the internal reasoning_content field.
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": response_text,
                }
                if reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
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
                logger.error("[ReasoningAwareToolCallingLoop] LLM call failed: %s", exc)
                return StepResult(
                    step_id=step_id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                    iterations_completed=iteration,
                )

            # Determine whether this was a reasoning-only round.
            has_tool_calls = bool(response_msg.tool_calls)
            has_final_answer = bool(response_text.strip())

            # Budget guard applies to all branches, not only reasoning-only rounds.
            budget_exceeded = total_reasoning_tokens > effective_reasoning_budget
            if budget_exceeded:
                logger.warning("[ReasoningAwareToolCallingLoop] Reasoning budget exceeded (%d > %d) on %s branch",
                               total_reasoning_tokens, effective_reasoning_budget,
                               "tool-call" if has_tool_calls else "final-answer" if has_final_answer else "reasoning-only")

            if has_tool_calls:
                # A tool call breaks the consecutive reasoning-only streak.
                reasoning_rounds = 0
                # Tool calls always count as an iteration
                iteration += 1
                if budget_exceeded:
                    # Budget exceeded: skip tool execution, force model to summarize
                    logger.warning("[ReasoningAwareToolCallingLoop] Budget exceeded on tool-call round, skipping tool execution")
                    tool_summary = ", ".join(tc.tool_name for tc in tool_calls_log) if tool_calls_log else "none"
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=f"Reasoning budget exceeded ({total_reasoning_tokens} > {effective_reasoning_budget} tokens) "
                               f"during tool-call round. Tools executed so far: [{tool_summary}].",
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )
                logger.debug("[ReasoningAwareToolCallingLoop] Iteration %d/%d (tool calls)", iteration, effective_max_iter)
            elif has_final_answer:
                # A final answer breaks the consecutive reasoning-only streak.
                reasoning_rounds = 0
                # Final answer — count as an iteration and return
                # Allow return even if budget exceeded (final answer is valuable data)
                iteration += 1
                logger.info("[ReasoningAwareToolCallingLoop] Completed in %d iterations (reasoning tokens: %d)", iteration, total_reasoning_tokens)
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
                # Reasoning-only round: do not increment action iteration.
                reasoning_rounds += 1
                logger.debug("[ReasoningAwareToolCallingLoop] Reasoning-only round %d/%d (tokens: %d/%d)", reasoning_rounds, self.max_reasoning_rounds, total_reasoning_tokens, effective_reasoning_budget)

                # Hard stop for consecutive reasoning-only rounds.
                if reasoning_rounds > self.max_reasoning_rounds:
                    logger.warning("[ReasoningAwareToolCallingLoop] Max reasoning-only rounds exceeded (%d > %d), forcing exit", reasoning_rounds, self.max_reasoning_rounds)
                    iteration += 1
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        output=f"Max consecutive reasoning-only rounds exceeded ({reasoning_rounds} > {self.max_reasoning_rounds}). "
                               f"Tools executed: [{', '.join(tc.tool_name for tc in tool_calls_log) if tool_calls_log else 'none'}].",
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )

                # Second reasoning-budget guard; depends on token tracking.
                if total_reasoning_tokens > effective_reasoning_budget:
                    logger.warning("[ReasoningAwareToolCallingLoop] Reasoning budget exceeded (%d > %d), forcing iteration count", total_reasoning_tokens, effective_reasoning_budget)
                    iteration += 1
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    tool_summary = ", ".join(tc.tool_name for tc in tool_calls_log) if tool_calls_log else "no tools called"
                    partial_response = response_text.strip()[:200] if response_text else "(empty)"
                    output = (
                        f"Reasoning budget exceeded ({total_reasoning_tokens} > "
                        f"{effective_reasoning_budget} tokens). "
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

            # Tool execution shares the same helper as ToolCallingLoop.
            tool_messages = await execute_tool_calls(
                response_msg.tool_calls,
                self.tools,
                self.tool_router,
                node_id=str(step_id),
                agent_name=self.agent_name,
                truncation_limit=effective_policy.truncation_limit,
                tool_calls_log=tool_calls_log,
                log_prefix="ReasoningAwareToolCallingLoop",
                policy=effective_policy,
                guardrail=self.guardrail,
                on_event=self._on_event,
            )
            messages.extend(tool_messages)

            # Handoff control transfer shared with the standard tool-calling loop.
            transfer = self._check_handoff_transfer(
                response_msg, step_id, tool_calls_log, iteration, on_iteration,
            )
            if transfer is not None:
                return transfer

            if on_iteration:
                on_iteration(iteration, tool_calls_log)

        logger.warning("[ReasoningAwareToolCallingLoop] Hit max iterations (%d)", effective_max_iter)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Task did not complete within {effective_max_iter} iterations.",
            tool_calls_log=tool_calls_log,
            iterations_completed=iteration,
        )
