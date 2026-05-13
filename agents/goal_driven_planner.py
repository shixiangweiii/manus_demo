"""
Goal-Driven Planner Agent - "Begin with the End in Mind" execution engine.
目标驱动规划智能体 —— 「以终为始」执行引擎。

Inspired by ReflAct (EMNLP 2025) goal-state reflection and backward planning,
this engine maintains a persistent GoalDocument throughout execution to prevent
goal drift in long-horizon tasks.

受 ReflAct（EMNLP 2025）目标状态反思和逆向规划启发，
该引擎在执行过程中维护持久化目标文档，防止长流程任务中的目标漂移。

Key differences from v5 EmergentPlanner:
与 v5 EmergentPlanner 的关键区别：
  - GoalDocument persists across all iterations (goal is never lost)
  - Backward planning derives milestones from end state, not task description
  - GoalReflection (ReflAct-style) before each action compares current vs goal
  - Bounded message context (not v5's unbounded flat history)
  - Proactive TODO refresh driven by reflection, not just on failure

  - GoalDocument 跨迭代持久化（目标永不丢失）
  - 逆向规划从终态推导里程碑，而非从任务描述正向规划
  - 每次行动前进行目标反思（ReflAct 风格），对比当前状态与目标
  - 有界消息上下文（非 v5 的无界扁平历史）
  - 反思驱动的主动 TODO 刷新，而非仅失败时被动刷新

Core loop:
核心循环：
  1. Build GoalDocument (define "done")
  2. Backward-plan milestones from goal state
  3. Convert milestones to TodoList
  4. while has_pending and iteration < max:
     - GoalReflection: compare current state vs goal
     - Select TODO guided by reflection
     - Execute TODO with goal-injected ReAct loop
     - Reanchor goal every N iterations
  5. Compile final answer against goal document
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

import config as config_module
from agents.base import BaseAgent
from context.manager import ContextManager
from llm.client import LLMClient
from schema import (
    GoalAction,
    GoalDocument,
    GoalReflection,
    GoalReanchorResult,
    Milestone,
    MilestonePlan,
    StepResult,
    TodoItem,
    TodoList,
    TodoStatus,
    ToolCallRecord,
)
from tools.base import BaseTool
from tools.router import ToolRouter

from agents.prompt_utils import build_system_prompt

logger = logging.getLogger(__name__)

_V8_BASE_PROMPT = """\
You are an autonomous task execution agent with a "begin with the end in mind" philosophy.

## Your Guiding Principle
Before every action, you compare the CURRENT STATE against the GOAL STATE.
You always know: (1) where you are, (2) where you need to be, (3) what gap remains.

## Workflow
For each iteration you follow this sequence:
1. REFLECT: Review the Goal Document below. Assess current progress.
2. IDENTIFY GAP: State what specifically remains to be done.
3. SELECT ACTION: Choose the tool call that best closes the gap.
4. EXECUTE: Call the tool with correct parameters.
5. OBSERVE: Process the result and update your understanding.

## Rules
- Never lose sight of the original task. If you drift, the REFLECT step will correct you.
- When you believe the goal is fully met, respond with a clear summary. Do NOT call tools.
- If a tool fails, re-reflect on whether the approach is still aligned with the goal.
- Prefer actions that directly contribute to the current milestone.
"""

V8_GOAL_DRIVEN_SYSTEM_PROMPT = build_system_prompt(_V8_BASE_PROMPT)

# JSON prompt templates for structured LLM calls
# 结构化 LLM 调用的 JSON 提示模板

_BUILD_GOAL_PROMPT = """\
Analyze this task and produce a Goal Document. Define what "done" looks like.

Task: {task}
{context_section}

Respond with JSON:
{{
  "success_criteria": "What does 'done' look like? Be specific and observable.",
  "target_state_description": "Describe the final state as if the task is already complete.",
  "key_deliverables": ["list of concrete outputs expected"],
  "constraints": ["list of boundaries or non-goals"]
}}"""

_BACKWARD_PLAN_PROMPT = """\
Starting from the GOAL STATE, plan backward to identify milestones.
Work backward from the goal. What must be true just before the goal is met?
And before that? List 2-5 milestones in REVERSE order (goal first, start last).

