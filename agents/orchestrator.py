"""
Orchestrator Agent - Central coordinator for the multi-agent pipeline.
Orchestrator 智能体 —— 多智能体流水线的中央协调者。

The orchestrator manages the full lifecycle of a task:
  1. Retrieve relevant memories and knowledge
  2. Classify task complexity (two-stage hybrid: rules + LLM fallback)
  3. Route to appropriate planning path:
     - simple: v1 flat Plan -> sequential execution -> reflect
     - complex: v2 DAG -> parallel super-step execution -> reflect_dag
     - emergent: v5 emergent planning via TODO list (Claude Code style)
  4. Handle re-planning if reflection fails
  5. Store learnings in long-term memory

Orchestrator 管理任务的完整生命周期：
  1. 检索相关记忆和知识
  2. 分类任务复杂度（两阶段混合：规则快筛 + LLM 兜底）
  3. 路由到对应规划路径：
     - simple：v1 扁平 Plan -> 顺序执行 -> reflect
     - complex：v2 DAG -> 并行 Super-step 执行 -> reflect_dag
     - emergent：v5 隐式规划（Claude Code 风格）-> TODO 列表管理 -> 汇总结果
  4. 若反思失败则处理重规划
  5. 将学习成果存入长期记忆

v2: DAG-based execution with parallel super-steps.
v3: ToolRouter integration for failure-based tool switching; adaptive DAG planning.
v4: Hybrid routing — automatically selects v1 or v2 based on task complexity.
v5: Added emergent planning path — Claude Code style TODO list management.
v6.0: Optional ReActEngine integration via ENABLE_REACT_ENGINE_V2 feature flag.
v2：基于 DAG 的并行 Super-step 执行。
v3：集成 ToolRouter（工具失败切换）和自适应 DAG 规划。
v4：混合路由——根据任务复杂度自动选择 v1 或 v2 路径。
v5：新增隐式规划路径——Claude Code 风格的 TODO 列表管理。
v6.0：可选的统一 ReActEngine 集成（ENABLE_REACT_ENGINE_V2 特性开关）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import config
from agents.emergent_planner import EmergentPlannerAgent
from agents.executor import ExecutorAgent
from agents.planner import PlannerAgent
from agents.reflector import ReflectorAgent
from context.manager import ContextManager
from dag.executor import DAGExecutor
from dag.graph import TaskDAG
from knowledge.retriever import KnowledgeRetriever
from llm.client import LLMClient
from memory.long_term import LongTermMemory
from memory.short_term import ShortTermMemory
from schema import MemoryEntry, NodeStatus, NodeType, Plan, Reflection, StepResult, StepStatus, TokenUsage, TokenUsageSummary, TodoStatus
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    Top-level agent that orchestrates the hybrid Plan-Execute-Reflect pipeline.
    顶层智能体，编排混合「规划 - 执行 - 反思」流水线。

    v4 Architecture (hybrid routing):
    v4 架构（混合路由）：
        User Task
           |
           v
        [Orchestrator] ── retrieves memory & knowledge（检索记忆和知识）
           |
           v
        [Planner.classify_task()] ── two-stage hybrid classifier
                                     （两阶段混合分类器：规则快筛 + LLM 兜底）
           |
      +----+----+----+
      |    |    |
    simple complex emergent
      |    |    |
      v    v    v
    [Planner.create_plan()]    [Planner.create_dag()]    [EmergentPlanner.execute()]
    flat 2-6 steps              3-level DAG               TODO list management
      |                           |                        |
      v                           v                        v
    [Executor] sequential       [DAGExecutor] parallel    [while(tool_use)]
      |                           |                        |
      v                           v                        v
    [Reflector.reflect()]      [Reflector.reflect_dag()]  [compile results]
      |                           |                        |
      v                           v                        v
    Final Answer ── stored in long-term memory（存入长期记忆）
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tools: list[BaseTool] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        interactive: bool = True,
    ):
        # interactive: whether the host environment can collect user input
        # synchronously. main.run_interactive passes True, run_single passes False.
        # When False AND HITL_ENABLED, the ask_user tool is NOT registered and
        # its system-prompt guidance is NOT injected — preventing wasted LLM
        # calls on a tool that would only return Error: in non-interactive mode.
        # interactive 控制宿主能否同步收集用户输入；False + HITL_ENABLED 时
        # ask_user 工具与引导都不注入，避免无意义调用。
        self.llm_client = llm_client or LLMClient()  # 共享 LLM 客户端
        self.context_manager = ContextManager()       # 上下文压缩管理器

        # --- Tracing integration (v7) ---
        # 全链路追踪集成（v7 新增）
        # 初始化 TracingBridge，通过多播模式与现有 on_event 共存
        self._tracing_bridge = None
        if config.TRACING_ENABLED:
            from tracing import TracingBridge, init_tracing
            init_tracing()  # 幂等初始化 TracerProvider
            self._tracing_bridge = TracingBridge()
            # 多播：事件同时发送给原始回调 + TracingBridge
            original_on_event = on_event or (lambda *_: None)
            on_event = self._make_multicast(original_on_event, self._tracing_bridge.on_event)

        # v9 SubAgent tool (feature-flagged, default off)
        # v9 子智能体工具（特性开关控制，默认关闭）
        self._subagent_tool = None
        if config.SUBAGENT_ENABLED:
            from tools.subagent_tool import SubAgentTool
            tool_dict = {t.name: t for t in tools or []}
            self._subagent_tool = SubAgentTool(
                llm_client=self.llm_client,
                available_tools=tool_dict,
                context_manager=self.context_manager,
                on_event=self._emit,
                parent_name="OrchestratorAgent",
            )
            tools = list(tools or []) + [self._subagent_tool]
            logger.info("[Orchestrator] SubAgent tool (v9) enabled")

        # v13 HITL tool (feature-flagged, default off)
        # Double-gated by config.HITL_ENABLED AND interactive: HITL only makes
        # sense when the host can actually collect user input. Tool registration
        # AND prompt-guidance injection are gated together — see
        # agents/prompt_utils.set_hitl_runtime_enabled.
        # v13 人机交互工具：config.HITL_ENABLED + interactive 双门控；
        # 工具注册和引导注入同步开关，避免非交互模式下 LLM 看到一个注定 Error 的工具。
        from agents.prompt_utils import set_hitl_runtime_enabled
        self._hitl_active = config.HITL_ENABLED and interactive
        set_hitl_runtime_enabled(self._hitl_active)

        self._ask_user_tool = None
        if self._hitl_active:
            from tools.ask_user import AskUserTool
            self._ask_user_tool = AskUserTool(
                on_user_prompt=self._handle_user_prompt,
                on_event=self._emit,
            )
            tools = list(tools or []) + [self._ask_user_tool]
            logger.info("[Orchestrator] HITL ask_user tool (v13) enabled")
        elif config.HITL_ENABLED and not interactive:
            logger.info(
                "[Orchestrator] HITL configured but suppressed (non-interactive mode)"
            )

        # Sub-agents（各专用子智能体）
        self.planner = PlannerAgent(self.llm_client, self.context_manager)
        self.executor_agent = ExecutorAgent(
            self.llm_client,
            tools=tools or [],
            context_manager=self.context_manager,
        )
        self.reflector = ReflectorAgent(self.llm_client, self.context_manager)
        self.emergent_planner = EmergentPlannerAgent(
            self.llm_client,
            tools=tools or [],
            context_manager=self.context_manager,
            on_event=self._emit,
        )

        # v8 Goal-Driven Planner (feature-flagged, default off)
        # v8 目标驱动规划器（特性开关控制，默认关闭）
        self.goal_driven_planner = None
        if config.ENABLE_GOAL_DRIVEN_PLANNER:
            from agents.goal_driven_planner import GoalDrivenPlannerAgent
            self.goal_driven_planner = GoalDrivenPlannerAgent(
                llm_client=self.llm_client,
                tools=tools or [],
                context_manager=self.context_manager,
                on_event=self._emit,
            )
            logger.info("[Orchestrator] Goal-driven planner (v8) enabled")

        # Memory & knowledge（记忆和知识检索）
        self.short_term = ShortTermMemory()    # 短期记忆：当前会话上下文
        self.long_term = LongTermMemory()      # 长期记忆：跨会话持久化
        self.knowledge = KnowledgeRetriever()  # 知识库：TF-IDF 检索本地文档

        # Event callback for UI updates（UI 事件回调）
        self._on_event = on_event or (lambda *_: None)

        self.max_replan = config.MAX_REPLAN_ATTEMPTS  # 最大重规划次数

    # ------------------------------------------------------------------
    # Main entry point
    # 主入口
    # ------------------------------------------------------------------

    async def run(self, task: str) -> str:
        """
        Execute a user task through the hybrid multi-agent pipeline.
        通过混合多智能体流水线执行用户任务。

        Flow:
        流程：
          1. Gather context (memory + knowledge) / 收集上下文
          2. Classify task complexity (rules + LLM fallback) / 分类任务复杂度
          3. Route to appropriate planning mode:
             - simple: v1 flat plan -> sequential execute -> reflect
             - complex: v2 DAG -> parallel super-step execute -> reflect_dag
             - emergent: v5 emergent planning via TODO list
          4. Store in memory / 存入长期记忆
        """
        self._emit("task_start", {"task": task})

        # Token 追踪：重置记录，开始新任务
        self.llm_client.reset_usage()

        # v9: Reset SubAgent per-task state for new task
        if self._subagent_tool:
            self._subagent_tool.reset_task_state()

        # v13: Reset HITL per-task state for new task
        if self._ask_user_tool:
            self._ask_user_tool.reset_task_state()

        if not config.EMERGENT_PLANNING_ENABLED:
            logger.info("[Orchestrator] Emergent planning mode is disabled via config")

        # --- Phase 1: Gather context ---
        # --- 阶段 1：收集上下文 ---
        self._emit("phase", "Gathering context...")
        combined_context = await self._gather_context(task)

        # --- Phase 2: Classify & Route ---
        # --- 阶段 2：分类 & 路由 ---
        self._emit("phase", "Classifying task complexity...")
        complexity = await self.planner.classify_task(task)
        self._emit("task_complexity", {"complexity": complexity, "task": task[:100]})

        # --- Phase 3: Plan & Execute (routed by complexity) ---
        # --- 阶段 3：规划 & 执行（按复杂度路由）---
        if complexity == "simple":
            self._emit("phase", "Planning (v1 simple flat plan)...")
            plan = await self.planner.create_plan(task, combined_context)
            self._emit("plan", plan)
            final_answer = await self._execute_and_reflect_simple(task, plan, combined_context)
        elif complexity == "complex":
            self._emit("phase", "Planning (v2 hierarchical DAG)...")
            dag = await self.planner.create_dag(task, combined_context)
            self._emit("dag_created", dag)
            final_answer = await self._execute_dag_and_reflect(dag)
        elif complexity == "emergent":
            self._emit("phase", "Planning (v5 emergent via TODO list)...")
            final_answer = await self._execute_emergent(task, combined_context)
        else:
            logger.error("[Orchestrator] Unknown complexity '%s', degrading to complex", complexity)
            self._emit("phase", f"Planning (v2 hierarchical DAG) - degraded from '{complexity}'...")
            dag = await self.planner.create_dag(task, combined_context)
            self._emit("dag_created", dag)
            final_answer = await self._execute_dag_and_reflect(dag)

        # --- Phase 4: Store in memory ---
        # --- 阶段 4：存入长期记忆 ---
        # Token 追踪：输出汇总
        summary = self._finalize_token_usage()
        self._emit("token_usage_summary", summary)
        self._store_memory(task, final_answer)
        self.short_term.add({"role": "assistant", "content": final_answer})
        self._emit("task_complete", {"answer": final_answer})
        return final_answer

    # ------------------------------------------------------------------
    # Context gathering
    # 上下文收集
    # ------------------------------------------------------------------

    async def _gather_context(self, task: str) -> str:
        """
        Retrieve relevant memories and knowledge for the task.
        检索与当前任务相关的历史记忆和知识库内容。
        """
        memories = self.long_term.search(task)
        memory_context = self.long_term.format_memories(memories)
        self._emit("memory", memory_context)

        knowledge_results = self.knowledge.search(task)
        knowledge_context = self.knowledge.format_results(knowledge_results)
        self._emit("knowledge", knowledge_context)

        self.short_term.add({"role": "user", "content": task})

        # 将记忆和知识合并为单一上下文字符串，注入后续规划/执行流程
        combined = ""
        if memories:
            combined += f"=== Past Experience ===\n{memory_context}\n\n"
        if knowledge_results:
            combined += f"=== Relevant Knowledge ===\n{knowledge_context}\n\n"
        return combined

    # ------------------------------------------------------------------
    # Simple Execute-Reflect loop (v1 path)
    # 简单执行 - 反思循环（v1 路径）
    # ------------------------------------------------------------------

    async def _execute_and_reflect_simple(
        self,
        task: str,
        plan: Plan,
        context: str,
    ) -> str:
        """
        Execute a flat plan sequentially and reflect, with re-planning support.
        顺序执行扁平计划并反思，支持重规划。

        This is the v1 lightweight path: iterate through plan.steps,
        execute each with the ReAct loop, then call reflector.reflect().
        这是 v1 轻量级路径：遍历 plan.steps，
        逐步通过 ReAct 循环执行，然后调用 reflector.reflect()。
        """
        all_results: list[StepResult] = []

        for attempt in range(self.max_replan + 1):
            self._emit("phase", f"Executing simple plan (attempt {attempt + 1})...")
            step_context = context

            for i, step in enumerate(plan.steps):
                if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                    continue

                # 依赖检查：若依赖步骤未成功完成，标记为 SKIPPED
                # 修复 v1 跨轮 dep bug：replan 后新 plan 可能引用先前 attempt 的 step ID,
                # 必须从 all_results(跨 attempt 累积)中获取已成功完成的 ID,
                # 而不仅仅看当前 plan.steps（参考 plan 文件 修复 5 设计）
                if step.dependencies:
                    completed_ids = {r.step_id for r in all_results if r.success}
                    completed_ids.update(s.id for s in plan.steps if s.status == StepStatus.COMPLETED)
                    unmet_deps = [d for d in step.dependencies if d not in completed_ids]
                    if unmet_deps:
                        step.status = StepStatus.SKIPPED
                        self._emit("step_skipped", {"step": step, "reason": f"dependencies {unmet_deps} not completed"})
                        logger.warning("[Orchestrator] Step %d skipped: deps %s not met", step.id, unmet_deps)
                        continue

                step.status = StepStatus.RUNNING
                plan.current_step_index = i
                self._emit("step_start", {"step": step, "index": i})

                if all_results:
                    prev_summary = "\n".join(
                        f"Step {r.step_id}: [{'SUCCESS' if r.success else 'FAILED'}] {r.output[:300]}"
                        for r in all_results
                    )
                    step_context = f"{context}\n\nPrevious results:\n{prev_summary}"

                result = await self.executor_agent.execute_step(step, step_context)
                all_results.append(result)

                if result.success:
                    step.status = StepStatus.COMPLETED
                    step.result = result.output
                    self._emit("step_complete", {"step": step, "result": result})
                else:
                    step.status = StepStatus.FAILED
                    self._emit("step_failed", {"step": step, "result": result})

                    # 条件性 early-break：若无剩余独立步骤，直接进入反思
                    failed_ids = {s.id for s in plan.steps if s.status in (StepStatus.FAILED, StepStatus.SKIPPED)}
                    remaining = [s for s in plan.steps if s.status == StepStatus.PENDING]
                    independent_remaining = [
                        s for s in remaining
                        if not any(d in failed_ids for d in s.dependencies)
                    ]
                    if not independent_remaining:
                        logger.info("[Orchestrator] No independent steps remaining after failure, breaking early")
                        break

            self._emit("phase", "Reflecting on results...")
            reflection = await self.reflector.reflect(task, plan, all_results)
            self._emit("reflection", reflection)

            if reflection.passed:
                return await self._compile_answer(task, all_results)

            if attempt < self.max_replan:
                self._emit("phase", f"Re-planning based on feedback (attempt {attempt + 2})...")
                failed_steps = [
                    s for s in plan.steps if s.status == StepStatus.FAILED
                ]
                plan = await self.planner.replan(
                    task,
                    completed_results=[r for r in all_results if r.success],
                    failed_steps=failed_steps,
                    feedback=reflection.feedback,
                )
                self._emit("plan", plan)
                # 保留所有成功 + 最近一次失败结果（供 replan 参考）
                preserved = [r for r in all_results if r.success]
                failed = [r for r in all_results if not r.success]
                all_results = preserved + (failed[-1:] if failed else [])
            else:
                logger.warning("Max re-plan attempts reached. Returning best effort.")
                return await self._compile_answer(task, all_results)

        return "Task could not be completed after maximum attempts."

    async def _compile_answer(self, task: str, results: list[StepResult]) -> str:
        """
        Compile step results into a coherent final answer (v1 path).
        将各步骤结果汇编为连贯的最终回答（v1 路径）。

        修复 6: 改造为异步实例方法 + LLM 最终合成,以确保:
          - 最终答案与用户任务语言一致(中文任务输出中文)
          - 诚实报告失败/未授权假设(不假装成功)
          - evaluation/runner.py 失败标记保留("无法完成"/"could not complete")
        """
        successful = [r for r in results if r.success]
        if not successful:
            return await self._synthesize_failure_answer(task)

        raw = "\n\n".join(r.output for r in successful)
        return await self._synthesize_final_answer(task, raw)

    async def _synthesize_final_answer(self, task: str, raw_results: str) -> str:
        """
        Synthesize a final user-facing answer in the user's language.
        把多步原始输出合成为面向用户的最终答案,并对齐用户语言。
        """
        # 防御性截断:避免 prompt 过长(LLM 上下文成本控制)
        truncated = raw_results[:8000] if len(raw_results) > 8000 else raw_results
        prompt = (
            "Synthesize a final, user-facing answer based on the execution "
            "results below.\n\n"
            f"User's original task: {task}\n\n"
            f"Step results (raw):\n{truncated}\n\n"
            "Requirements:\n"
            "1. Respond in the SAME language as the user's task.\n"
            "2. If the steps did not yield a satisfying answer, say so HONESTLY "
            "   — do NOT fabricate or guess. If responding in Chinese, include "
            "   the phrase '无法完成' or '未能完成'; in English include "
            "   'could not complete' or similar.\n"
            "3. If any step assumed default values for unspecified data "
            "   (e.g., a default city), explicitly note this caveat to the user.\n"
            "4. If the ask_user tool was used during execution and the user "
            "   provided a correction (e.g., a different city), use the user's "
            "   corrected information in the final answer, not the original "
            "   approximate value.\n"
            "5. Be concise and directly address the user's question."
        )
        try:
            return await self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        except Exception as exc:
            logger.warning(
                "[Orchestrator] Final answer synthesis failed: %s. "
                "Falling back to raw concat with marker.", exc,
            )
            # 不静默吞失败:附加显式标记,便于运维/调试感知
            return (
                f"{raw_results}\n\n"
                f"[Note: Final answer synthesis failed ({exc.__class__.__name__}); "
                f"raw concatenated step results shown above.]"
            )

    async def _synthesize_failure_answer(self, task: str) -> str:
        """
        Generate an honest failure message in the user's task language.
        所有 step 都失败时,生成符合用户语言的诚实失败说明。

        必须保证返回内容包含 evaluation/runner.py 识别的失败标记:
          - 中文任务: 含 '无法完成'
          - 英文任务: 含 'could not complete'
        """
        prompt = (
            f"The user asked: {task}\n\n"
            "All execution steps failed. Write a brief, honest message to the "
            "user in their task language explaining that the request could "
            "not be completed.\n\n"
            "REQUIREMENTS:\n"
            "1. If the user's task is in Chinese, respond in Chinese AND "
            "   include the phrase '无法完成'.\n"
            "2. If the user's task is in English, respond in English AND "
            "   include the phrase 'could not complete'.\n"
            "3. Do NOT fabricate an answer. Be concise."
        )
        try:
            return await self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        except Exception:
            # 双语兜底,确保 evaluation runner 识别失败状态
            return "无法完成 / could not complete: all execution steps failed."

    # ------------------------------------------------------------------
    # Emergent Planning execution (v5 path)
    # 隐式规划执行（v5 路径）
    # ------------------------------------------------------------------

    async def _execute_emergent(self, task: str, context: str) -> str:
        """
        Execute task using emergent planning (Claude Code style).
        使用隐式规划执行任务（Claude Code 风格）。

        When ENABLE_GOAL_DRIVEN_PLANNER=true, uses v8 goal-driven engine.
        Otherwise falls back to v5 EmergentPlannerAgent.

        当 ENABLE_GOAL_DRIVEN_PLANNER=true 时，使用 v8 目标驱动引擎。
        否则回退到 v5 EmergentPlannerAgent。
        """
        # v8 Goal-Driven path
        if self.goal_driven_planner:
            self._emit("phase", "Executing with goal-driven planning (v8)...")
            final_answer = await self.goal_driven_planner.execute(task, context)

            # Quality gate: check for blocked TODOs
            blocked_todos = []
            if self.goal_driven_planner._todo_list:
                blocked_todos = [
                    t for t in self.goal_driven_planner._todo_list.todos.values()
                    if t.status == TodoStatus.BLOCKED
                ]
            if blocked_todos:
                logger.warning(
                    "[Orchestrator] Goal-driven planning completed with %d blocked TODOs",
                    len(blocked_todos),
                )
                self._emit("reflection", Reflection(
                    passed=False, score=0.4,
                    feedback=f"Goal-driven planning completed but {len(blocked_todos)} TODOs were blocked: "
                             + "; ".join(t.description[:80] for t in blocked_todos[:3]),
                    suggestions=["Consider re-running with complex mode for structured planning"],
                ))

            self._emit("phase", "Goal-driven planning completed.")
            return final_answer

        # v5 Emergent Planning path (default)
        self._emit("phase", "Executing with emergent planning (TODO list)...")
        final_answer = await self.emergent_planner.execute(task, context)

        # 轻量级质量门控：检查是否有 BLOCKED TODO
        blocked_todos = []
        if self.emergent_planner._todo_list:
            blocked_todos = [
                t for t in self.emergent_planner._todo_list.todos.values()
                if t.status == TodoStatus.BLOCKED
            ]
        if blocked_todos:
            logger.warning(
                "[Orchestrator] Emergent planning completed with %d blocked TODOs",
                len(blocked_todos),
            )
            self._emit("reflection", Reflection(
                passed=False, score=0.4,
                feedback=f"Emergent planning completed but {len(blocked_todos)} TODOs were blocked: "
                         + "; ".join(t.description[:80] for t in blocked_todos[:3]),
                suggestions=["Consider re-running with complex mode for structured planning"],
            ))

        self._emit("phase", "Emergent planning completed.")
        return final_answer

    # ------------------------------------------------------------------
    # DAG Execute-Reflect loop (v2 path)
    # DAG 执行 - 反思循环（v2 路径）
    # ------------------------------------------------------------------

    async def _execute_dag_and_reflect(self, dag: TaskDAG) -> str:
        """
        Execute the DAG and reflect on results, with partial re-planning.
        执行 DAG 并反思结果，支持局部重规划。

        Unlike v1 which replans failed steps individually, v2 replans
        the failed subtree — preserving all completed work at a finer DAG granularity.
        与 v1 逐个步骤重规划不同，v2 以子树粒度重规划，保留所有已完成工作。
        """
        dag_executor = DAGExecutor(
            executor_agent=self.executor_agent,
            reflector_agent=self.reflector,
            planner_agent=self.planner,  # v3: 传入 Planner 以支持超步间自适应规划
            on_event=self._emit,  # 将 DAG 执行事件转发给 UI
        )

        for attempt in range(self.max_replan + 1):
            self._emit("phase", f"Executing DAG (attempt {attempt + 1})...")

            # 通过 Super-step 模型运行整个 DAG
            final_output = await dag_executor.execute(dag)

            # 收集所有 ACTION 节点结果，用于反思评估（过滤回滚节点等非执行结果）
            results = [
                r for r in [
                    self._node_to_result(nid, dag)
                    for nid in dag.state.node_results
                    if dag.nodes.get(nid) and dag.nodes[nid].node_type == NodeType.ACTION
                ]
                if r is not None
            ]

            # 对完整 DAG 执行结果进行反思
            self._emit("phase", "Reflecting on DAG results...")
            reflection = await self.reflector.reflect_dag(
                dag.state.task, dag, results,
            )
            self._emit("reflection", reflection)

            if reflection.passed:
                return final_output  # 反思通过，直接返回结果

            # 找出问题节点（FAILED 或 SKIPPED），准备局部重规划
            # SKIPPED 节点代表因条件不满足而跳过的子任务，同样需要重规划
            problematic_nodes = [
                n for n in dag.nodes.values()
                if n.status in (NodeStatus.FAILED, NodeStatus.SKIPPED)
            ]

            if attempt < self.max_replan and problematic_nodes:
                failed_node = problematic_nodes[0]
                self._emit("phase", f"Partial replan: replanning subtree from {failed_node.id}...")

                # 只重规划失败节点的子树，保留其余已完成工作
                # replan_subtree 的合并逻辑保留了所有已完成的工作
                # 动态性体现：DAG 的结构本身在运行过程中也是可变的——失败的子树被新生成的子树替换，而已完成的节点和结果原封不动地保留。
                # 这意味着 DAG 不是一份「执行完就扔的文本计划」，而是一个持续演进的运行时数据结构。
                dag = await self.planner.replan_subtree(
                    dag,
                    failed_node_id=failed_node.id,
                    feedback=reflection.feedback,
                )
                # 将 Executor 的状态机注入新 DAG，确保 UI 事件不丢失
                dag._sm = dag_executor._sm
                self._emit("dag_created", dag)
            else:
                logger.warning("No replan triggered (attempt %d/%d, %d problematic nodes). Returning best effort.",
                    attempt+1, self.max_replan+1, len(problematic_nodes))
                return final_output  # 达到最大重规划次数，返回当前最佳结果

        return "Task could not be completed after maximum attempts."

    # ------------------------------------------------------------------
    # Helpers
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _node_to_result(node_id: str, dag: TaskDAG):
        """
        Convert a node's result from DAGState into a StepResult-like object.
        将 DAGState 中节点的结果转换为 StepResult 对象，供 Reflector 使用。
        """
        from schema import StepResult
        output = dag.state.node_results.get(node_id, "")
        node = dag.nodes.get(node_id)
        success = node.status == NodeStatus.COMPLETED if node else False
        return StepResult(step_id=node_id, success=success, output=output)

    # ------------------------------------------------------------------
    # Token Usage Tracking helpers
    # Token 消耗追踪辅助方法
    # ------------------------------------------------------------------

    def _finalize_token_usage(self) -> TokenUsageSummary:
        """Compute token usage summary from per-call records.

        Wave-6: in addition to the existing by_engine view, build a by_caller
        view (one bucket per agent that issued LLM calls). Records lacking a
        caller_tag (legacy / pre-Wave-6) fall into the "unknown" bucket so
        they remain visible rather than silently dropped.
        """
        call_records = self.llm_client.get_call_records()
        summary = TokenUsageSummary(call_records=call_records)

        # 按引擎汇总
        by_engine: dict[str, TokenUsage] = {}
        # Wave-6: 按调用者(Agent)汇总
        by_caller: dict[str, TokenUsage] = {}
        for record in call_records:
            if record.engine not in by_engine:
                by_engine[record.engine] = TokenUsage(engine=record.engine)
            by_engine[record.engine].prompt_tokens += record.prompt_tokens
            by_engine[record.engine].completion_tokens += record.completion_tokens
            by_engine[record.engine].total_tokens += record.total_tokens

            # by_caller: empty caller_tag → "unknown" bucket so untagged calls
            # remain visible (any non-zero count there points to a missing
            # caller_tag wiring upstream).
            caller_key = record.caller_tag or "unknown"
            if caller_key not in by_caller:
                by_caller[caller_key] = TokenUsage(engine=caller_key)
            by_caller[caller_key].prompt_tokens += record.prompt_tokens
            by_caller[caller_key].completion_tokens += record.completion_tokens
            by_caller[caller_key].total_tokens += record.total_tokens
        summary.by_engine = by_engine
        summary.by_caller = by_caller

        # 全局总量
        total = TokenUsage(engine=self.llm_client.model)
        for record in call_records:
            total.prompt_tokens += record.prompt_tokens
            total.completion_tokens += record.completion_tokens
            total.total_tokens += record.total_tokens
        summary.total = total
        return summary

    def _store_memory(self, task: str, answer: str) -> None:
        """
        Store task completion in long-term memory.
        将任务完成情况存入长期记忆，供后续类似任务参考。
        """
        entry = MemoryEntry(
            task=task,
            summary=answer[:500],  # 只存储前 500 字符作为摘要
            learnings=[f"Completed task: {task[:100]}"],
        )
        self.long_term.store(entry)
        self._emit("memory_stored", entry)

    @staticmethod
    def _make_multicast(*callbacks: Callable) -> Callable:
        """
        Create a multicast event callback that dispatches to multiple subscribers.
        创建多播事件回调，将事件分发给多个订阅者。

        Each subscriber's failure is isolated — one failing callback doesn't
        prevent others from receiving the event.
        每个订阅者的失败是隔离的 —— 一个回调失败不影响其他订阅者。
        """
        def multicast(event: str, data: Any = None) -> None:
            for cb in callbacks:
                try:
                    cb(event, data)
                except Exception:
                    logger.debug(
                        "[Orchestrator] Multicast callback failed for event '%s'",
                        event, exc_info=True,
                    )
        return multicast

    def _emit(self, event: str, data: Any = None) -> None:
        """
        Emit an event to the UI callback.
        向 UI 回调函数发送事件。
        事件驱动 UI 更新，UI 异常不影响主流程。
        """
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[Orchestrator] UI callback error for event '%s'", event, exc_info=True)

    # ------------------------------------------------------------------
    # v13 HITL: User prompt bridging
    # v13 人机交互：用户提问桥接
    # ------------------------------------------------------------------

    def _handle_user_prompt(
        self,
        question: str,
        prompt_id: str,
        response_future: asyncio.Future[str],
    ) -> None:
        """
        Bridge between AskUserTool and the UI layer.
        桥接 AskUserTool 与 UI 层。

        Emits an ask_user_prompt event carrying the Future so the UI
        can collect user input and resolve it. The UI layer (main.py)
        is responsible for resolving the Future.
        通过 emit 携带 Future 的事件通知 UI 层，由 UI 层收集用户输入并 resolve Future。
        """
        self._emit("ask_user_prompt", {
            "question": question,
            "prompt_id": prompt_id,
            "response_future": response_future,
        })
