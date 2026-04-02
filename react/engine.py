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

The module is backward compatible - existing Agent classes can continue
to use their internal _react_loop methods via Feature Flag control.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config
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
    ):
        self.llm_client = llm_client
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
    ) -> StepResult:
        """
        Execute a single task using the ReAct loop.

        Args:
            prompt: The main task prompt for the LLM
            context: Additional context from dependencies/previous steps
            node_id: Optional identifier for tool routing (per-node stats)
            system_hint: Additional system-level hint for the LLM

        Returns:
            StepResult: Contains success status, output text, and tool call log
        """
        step_id = node_id or "default"
        self.tool_router.reset_node(str(step_id))

        if context:
            prompt = f"{prompt}\n\nContext from previous steps:\n{context}"

        tool_calls_log: list[ToolCallRecord] = []
        iteration = 0
        messages: list[dict[str, Any]] = []

        logger.info("[ReActEngine] Starting execution for %s: %s", step_id, prompt[:100])

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug("[ReActEngine] Iteration %d/%d", iteration, self.max_iterations)

            try:
                continue_msg = "Continue executing based on the tool results above."
                router_hint = self.tool_router.get_hint(str(step_id))

                if router_hint and iteration > 1:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

                if system_hint and iteration == 1:
                    user_input = f"{system_hint}\n\n{prompt}"
                else:
                    user_input = prompt if iteration == 1 else continue_msg

                response_msg = await self.llm_client.chat_with_tools(
                    [{"role": "user", "content": user_input}],
                    tools=self.tool_schemas,
                    temperature=0.5,
                )

            except Exception as exc:
                logger.error("[ReActEngine] LLM call failed: %s", exc)
                return StepResult(
                    step_id=step_id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                )

            if not response_msg.tool_calls:
                final_output = response_msg.content or "Task completed (no output)."
                logger.info("[ReActEngine] Completed in %d iterations", iteration)
                return StepResult(
                    step_id=step_id,
                    success=True,
                    output=final_output,
                    tool_calls_log=tool_calls_log,
                )

            has_error = False
            tool_messages: list[dict[str, Any]] = []

            for tool_call in response_msg.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                logger.info("[ReActEngine] Tool call: %s(%s)", func_name, func_args)

                tool = self.tools.get(func_name)
                if tool is None:
                    result = f"Error: Unknown tool '{func_name}'"
                    self.tool_router.record_failure(str(step_id), func_name)
                    has_error = True
                else:
                    try:
                        result = await tool.execute(**func_args)
                        self.tool_router.record_success(str(step_id), func_name)
                    except Exception as exc:
                        result = f"Error: Tool execution error: {exc}"
                        self.tool_router.record_failure(str(step_id), func_name)
                        has_error = True

                if isinstance(result, str) and result.startswith("Error:"):
                    has_error = True

                tool_calls_log.append(ToolCallRecord(
                    tool_name=func_name,
                    parameters=func_args,
                    result=result[:1000],
                ))

                if has_error:
                    result_with_marker = (
                        f"[TOOL ERROR] {result}\n\n"
                        "IMPORTANT: The tool returned an error. Please analyze "
                        "the error and decide whether to retry with different "
                        "parameters or report the failure."
                    )
                else:
                    result_with_marker = result

                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_with_marker,
                })

            messages.extend(tool_messages)

        logger.warning("[ReActEngine] Hit max iterations (%d)", self.max_iterations)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Task did not complete within {self.max_iterations} iterations.",
            tool_calls_log=tool_calls_log,
        )

    def get_node_summary(self, node_id: str) -> dict[str, Any]:
        """Return tool usage summary for a node (for observability)."""
        return self.tool_router.get_node_summary(str(node_id))