Example (for task: "Make a cup of tea"):
  milestones: [
    {{\"description\": \"Tea is ready to drink\"}},             ← Goal (position 1, the final state)
    {{\"description\": \"Hot water poured over tea leaves\"}},  ← Precondition
    {{\"description\": \"Water is boiling\"}},                  ← Precondition
    {{\"description\": \"Kettle filled with water\"}}            ← Start (position 4, the first action)
  ]

Goal State: {target_state}
Success Criteria: {criteria}
Key Deliverables: {deliverables}

Respond with JSON:
{{
  "milestones": [
    {{
      "description": "What this milestone achieves",
      "completion_criteria": "How to verify this milestone is done",
      "estimated_complexity": "low/medium/high"
    }}
  ],
  "backward_reasoning": "Why this sequence leads to the goal"
}}"""

_GOAL_REFLECT_PROMPT = """\
REFLECT on progress toward the goal.

=== GOAL ===
Success Criteria: {criteria}
Target State: {target_state}
Key Deliverables: {deliverables}

=== CURRENT STATE ===
{state_summary}

=== CURRENT FOCUS ===
{current_focus}

Provide your assessment as JSON:
{{
  "current_state_summary": "What has been accomplished so far",
  "gap_analysis": "What specifically remains between current and goal",
  "next_milestone": "The ONE thing to focus on next",
  "progress_pct": 0-100,
  "suggested_action": "execute_todo" / "replan" / "complete",
  "reasoning": "Why this is the right next step"
}}"""

_REANCHOR_PROMPT = """\
Re-evaluate the goal document based on execution progress so far.
The original goal was:
  Task: {original_task}
  Success Criteria: {criteria}

Current progress: {progress_pct}%
Completed milestones: {completed_summary}
Remaining TODOs: {remaining_todos}
Last result: {last_result}

Respond with JSON:
{{
  "success_criteria": "Updated success criteria (or same if still valid)",
  "target_state_description": "Updated target state (or same if still valid)",
  "key_deliverables": ["updated list of deliverables"],
  "constraints": ["updated constraints"],
  "progress_pct": 0-100,
  "current_focus": "What to focus on next",
  "completed_milestones_summary": "Compressed summary of all completed work",
  "goal_drift_detected": false,
  "correction_applied": "Description of any correction, or empty string"
}}"""

_REFRESH_TODO_PROMPT = """\
Review the current TODO list and execution progress, then update it if needed.

Goal: {criteria}
Current progress: {progress_pct}%
Last execution result: {last_result}

Current TODOs:
{todo_list_str}

Respond with JSON:
{{
  "new_todos": [{{"description": "...", "dependencies": []}}],
  "modify_todos": [{{"id": 0, "description": "updated description"}}],
  "blocked_todos": [0]
}}
Only include sections that need changes. Empty arrays are fine."""

_COMPILE_ANSWER_PROMPT = """\
Synthesize the following execution results into a final answer.
The original task was: {task}
Success criteria: {criteria}

Results:
{results_str}

Provide a comprehensive answer that directly addresses the success criteria."""


class GoalDrivenPlannerAgent(BaseAgent):
    """
    Goal-driven emergent planner with "begin with the end in mind" philosophy.
    目标驱动的隐式规划器，遵循「以终为始」设计哲学。

    Maintains a persistent GoalDocument throughout execution. Each iteration
    starts by comparing current state against the goal (ReflAct-style reflection),
    and milestones are derived via backward planning from the goal state.

    在执行过程中维护持久化目标文档。每次迭代首先对比当前状态与目标
    （ReflAct 风格反思），里程碑通过从目标状态逆向规划生成。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        max_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        super().__init__(
            name="GoalDrivenPlanner",
            system_prompt=V8_GOAL_DRIVEN_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )
        self.tools = {t.name: t for t in tools}
        self.tool_schemas = [t.to_openai_tool() for t in tools]
        self.max_iterations = max_iterations or config_module.MAX_REACT_ITERATIONS
        self.max_outer_iterations = config_module.MAX_GOAL_DRIVEN_ITERATIONS
        self.reanchor_interval = config_module.GOAL_REANCHOR_INTERVAL
        self.reflection_interval = config_module.GOAL_REFLECTION_INTERVAL
        self.stagnation_window = config_module.GOAL_DRIVEN_STAGNATION_WINDOW
        self.tool_router = tool_router or ToolRouter(available_tools=list(self.tools.keys()))
        self._on_event = on_event or (lambda *_: None)

        self._goal_doc: GoalDocument | None = None
        self._todo_list: TodoList | None = None
        self._reanchor_counter: int = 0

    # ------------------------------------------------------------------
    # Main entry point
    # 主入口
    # ------------------------------------------------------------------

    async def execute(self, task: str, context: str = "") -> str:
        """
        Goal-driven planning and execution.
        目标驱动规划与执行。

        Flow:
          1. Build GoalDocument
          2. Backward-plan milestones
          3. Convert to TodoList
          4. Goal-guided execution loop
          5. Compile answer against goal
        """
        # Reset per-task state to ensure clean start on reused instances
        self._reanchor_counter = 0
        self._goal_doc = None
        self._todo_list = None

        logger.info("[GoalDrivenPlanner] Starting execution: %s", task[:100])
        self._emit("phase", "Building goal document...")

        # Phase 1: Goal Anchoring
        goal_doc = await self._build_goal_document(task, context)
        self._goal_doc = goal_doc
        self._emit("goal_anchor", goal_doc.model_dump())

        # Phase 2: Backward Planning
        self._emit("phase", "Planning backward from goal state...")
        milestone_plan = await self._backward_plan(goal_doc)

        # Phase 3: Convert milestones to TodoList
        self._todo_list = self._milestones_to_todos(milestone_plan, task)
        self._emit("todo_list_initialized", {
            "total": len(self._todo_list.todos),
            "todos": [t.description for t in self._todo_list.todos.values()],
        })

        # Phase 4: Goal-Guided Execution Loop
        all_results: list[StepResult] = []
        iteration = 0
        last_reflection: GoalReflection | None = None
        completed_count_at_last_check = 0
        stagnation_rounds = 0

        while self._todo_list.has_pending() and iteration < self.max_outer_iterations:
            iteration += 1

            # Guard: stagnation detection
            if iteration > self.stagnation_window:
                current_completed = sum(
                    1 for t in self._todo_list.todos.values()
                    if t.status == TodoStatus.COMPLETED
                )
                if current_completed == completed_count_at_last_check:
                    stagnation_rounds += 1
                    if stagnation_rounds >= self.stagnation_window:
                        logger.warning(
                            "[GoalDrivenPlanner] Stagnation detected (%d rounds), breaking",
                            stagnation_rounds,
                        )
                        self._emit("stagnation_detected", {
                            "stagnation_rounds": stagnation_rounds,
                            "completed_count": current_completed,
                            "total_todos": len(self._todo_list.todos),
                        })
                        break
                else:
                    stagnation_rounds = 0
                    completed_count_at_last_check = current_completed

            # Step A: Goal-State Reflection
            if iteration % self.reflection_interval == 0 or last_reflection is None:
                self.reset()  # clear meta-call history
                last_reflection = await self._goal_reflect(goal_doc, self._todo_list, iteration)
                goal_doc.progress_pct = last_reflection.progress_pct
                self._emit("goal_reflection", last_reflection.model_dump())

                if last_reflection.suggested_action == GoalAction.COMPLETE or last_reflection.progress_pct >= 100:
                    logger.info("[GoalDrivenPlanner] Goal reflection indicates completion")
                    break

            # Step B: Select next TODO guided by reflection
            current_todo = self._select_todo_by_reflection(last_reflection)
            if current_todo is None:
                break

            self._emit("todo_start", {"todo": current_todo})

            # Step C: Execute TODO with goal-guided ReAct
            try:
                result = await asyncio.wait_for(
                    self._execute_todo_goal_guided(current_todo, goal_doc, last_reflection),
                    timeout=config_module.NODE_EXECUTION_TIMEOUT,
                )
            except asyncio.TimeoutError:
                result = StepResult(
                    step_id=str(current_todo.id),
                    success=False,
                    output=f"TODO timed out after {config_module.NODE_EXECUTION_TIMEOUT}s",
                )
            except Exception as exc:
                result = StepResult(
                    step_id=str(current_todo.id),
                    success=False,
                    output=f"TODO execution error: {exc}",
                )

            all_results.append(result)

            # Step D: Update TODO status
            if result.success:
                self._todo_list.mark_completed(current_todo.id, result.output[:500])
                self._emit("todo_complete", {"todo": current_todo, "result": result})
            else:
                current_todo.retry_count += 1
                if current_todo.retry_count >= config_module.MAX_TODO_RETRIES:
                    self._todo_list.mark_blocked(current_todo.id)
                    self._emit("todo_blocked", {"todo": current_todo, "result": result})
                else:
                    self._todo_list.mark_pending(current_todo.id)
                    self._emit("todo_failed", {"todo": current_todo, "result": result})

            # Step E: Goal Re-Anchoring
            self._reanchor_counter += 1
            if self._should_reanchor(iteration, result):
                self.reset()
                goal_doc = await self._reanchor_goal(goal_doc, self._todo_list, result)
                self._goal_doc = goal_doc
                self._reanchor_counter = 0

            # Step F: Proactive TODO refresh
            if self._should_refresh_todos(iteration, last_reflection, result):
                self.reset()
                await self._refresh_todo_list(goal_doc, self._todo_list, result)

        # Phase 5: Compile answer against goal
        self._emit("phase", "Compiling final answer against goal...")
        final = await self._compile_goal_anchored_answer(task, goal_doc, all_results)
        self._emit("phase", "Goal-driven planning completed.")
        return final

    # ------------------------------------------------------------------
    # Goal Document Building
    # 目标文档构建
    # ------------------------------------------------------------------

    async def _build_goal_document(self, task: str, context: str = "") -> GoalDocument:
        """Ask the LLM to define what "done" looks like for this task."""
        context_section = f"Context: {context}" if context else ""
        prompt = _BUILD_GOAL_PROMPT.format(task=task, context_section=context_section)
        data = await self.think_json(prompt)
        self.reset()  # clear meta-call history
        return GoalDocument(
            original_task=task,
            success_criteria=data.get("success_criteria", "Task completed successfully"),
            target_state_description=data.get("target_state_description", ""),
            key_deliverables=data.get("key_deliverables", []),
            constraints=data.get("constraints", []),
        )

    # ------------------------------------------------------------------
    # Backward Planning
    # 逆向规划
    # ------------------------------------------------------------------

    async def _backward_plan(self, goal_doc: GoalDocument) -> MilestonePlan:
        """Plan milestones backward from the goal state."""
        prompt = _BACKWARD_PLAN_PROMPT.format(
            target_state=goal_doc.target_state_description,
            criteria=goal_doc.success_criteria,
            deliverables=", ".join(goal_doc.key_deliverables) if goal_doc.key_deliverables else "none specified",
        )
        data = await self.think_json(prompt)
        self.reset()

        raw_milestones = data.get("milestones", [])
        if not raw_milestones:
            # Fallback: single milestone = the goal itself
            raw_milestones = [{
                "description": goal_doc.success_criteria,
                "completion_criteria": goal_doc.success_criteria,
                "estimated_complexity": "medium",
            }]

        # Reverse order: LLM returns goal-first, we need start-first
        # Build milestones with field-level fallback to handle malformed LLM output
        milestones = []
        for i, ms in enumerate(reversed(raw_milestones)):
            ms.setdefault("description", f"Milestone {i + 1}")
            ms.setdefault("completion_criteria", f"Milestone {i + 1} completed")
            ms.setdefault("estimated_complexity", "medium")
            try:
                milestones.append(Milestone(id=i + 1, **ms))
            except Exception as exc:
                logger.warning("[GoalDrivenPlanner] Skipping malformed milestone: %s", exc)
                milestones.append(Milestone(
                    id=i + 1,
                    description=ms.get("description", f"Milestone {i + 1}"),
                    completion_criteria=ms.get("completion_criteria", f"Milestone {i + 1} completed"),
                ))
        return MilestonePlan(
            goal_description=goal_doc.target_state_description,
            milestones=milestones,
            backward_reasoning=data.get("backward_reasoning", ""),
        )

    def _milestones_to_todos(self, plan: MilestonePlan, task: str) -> TodoList:
        """Convert backward-planned milestones into a TodoList."""
        todo_list = TodoList(task=task)
        prev_id = None
        for ms in plan.milestones:
            deps = [prev_id] if prev_id is not None else []
            item = TodoItem(
                id=ms.id,
                description=ms.description,
                dependencies=deps,
            )
            todo_list.todos[ms.id] = item
            prev_id = ms.id
        return todo_list

    # ------------------------------------------------------------------
    # Goal Reflection (ReflAct-style)
    # 目标反思（ReflAct 风格）
    # ------------------------------------------------------------------

    async def _goal_reflect(
        self,
        goal_doc: GoalDocument,
        todo_list: TodoList,
        iteration: int,
    ) -> GoalReflection:
        """Compare current state against goal document."""
        state_summary = self._get_state_summary(todo_list)
        prompt = _GOAL_REFLECT_PROMPT.format(
            criteria=goal_doc.success_criteria,
            target_state=goal_doc.target_state_description,
            deliverables=", ".join(goal_doc.key_deliverables) if goal_doc.key_deliverables else "none",
            state_summary=state_summary,
            current_focus=goal_doc.current_focus or "Starting execution",
        )
        data = await self.think_json(prompt)
        self.reset()

        # Normalize suggested_action: LLM may return variations like "Replan"/"REPLAN"/"re-plan"
        raw_action = str(data.get("suggested_action", "execute_todo")).lower().strip().replace("-", "_")
        action_map = {
            "execute_todo": GoalAction.EXECUTE_TODO,
            "execute": GoalAction.EXECUTE_TODO,
            "replan": GoalAction.REPLAN,
            "re_plan": GoalAction.REPLAN,
            "complete": GoalAction.COMPLETE,
            "done": GoalAction.COMPLETE,
            "finish": GoalAction.COMPLETE,
        }
        suggested_action = action_map.get(raw_action, GoalAction.EXECUTE_TODO)

        return GoalReflection(
            current_state_summary=data.get("current_state_summary", ""),
            gap_analysis=data.get("gap_analysis", ""),
            next_milestone=data.get("next_milestone", ""),
            progress_pct=float(data.get("progress_pct", 0.0)),
            suggested_action=suggested_action,
            reasoning=data.get("reasoning", ""),
        )

    def _get_state_summary(self, todo_list: TodoList) -> str:
        """Build a human-readable state summary of the TODO list, including execution results."""
        lines = []
        for todo in todo_list.todos.values():
            status_icon = {
                TodoStatus.PENDING: "[ ]",
                TodoStatus.IN_PROGRESS: "[>]",
                TodoStatus.COMPLETED: "[x]",
                TodoStatus.BLOCKED: "[!]",
            }.get(todo.status, "[?]")
            line = f"  {status_icon} #{todo.id}: {todo.description} (retries: {todo.retry_count})"
            if todo.status == TodoStatus.COMPLETED and todo.result:
                line += f"\n      → Result: {todo.result[:200]}"
            lines.append(line)
        return "\n".join(lines) if lines else "No TODOs yet."

    # ------------------------------------------------------------------
    # TODO Selection
    # TODO 选择
    # ------------------------------------------------------------------

    def _select_todo_by_reflection(self, reflection: GoalReflection) -> TodoItem | None:
        """Select next TODO guided by the reflection's next_milestone hint."""
        if self._todo_list is None:
            return None

        ready = self._todo_list.get_ready_todos()
        if not ready:
            # Escape hatch: pick first pending TODO if no ready ones
            pending = [t for t in self._todo_list.todos.values() if t.status == TodoStatus.PENDING]
            return pending[0] if pending else None

        # Try to match reflection's next_milestone to a ready TODO
        if reflection.next_milestone:
            for todo in ready:
                # Simple keyword overlap matching
                milestone_words = set(reflection.next_milestone.lower().split())
                todo_words = set(todo.description.lower().split())
                if milestone_words & todo_words:
                    return todo

        # Default: first ready TODO
        return ready[0]

    # ------------------------------------------------------------------
    # Goal-Guided ReAct Execution
    # 目标引导的 ReAct 执行
    # ------------------------------------------------------------------

    async def _execute_todo_goal_guided(
        self,
        todo: TodoItem,
        goal_doc: GoalDocument,
        reflection: GoalReflection,
    ) -> StepResult:
        """
        Execute a single TODO using a bounded ReAct loop with goal injection.
        使用有界 ReAct 循环（注入目标文档）执行单个 TODO。

        Unlike v5's unbounded flat history, this manages its own messages list.
        与 v5 的无界扁平历史不同，此方法管理自己的消息列表。
        """
        todo.status = TodoStatus.IN_PROGRESS
        step_id = str(todo.id)

        # Reset tool router for this TODO
        self.tool_router.reset_node(step_id)

        # Build dependency context
        dep_context = ""
        if todo.dependencies and self._todo_list:
            dep_results = []
            for dep_id in todo.dependencies:
                dep_todo = self._todo_list.todos.get(dep_id)
                if dep_todo and dep_todo.result:
                    dep_results.append(f"Dependency #{dep_id} result: {dep_todo.result[:500]}")
            if dep_results:
                dep_context = "\n".join(dep_results)

        # Initialize bounded message list with system prompt
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": V8_GOAL_DRIVEN_SYSTEM_PROMPT},
        ]
        tool_calls_log: list[ToolCallRecord] = []

        goal_injection = self._format_goal_for_prompt(goal_doc)

        iteration = 0
        while iteration < self.max_iterations:
            iteration += 1

            # Build goal-aware prompt
            if iteration == 1:
                user_msg = f"{goal_injection}\n\nCurrent TODO: {todo.description}"
                if dep_context:
                    user_msg += f"\n\n{dep_context}"
                if reflection.reasoning:
                    user_msg += f"\n\nReasoning for this TODO: {reflection.reasoning}"
            else:
                user_msg = f"Continue.\n\n{goal_injection}\n\nFocus: {goal_doc.current_focus or todo.description}"
                router_hint = self.tool_router.get_hint(step_id)
                if router_hint:
                    user_msg += f"\n\nIMPORTANT: {router_hint}"

            messages.append({"role": "user", "content": user_msg})

            # Sliding window: keep system msgs + last ~20 messages, preserving tool_calls pairing
            if len(messages) > 24:
                system_msgs = [m for m in messages if m.get("role") == "system"]
                non_system = [m for m in messages if m.get("role") != "system"]
                kept = non_system[-20:]
                # Protect tool_calls pairing: if first kept msg is a tool response
                # whose parent assistant(tool_calls) was trimmed, include that assistant
                if kept and kept[0].get("role") == "tool":
                    for i in range(len(non_system) - 20 - 1, -1, -1):
                        if non_system[i].get("role") == "assistant" and non_system[i].get("tool_calls"):
                            kept = non_system[i:]
                            if len(kept) > 24:
                                kept = kept[-22:]
                            break
                messages = system_msgs + kept

            # Call LLM with tools
            try:
                response_msg = await self.llm_client.chat_with_tools(
                    messages,
                    tools=self.tool_schemas,
                    temperature=0.5,
                )
            except Exception as exc:
                logger.error("[GoalDrivenPlanner] LLM call failed: %s", exc)
                return StepResult(
                    step_id=step_id,
                    success=False,
                    output=f"LLM call failed: {exc}",
                    tool_calls_log=tool_calls_log,
                )

            # Record assistant response
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

            # No tool calls = TODO is done
            if not response_msg.tool_calls:
                return StepResult(
                    step_id=step_id,
                    success=True,
                    output=response_msg.content or "TODO completed.",
                    tool_calls_log=tool_calls_log,
                )

            # Execute tool calls
            tool_messages: list[dict[str, Any]] = []
            for tool_call in response_msg.tool_calls:
                func_name = tool_call.function.name
                try:
                    func_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                logger.info("[GoalDrivenPlanner] Tool call: %s(%s)", func_name, func_args)

                tool = self.tools.get(func_name)
                is_error = False
                if tool is None:
                    result = f"Error: Unknown tool '{func_name}'"
                    self.tool_router.record_failure(step_id, func_name)
                    is_error = True
                else:
                    try:
                        result = await tool.traced_execute(**func_args)
                        self.tool_router.record_success(step_id, func_name)
                    except Exception as exc:
                        result = f"Error: Tool execution error: {exc}"
                        self.tool_router.record_failure(step_id, func_name)
                        is_error = True

                if isinstance(result, str) and result.startswith("Error:"):
                    is_error = True

                tool_calls_log.append(ToolCallRecord(
                    tool_name=func_name,
                    parameters=func_args,
                    result=result if is_error else result[:1000],
                ))

                if is_error:
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

        # Max iterations reached
        logger.warning("[GoalDrivenPlanner] Hit max iterations (%d) for TODO %s", self.max_iterations, step_id)
        return StepResult(
            step_id=step_id,
            success=False,
            output=f"TODO did not complete within {self.max_iterations} iterations.",
            tool_calls_log=tool_calls_log,
        )

    # ------------------------------------------------------------------
    # Goal Re-Anchoring
    # 目标重锚定
    # ------------------------------------------------------------------

    async def _reanchor_goal(
        self,
        goal_doc: GoalDocument,
        todo_list: TodoList,
        last_result: StepResult,
    ) -> GoalDocument:
        """Periodically re-evaluate the goal document against execution progress."""
        remaining = [
            f"#{t.id}: {t.description} ({t.status.value})"
            for t in todo_list.todos.values()
            if t.status != TodoStatus.COMPLETED
        ]
        prompt = _REANCHOR_PROMPT.format(
            original_task=goal_doc.original_task,
            criteria=goal_doc.success_criteria,
            progress_pct=goal_doc.progress_pct,
            completed_summary=goal_doc.completed_milestones_summary or "none yet",
            remaining_todos="; ".join(remaining) if remaining else "none",
            last_result=last_result.output[:500] if last_result else "none",
        )
        data = await self.think_json(prompt)
        self.reset()

        drift_detected = data.get("goal_drift_detected", False)
        correction = data.get("correction_applied", "")

        updated_doc = GoalDocument(
            original_task=goal_doc.original_task,
            success_criteria=goal_doc.success_criteria,                   # 冻结：核心字段不可变
            target_state_description=goal_doc.target_state_description,   # 冻结：核心字段不可变
            key_deliverables=goal_doc.key_deliverables,                   # 冻结：核心字段不可变
            constraints=goal_doc.constraints,                             # 冻结：核心字段不可变
            progress_pct=float(data.get("progress_pct", goal_doc.progress_pct)),
            completed_milestones_summary=data.get("completed_milestones_summary", goal_doc.completed_milestones_summary),
            current_focus=data.get("current_focus", goal_doc.current_focus),
        )

        if drift_detected:
            logger.warning("[GoalDrivenPlanner] Goal drift detected: %s", correction)
            self._emit("goal_drift_alert", {
                "correction_applied": correction,
                "original_criteria": goal_doc.success_criteria,
                "suggested_criteria": data.get("success_criteria", ""),
            })

        reanchor_result = GoalReanchorResult(
            updated_goal_doc=updated_doc,
            goal_drift_detected=drift_detected,
            correction_applied=correction,
        )
        self._emit("goal_reanchor", reanchor_result.model_dump())
        return updated_doc

    def _should_reanchor(self, iteration: int, last_result: StepResult) -> bool:
        """Determine whether to re-anchor the goal this iteration."""
        if self.reanchor_interval <= 0:
            return False
        if self._reanchor_counter >= self.reanchor_interval:
            return True
        if not last_result.success:
            return True
        return False

    # ------------------------------------------------------------------
    # Proactive TODO Refresh
    # 主动 TODO 刷新
    # ------------------------------------------------------------------

    async def _refresh_todo_list(
        self,
        goal_doc: GoalDocument,
        todo_list: TodoList,
        last_result: StepResult,
    ) -> None:
        """Proactively update the TODO list based on goal reflection."""
        todos_str = self._get_state_summary(todo_list)
        prompt = _REFRESH_TODO_PROMPT.format(
            criteria=goal_doc.success_criteria,
            progress_pct=goal_doc.progress_pct,
            last_result=last_result.output[:500] if last_result else "none",
            todo_list_str=todos_str,
        )
        data = await self.think_json(prompt)
        self.reset()

        # Add new TODOs (using add_todo to preserve cycle detection)
        for new_todo_data in data.get("new_todos", []):
            if len(todo_list.todos) >= config_module.MAX_TODO_ITEMS:
                break
            deps = new_todo_data.get("dependencies", [])
            valid_deps = [d for d in deps if d in todo_list.todos]
            try:
                todo_list.add_todo(
                    description=new_todo_data.get("description", f"New TODO"),
                    dependencies=valid_deps,
                )
            except ValueError:
                logger.warning("[GoalDrivenPlanner] Skipping new TODO that would create cycle")

        # Modify existing TODOs
        for mod in data.get("modify_todos", []):
            mod_id = mod.get("id")
            if mod_id in todo_list.todos:
                todo_list.todos[mod_id].description = mod.get("description", todo_list.todos[mod_id].description)

        # Block TODOs
        for block_id in data.get("blocked_todos", []):
            if block_id in todo_list.todos:
                todo_list.mark_blocked(block_id)

        self._emit("todo_list_update", {
            "total": len(todo_list.todos),
            "todos": [t.description for t in todo_list.todos.values()],
        })

    def _should_refresh_todos(
        self,
        iteration: int,
        reflection: GoalReflection,
        last_result: StepResult,
    ) -> bool:
        """Determine whether to refresh the TODO list this iteration."""
        if not last_result.success:
            return True
        if reflection.suggested_action == GoalAction.REPLAN:
            return True
        # Periodic refresh every 3 iterations
        if iteration % 3 == 0:
            return True
        return False

    # ------------------------------------------------------------------
    # Answer Compilation
    # 答案汇编
    # ------------------------------------------------------------------

    async def _compile_goal_anchored_answer(
        self,
        task: str,
        goal_doc: GoalDocument,
        results: list[StepResult],
    ) -> str:
        """Compile final answer, explicitly checking against goal success criteria."""
        successful = [r for r in results if r.success]
        if not successful:
            return "No TODOs completed successfully. Goal was not achieved."

        results_str = "\n\n".join(
            f"[Result {i}]:\n{r.output}"
            for i, r in enumerate(successful, 1)
        )

        prompt = _COMPILE_ANSWER_PROMPT.format(
            task=task,
            criteria=goal_doc.success_criteria,
            results_str=results_str,
        )

        try:
            self.reset()
            synthesis = await self.think(prompt)
            return synthesis
        except Exception:
            return results_str

    # ------------------------------------------------------------------
    # Utility
    # 工具方法
    # ------------------------------------------------------------------

    def _format_goal_for_prompt(self, goal_doc: GoalDocument) -> str:
        """Format the goal document as a prompt injection string."""
        deliverables = "\n".join(f"  - {d}" for d in goal_doc.key_deliverables) if goal_doc.key_deliverables else "  - none specified"
        constraints = "\n".join(f"  - {c}" for c in goal_doc.constraints) if goal_doc.constraints else "  - none"
        completed = goal_doc.completed_milestones_summary or "none yet"

        return (
            f"=== GOAL DOCUMENT ===\n"
            f"Original Task: {goal_doc.original_task}\n"
            f"Success Criteria: {goal_doc.success_criteria}\n"
            f"Target State: {goal_doc.target_state_description}\n"
            f"Key Deliverables:\n{deliverables}\n"
            f"Constraints:\n{constraints}\n"
            f"Completed Milestones: {completed}\n"
            f"Progress: {goal_doc.progress_pct:.0f}%\n"
            f"Current Focus: {goal_doc.current_focus or 'N/A'}\n"
            f"====================="
        )

    def _emit(self, event: str, data: Any = None) -> None:
        """Emit an event to the UI callback (if configured)."""
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[GoalDrivenPlanner] UI callback error for event '%s'", event, exc_info=True)
