"""
Native tool-calling action loop.
基于模型原生工具调用的动作执行循环。

This module provides the shared structured tool-calling loop used by runtime
executors. ``ToolCallingLoop`` names the implementation by its actual runtime
mechanism: model-emitted ``tool_calls`` are executed and their results are fed
back into the next model turn.

This implementation does NOT prompt for or parse a label-based textual protocol
with literal ``Thought:``, ``Action:``, and ``Observation:`` labels. A provider
``tool_call`` is the Action, and its matching ``role="tool"`` result is the
Observation. Reasoning content, when a provider returns it, is separate metadata
and is not required to be displayed.

本模块不要求模型输出、也不解析字面的 ``Thought:`` / ``Action:`` /
``Observation:`` 文本协议。结构化 ``tool_call`` 对应 Action，匹配的
``role="tool"`` 结果对应 Observation；供应商返回的 reasoning 内容属于独立
元数据，不是本循环成立或对外展示的必要条件。

Features:
  - Standardized native tool-calling action loop
  - Integrated ToolRouter for failure-based tool switching
  - Configurable iteration limits
  - Tool call result recording
  - Error handling with detailed logs

Usage:
  loop = ToolCallingLoop(
      llm_client=llm_client,
      tools=tools,
      max_iterations=10,
  )
  result = await loop.execute(prompt, context)

Runtime executors select this implementation explicitly through executor
configuration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

# NOTE: `build_convergence_hint` is imported lazily inside execute() to break a
# latent circular import: tool_calling.loop -> agents.prompt_utils ->
# agents/__init__.py (eager) -> agents.subagent -> tool_calling.loop. The
# top-level import worked under specific test orderings but failed for direct
# module-import probes. Lazy import keeps the module load graph acyclic.
# 延迟导入，打破 tool_calling.loop ↔ agents 包的潜在循环依赖。
from context.manager import ContextManager
from llm.client import LLMClient, _extract_reasoning_content_from_tags, _strip_reasoning_from_content
from tool_calling.tool_execution import ToolExecutionPolicy, execute_tool_calls
from execution.models import ResolvedEffort, StepResult, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)


class ToolCallingLoop:
    """
    Native tool-calling loop for executing one runtime action.

    The core loop:
      while not done:
          response = LLM(messages, tools)
          if response has tool_calls:
              for each tool_call:
                  result = tool.execute(**args)
                  append result as a role="tool" observation
          else:
              step is done, return final answer

    It does not implement a label-based
    ``Thought:/Action:/Observation:`` text protocol.

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
        guardrail: Any | None = None,
        temperature: float = 0.5,
        result_truncation_limit: int = 2000,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        self.llm_client = llm_client
        self.context_manager = context_manager
        self.max_iterations = max_iterations or 10
        self.temperature = temperature
        self.result_truncation_limit = result_truncation_limit
        # Name of the runtime component owning this loop — propagated to tools
        # via tool.set_caller(name) right before each traced_execute call so that
        # SubAgentTool can correctly attribute parent_agent in tracing.
        # 拥有此循环的 Agent 名称——在每次 tool 执行前通过 set_caller 注入，
        # 用于 SubAgentTool 准确归因 parent_agent。
        self.agent_name = agent_name
        self.guardrail = guardrail
        self._on_event = on_event

        if isinstance(tools, dict):
            self.tools = tools
        else:
            self.tools = {t.name: t for t in tools}

        # Backup of the full tool set before any skill-based filtering.
        # When a skill with allowed_tools is activated, set_allowed_tools() narrows
        # self.tools; passing None restores the full set from this backup.
        # 完整工具集备份，用于技能过滤后的恢复。
        # 激活含 allowed_tools 的技能时，set_allowed_tools() 收窄 self.tools；
        # 传 None 从此备份恢复完整集合。
        self._tools_full = dict(self.tools)

        self.tool_schemas = [t.to_openai_tool() for t in self.tools.values()]

        available_tool_names = list(self.tools.keys())
        self.tool_router = tool_router or ToolRouter(available_tools=available_tool_names)

        # Tools that transfer control on success (Handoff). When such a
        # tool succeeds, end the loop and use its FULL output as the final answer.
        # Empty set (the default for every loop without a handoff tool) makes
        # the check below a no-op — zero behavior change for existing loops.
        # 控制权转移类工具集合（Handoff）。成功时终止循环、以其完整输出为答案。
        # 无 handoff 工具时为空集 → 下方检查零开销，现有循环行为不变。
        self._handoff_tool_names = {
            n for n, t in self.tools.items() if getattr(t, "is_handoff", False)
        }

        # Keep the current tool log on the instance so external
        # observers (notably SubAgent timeout/budget paths) can read the
        # in-progress log when execute() does not return its StepResult.
        # `on_iteration` only fires at iteration boundaries — if a timeout fires
        # mid-iteration, on_iteration's snapshot misses the most recent calls.
        # Reading self._current_log instead recovers them.
        # 把 tool_calls_log 升为成员属性,timeout/budget cancel 时外部可读最新状态;
        # on_iteration 只在迭代末触发,中途取消会丢最后一轮——成员属性兜底。
        self._current_log: list[ToolCallRecord] = []

    def _apply_effort(
        self, effort: ResolvedEffort,
    ) -> tuple[float, int]:
        """Return (temperature, max_iterations) for effort level."""
        if effort == ResolvedEffort.LOW:
            return 0.3, max(3, self.max_iterations // 2)
        elif effort == ResolvedEffort.HIGH:
            return 0.7, self.max_iterations
        else:  # MEDIUM
            return self.temperature, self.max_iterations

    def _check_handoff_transfer(
        self,
        response_msg: Any,
        step_id: str,
        tool_calls_log: list[ToolCallRecord],
        iteration: int,
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None,
    ) -> StepResult | None:
        """Handle Handoff control transfer for both action-loop variants.

        If a handoff tool succeeded this iteration, return a terminal StepResult
        carrying the specialist's FULL output (read from the tool instance to
        avoid message-level truncation); otherwise None (loop continues). A failed
        handoff (Error:) leaves _last_ok False → no transfer. Empty
        _handoff_tool_names (no handoff tool) → no-op.

        Extracted so both action loops honor #20 — fixes drift where only
        ToolCallingLoop.execute had the inline check while the
        reasoning-aware subclass bypassed it.
        此逻辑由两个执行循环共享，避免推理感知路径漏掉 handoff 终止。
        """
        if not self._handoff_tool_names or not getattr(response_msg, "tool_calls", None):
            return None
        for tc in response_msg.tool_calls:
            if tc.function.name in self._handoff_tool_names:
                ho = self.tools.get(tc.function.name)
                if ho is not None and getattr(ho, "_last_ok", False):
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    logger.info(
                        "[%s] Handoff control transfer via '%s' — ending loop",
                        type(self).__name__, tc.function.name,
                    )
                    return StepResult(
                        step_id=step_id,
                        success=True,
                        output=getattr(ho, "_last_output", "") or "",
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )
                break
        return None

    async def execute(
        self,
        prompt: str,
        context: str = "",
        node_id: str | None = None,
        system_hint: str = "",
        on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,
        effort: ResolvedEffort | None = None,
    ) -> StepResult:
        """
        Execute a single task using the native tool-calling action loop.

        Args:
            prompt: The main task prompt for the LLM
            context: Additional context from dependencies/previous steps
            node_id: Optional identifier for tool routing (per-node stats)
            system_hint: Additional system-level hint for the LLM
            on_iteration: Optional callback invoked after each iteration with
                (iteration_number, current_tool_calls_log). Can raise to abort.
            effort: Resolved runtime effort affecting temperature, iteration,
                and truncation policies.

        Returns:
            StepResult: Contains success status, output text, and tool call log
        """
        step_id = node_id or "default"
        effective_effort = effort or ResolvedEffort.MEDIUM
        effective_temp, effective_max_iter = self._apply_effort(effective_effort)
        effective_policy = ToolExecutionPolicy.for_effort(
            effective_effort,
            self.result_truncation_limit,
        )
        # Note: callers can access self.tool_router.reset_node() to clear
        # per-node failure counts between independent executions.

        if context:
            prompt = f"{prompt}\n\nContext from previous steps:\n{context}"

        # Create a fresh local list per execution and rebind
        # self._current_log to it. Do NOT reuse + clear the previous list —
        # if the same ToolCallingLoop instance is invoked concurrently (e.g.
        # parallel DAG execution), clearing would
        # wipe a list that the other in-flight execute() is still appending
        # to. New list per call isolates lifetimes. Outsiders reading
        # self._current_log see whichever execute() rebound it last; the
        # canonical "SubAgent failure path" reader is safe because SubAgent
        # owns a PRIVATE ToolCallingLoop (created in SubAgent.__init__) so at
        # most one execute() is in flight on it at a time.
        # 每次 execute() 用新 list,避免并发 execute() 的 clear 互相清空。
        # SubAgent 路径安全：SubAgent 自己创建的私有 loop 无并发。
        tool_calls_log: list[ToolCallRecord] = []
        self._current_log = tool_calls_log
        iteration = 0
        needs_explicit_answer = False
        messages: list[dict[str, Any]] = []
        if system_hint:
            messages.append({"role": "system", "content": system_hint})

        logger.info("[ToolCallingLoop] Starting execution for %s: %s", step_id, prompt[:100])

        while iteration < effective_max_iter:
            iteration += 1
            logger.debug("[ToolCallingLoop] Iteration %d/%d", iteration, effective_max_iter)

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
                        messages, self.llm_client, caller_tag=self.agent_name or "ToolCallingLoop"
                    )

                response_msg = await self.llm_client.chat_with_tools(
                    messages,
                    tools=self.tool_schemas,
                    temperature=effective_temp,
                    caller_tag=self.agent_name or "ToolCallingLoop",
                )

                # Separate provider reasoning metadata from answer content.
                # This is not parsing a literal ``Thought:`` field: the
                # provider may expose reasoning_content or embed <think> tags.
                reasoning_content = getattr(response_msg, "reasoning_content", None) or ""
                if not reasoning_content:
                    reasoning_content = _extract_reasoning_content_from_tags(response_msg.content or "")
                response_text = _strip_reasoning_from_content(
                    response_msg.content or "",
                    reasoning_content,
                )
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
                logger.error("[ToolCallingLoop] LLM call failed: %s", exc)
                return StepResult(
                    step_id=step_id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                    iterations_completed=iteration,
                )

            if not response_msg.tool_calls:
                if response_text.strip():
                    # Real answer after provider reasoning metadata was separated.
                    final_output = response_text
                elif reasoning_content and not response_text.strip():
                    # Reasoning-only round: no final answer yet.
                    logger.info("[ToolCallingLoop] Reasoning-only response, requesting explicit answer")
                    needs_explicit_answer = True
                    continue
                else:
                    # Truly empty response (edge case)
                    final_output = response_msg.content or "Task completed (no output)."
                    logger.info("[ToolCallingLoop] Completed in %d iterations", iteration)
                    if on_iteration:
                        on_iteration(iteration, tool_calls_log)
                    return StepResult(
                        step_id=step_id,
                        success=True,
                        output=final_output,
                        tool_calls_log=tool_calls_log,
                        iterations_completed=iteration,
                    )

                logger.info("[ToolCallingLoop] Completed in %d iterations", iteration)
                if on_iteration:
                    on_iteration(iteration, tool_calls_log)
                return StepResult(
                    step_id=step_id,
                    success=True,
                    output=final_output,
                    tool_calls_log=tool_calls_log,
                    iterations_completed=iteration,
                )

            # Execute structured Actions. Independent calls run concurrently via
            # shared tool-call execution so all tool-calling paths stay aligned.
            # + gather + result processing loop. Same behavior, single source.
            tool_messages = await execute_tool_calls(
                response_msg.tool_calls,
                self.tools,
                self.tool_router,
                node_id=str(step_id),
                agent_name=self.agent_name,
                truncation_limit=effective_policy.truncation_limit,
                tool_calls_log=tool_calls_log,
                log_prefix="ToolCallingLoop",
                policy=effective_policy,
                guardrail=self.guardrail,
                on_event=self._on_event,
            )
            messages.extend(tool_messages)

            # Handoff control transfer shared by both tool-calling variants.
            transfer = self._check_handoff_transfer(
                response_msg, step_id, tool_calls_log, iteration, on_iteration,
            )
            if transfer is not None:
                return transfer

            if on_iteration:
                on_iteration(iteration, tool_calls_log)

        logger.warning("[ToolCallingLoop] Hit max iterations (%d)", effective_max_iter)
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

    def set_allowed_tools(self, tool_names: list[str] | None) -> None:
        """Apply a skill-based tool filter. None restores the full set.
        应用基于技能的工具过滤。None 恢复完整集合。

        When tool_names is a non-empty list, restricts self.tools and
        self.tool_schemas to only those named tools that exist in the
        original full set (_tools_full). Unknown tool names are silently
        ignored (content authoring error, not a runtime error).

        When tool_names is None or empty, restores the full tool set
        from the backup made at construction time.

        Priority chain: ToolGuardrail > allowed-tools > default tool set.
        Guardrails are checked in tool_execution.execute_tool_calls() before
        tool execution, independently of this filter. So even if a tool is
        in allowed_tools, the guardrail can still BLOCK it.

        当 tool_names 为非空列表时，将 self.tools 和 self.tool_schemas
        限制为原始完整集合中存在的指定工具。未知工具名被静默忽略。

        当 tool_names 为 None 或空时，恢复构造时的完整工具集。

        优先级链：ToolGuardrail > allowed-tools > default tool set。
        护栏在 tool_execution.execute_tool_calls() 中工具执行前检查，
        独立于本过滤。因此即使工具在 allowed_tools 中，护栏仍可 BLOCK。
        """
        if tool_names:
            self.tools = {n: t for n, t in self._tools_full.items() if n in tool_names}
            self.tool_schemas = [t.to_openai_tool() for t in self.tools.values()]
        else:
            self.tools = dict(self._tools_full)
            self.tool_schemas = [t.to_openai_tool() for t in self.tools.values()]
