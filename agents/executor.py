"""
Executor Agent - Implements the ReAct (Reasoning + Acting) pattern.
Executor 智能体 —— 实现 ReAct（推理 + 行动）模式。

For each plan step/node, the executor runs a loop:
  1. Thought  - LLM reasons about what to do next
  2. Action   - LLM selects a tool and provides parameters
  3. Observe  - Tool is executed, result is fed back to the LLM
  4. Repeat until the step objective is met or max iterations reached

对每个计划步骤/节点，Executor 运行如下循环：
  1. Thought（思考）  - LLM 推理下一步应该做什么
  2. Action（行动）   - LLM 选择工具并提供参数（通过 function calling）
  3. Observe（观察）  - 工具执行，结果反馈给 LLM
  4. 重复直到完成步骤目标或达到最大迭代次数

Uses OpenAI-compatible function calling to let the LLM naturally select tools.
使用 OpenAI 兼容的 function calling 让 LLM 自然地选择和调用工具。

v2: Added execute_node() for DAG-based execution (TaskNode input).
    The core ReAct loop is shared between legacy and DAG paths.
v2: 新增 execute_node() 方法，用于 DAG 执行（接受 TaskNode 输入）。
    核心 ReAct 循环在旧版和 DAG 路径之间共用。

v6.0: Optional ReActEngine integration via Feature Flag.
      Set ENABLE_REACT_ENGINE_V2=true to use the unified engine.
      Default: false (backward compatible).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config as config_module
from agents.base import BaseAgent
from context.manager import ContextManager
from llm.client import LLMClient
from schema import Step, StepResult, TaskNode, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)

EXECUTOR_SYSTEM_PROMPT = """\
You are a task execution agent that follows the ReAct paradigm.

For each step you receive, you should:
1. THINK about what needs to be done and which tool to use.
2. ACT by calling the appropriate tool with correct parameters.
3. OBSERVE the tool's output.
4. REPEAT if needed, or provide a final answer.

When you have completed the step objective, respond with a clear summary of
what was accomplished. Do NOT call any more tools once the step is done.

