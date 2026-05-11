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
  2. while has_pending_todos and iteration < max_outer_iterations:
     - Select next ready TODO
     - think_with_tools() to reason + call tools
     - On success: mark TODO complete, optionally update TODO list
     - On failure: retry up to MAX_TODO_RETRIES, then mark BLOCKED
     - Stagnation detection: break if no TODOs complete for 3+ rounds
  3. Compile final answer from completed TODO results

v6.0: Optional ReActEngine integration via Feature Flag.
      Set ENABLE_REACT_ENGINE_V2=true to use the unified engine.
      Default: false (backward compatible).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import config as config_module
from agents.base import BaseAgent
from context.manager import ContextManager
from llm.client import LLMClient
from schema import StepResult, TodoItem, TodoList, TodoStatus, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)

EMERGENT_PLANNER_SYSTEM_PROMPT = """\
You are an autonomous task execution agent that follows the ReAct paradigm.

Your workflow for each TODO item:
1. Read the TODO description and reason about what to do
2. Select and call the appropriate tool with correct parameters
3. Observe the tool's output and decide next steps
4. Repeat until the TODO objective is met (stop calling tools when done)

Available tools will be provided via function calling. Use them wisely.
When you believe the overall task is complete, respond with a clear summary
of what was accomplished. Do NOT call any more tools once done.

IMPORTANT: The system manages a TODO list on your behalf. After each
execution step, the system may ask you to review and update the TODO
list. You can suggest new TODOs, modifications, or mark items as blocked
through your responses. Focus on executing each TODO with the tools available.
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
        max_outer_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
        use_react_engine: bool | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        super().__init__(
            name="EmergentPlanner",
            system_prompt=EMERGENT_PLANNER_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )
        self.tools = {t.name: t for t in tools}
        self.tool_schemas = [t.to_openai_tool() for t in tools]
        self.max_iterations = max_iterations or config_module.MAX_REACT_ITERATIONS
        self.max_outer_iterations = max_outer_iterations or config_module.MAX_EMERGENT_OUTER_ITERATIONS
        self.tool_router = tool_router or ToolRouter(available_tools=list(self.tools.keys()))
        self._on_event = on_event or (lambda *_: None)
        self._todo_list: TodoList | None = None

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
            logger.info("[EmergentPlanner] Using unified ReActEngine (v6.0)")
        else:
            logger.info("[EmergentPlanner] Using legacy _execute_todo implementation")

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
        prev_completed = 0
        stagnation = 0

        # 主循环：while(has_pending_todos)
        while self._todo_list.has_pending():
            iteration += 1
            self._emit("phase", f"Emergent planning iteration {iteration}...")

            # 检查是否超过最大迭代次数
            if iteration > self.max_outer_iterations:
                logger.warning("[EmergentPlanner] Hit max outer iterations (%d)", self.max_outer_iterations)
                break

            # 停滞检测：连续 N 轮无 COMPLETED 增量则提前退出
            if iteration > 5:
                cur_completed = sum(
                    1 for t in self._todo_list.todos.values()
                    if t.status == TodoStatus.COMPLETED
                )
                if cur_completed == prev_completed:
                    stagnation += 1
                else:
                    stagnation = 0
                prev_completed = cur_completed
                if stagnation > 3:
                    logger.warning("[EmergentPlanner] Planning stagnation detected (%d rounds), breaking", stagnation)
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

            # 为该 TODO 执行 ReAct 循环（含超时和异常保护）
            try:
                result = await asyncio.wait_for(
                    self._execute_todo(current_todo),
                    timeout=config_module.NODE_EXECUTION_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[EmergentPlanner] TODO %d timed out after %ds",
                    current_todo.id, config_module.NODE_EXECUTION_TIMEOUT,
                )
                result = StepResult(
                    step_id=current_todo.id, success=False,
                    output=f"TODO timed out after {config_module.NODE_EXECUTION_TIMEOUT}s",
                    tool_calls_log=[],
                )
            except Exception as exc:
                logger.error(
                    "[EmergentPlanner] TODO %d crashed: %s",
                    current_todo.id, exc, exc_info=True,
                )
                result = StepResult(
                    step_id=current_todo.id, success=False,
                    output=f"Unhandled exception: {exc}",
                    tool_calls_log=[],
                )
            all_results.append(result)

            # 更新 TODO 状态
            if result.success:
                self._todo_list.mark_completed(current_todo.id, result.output)
                self._emit("todo_complete", {"todo": current_todo, "result": result})
            else:
                current_todo.retry_count += 1
                max_retries = config_module.MAX_TODO_RETRIES
                if current_todo.retry_count >= max_retries:
                    logger.warning(
                        "[EmergentPlanner] TODO %d failed %d times, marking as BLOCKED: %s",
                        current_todo.id, current_todo.retry_count, result.output[:200]
                    )
                    self._todo_list.mark_blocked(current_todo.id)
                    self._emit("todo_blocked", {"todo": current_todo, "result": result})
                else:
                    logger.warning(
                        "[EmergentPlanner] TODO %d failed (retry %d/%d): %s",
                        current_todo.id, current_todo.retry_count, max_retries, result.output[:200]
                    )
                    self._todo_list.mark_pending(current_todo.id)
                    self._emit("todo_failed", {"todo": current_todo, "result": result})

            # 检查是否需要添加新 TODO（失败时必触发，每 3 步周期性 review 以保留涌现能力）
            should_update = (
                not result.success
                or not self._todo_list.get_ready_todos()
                or iteration % 3 == 0
            )
            if should_update:
                await self._update_todo_list(result)

            # 显示当前 TODO 列表状态
            self._emit("todo_list_update", self._get_todo_summary())

        # 汇总所有已完成 TODO 的结果
        final_answer = await self._compile_answer(task, all_results)
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
            logger.warning("[EmergentPlanner] Failed to parse initial TODOs: %s. Retrying...", exc)
            try:
                self.reset()
                data = await self.think_json(
                    prompt + "\n\nIMPORTANT: Respond with valid JSON only.",
                    temperature=0.1,
                )
                for todo_data in data.get("todos", []):
                    self._todo_list.add_todo(
                        description=todo_data.get("description", ""),
                        dependencies=todo_data.get("dependencies", []),
                    )
                self._emit("todo_list_initialized", self._get_todo_summary())
                return
            except Exception as retry_exc:
                logger.warning("[EmergentPlanner] Retry also failed: %s. Creating default.", retry_exc)
                fallback = self._todo_list.add_todo(description=f"Complete task: {task}")
                fallback.retry_count = config_module.MAX_TODO_RETRIES - 1  # 限制兜底 TODO 仅重试 1 次
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
        prompt = (
            f"Review the execution progress and determine if the TODO list needs updates.\n\n"
            f"Current task: {self._todo_list.task}\n\n"
            f"Last execution result:\n{last_result.output[:2000]}\n\n"
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
            f"  ],\n"
            f'  "modify_todos": [\n'
            f"    {{\n"
            f'      "id": 2,\n'
            f'      "description": "Updated description"\n'
            f"    }}\n"
            f"  ],\n"
            f'  "blocked_todos": [3, 4]\n'
            f"}}"
        )

        try:
            data = await self.think_json(prompt, temperature=0.3)
            if data.get("needs_update", False):
                # 处理新增 TODO
                new_todos = data.get("new_todos", [])
                if new_todos:
                    # 检查 TODO 数量限制
                    current_count = len(self._todo_list.todos)
                    max_todos = config_module.MAX_TODO_ITEMS

                    for todo_data in new_todos:
                        if current_count >= max_todos:
                            logger.warning(
                                "[EmergentPlanner] TODO list full (%d/%d), skipping new TODOs",
                                current_count, max_todos
                            )
                            break

                        # 修复 H3: 验证依赖ID存在
                        raw_deps = todo_data.get("dependencies", [])
                        valid_deps = [dep_id for dep_id in raw_deps if dep_id in self._todo_list.todos]
                        if raw_deps and not valid_deps:
                            logger.warning(
                                "[EmergentPlanner] Skipping TODO '%s' - all dependencies %s are invalid",
                                todo_data.get("description", "")[:50], raw_deps
                            )
                            continue
                        if not todo_data.get("description"):
                            continue  # 跳过空描述的TODO

                        try:
                            self._todo_list.add_todo(
                                description=todo_data.get("description", ""),
                                dependencies=valid_deps,
                            )
                        except ValueError as e:
                            logger.warning("[EmergentPlanner] Skipping: %s", e)
                            continue
                        current_count += 1
                        logger.info(
                            "[EmergentPlanner] Added new TODO: %s",
                            todo_data.get("description", "")[:100]
                        )

                # 处理 modify_todos（与 new_todos 并列，纯 modify 场景也能生效）
                for mod in data.get("modify_todos", []):
                    todo_id = mod.get("id")
                    if todo_id and todo_id in self._todo_list.todos:
                        todo_item = self._todo_list.todos[todo_id]
                        if todo_item.status == TodoStatus.COMPLETED:
                            continue
                        new_desc = mod.get("description")
                        if new_desc:
                            self._todo_list.todos[todo_id].description = new_desc
                            self._todo_list.todos[todo_id].updated_at = time.time()
                            logger.info(
                                "[EmergentPlanner] Modified TODO %d: %s",
                                todo_id, new_desc[:100]
                            )

                # 处理 blocked_todos（与 new_todos 并列，纯 blocked 场景也能生效）
                for todo_id in data.get("blocked_todos", []):
                    if todo_id in self._todo_list.todos:
                        self._todo_list.mark_blocked(todo_id)
                        logger.info("[EmergentPlanner] Blocked TODO %d", todo_id)

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

        This is similar in structure to ExecutorAgent's ReAct loop, but differs:
        (1) does NOT call self.reset() to preserve flat message history,
        (2) retry logic is handled at the TODO scheduling level.
        这与 ExecutorAgent 的 ReAct 循环结构类似，但有以下差异：
        (1) 不调用 self.reset()，保留扁平消息历史；
        (2) 重试逻辑在 TODO 调度层处理。

        v6.0: If ENABLE_REACT_ENGINE_V2=true, delegates to unified ReActEngine.
        """
        if todo.retry_count == 0:
            self.tool_router.reset_node(str(todo.id))

        separator = (
            f"--- Switching to TODO {todo.id}: {todo.description} ---\n\n"
            if todo.retry_count == 0 else ""
        )
        prompt = f"{separator}Execute the following TODO:\n\nTODO {todo.id}: {todo.description}"

        if todo.dependencies:
            dep_results = []
            for dep_id in todo.dependencies:
                dep_todo = self._todo_list.todos.get(dep_id)
                if dep_todo and dep_todo.result:
                    dep_results.append(f"[TODO {dep_id} result]:\n{dep_todo.result}")
            if dep_results:
                prompt += f"\n\nResults from dependencies:\n" + "\n".join(dep_results)

        logger.info("[EmergentPlanner] Executing TODO %d: %s", todo.id, todo.description[:100])
        self._todo_list.mark_in_progress(todo.id)

        if self._react_engine:
            result = await self._react_engine.execute(
                prompt=prompt,
                context="",
                node_id=str(todo.id),
                system_hint=EMERGENT_PLANNER_SYSTEM_PROMPT,
            )
            return result

        tool_calls_log: list[ToolCallRecord] = []
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            try:
                continue_msg = "Continue executing the TODO based on the tool results above."
                router_hint = self.tool_router.get_hint(str(todo.id))
                if router_hint:
                    continue_msg += f"\n\nIMPORTANT: {router_hint}"

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
                final_output = response_msg.content or "TODO completed (no output)."
                logger.info("[EmergentPlanner] TODO %d completed in %d iterations", todo.id, iteration)
                return StepResult(
                    step_id=todo.id,
                    success=True,
                    output=final_output,
                    tool_calls_log=tool_calls_log,
                )

            for tool_call in response_msg.tool_calls:
                func_name = tool_call.function.name
                func_args = self._parse_json(tool_call.function.arguments)
                if func_args is None:
                    func_args = {}

                logger.info("[EmergentPlanner] Tool call: %s(%s)", func_name, func_args)

                tool = self.tools.get(func_name)
                is_error = False
                if tool is None:
                    result = f"Error: Unknown tool '{func_name}'"
                    self.tool_router.record_failure(str(todo.id), func_name)
                    is_error = True
                else:
                    try:
                        result = await tool.traced_execute(**func_args)
                        self.tool_router.record_success(str(todo.id), func_name)
                    except Exception as exc:
                        result = f"Tool execution error: {exc}"
                        self.tool_router.record_failure(str(todo.id), func_name)
                        is_error = True

                tool_calls_log.append(ToolCallRecord(
                    tool_name=func_name,
                    parameters=func_args,
                    result=result if is_error else result[:1000],
                ))
                self.add_tool_result(tool_call.id, result)

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
    def _parse_json(text: str) -> dict[str, Any] | None:
        """Parse JSON string, handling markdown code blocks.
        解析 JSON 字符串，处理 Markdown 代码块。
        Delegates to LLMClient.parse_json(); returns None if result is not a dict.
        委托 LLMClient.parse_json() 解析；若结果非 dict 则返回 None。"""
        from llm.client import LLMClient
        try:
            result = LLMClient.parse_json(text)
            return result if isinstance(result, dict) else None
        except Exception:
            return None

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

    async def _compile_answer(self, task: str, results: list[StepResult]) -> str:
        """
        Compile results from all completed TODOs into final answer using LLM synthesis.
        使用 LLM 综合所有已完成 TODO 的结果为最终答案。
        """
        successful = [r for r in results if r.success]
        blocked_step_results = [r for r in results if not r.success]

        # 补充：收集无 StepResult 的 BLOCKED TodoItem
        blocked_todo_only = []
        if self._todo_list:
            blocked_todo_only = [
                t for t in self._todo_list.todos.values()
                if t.status == TodoStatus.BLOCKED
                and not any(r.step_id == t.id for r in blocked_step_results)
            ]

        if not successful and not blocked_step_results and not blocked_todo_only:
            return "No TODOs were processed."

        if not successful:
            all_blocked = blocked_step_results + [
                StepResult(step_id=t.id, success=False, output=t.result or f"BLOCKED: {t.description}", tool_calls_log=[])
                for t in blocked_todo_only
            ]
            blocked_summary = "\n".join(
                f"- TODO {r.step_id}: {r.output[:200]}" for r in all_blocked
            )
            return f"Unfortunately, all TODOs failed or were blocked:\n{blocked_summary}"

        results_summary = "\n".join(
            f"[TODO {r.step_id}]: {r.output}" for r in successful
        )
        all_blocked = blocked_step_results + [
            StepResult(step_id=t.id, success=False, output=t.result or f"BLOCKED: {t.description}", tool_calls_log=[])
            for t in blocked_todo_only
        ]
        if all_blocked:
            results_summary += "\n\nBlocked/failed TODOs:\n" + "\n".join(
                f"- TODO {r.step_id}: {r.output[:200]}" for r in all_blocked
            )

        try:
            synthesis = await self.think(
                f"Based on these execution results, provide a clear, concise "
                f"summary answering the original task: '{task}'\n\n"
                f"Results:\n{results_summary}"
            )
            return synthesis
        except Exception:
            parts = [f"[Result {i}]:\n{r.output}" for i, r in enumerate(successful, 1)]
            return "\n\n".join(parts)

    def _emit(self, event: str, data: Any = None) -> None:
        """
        Emit an event to the UI callback (if configured).
        向 UI 回调函数发送事件（如果已配置）。
        """
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[EmergentPlanner] UI callback error for event '%s'", event, exc_info=True)
