"""
Emergent Planner Agent - Claude Code-style implicit planning via while(tool_use) loop.
Emergent Planner 智能体 —— 通过 while(tool_use) 主循环实现隐式涌现规划。

Unlike the explicit DAG planner (v2) that generates a complete plan upfront,
this agent follows Claude Code's philosophy:
  - No independent planning phase
  - Planning emerges naturally through TODO list management
  - Single flat message history (all tool calls and results in one context)
  - Dynamic TODO creation, update, and completion during execution

与 v2 显式 DAG 规划器（预先完整规划）不同，
该智能体遵循 Claude Code 的设计哲学：
  - 无独立规划阶段
  - 规划通过 TODO 列表管理自然涌现
  - 单一扁平消息历史（所有工具调用和结果在同一上下文中）
  - 执行过程中动态创建、更新、完成 TODO

Core loop:
核心循环：
  1. Initialize TODO list from task (1-3 items)
  2. while has_pending_todos:
     - Select next ready TODO
     - think_with_tools() to reason + call tools
     - Update TODO list (mark complete, add new discoveries)
     - Check if all TODOs done
  3. Compile final answer from completed TODO results
"""

from __future__ import annotations

import logging
from typing import Any

import config
from agents.base import BaseAgent
from context.manager import ContextManager
from llm.client import LLMClient
from schema import StepResult, TodoItem, TodoList, TodoStatus, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)

EMERGENT_PLANNER_SYSTEM_PROMPT = """\
You are an autonomous task execution agent that follows the ReAct paradigm.

You manage a TODO list that tracks what needs to be done. Your workflow:
1. Review the current TODO list and select the next actionable item
2. Reason about what to do and which tool to use
3. Call the appropriate tool with correct parameters
4. Observe the tool's output and record the result
5. Mark the TODO as completed or update it based on progress
6. Add new TODOs if you discover additional work is needed
7. Repeat until all TODOs are completed

Available tools will be provided via function calling. Use them wisely.
When you believe the overall task is complete, respond with a clear summary
of what was accomplished. Do NOT call any more tools once done.

IMPORTANT: You can dynamically modify the TODO list during execution:
- Add new TODOs when you discover additional work
- Mark TODOs as completed when their objectives are met
- Update TODO descriptions if the goal changes
"""