Available tools will be provided via function calling. Use them wisely.
Be concise and focused on completing the step objective.
"""


class ExecutorAgent(BaseAgent):
    """
    ReAct executor that runs individual plan steps using tools.
    ReAct 执行智能体，使用工具逐步执行计划中的每个步骤/节点。

    The core loop:
      while not done:
          response = LLM(messages, tools)
          if response has tool_calls:
              for each tool_call:
                  result = tool.execute(**args)
                  record observation
          else:
              step is done, return final answer

    核心循环：
      while 未完成:
          response = LLM(消息历史, 工具列表)
          if response 包含 tool_calls:
              for 每个 tool_call:
                  result = tool.execute(**args)
                  记录 observation（工具结果）
          else:
              步骤完成，返回最终答案
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        max_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
        use_react_engine: bool | None = None,
    ):
        super().__init__(
            name="Executor",
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )
        self.tools = {t.name: t for t in tools}
        self.tool_schemas = [t.to_openai_tool() for t in tools]
        self.max_iterations = max_iterations or config_module.MAX_REACT_ITERATIONS
        self.tool_router = tool_router or ToolRouter(available_tools=list(self.tools.keys()))

        use_engine = use_react_engine if use_react_engine is not None else config_module.ENABLE_REACT_ENGINE_V2
        self._react_engine = None
        if use_engine:
            from react.engine import ReActEngine
            self._react_engine = ReActEngine(
                llm_client=llm_client,
                tools=self.tools,
                max_iterations=self.max_iterations,
                tool_router=self.tool_router,
            )
            logger.info("[Executor] Using unified ReActEngine (v6.0)")
        else:
            logger.info("[Executor] Using legacy _react_loop implementation")

    # ------------------------------------------------------------------
    # DAG execution entry point (v2)
    # DAG 执行入口（v2 新增）
    # ------------------------------------------------------------------

    async def execute_node(self, node: TaskNode, context: str = "") -> StepResult:
        """
        Execute a single DAG TaskNode using the ReAct loop.
        使用 ReAct 循环执行单个 DAG TaskNode。

        This is the v2 entry point used by DAGExecutor. It reads context
        from DAGState (passed as a string) and returns a StepResult.
        The caller (DAGExecutor) writes the result back into DAGState.

        这是 v2 路径的入口，由 DAGExecutor 调用。
        从 DAGState 中获取上下文（以字符串传入），执行后返回 StepResult。
        调用方（DAGExecutor）负责将结果写回 DAGState。

        Args:
            node:    要执行的 TaskNode（必须是 ACTION 类型）。
            context: 由 DAGState.get_node_context() 构建的上下文字符串。

        Returns:
            StepResult：包含成功状态、输出文本和工具调用日志。
        """
        prompt = f"Execute the following action:\n\nAction {node.id}: {node.description}"
        if node.exit_criteria and node.exit_criteria.description:
            prompt += f"\n\nSuccess criteria: {node.exit_criteria.description}"

        if self._react_engine:
            return await self._react_engine.execute(
                prompt=prompt,
                context=context,
                node_id=node.id,
                system_hint=EXECUTOR_SYSTEM_PROMPT,
            )
        return await self._react_loop(node.id, prompt, context)

    # ------------------------------------------------------------------
    # Legacy execution entry point (v1)
    # 旧版执行入口（v1）
    # ------------------------------------------------------------------

    async def execute_step(self, step: Step, context: str = "") -> StepResult:
        """
        Execute a single plan step using the ReAct loop.
        使用 ReAct 循环执行单个计划步骤（旧版 v1 接口，保留向后兼容）。

        v6.0: If ENABLE_REACT_ENGINE_V2=true, delegates to unified ReActEngine.
        """
        prompt = f"Execute the following step:\n\nStep {step.id}: {step.description}"

        if self._react_engine:
            return await self._react_engine.execute(
                prompt=prompt,
                context=context,
                node_id=str(step.id),
                system_hint=EXECUTOR_SYSTEM_PROMPT,
            )
        return await self._react_loop(step.id, prompt, context)

    # ------------------------------------------------------------------
    # Shared ReAct loop
    # 共用 ReAct 循环（v1 和 v2 共享）
    # ------------------------------------------------------------------

    async def _react_loop(
        self,
        step_id: int | str,
        prompt: str,
        context: str = "",
    ) -> StepResult:
        """
        Core ReAct (Reasoning + Acting) loop shared by both v1 and v2 paths.
        v1 和 v2 共用的核心 ReAct（推理 + 行动）循环。

        Loop:
          1. LLM reasons about what to do (with tool schemas)
          2. If tool_calls: execute tools, feed results back
          3. If no tool_calls: LLM is done, return final answer
          4. Repeat up to max_iterations

        循环流程：
          1. LLM 推理下一步操作（携带工具 Schema 发送请求）
          2. 若有 tool_calls：执行工具，将结果作为 observation 反馈给 LLM
          3. 若无 tool_calls：LLM 认为任务已完成，返回最终答案
          4. 重复直到达到 max_iterations

        v3: Integrated ToolRouter for failure-based tool switching hints.
        v3: 集成 ToolRouter，在工具连续失败时向 LLM 提供切换建议。
        """
        self.reset()  # 清空历史消息，避免上一个步骤的历史干扰
        node_id = str(step_id)
        self.tool_router.reset_node(node_id)  # v3: 清除该节点的工具路由统计

        if context:
            # 将依赖节点的结果作为背景上下文注入 prompt
            prompt += f"\n\nContext from previous steps:\n{context}"

        tool_calls_log: list[ToolCallRecord] = []
        iteration = 0

        logger.info("[Executor] Starting %s: %s", step_id, prompt[:100])

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug("[Executor] ReAct iteration %d/%d", iteration, self.max_iterations)

            try:
                # v3: 检查工具路由器是否有切换建议
                continue_msg = "Continue executing the step based on the tool results above."
                router_hint = self.tool_router.get_hint(node_id)
                if router_hint and iteration > 1:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

                # 第一轮发送完整 prompt，后续轮次告知 LLM 继续基于工具结果执行
                response_msg = await self.think_with_tools(
                    prompt if iteration == 1 else continue_msg,
                    tools=self.tool_schemas,
                    temperature=0.5,
                )
            except Exception as exc:
                logger.error("[Executor] LLM call failed: %s", exc)
                return StepResult(
                    step_id=step_id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                )

            if not response_msg.tool_calls:
                # LLM 没有发起工具调用，说明认为步骤已完成，返回最终文本
                final_output = response_msg.content or "Step completed (no output)."
                logger.info("[Executor] %s completed in %d iterations", step_id, iteration)
                return StepResult(
                    step_id=step_id,
                    success=True,
                    output=final_output,
                    tool_calls_log=tool_calls_log,
                )

            # LLM 发起了工具调用，依次执行并记录结果（Observe 步骤）
            has_error = False
            for tool_call in response_msg.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                logger.info("[Executor] Tool call: %s(%s)", func_name, func_args)

                tool = self.tools.get(func_name)
                if tool is None:
                    result = f"Error: Unknown tool '{func_name}'"
                    self.tool_router.record_failure(node_id, func_name)
                    has_error = True
                else:
                    try:
                        result = await tool.execute(**func_args)
                        self.tool_router.record_success(node_id, func_name)
                    except Exception as exc:
                        result = f"Error: Tool execution error: {exc}"
                        self.tool_router.record_failure(node_id, func_name)
                        has_error = True

                # 修复 Critical #2: 检测 Error 字符串
                # 如果工具返回以 "Error:" 开头的字符串，标记为失败
                if isinstance(result, str) and result.startswith("Error:"):
                    has_error = True

                # 记录工具调用详情（用于 UI 展示）
                tool_calls_log.append(ToolCallRecord(
                    tool_name=func_name,
                    parameters=func_args,
                    result=result[:1000],
                ))
                # 将工具结果以 tool 角色加入消息历史，供 LLM 下一轮观察
                # 如果检测到错误，在结果中添加明确的失败标记，引导 LLM 停止执行
                if has_error:
                    result_with_marker = f"[TOOL ERROR] {result}\n\nIMPORTANT: The tool returned an error. Please analyze the error and decide whether to retry with different parameters or report the failure."
                else:
                    result_with_marker = result
                self.add_tool_result(tool_call.id, result_with_marker)

        # 超过最大迭代次数，返回失败
        logger.warning("[Executor] %s hit max iterations (%d)", step_id, self.max_iterations)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"Step did not complete within {self.max_iterations} iterations.",
            tool_calls_log=tool_calls_log,
        )
