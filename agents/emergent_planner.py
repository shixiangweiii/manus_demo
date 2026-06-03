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
from schema import ReasoningEffort, StepResult, TodoItem, TodoList, TodoStatus, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

from agents.prompt_utils import (
    build_system_prompt,
    build_convergence_hint,
    get_emergent_parallel_guidance,
)
from react.engine_helpers import ToolExecutionPolicy, execute_tool_calls
from react.tool_call_helpers import attribute_caller, classify_result

logger = logging.getLogger(__name__)

_EMERGENT_BASE_PROMPT = """\
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

# Wave-2: prompt built per-instance in __init__ (see ExecutorAgent for rationale).


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
        # Wave-2: build prompt per-instance, fresh date + HITL gating respected.
        # Append emergent parallel-dispatch guidance to the base prompt (no-op
        # unless EMERGENT_PARALLEL_TODOS + SUBAGENT_ENABLED) so independent
        # subjects stay as dependency-free TODOs the scheduler can fan out.
        super().__init__(
            name="EmergentPlanner",
            system_prompt=build_system_prompt(
                _EMERGENT_BASE_PROMPT + get_emergent_parallel_guidance()
            ),
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
        self._current_effort: ReasoningEffort = ReasoningEffort.MEDIUM

        use_engine = use_react_engine if use_react_engine is not None else config_module.ENABLE_REACT_ENGINE_V2
        self._react_engine = None
        if use_engine:
            from react.engine import ReActEngine
            self._react_engine = ReActEngine(
                llm_client=llm_client,
                tools=self.tools,
                max_iterations=self.max_iterations,
                tool_router=self.tool_router,
                context_manager=self.context_manager,
                agent_name="EmergentPlannerAgent",  # Wave C #7: dynamic SubAgent parent attribution
            )
            logger.info("[EmergentPlanner] Using unified ReActEngine (v6.0)")
        else:
            logger.info("[EmergentPlanner] Using legacy _execute_todo implementation")

    # ------------------------------------------------------------------
    # Main entry point
    # 主入口
    # ------------------------------------------------------------------

    async def execute(
        self,
        task: str,
        context: str = "",
        *,
        effort: ReasoningEffort | None = None,
        on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
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

        self._current_effort = effort or ReasoningEffort.MEDIUM

        # 初始化 TODO 列表
        self._todo_list = TodoList(task=task)
        await self._init_todo_list(task, context)

        return await self._run_emergent_loop(
            task=task,
            iteration=0,
            all_results=[],
            prev_completed=0,
            stagnation=0,
            on_checkpoint=on_checkpoint,
        )

    async def resume_execute(
        self,
        task: str,
        context: str,
        effort: ReasoningEffort,
        todo_list: TodoList,
        all_results: list[StepResult],
        iteration: int,
        stagnation_state: dict,
        on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """Resume emergent planning from a restored state (v14.5).
        从恢复的状态继续隐式规划。"""
        self._current_effort = effort
        self._todo_list = todo_list
        prev_completed = stagnation_state.get("prev_completed", 0)
        stagnation = stagnation_state.get("stagnation_rounds", 0)

        return await self._run_emergent_loop(
            task=task,
            iteration=iteration,
            all_results=all_results,
            prev_completed=prev_completed,
            stagnation=stagnation,
            on_checkpoint=on_checkpoint,
        )

    async def _run_emergent_loop(
        self,
        task: str,
        iteration: int,
        all_results: list[StepResult],
        prev_completed: int,
        stagnation: int,
        on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """Core emergent planning loop, shared by execute() and resume_execute().
        核心隐式规划主循环，execute() 和 resume_execute() 共享。"""
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

            # 决定本轮执行：并行波次（多个独立 ready TODO 并发委派给隔离 SubAgent）
            # 还是串行单 TODO（默认/回退）。
            # Decide this round: a PARALLEL WAVE (fan out independent ready TODOs to
            # isolated sub-agents) vs a single serial TODO (default / fallback).
            parallel_eligible = (
                config_module.EMERGENT_PARALLEL_TODOS
                and "subagent" in self.tools
                and len(ready_todos) >= 2
            )
            logger.info(
                "[EmergentPlanner] iteration %d: %d ready TODO(s) %s | mode=%s",
                iteration, len(ready_todos), [t.id for t in ready_todos],
                "PARALLEL" if parallel_eligible else "serial",
            )

            if parallel_eligible:
                wave_results = await self._execute_todos_parallel(ready_todos)
            else:
                # 串行单 TODO 路径（逻辑保持不变，带超时/异常保护）
                current_todo = ready_todos[0]
                self._emit("todo_start", {"todo": current_todo})
                result = await self._execute_todo_guarded(current_todo)
                wave_results = [(current_todo, result)]

            # 处理本轮（一个或多个 TODO）的结果与状态转移
            # Apply results + status transitions for every TODO executed this round.
            for todo, result in wave_results:
                all_results.append(result)
                if result.success:
                    self._todo_list.mark_completed(todo.id, result.output)
                    self._emit("todo_complete", {"todo": todo, "result": result})
                else:
                    todo.retry_count += 1
                    max_retries = config_module.MAX_TODO_RETRIES
                    if todo.retry_count >= max_retries:
                        logger.warning(
                            "[EmergentPlanner] TODO %d failed %d times, marking as BLOCKED: %s",
                            todo.id, todo.retry_count, result.output[:200]
                        )
                        self._todo_list.mark_blocked(todo.id)
                        self._emit("todo_blocked", {"todo": todo, "result": result})
                    else:
                        logger.warning(
                            "[EmergentPlanner] TODO %d failed (retry %d/%d): %s",
                            todo.id, todo.retry_count, max_retries, result.output[:200]
                        )
                        self._todo_list.mark_pending(todo.id)
                        self._emit("todo_failed", {"todo": todo, "result": result})

            # 聚合本轮结果，供涌现 review（_update_todo_list 仅取单个 result.output）
            # Aggregate the wave's results for emergent review.
            wave_any_failed = any(not r.success for _, r in wave_results)
            if len(wave_results) == 1:
                agg_result = wave_results[0][1]
            else:
                agg_output = "\n\n".join(
                    f"[TODO {todo.id}] {'OK' if r.success else 'FAILED'}: {r.output[:500]}"
                    for todo, r in wave_results
                )
                agg_result = StepResult(
                    step_id=wave_results[-1][0].id,
                    success=not wave_any_failed,
                    output=agg_output,
                    tool_calls_log=[],
                )

            # 检查是否需要添加新 TODO（失败时必触发，每 3 步周期性 review 以保留涌现能力）
            should_update = (
                wave_any_failed
                or not self._todo_list.get_ready_todos()
                or iteration % 3 == 0
            )
            if should_update:
                await self._update_todo_list(agg_result)

            self._emit_checkpoint(
                on_checkpoint,
                all_results=all_results,
                iteration=iteration,
                prev_completed=prev_completed,
                stagnation=stagnation,
            )

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

    def _emit_checkpoint(
        self,
        on_checkpoint: Callable[[dict[str, Any]], None] | None,
        *,
        all_results: list[StepResult],
        iteration: int,
        prev_completed: int,
        stagnation: int,
    ) -> None:
        """Emit a resume-safe checkpoint payload after a TODO boundary."""
        if on_checkpoint is None or self._todo_list is None:
            return
        current_completed = sum(
            1 for todo in self._todo_list.todos.values()
            if todo.status == TodoStatus.COMPLETED
        )
        on_checkpoint({
            "boundary": "after_todo",
            "committed_ids": [
                str(todo.id)
                for todo in self._todo_list.todos.values()
                if todo.status == TodoStatus.COMPLETED
            ],
            "todo_list": self._todo_list.model_dump(),
            "all_results": [result.model_dump() for result in all_results],
            "iteration": iteration,
            "stagnation_state": {
                "prev_completed": current_completed,
                "stagnation_rounds": stagnation,
            },
        })

    async def _init_todo_list(self, task: str, context: str) -> None:
        """
        Initialize the TODO list from the task description.
        从任务描述初始化 TODO 列表（1-3 个初始项）。

        This is a lightweight planning step - we don't generate a complete
        DAG, just a few high-level TODOs to get started.
        这是一个轻量级规划步骤——不生成完整 DAG，只创建几个高层 TODO 来启动。
        """
        self.reset()

        # When parallel dispatch is active, steer decomposition toward independent,
        # dependency-free TODOs so the scheduler can fan them out to sub-agents.
        # 并行派发开启时，引导把独立主题拆成无依赖 TODO，供调度层并发委派。
        parallel_rule = ""
        if config_module.EMERGENT_PARALLEL_TODOS and config_module.SUBAGENT_ENABLED:
            parallel_rule = (
                "5. PARALLELISM: If the task contains MULTIPLE INDEPENDENT "
                "subjects/subtasks (e.g. researching several distinct topics), "
                "create them as SEPARATE TODO items each with EMPTY dependencies "
                "so they can run in parallel. Do NOT merge independent subjects "
                "into a single TODO, and do NOT invent dependencies between "
                "subjects that don't actually depend on each other.\n\n"
            )

        prompt = (
            f"Initialize a TODO list for this task. Create 1-3 high-level TODO items "
            f"to get started. We will add more during execution if needed.\n\n"
            f"Task: {task}\n\n"
            f"CRITICAL RULES — anti-hallucination:\n"
            f"1. Do NOT invent default values for parameters the user did "
            f"not specify (location, time window, target service, account, "
            f"recipient, etc.). Phrases like \"如未指定，默认 X\" / "
            f"\"default to X\" are FORBIDDEN in TODO descriptions.\n"
            f"2. When required information is missing from the task, the "
            f"FIRST TODO must be a discovery step that obtains it via "
            f"available tools (do NOT bake an assumed value into a "
            f"downstream TODO).\n"
            f"3. Examples:\n"
            f"   - Task '明天天气怎么样' (no city specified) → first TODO "
            f"must be '识别用户当前所在城市', NOT '查询北京的天气'.\n"
            f"   - Task 'send a message' (no recipient) → first TODO must "
            f"be 'identify the recipient', NOT 'send to <some default>'.\n"
            f"4. Write TODO descriptions in the SAME language as the user's "
            f"task (Chinese task → Chinese descriptions; English task → "
            f"English descriptions).\n\n"
            f"{parallel_rule}"
            f"TODO IDs: items are numbered by their position in the "
            f"\"todos\" array starting at 1 (first item = 1, second = 2, ...). "
            f'"dependencies" MUST be a list of these integer IDs ([] = no '
            f"prerequisites). Reference ONLY earlier items by their integer "
            f"number; never invent string IDs like \"todo_0\".\n"
            f"Respond with JSON:\n"
            f"{{\n"
            f'  "todos": [\n'
            f"    {{\n"
            f'      "description": "First TODO item",\n'
            f'      "dependencies": []\n'
            f"    }},\n"
            f"    {{\n"
            f'      "description": "Second TODO item that needs the first one done",\n'
            f'      "dependencies": [1]  // integer IDs of prerequisite TODOs\n'
            f"    }}\n"
            f"  ]\n"
            f"}}"
        )

        if context:
            prompt += f"\n\nContext:\n{context}"

        # review F-eval-2: a parseable-but-empty/wrong-shape response (e.g. {"todos": []}
        # or the list under another key) must NOT be silently accepted as 0 TODOs.
        # Try twice (shape-tolerant extraction), then fall back to a single whole-task
        # TODO — so emergent mode never spins on "0 TODOs processed".
        # 可解析但空/异形的响应不再被静默当成 0 个 TODO：两次尝试 + 单 TODO 兜底。
        attempts = [
            (prompt, 0.3),
            (prompt + "\n\nIMPORTANT: Respond with valid JSON only. Return at LEAST one TODO.", 0.1),
        ]
        for attempt_prompt, temp in attempts:
            todo_dicts: list[dict] = []
            try:
                self.reset()
                data = await self.think_json(attempt_prompt, temperature=temp)
                todo_dicts = self._extract_todo_dicts(data)
                if not todo_dicts:
                    logger.debug("[EmergentPlanner] TODO init returned no items; data=%s", str(data)[:300])
            except Exception as exc:
                logger.warning("[EmergentPlanner] Failed to parse initial TODOs: %s", exc)
            for td in todo_dicts:
                desc = (td.get("description") or "").strip()
                if not desc:
                    continue
                # 与 _update_todos / goal-driven 一致：先把依赖规范化为整数并过滤无效 ID，
                # 再 try/except 包住 add_todo（环检测会抛 ValueError），避免单条坏数据让整个进程崩溃。
                # mirror _update_todos: coerce deps to int + filter invalid before add_todo,
                # so a malformed dependency degrades gracefully instead of crashing init.
                deps = self._coerce_dep_ids(td.get("dependencies", []))
                valid_deps = [d for d in deps if d in self._todo_list.todos]
                try:
                    self._todo_list.add_todo(description=desc, dependencies=valid_deps)
                except ValueError as e:
                    logger.warning("[EmergentPlanner] Skipping initial TODO: %s", e)
                    continue
            if self._todo_list.todos:
                break  # got at least one actionable TODO

        if not self._todo_list.todos:
            logger.warning("[EmergentPlanner] TODO init empty after retries; using single-TODO fallback.")
            fallback = self._todo_list.add_todo(description=f"Complete task: {task}")
            fallback.retry_count = config_module.MAX_TODO_RETRIES - 1  # 兜底 TODO 仅重试 1 次

        logger.info("[EmergentPlanner] Initialized TODO list with %d items", len(self._todo_list.todos))
        self._emit("todo_list_initialized", self._get_todo_summary())

    @staticmethod
    def _coerce_dep_ids(raw_deps: Any) -> list[int]:
        """Coerce an LLM-provided dependency list into integer TODO IDs.
        把 LLM 返回的依赖列表规范化为整数 ID：

        - int → 保留
        - 纯数字字符串（如 "2"）→ int()
        - 其它（如 "todo_0"）→ 丢弃并 debug 记录（不猜测 0/1-based 偏移，
          正确性由 _init_todo_list 的 prompt 约定保证）。
        """
        if not isinstance(raw_deps, list):
            return []
        result: list[int] = []
        for dep in raw_deps:
            if isinstance(dep, bool):  # bool 是 int 子类，显式排除
                continue
            if isinstance(dep, int):
                result.append(dep)
            elif isinstance(dep, str) and dep.strip().lstrip("-").isdigit():
                result.append(int(dep.strip()))
            else:
                logger.debug("[EmergentPlanner] Dropping unparseable dependency id: %r", dep)
        return result

    @staticmethod
    def _extract_todo_dicts(data: Any) -> list[dict]:
        """Shape-tolerant extraction of TODO dicts from an LLM JSON response.
        容错抽取 TODO 列表：兼容 {"todos":[...]} / 裸 list / {"plan"|"items"|"steps"|"tasks":[...]} / 首个 list-of-dict 值。"""
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if not isinstance(data, dict):
            return []
        for key in ("todos", "plan", "items", "steps", "tasks"):
            v = data.get(key)
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
        for v in data.values():  # last resort: first list-of-dict field
            if isinstance(v, list) and v and all(isinstance(d, dict) for d in v):
                return v
        return []

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
            # KNOWN LIMITATION: ReActEngine creates a fresh messages list for each
            # _execute_todo call, so cross-TODO context is lost. The legacy path
            # preserves flat message history across TODOs by NOT calling self.reset().
            # A future fix could populate the `context` parameter with prior TODO
            # results before calling ReActEngine.execute().
            result = await self._react_engine.execute(
                prompt=prompt,
                context="",
                node_id=str(todo.id),
                system_hint=self.system_prompt,
                effort=self._current_effort,
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

                # v11: Dynamic convergence guidance based on tool call frequency
                tool_call_counts: dict[str, int] = {}
                for tc in tool_calls_log:
                    tool_call_counts[tc.tool_name] = tool_call_counts.get(tc.tool_name, 0) + 1

                continue_msg += build_convergence_hint(tool_call_counts)

                response_msg = await self.think_with_tools(
                    prompt if iteration == 1 else continue_msg,
                    tools=self.tool_schemas,
                    temperature=0.3 if self._current_effort == ReasoningEffort.LOW
                    else 0.7 if self._current_effort == ReasoningEffort.HIGH
                    else config_module.REACT_TEMPERATURE,
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

            # Batch 4.1 DRY: shared execute_tool_calls replaces inline block.
            tool_messages = await execute_tool_calls(
                response_msg.tool_calls,
                self.tools,
                self.tool_router,
                node_id=str(todo.id),
                agent_name=self.name,
                truncation_limit=config_module.TOOL_RESULT_TRUNCATION_LIMIT,
                tool_calls_log=tool_calls_log,
                log_prefix="EmergentPlanner",
                policy=ToolExecutionPolicy.for_effort(self._current_effort),
                parse_args=self._parse_json_for_tool_args,
            )
            for msg in tool_messages:
                self.add_tool_result(msg["tool_call_id"], msg["content"])

        logger.warning("[EmergentPlanner] TODO %d hit max iterations (%d)", todo.id, self.max_iterations)
        return StepResult(
            step_id=todo.id,
            success=False,
            output=f"TODO did not complete within {self.max_iterations} iterations.",
            tool_calls_log=tool_calls_log,
        )

    async def _execute_todo_guarded(self, todo: TodoItem) -> StepResult:
        """Run a single TODO via the in-process ReAct loop with timeout/exception guard.
        带超时与异常保护地执行单个 TODO（进程内 ReAct 循环，串行路径与预算耗尽回退共用）。"""
        try:
            return await asyncio.wait_for(
                self._execute_todo(todo),
                timeout=config_module.NODE_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[EmergentPlanner] TODO %d timed out after %ds",
                todo.id, config_module.NODE_EXECUTION_TIMEOUT,
            )
            return StepResult(
                step_id=todo.id, success=False,
                output=f"TODO timed out after {config_module.NODE_EXECUTION_TIMEOUT}s",
                tool_calls_log=[],
            )
        except Exception as exc:
            logger.error(
                "[EmergentPlanner] TODO %d crashed: %s",
                todo.id, exc, exc_info=True,
            )
            return StepResult(
                step_id=todo.id, success=False,
                output=f"Unhandled exception: {exc}",
                tool_calls_log=[],
            )

    # ------------------------------------------------------------------
    # Parallel multi-agent dispatch (EMERGENT_PARALLEL_TODOS)
    # 并行多智能体派发（EMERGENT_PARALLEL_TODOS）
    # ------------------------------------------------------------------

    async def _execute_todos_parallel(
        self, ready_todos: list[TodoItem]
    ) -> list[tuple[TodoItem, StepResult]]:
        """Fan out independent ready TODOs concurrently to isolated sub-agents.

        Each TODO is delegated to the `subagent` tool (its own context,
        summary-only return), so they run in parallel WITHOUT sharing this
        agent's flat message history (which `_execute_todo` mutates in place).
        The wave size is capped by the remaining per-task SubAgent budget; any
        overflow is deferred to the next loop iteration.

        把相互独立的 ready TODO 一次性并发委派给隔离 SubAgent（各自上下文、摘要返回），
        从而避免共享本智能体的扁平消息历史。波次大小受 SubAgent 单任务预算裁剪，溢出顺延下轮。
        """
        subagent_tool = self.tools["subagent"]
        # 估算剩余 SubAgent 预算（属性缺失时回退为全量并发）
        max_calls = getattr(subagent_tool, "_max_calls", len(ready_todos))
        used_calls = getattr(subagent_tool, "_call_count", 0)
        budget = max(0, max_calls - used_calls)

        if budget <= 0:
            # 预算耗尽：回退单个串行执行，避免本轮空转或触发 cap Error
            logger.warning(
                "[EmergentPlanner] SubAgent budget exhausted (%d/%d used); "
                "falling back to serial execution for TODO %d",
                used_calls, max_calls, ready_todos[0].id,
            )
            self._emit("todo_start", {"todo": ready_todos[0]})
            result = await self._execute_todo_guarded(ready_todos[0])
            return [(ready_todos[0], result)]

        wave = ready_todos[:budget]
        deferred = ready_todos[budget:]
        logger.info(
            "[EmergentPlanner] PARALLEL WAVE: dispatching %d/%d ready TODO(s) to sub-agents "
            "(SubAgent budget %d/%d used; %d deferred) -> TODO ids %s",
            len(wave), len(ready_todos), used_calls, max_calls, len(deferred),
            [t.id for t in wave],
        )
        self._emit(
            "phase",
            f"Parallel wave: dispatching {len(wave)} independent TODO(s) to sub-agents...",
        )

        results = await asyncio.gather(
            *(self._dispatch_one_subagent(todo) for todo in wave),
            return_exceptions=True,
        )

        wave_results: list[tuple[TodoItem, StepResult]] = []
        for todo, res in zip(wave, results):
            if isinstance(res, BaseException):
                logger.error(
                    "[EmergentPlanner] PARALLEL TODO %d dispatch crashed: %s",
                    todo.id, res, exc_info=res,
                )
                res = StepResult(
                    step_id=todo.id, success=False,
                    output=f"Parallel sub-agent dispatch failed: {res}",
                    tool_calls_log=[],
                )
            wave_results.append((todo, res))

        succeeded = sum(1 for _, r in wave_results if r.success)
        logger.info(
            "[EmergentPlanner] PARALLEL WAVE done: %d/%d sub-agent TODO(s) succeeded",
            succeeded, len(wave_results),
        )
        return wave_results

    async def _dispatch_one_subagent(self, todo: TodoItem) -> StepResult:
        """Execute one TODO via the isolated `subagent` tool (with dependency context).
        通过隔离的 subagent 工具执行单个 TODO（注入已完成依赖的结果上下文）。"""
        self._todo_list.mark_in_progress(todo.id)
        self._emit("todo_start", {"todo": todo})

        # 复用 _execute_todo 的依赖上下文拼装方式
        task_description = f"TODO {todo.id}: {todo.description}"
        if todo.dependencies:
            dep_results = []
            for dep_id in todo.dependencies:
                dep_todo = self._todo_list.todos.get(dep_id)
                if dep_todo and dep_todo.result:
                    dep_results.append(f"[TODO {dep_id} result]:\n{dep_todo.result}")
            if dep_results:
                task_description += "\n\nResults from dependencies:\n" + "\n".join(dep_results)

        logger.info(
            "[EmergentPlanner] -> SubAgent dispatch START for TODO %d: %s",
            todo.id, todo.description[:100],
        )
        # This direct dispatch bypasses execute_tool_calls(), so set the caller
        # attribution ourselves (no await between this and traced_execute) — keeps
        # the SubAgent's parent_agent = this planner for tracing/eval, consistent
        # with the serial path (execute_tool_calls(agent_name=self.name)) instead
        # of the SubAgentTool constructor default "OrchestratorAgent".
        # 直接派发绕过了 execute_tool_calls，需自行设置归因（与 traced_execute 间无 await），
        # 让 SubAgent.parent_agent 落到本规划器，和串行路径一致。
        subagent_tool = self.tools["subagent"]
        attribute_caller(subagent_tool, self.name)
        try:
            summary = await asyncio.wait_for(
                subagent_tool.traced_execute(task_description=task_description),
                timeout=config_module.NODE_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[EmergentPlanner] SubAgent for TODO %d timed out after %ds",
                todo.id, config_module.NODE_EXECUTION_TIMEOUT,
            )
            return StepResult(
                step_id=todo.id, success=False,
                output=f"Sub-agent timed out after {config_module.NODE_EXECUTION_TIMEOUT}s",
                tool_calls_log=[],
            )

        summary_text = str(summary)
        is_error, _ = classify_result(summary_text)
        if is_error:
            logger.warning(
                "[EmergentPlanner] <- SubAgent dispatch FAILED for TODO %d: %s",
                todo.id, summary_text[:200],
            )
        else:
            logger.info(
                "[EmergentPlanner] <- SubAgent dispatch DONE for TODO %d (summary %d chars)",
                todo.id, len(summary_text),
            )
        return StepResult(
            step_id=todo.id,
            success=not is_error,
            output=summary_text,
            tool_calls_log=[],
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

    @staticmethod
    def _parse_json_for_tool_args(text: str) -> dict[str, Any]:
        """Parse tool-call arguments, handling markdown-fenced JSON.
        解析工具调用参数，兼容 Markdown 代码块包裹的 JSON。
        Returns {} on failure instead of None (safe for **kwargs unpacking)."""
        from llm.client import LLMClient
        try:
            result = LLMClient.parse_json(text)
            return result if isinstance(result, dict) else {}
        except (ValueError, json.JSONDecodeError):
            return {}

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
