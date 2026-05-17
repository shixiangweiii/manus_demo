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
from llm.client import LLMClient
from react.tool_call_helpers import (
    attribute_caller,
    classify_result,
    truncate_for_llm,
)
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
                    caller_tag=self.agent_name or "ReActEngine",
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
            # Wave-1: classify_result / attribute_caller / truncate_for_llm
            # extracted into react.tool_call_helpers so GoalDrivenPlanner and
            # EmergentPlanner legacy paths share the exact same behavior.
            async def _exec_one(tc) -> tuple[Any, str, dict, str, bool, bool]:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    fn_args = {}
                logger.info("[ReActEngine] Tool call: %s(%s)", fn_name, fn_args)
                t = self.tools.get(fn_name)
                if t is None:
                    res = f"Error: Unknown tool '{fn_name}'"
                    is_err, rl = classify_result(res, None)
                    return tc, fn_name, fn_args, res, is_err, rl
                # set_caller -> traced_execute 之间无 await,asyncio 单线程下原子。
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
                # Three-state ToolRouter accounting (rate_limited > error > success).
                # 业务限流（如 SubAgent 调用上限）单独入桶，不污染 failure 阈值。
                if is_rate_limited:
                    self.tool_router.record_rate_limited(str(step_id), func_name)
                elif is_error:
                    self.tool_router.record_failure(str(step_id), func_name)
                else:
                    self.tool_router.record_success(str(step_id), func_name)

                # Apply truncation BOTH to record (UI/stats) AND to LLM context
                # via the shared helper — single source of truth.
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
