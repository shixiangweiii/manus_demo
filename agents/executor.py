"""
Executor Agent - Implements the ReAct (Reasoning + Acting) pattern.
Executor 智能体 —— 实现 ReAct（推理 + 行动）模式。

For each plan step/node, the executor delegates to the unified ReActEngine:
对每个计划步骤/节点，Executor 委托给统一的 ReActEngine 执行：
  1. Thought（思考）  - LLM 推理下一步应该做什么
  2. Action（行动）   - LLM 选择工具并提供参数（通过 function calling）
  3. Observe（观察）  - 工具执行，结果反馈给 LLM
  4. 重复直到完成步骤目标或达到最大迭代次数

v2: 新增 execute_node() 方法，用于 DAG 执行（接受 TaskNode 输入）。
v6.0: 引入 ReActEngine 作为统一 ReAct 实现（feature flag）。
v12: 移除 legacy `_react_loop`，统一委托 ReActEngine。
     `use_react_engine` 参数保留但 deprecated，仅向后兼容；
     `config.ENABLE_REACT_ENGINE_V2` 不再影响行为。
"""

from __future__ import annotations

import logging

import config as config_module
from agents.base import BaseAgent
from context.manager import ContextManager
from llm.client import LLMClient
from schema import Step, StepResult, TaskNode
from tools.base import BaseTool
from tools.router import ToolRouter

from agents.prompt_utils import build_system_prompt

logger = logging.getLogger(__name__)

_EXECUTOR_BASE_PROMPT = """\
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

LANGUAGE: Match the language of the user's task / step description in your
responses.
- If the step description is in Chinese, respond in Chinese.
- If in English, respond in English.
This applies to ALL of: thinking, summaries, and step completion reports.
Tool parameters (search queries, code, shell commands) can be in any
language as needed for the underlying API.
"""

EXECUTOR_SYSTEM_PROMPT = build_system_prompt(_EXECUTOR_BASE_PROMPT)


class ExecutorAgent(BaseAgent):
    """
    ReAct executor that runs individual plan steps using the unified ReActEngine.
    ReAct 执行智能体，委托给统一 ReActEngine 执行计划步骤/节点。

    v12: legacy `_react_loop` 已移除；始终走 ReActEngine。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        max_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
        use_react_engine: bool | None = None,  # deprecated, accepted for backward compat
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

        from react.engine import ReActEngine
        self._react_engine = ReActEngine(
            llm_client=llm_client,
            tools=self.tools,
            max_iterations=self.max_iterations,
            tool_router=self.tool_router,
            context_manager=self.context_manager,
        )
        logger.info("[Executor] Using unified ReActEngine")

    def create_for_node(self, node_id: str) -> ExecutorAgent:
        """
        Create a fresh ExecutorAgent instance for parallel node execution.
        Shares immutable resources (LLMClient, tools, schemas, ContextManager,
        ToolRouter, ReActEngine) but has an independent _messages list.

        为并行节点执行创建独立的 ExecutorAgent 实例。
        共享不可变资源（LLMClient、工具、Schema、ContextManager、ToolRouter、ReActEngine），
        但拥有独立的 _messages 消息历史列表，从根本上隔离并发竞态。
        """
        new_executor = ExecutorAgent.__new__(ExecutorAgent)
        new_executor.name = f"Executor-{node_id}"
        new_executor.system_prompt = self.system_prompt
        new_executor.llm_client = self.llm_client
        new_executor.context_manager = self.context_manager
        new_executor._messages = [{"role": "system", "content": self.system_prompt}]
        new_executor.tools = self.tools
        new_executor.tool_schemas = self.tool_schemas
        new_executor.max_iterations = self.max_iterations
        new_executor.tool_router = self.tool_router
        new_executor._react_engine = self._react_engine
        return new_executor

    # ------------------------------------------------------------------
    # DAG execution entry point (v2)
    # DAG 执行入口（v2 新增）
    # ------------------------------------------------------------------

    async def execute_node(self, node: TaskNode, context: str = "") -> StepResult:
        """
        Execute a single DAG TaskNode via the unified ReActEngine.
        通过统一 ReActEngine 执行单个 DAG TaskNode。

        这是 v2 路径的入口，由 DAGExecutor 调用。
        从 DAGState 中获取上下文（以字符串传入），执行后返回 StepResult。
        调用方（DAGExecutor）负责将结果写回 DAGState。
        """
        prompt = f"Execute the following action:\n\nAction {node.id}: {node.description}"
        if node.exit_criteria and node.exit_criteria.description:
            prompt += f"\n\nSuccess criteria: {node.exit_criteria.description}"

        return await self._react_engine.execute(
            prompt=prompt,
            context=context,
            node_id=node.id,
            system_hint=EXECUTOR_SYSTEM_PROMPT,
        )

    # ------------------------------------------------------------------
    # v1 entry point (flat plan)
    # v1 入口（扁平计划）
    # ------------------------------------------------------------------

    async def execute_step(self, step: Step, context: str = "") -> StepResult:
        """
        Execute a single plan step via the unified ReActEngine.
        通过统一 ReActEngine 执行单个计划步骤。
        """
        prompt = f"Execute the following step:\n\nStep {step.id}: {step.description}"
        self.tool_router.reset_node(str(step.id))
        return await self._react_engine.execute(
            prompt=prompt,
            context=context,
            node_id=str(step.id),
            system_hint=EXECUTOR_SYSTEM_PROMPT,
        )
