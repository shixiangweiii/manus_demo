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
from agents.prompt_utils import build_convergence_hint
from context.manager import ContextManager
from llm.client import LLMClient
from schema import StepResult, ToolCallRecord
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
    ):
        self.llm_client = llm_client
        self.context_manager = context_manager
        self.max_iterations = max_iterations or getattr(config, 'MAX_REACT_ITERATIONS', 10)

        if isinstance(tools, dict):
            self.tools = tools
        else:
            self.tools = {t.name: t for t in tools}

        self.tool_schemas = [t.to_openai_tool() for t in self.tools.values()]

        available_tool_names = list(self.tools.keys())
        self.tool_router = tool_router or ToolRouter(available_tools=available_tool_names)

    async def execute(
        self,
        prompt: str,
        context: str = "",
        node_id: str | None = None,
        system_hint: str = "",
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,
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

        Returns:
            StepResult: Contains success status, output text, and tool call log
        """
        step_id = node_id or "default"
        # Note: callers can access self.tool_router.reset_node() to clear
        # per-node failure counts between independent executions.

        if context:
            prompt = f"{prompt}\n\nContext from previous steps:\n{context}"

        tool_calls_log: list[ToolCallRecord] = []
        iteration = 0
        messages: list[dict[str, Any]] = []
        if system_hint:
            messages.append({"role": "system", "content": system_hint})

        logger.info("[ReActEngine] Starting execution for %s: %s", step_id, prompt[:100])

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug("[ReActEngine] Iteration %d/%d", iteration, self.max_iterations)

            try:
                continue_msg = "Continue executing based on the tool results above."
                router_hint = self.tool_router.get_hint(str(step_id))

                if router_hint:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

                # Dynamic convergence guidance based on tool call frequency
                tool_call_counts: dict[str, int] = {}
                for tc in tool_calls_log:
                    tool_call_counts[tc.tool_name] = tool_call_counts.get(tc.tool_name, 0) + 1

                continue_msg += build_convergence_hint(tool_call_counts)

                if iteration == 1:
                    user_input = prompt
                else:
                    user_input = continue_msg

                messages.append({"role": "user", "content": user_input})

                if self.context_manager is not None:
                    messages = await self.context_manager.compress_if_needed(
                        messages, self.llm_client
                    )

                response_msg = await self.llm_client.chat_with_tools(
                    messages,
                    tools=self.tool_schemas,
                    temperature=0.5,
                )

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": response_msg.content or "",
                }
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

            # Execute tool calls. Independent calls run concurrently via
            # asyncio.gather; results are then processed in original order to
            # preserve assistant.tool_calls ↔ tool messages alignment required
            # by the OpenAI protocol.
            async def _exec_one(tc) -> tuple[Any, str, dict, str, bool]:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                logger.info("[ReActEngine] Tool call: %s(%s)", fn_name, fn_args)
                t = self.tools.get(fn_name)
                if t is None:
                    return tc, fn_name, fn_args, f"Error: Unknown tool '{fn_name}'", True
                try:
                    res = await t.traced_execute(**fn_args)
                    err = isinstance(res, str) and res.startswith("Error:")
                    return tc, fn_name, fn_args, res, err
                except Exception as exc:
                    return tc, fn_name, fn_args, f"Error: Tool execution error: {exc}", True

            executions = await asyncio.gather(
                *(_exec_one(tc) for tc in response_msg.tool_calls)
            )

            tool_messages: list[dict[str, Any]] = []
            truncation_limit = config.TOOL_RESULT_TRUNCATION_LIMIT

            for tool_call, func_name, func_args, result, is_error in executions:
                # Single ToolRouter accounting point — record after error detection
                # so that Error:-prefixed returns are counted as failures (not successes).
                if is_error:
                    self.tool_router.record_failure(str(step_id), func_name)
                else:
                    self.tool_router.record_success(str(step_id), func_name)

                # Apply truncation BOTH to record (UI/stats) AND to LLM
                # context. Previously only ToolCallRecord was truncated, which
                # let oversized successful results (e.g., 39k-char wttr.in JSON)
                # blow up subsequent prompts.
                if is_error:
                    # Keep full error text — debugging value high, size usually small
                    record_result = result
                    llm_result = result
                else:
                    if isinstance(result, str) and len(result) > truncation_limit:
                        truncated = result[:truncation_limit]
                        record_result = truncated
                        llm_result = (
                            truncated
                            + f"\n\n[Tool output truncated at {truncation_limit} characters "
                              f"to control context size; original length={len(result)}]"
                        )
                    else:
                        record_result = result
                        llm_result = result

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

        logger.warning("[ReActEngine] Hit max iterations (%d)", self.max_iterations)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Task did not complete within {self.max_iterations} iterations.",
            tool_calls_log=tool_calls_log,
            iterations_completed=iteration,
        )

    def get_node_summary(self, node_id: str) -> dict[str, Any]:
        """Return tool usage summary for a node (for observability)."""
        return self.tool_router.get_node_summary(str(node_id))