class EmergentPlannerAgent(BaseAgent):
    """
    Claude Code-style emergent planner with a while(tool_use) main loop.
    Claude Code 风格的隐式规划器，具有 while(tool_use) 主循环。

    Key characteristics:
    - No pre-defined plan structure
    - TODO list evolves dynamically during execution
    - Single flat message history (all tool calls visible to LLM)
    - LLM self-organizes through natural language reasoning

    关键特征：
    - 无预定义的计划结构
    - TODO 列表在执行过程中动态演化
    - 单一扁平消息历史（LLM 可见所有工具调用）
    - LLM 通过自然语言推理自组织
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        max_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
    ):
        super().__init__(
            name="EmergentPlanner",
            system_prompt=EMERGENT_PLANNER_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )
        self.tools = {t.name: t for t in tools}              # 工具名 -> 工具实例
        self.tool_schemas = [t.to_openai_tool() for t in tools]  # OpenAI function calling 格式
        self.max_iterations = max_iterations or __import__("config").MAX_REACT_ITERATIONS
        self.tool_router = tool_router or ToolRouter(available_tools=list(self.tools.keys()))
        self._todo_list: TodoList | None = None              # 当前任务的 TODO 列表

    # ------------------------------------------------------------------
    # Main entry point
    # 主入口
    # ------------------------------------------------------------------

    async def execute(self, task: str, context: str = "") -> str:
        """
        Claude Code-style emergent planning and execution.

        Flow:
          1. Initialize TODO list from task description
          2. while has_pending_todos:
             - Select next ready TODO
             - Run ReAct loop for that TODO
             - Update TODO list based on progress
             - Add new TODOs if discovered
          3. Compile final answer from all completed TODOs

        流程：
          1. 从任务描述初始化 TODO 列表
          2. 当有待执行 TODO 时循环：
             - 选择下一个就绪 TODO
             - 为该 TODO 运行 ReAct 循环
             - 根据进度更新 TODO 列表
             - 发现新工作时添加 TODO
          3. 从所有已完成的 TODO 汇总最终答案
        """
        self._emit("phase", "Initializing emergent planning...")

        # 初始化 TODO 列表
        self._todo_list = TodoList(task=task)
        await self._init_todo_list(task, context)

        iteration = 0
        all_results: list[StepResult] = []

        # 主循环：while(has_pending_todos)
        while self._todo_list.has_pending():
            iteration += 1
            self._emit("phase", f"Emergent planning iteration {iteration}...")

            # 检查是否超过最大迭代次数
            if iteration > self.max_iterations:
                logger.warning("[EmergentPlanner] Hit max iterations (%d)", self.max_iterations)
                break

            # 选择下一个就绪 TODO
            ready_todos = self._todo_list.get_ready_todos()
            if not ready_todos:
                # 没有就绪 TODO 但还有待执行的 -> 有阻塞
                logger.warning(
                    "[EmergentPlanner] No ready TODOs but %d pending. Blocked?",
                    len([t for t in self._todo_list.todos.values() if t.status == TodoStatus.PENDING])
                )
                # 强制选择一个 PENDING 的 TODO
                pending = [t for t in self._todo_list.todos.values() if t.status == TodoStatus.PENDING]
                if pending:
                    ready_todos = [pending[0]]
                else:
                    break

            # 选择第一个就绪 TODO
            current_todo = ready_todos[0]
            self._emit("todo_start", {"todo": current_todo})

            # 为该 TODO 执行 ReAct 循环
            result = await self._execute_todo(current_todo)
            all_results.append(result)

            # 更新 TODO 状态
            if result.success:
                self._todo_list.mark_completed(current_todo.id, result.output)
                self._emit("todo_complete", {"todo": current_todo, "result": result})
            else:
                # 执行失败，标记为 PENDING 以便重试
                logger.warning("[EmergentPlanner] TODO %d failed: %s", current_todo.id, result.output[:200])
                self._emit("todo_failed", {"todo": current_todo, "result": result})

            # 检查是否需要添加新 TODO（基于执行结果）
            await self._update_todo_list(result)

            # 显示当前 TODO 列表状态
            self._emit("todo_list_update", self._get_todo_summary())

        # 汇总所有已完成 TODO 的结果
        final_answer = self._compile_answer(task, all_results)
        self._emit("phase", "Emergent planning completed.")
        return final_answer

    # ------------------------------------------------------------------
    # TODO list management
    # TODO 列表管理
    # ------------------------------------------------------------------

    async def _init_todo_list(self, task: str, context: str) -> None:
        """
        Initialize the TODO list from the task description.
        从任务描述初始化 TODO 列表（1-3 个初始项）。

        This is a lightweight planning step - we don't generate a complete
        DAG, just a few high-level TODOs to get started.
        这是一个轻量级规划步骤——不生成完整 DAG，只创建几个高层 TODO 来启动。
        """
        self.reset()

        prompt = (
            f"Initialize a TODO list for this task. Create 1-3 high-level TODO items "
            f"to get started. We will add more during execution if needed.\n\n"
            f"Task: {task}\n\n"
            f"Respond with JSON:\n"
            f"{{\n"
            f'  "todos": [\n'
            f"    {{\n"
            f'      "description": "First TODO item",\n'
            f'      "dependencies": []  // list of prerequisite TODO IDs (empty for initial items)\n'
            f"    }}\n"
            f"  ]\n"
            f"}}"
        )

        if context:
            prompt += f"\n\nContext:\n{context}"

        try:
            data = await self.think_json(prompt, temperature=0.3)
            for todo_data in data.get("todos", []):
                self._todo_list.add_todo(
                    description=todo_data.get("description", ""),
                    dependencies=todo_data.get("dependencies", []),
                )

            logger.info(
                "[EmergentPlanner] Initialized TODO list with %d items",
                len(self._todo_list.todos)
            )
            self._emit("todo_list_initialized", self._get_todo_summary())

        except Exception as exc:
            logger.warning("[EmergentPlanner] Failed to parse initial TODOs: %s. Creating default.", exc)
            # 降级处理：创建一个默认 TODO
            self._todo_list.add_todo(description=f"Complete task: {task}")
            self._emit("todo_list_initialized", self._get_todo_summary())

    async def _update_todo_list(self, last_result: StepResult) -> None:
        """
        Update the TODO list based on execution progress.
        根据执行进度更新 TODO 列表。

        This is where planning "emerges" - the LLM can:
        - Add new TODOs when it discovers additional work
        - Modify existing TODO descriptions
        - Mark TODOs as blocked if dependencies are not met

        这就是规划「涌现」的地方——LLM 可以：
        - 发现新工作时添加 TODO
        - 修改现有 TODO 描述
        - 在依赖未满足时将 TODO 标记为阻塞
        """
        self.reset()

        prompt = (
            f"Review the execution progress and determine if the TODO list needs updates.\n\n"
            f"Current task: {self._todo_list.task}\n\n"
            f"Last execution result:\n{last_result.output[:500]}\n\n"
            f"Current TODO list:\n{self._get_todo_summary()}\n\n"
            f"Do you need to:\n"
            f"- Add new TODOs (discovered additional work)?\n"
            f"- Modify existing TODO descriptions?\n"
            f"- Mark any TODOs as blocked?\n\n"
            f"Respond with JSON:\n"
            f"{{\n"
            f'  "needs_update": true/false,\n'
            f'  "reason": "Why update is or is not needed",\n'
            f'  "new_todos": [\n'
            f"    {{\n"
            f'      "description": "New TODO description",\n'
            f'      "dependencies": [1, 2]  // IDs of prerequisite TODOs\n'
            f"    }}\n"
            f"  ]\n"
            f"}}"
        )

        try:
            data = await self.think_json(prompt, temperature=0.3)
            if data.get("needs_update", False):
                new_todos = data.get("new_todos", [])
                if new_todos:
                    # 检查 TODO 数量限制
                    current_count = len(self._todo_list.todos)
                    max_todos = config.MAX_TODO_ITEMS if hasattr(config, 'MAX_TODO_ITEMS') else 20

                    for todo_data in new_todos:
                        if current_count >= max_todos:
                            logger.warning(
                                "[EmergentPlanner] TODO list full (%d/%d), skipping new TODOs",
                                current_count, max_todos
                            )
                            break

                        self._todo_list.add_todo(
                            description=todo_data.get("description", ""),
                            dependencies=todo_data.get("dependencies", []),
                        )
                        current_count += 1
                        logger.info(
                            "[EmergentPlanner] Added new TODO: %s",
                            todo_data.get("description", "")[:100]
                        )

        except Exception as exc:
            logger.warning("[EmergentPlanner] Failed to update TODO list: %s", exc)

    # ------------------------------------------------------------------
    # TODO execution
    # TODO 执行
    # ------------------------------------------------------------------

    async def _execute_todo(self, todo: TodoItem) -> StepResult:
        """
        Execute a single TODO using the ReAct loop.
        使用 ReAct 循环执行单个 TODO。

        This is similar to ExecutorAgent's execute_node(), but integrated
        into the emergent planning flow.
        这类似于 ExecutorAgent 的 execute_node()，但集成在隐式规划流程中。
        """
        self.tool_router.reset_node(str(todo.id))

        # 构建 TODO 的执行 prompt
        prompt = f"Execute the following TODO:\n\nTODO {todo.id}: {todo.description}"

        # 添加依赖结果作为上下文
        if todo.dependencies:
            dep_results = []
            for dep_id in todo.dependencies:
                dep_todo = self._todo_list.todos.get(dep_id)
                if dep_todo and dep_todo.result:
                    dep_results.append(f"[TODO {dep_id} result]:\n{dep_todo.result}")
            if dep_results:
                prompt += f"\n\nResults from dependencies:\n" + "\n".join(dep_results)

        tool_calls_log: list[ToolCallRecord] = []
        iteration = 0

        logger.info("[EmergentPlanner] Executing TODO %d: %s", todo.id, todo.description[:100])
        self._todo_list.mark_in_progress(todo.id)

        while iteration < self.max_iterations:
            iteration += 1

            try:
                # 检查工具路由器是否有切换建议
                continue_msg = "Continue executing the TODO based on the tool results above."
                router_hint = self.tool_router.get_hint(str(todo.id))
                if router_hint and iteration > 1:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

                # 第一轮发送完整 prompt，后续轮次告知继续
                response_msg = await self.think_with_tools(
                    prompt if iteration == 1 else continue_msg,
                    tools=self.tool_schemas,
                    temperature=0.5,
                )
            except Exception as exc:
                logger.error("[EmergentPlanner] LLM call failed: %s", exc)
                return StepResult(
                    step_id=todo.id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                )

            if not response_msg.tool_calls:
                # LLM 认为 TODO 已完成，返回最终文本
                final_output = response_msg.content or "TODO completed (no output)."
                logger.info("[EmergentPlanner] TODO %d completed in %d iterations", todo.id, iteration)
                return StepResult(
                    step_id=todo.id,
                    success=True,
                    output=final_output,
                    tool_calls_log=tool_calls_log,
                )

            # 执行工具调用
            for tool_call in response_msg.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = self._parse_json(tool_call.function.arguments)
                except Exception:
                    func_args = {}

                logger.info("[EmergentPlanner] Tool call: %s(%s)", func_name, func_args)

                tool = self.tools.get(func_name)
                if tool is None:
                    result = f"Error: Unknown tool '{func_name}'"
                    self.tool_router.record_failure(str(todo.id), func_name)
                else:
                    try:
                        result = await tool.execute(**func_args)
                        self.tool_router.record_success(str(todo.id), func_name)
                    except Exception as exc:
                        result = f"Tool execution error: {exc}"
                        self.tool_router.record_failure(str(todo.id), func_name)

                # 记录工具调用
                tool_calls_log.append(ToolCallRecord(
                    tool_name=func_name,
                    parameters=func_args,
                    result=result[:1000],
                ))
                self.add_tool_result(tool_call.id, result)

        # 超过最大迭代次数
        logger.warning("[EmergentPlanner] TODO %d hit max iterations (%d)", todo.id, self.max_iterations)
        return StepResult(
            step_id=todo.id,
            success=False,
            output=f"TODO did not complete within {self.max_iterations} iterations.",
            tool_calls_log=tool_calls_log,
        )

    # ------------------------------------------------------------------
    # Helpers
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Parse JSON string, handling common issues."""
        import json
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试去除首尾的 markdown 代码块标记
        if text.startswith("```"):
            text = text.split("```", 1)[1]
            if "```" in text:
                text = text.rsplit("```", 1)[0]
            text = text.strip()
        return json.loads(text)

    def _get_todo_summary(self) -> str:
        """
        Get a human-readable summary of the current TODO list.
        获取当前 TODO 列表的人类可读摘要。
        """
        if not self._todo_list:
            return "No TODO list"

        lines = []
        for todo_id in sorted(self._todo_list.todos.keys()):
            todo = self._todo_list.todos[todo_id]
            status_icon = {
                TodoStatus.PENDING: "⏳",
                TodoStatus.IN_PROGRESS: "🔄",
                TodoStatus.COMPLETED: "✅",
                TodoStatus.BLOCKED: "🚫",
            }.get(todo.status, "?")
            deps = f" (deps: {todo.dependencies})" if todo.dependencies else ""
            lines.append(f"{status_icon} TODO {todo_id}: {todo.description}{deps}")

        return "\n".join(lines)

    def _compile_answer(self, task: str, results: list[StepResult]) -> str:
        """
        Compile results from all completed TODOs into final answer.
        将所有已完成 TODO 的结果汇总为最终答案。
        """
        successful = [r for r in results if r.success]
        if not successful:
            return "Unfortunately, no TODOs were completed successfully."

        # 简单汇总所有结果
        parts = []
        for i, result in enumerate(successful, 1):
            parts.append(f"[Result {i}]:\n{result.output}")

        return "\n\n".join(parts)

    def _emit(self, event: str, data: Any = None) -> None:
        """
        Emit an event to the UI callback (if configured).
        向 UI 回调函数发送事件（如果已配置）。
        """
        # Note: EmergentPlanner doesn't have direct access to on_event callback
        # It's typically handled by the caller (Orchestrator)
        # TODO: Consider passing callback through constructor if needed
        pass
