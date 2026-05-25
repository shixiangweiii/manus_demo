"""
ReAct Engine - Unified ReAct (Reasoning + Acting) execution engine.
ReAct 引擎 —— 统一的 ReAct（推理 + 行动）执行引擎。

This module extracts the common ReAct loop logic from ExecutorAgent and
EmergentPlannerAgent into a shared, maintainable engine.

Features:
  - Standardized ReAct loop implementation
  - Integrated ToolRouter for failure-based tool switching
  - Configurable iteration limits
  - Tool call result recording
  - Error handling with detailed logs

Usage:
  engine = ReActEngine(
      llm_client=llm_client,
      tools=tools,
      max_iterations=10,
  )
  result = await engine.execute(prompt, context)

Agent classes (ExecutorAgent, EmergentPlannerAgent) can switch between
their legacy _react_loop and this engine via the ENABLE_REACT_ENGINE_V2
config flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import config
# NOTE: `build_convergence_hint` is imported lazily inside execute() to break a
# latent circular import: react.engine -> agents.prompt_utils -> agents/__init__.py
# (eager) -> agents.subagent -> react.engine. The top-level import worked under
# specific test orderings but failed for direct `from react.engine import X`
# probes. Lazy import keeps the module load graph acyclic.
# 延迟导入,打破 react.engine ↔ agents 包的潜在循环依赖。
from context.manager import ContextManager
from llm.client import LLMClient, _extract_thinking_content, _strip_thinking_from_content
from react.engine_helpers import ToolExecutionPolicy, execute_tool_calls
from schema import ReasoningEffort, StepResult, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)


class ReActEngine:
    """
    Unified ReAct execution engine for Manus Demo.

    The core loop:
      while not done:
          response = LLM(messages, tools)
          if response has tool_calls:
              for each tool_call:
                  result = tool.execute(**args)
                  record observation
          else:
              step is done, return final answer

    Key features:
      - Integrated ToolRouter for failure handling
      - Configurable max iterations
      - Comprehensive tool call logging
      - Error recovery support
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool] | dict[str, BaseTool],
        max_iterations: int | None = None,
        tool_router: ToolRouter | None = None,
        context_manager: ContextManager | None = None,
        agent_name: str = "",
    ):
        self.llm_client = llm_client
        self.context_manager = context_manager
        self.max_iterations = max_iterations or getattr(config, 'MAX_REACT_ITERATIONS', 10)
        # Wave C #7: name of the agent owning this engine — propagated to tools
        # via tool.set_caller(name) right before each traced_execute call so that
        # SubAgentTool can correctly attribute parent_agent in tracing.
        # 拥有此引擎的 Agent 名称——在每次 tool 执行前通过 set_caller 注入，
        # 用于 SubAgentTool 准确归因 parent_agent（替换原硬编码 OrchestratorAgent）。
        self.agent_name = agent_name

        if isinstance(tools, dict):
            self.tools = tools
        else:
            self.tools = {t.name: t for t in tools}

        self.tool_schemas = [t.to_openai_tool() for t in self.tools.values()]

        available_tool_names = list(self.tools.keys())
        self.tool_router = tool_router or ToolRouter(available_tools=available_tool_names)

        # Wave-3 M2: tool_calls_log lifted to a member attribute so external
        # observers (notably SubAgent timeout/budget paths) can read the
        # in-progress log when execute() does not return its StepResult.
        # `on_iteration` only fires at iteration boundaries — if a timeout fires
        # mid-iteration, on_iteration's snapshot misses the most recent calls.
        # Reading self._current_log instead recovers them.
        # 把 tool_calls_log 升为成员属性,timeout/budget cancel 时外部可读最新状态;
        # on_iteration 只在迭代末触发,中途取消会丢最后一轮——成员属性兜底。
        self._current_log: list[ToolCallRecord] = []

    def _apply_effort(
        self, effort: ReasoningEffort,
    ) -> tuple[float, int]:
        """Return (temperature, max_iterations) for effort level."""
        if effort == ReasoningEffort.LOW:
            return 0.3, max(3, self.max_iterations // 2)
        elif effort == ReasoningEffort.HIGH:
            return 0.7, self.max_iterations
        else:  # MEDIUM
            return config.REACT_TEMPERATURE, self.max_iterations

    async def execute(
        self,
        prompt: str,
        context: str = "",
        node_id: str | None = None,
        system_hint: str = "",
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,
        effort: ReasoningEffort | None = None,
    ) -> StepResult:
        """
        Execute a single task using the ReAct loop.

        Args:
            prompt: The main task prompt for the LLM
            context: Additional context from dependencies/previous steps
            node_id: Optional identifier for tool routing (per-node stats)
            system_hint: Additional system-level hint for the LLM
            on_iteration: Optional callback invoked after each iteration with
                (iteration_number, current_tool_calls_log). Can raise to abort.
            effort: Reasoning effort level affecting temperature/iterations/truncation.

        Returns:
            StepResult: Contains success status, output text, and tool call log
        """
        step_id = node_id or "default"
        effective_effort = effort or ReasoningEffort.MEDIUM
        effective_temp, effective_max_iter = self._apply_effort(effective_effort)
        effective_policy = ToolExecutionPolicy.for_effort(effective_effort)
        # Note: callers can access self.tool_router.reset_node() to clear
        # per-node failure counts between independent executions.

        if context:
            prompt = f"{prompt}\n\nContext from previous steps:\n{context}"

        # Wave-3 M2 (concurrency-safe): create a FRESH local list and rebind
        # self._current_log to it. Do NOT reuse + clear the previous list —
        # if the same ReActEngine instance is invoked concurrently (e.g.
        # DAG_SERIAL_EXECUTION=false where ExecutorAgent.create_for_node()
        # shares self._react_engine across parallel nodes), clearing would
        # wipe a list that the other in-flight execute() is still appending
        # to. New list per call isolates lifetimes. Outsiders reading
        # self._current_log see whichever execute() rebound it last; the
        # canonical "SubAgent failure path" reader is safe because SubAgent
        # owns a PRIVATE ReActEngine (created in SubAgent.__init__) so at
        # most one execute() is in flight on it at a time.
        # 每次 execute() 用新 list,避免并发 execute() 的 clear 互相清空。
        # SubAgent 路径安全:SubAgent 自己 new 的私有 engine,无并发。
        tool_calls_log: list[ToolCallRecord] = []
        self._current_log = tool_calls_log
        iteration = 0
        needs_explicit_answer = False
        messages: list[dict[str, Any]] = []
        if system_hint:
            messages.append({"role": "system", "content": system_hint})

        logger.info("[ReActEngine] Starting execution for %s: %s", step_id, prompt[:100])

        while iteration < effective_max_iter:
            iteration += 1
            logger.debug("[ReActEngine] Iteration %d/%d", iteration, effective_max_iter)

            try:
                continue_msg = "Continue executing based on the tool results above."
                router_hint = self.tool_router.get_hint(str(step_id))

                if router_hint:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

                # Dynamic convergence guidance based on tool call frequency.
                # Lazy import (see module header) — breaks circular dep with
                # agents package. After first call the import is cached in
                # sys.modules so subsequent calls are zero-cost.
                from agents.prompt_utils import build_convergence_hint
                tool_call_counts: dict[str, int] = {}
                for tc in tool_calls_log:
                    tool_call_counts[tc.tool_name] = tool_call_counts.get(tc.tool_name, 0) + 1

                continue_msg += build_convergence_hint(tool_call_counts)

                if iteration == 1:
                    user_input = prompt
                elif needs_explicit_answer:
                    user_input = "Please provide your final answer based on the reasoning above."
                    needs_explicit_answer = False
                else:
                    user_input = continue_msg

                messages.append({"role": "user", "content": user_input})

                if self.context_manager is not None:
                    messages = await self.context_manager.compress_if_needed(
                        messages, self.llm_client, caller_tag=self.agent_name or "ReActEngine"
                    )

                response_msg = await self.llm_client.chat_with_tools(
                    messages,
                    tools=self.tool_schemas,
                    temperature=effective_temp,
                    caller_tag=self.agent_name or "ReActEngine",
                )

                # Extract and separate thinking from response content
                thinking = getattr(response_msg, "reasoning_content", None) or ""
                if not thinking:
                    thinking = _extract_thinking_content(response_msg.content or "")
                response_text = _strip_thinking_from_content(response_msg.content or "", thinking)
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
                logger.error("[ReActEngine] LLM call failed: %s", exc)
                return StepResult(
                    step_id=step_id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                    iterations_completed=iteration,
                )

            if not response_msg.tool_calls:
                if response_text.strip():
                    # Real answer after thinking was stripped
                    final_output = response_text
                elif thinking and not response_text.strip():
                    # Reasoning-only round: model is still thinking, no final answer yet
                    logger.info("[ReActEngine] Reasoning-only response, requesting explicit answer")
                    needs_explicit_answer = True
                    continue
                else:
                    # Truly empty response (edge case)
                    final_output = response_msg.content or "Task completed (no output)."
                    logger.info("[ReActEngine] Completed in %d iterations", iteration)
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    return StepResult(
                        step_id=step_id,
                        success=True,
                        output=final_output,
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )

                logger.info("[ReActEngine] Completed in %d iterations", iteration)
                if on_iteration:
                    on_iteration(iteration, tool_calls_log)
                return StepResult(
                    step_id=step_id,
                    success=True,
                    output=final_output,
                    tool_calls_log=tool_calls_log,
                    iterations_completed=iteration,
                )

            # Execute tool calls. Independent calls run concurrently via
            # Batch 4.1 DRY: shared execute_tool_calls replaces inline _exec_one
            # + gather + result processing loop. Same behavior, single source.
            tool_messages = await execute_tool_calls(
                response_msg.tool_calls,
                self.tools,
                self.tool_router,
                node_id=str(step_id),
                agent_name=self.agent_name,
                truncation_limit=effective_policy.truncation_limit,
                tool_calls_log=tool_calls_log,
                log_prefix="ReActEngine",
                policy=effective_policy,
            )
            messages.extend(tool_messages)

            if on_iteration:
                on_iteration(iteration, tool_calls_log)

        logger.warning("[ReActEngine] Hit max iterations (%d)", effective_max_iter)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Task did not complete within {effective_max_iter} iterations.",
            tool_calls_log=tool_calls_log,
            iterations_completed=iteration,
        )

    def get_node_summary(self, node_id: str) -> dict[str, Any]:
        """Return tool usage summary for a node (for observability)."""
        return self.tool_router.get_node_summary(str(node_id))
