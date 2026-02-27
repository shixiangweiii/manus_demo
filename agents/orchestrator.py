"""
Orchestrator Agent - Central coordinator for the multi-agent pipeline.
Orchestrator 智能体 —— 多智能体流水线的中央协调者。

The orchestrator manages the full lifecycle of a task:
  1. Retrieve relevant memories and knowledge
  2. Classify task complexity (two-stage hybrid: rules + LLM fallback)
  3. Route to appropriate planning path:
     - simple: v1 flat Plan -> sequential execution -> reflect
     - complex: v2 DAG -> parallel super-step execution -> reflect_dag
  4. Handle re-planning if reflection fails
  5. Store learnings in long-term memory

Orchestrator 管理任务的完整生命周期：
  1. 检索相关记忆和知识
  2. 分类任务复杂度（两阶段混合：规则快筛 + LLM 兜底）
  3. 路由到对应规划路径：
     - simple：v1 扁平 Plan -> 顺序执行 -> reflect
     - complex：v2 DAG -> 并行 Super-step 执行 -> reflect_dag
  4. 若反思失败则处理重规划
  5. 将学习成果存入长期记忆

v2: DAG-based execution with parallel super-steps.
v4: Hybrid routing — automatically selects v1 or v2 based on task complexity.
v2：基于 DAG 的并行 Super-step 执行。
v4：混合路由——根据任务复杂度自动选择 v1 或 v2 路径。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import config
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
from schema import MemoryEntry, NodeStatus, Plan, StepResult, StepStatus
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    Top-level agent that orchestrates the hybrid Plan-Execute-Reflect pipeline.
    顶层智能体，编排混合「规划-执行-反思」流水线。

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
      +----+----+
      |         |
    simple    complex
      |         |
      v         v
    [Planner.create_plan()]    [Planner.create_dag()]
    flat 2-6 steps              3-level DAG
      |                           |
      v                           v
    [Executor] sequential       [DAGExecutor] parallel super-steps
      |                           |
      v                           v
    [Reflector.reflect()]      [Reflector.reflect_dag()]
      |                           |
      v                           v
    Final Answer ── stored in long-term memory（存入长期记忆）
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tools: list[BaseTool] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        self.llm_client = llm_client or LLMClient()  # 共享 LLM 客户端
        self.context_manager = ContextManager()       # 上下文压缩管理器

        # Sub-agents（各专用子智能体）
        self.planner = PlannerAgent(self.llm_client, self.context_manager)
        self.executor_agent = ExecutorAgent(
            self.llm_client,
            tools=tools or [],
            context_manager=self.context_manager,
        )
        self.reflector = ReflectorAgent(self.llm_client, self.context_manager)

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
          3a. Simple -> v1 flat plan -> sequential execute -> reflect
              简单 -> v1 扁平计划 -> 顺序执行 -> 反思
          3b. Complex -> v2 DAG -> parallel super-step execute -> reflect_dag
              复杂 -> v2 DAG -> 并行 Super-step 执行 -> 反思
          4. Store in memory / 存入长期记忆
        """
        self._emit("task_start", {"task": task})

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
        else:
            self._emit("phase", "Planning (v2 hierarchical DAG)...")
            dag = await self.planner.create_dag(task, combined_context)
            self._emit("dag_created", dag)
            final_answer = await self._execute_dag_and_reflect(dag)

        # --- Phase 4: Store in memory ---
        # --- 阶段 4：存入长期记忆 ---
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
    # 简单执行-反思循环（v1 路径）
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
                if step.status == StepStatus.COMPLETED:
                    continue

                step.status = StepStatus.RUNNING
                plan.current_step_index = i
                self._emit("step_start", {"step": step, "index": i})

                if all_results:
                    prev_summary = "\n".join(
                        f"Step {r.step_id}: {r.output[:300]}"
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

            self._emit("phase", "Reflecting on results...")
            reflection = await self.reflector.reflect(task, plan, all_results)
            self._emit("reflection", reflection)

            if reflection.passed:
                return self._compile_answer(task, all_results)

            if attempt < self.max_replan:
                self._emit("phase", f"Re-planning based on feedback (attempt {attempt + 2})...")
                failed_steps = [
                    s for s in plan.steps if s.status == StepStatus.FAILED
                ]
                plan = await self.planner.replan(
                    task,
                    completed_results=[r for r in all_results if r.success],
                    failed_step=failed_steps[0] if failed_steps else None,
                    feedback=reflection.feedback,
                )
                self._emit("plan", plan)
                all_results = [r for r in all_results if r.success]
            else:
                logger.warning("Max re-plan attempts reached. Returning best effort.")
                return self._compile_answer(task, all_results)

        return "Task could not be completed after maximum attempts."

    @staticmethod
    def _compile_answer(task: str, results: list[StepResult]) -> str:
        """
        Compile step results into a coherent final answer (v1 path).
        将各步骤结果汇编为连贯的最终回答（v1 路径）。
        """
        successful = [r for r in results if r.success]
        if not successful:
            return "Unfortunately, no steps completed successfully."
        return "\n\n".join(r.output for r in successful)

    # ------------------------------------------------------------------
    # DAG Execute-Reflect loop (v2 path)
    # DAG 执行-反思循环（v2 路径）
    # ------------------------------------------------------------------

    async def _execute_dag_and_reflect(self, dag: TaskDAG) -> str:
        """
        Execute the DAG and reflect on results, with partial re-planning.
        执行 DAG 并反思结果，支持局部重规划。

        Unlike v1 which discarded the entire plan on failure, v2 only
        replans the failed subtree — preserving all completed work.
        与 v1 失败时丢弃整个计划不同，v2 仅重规划失败子树，保留所有已完成工作。
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

            # 收集所有节点结果，用于反思评估
            results = [
                r for r in [
                    self._node_to_result(nid, dag)
                    for nid in dag.state.node_results
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

            # 找出失败节点，准备局部重规划
            # 动态性 5：局部重规划（仅重建失败子树），Orchestrator 在反思失败后，不是重新规划整个任务，而是只重建失败的那个子树
            failed_nodes = [
                n for n in dag.nodes.values()
                if n.status == NodeStatus.FAILED
            ]

            if attempt < self.max_replan and failed_nodes:
                failed_node = failed_nodes[0]
                self._emit("phase", f"Partial replan: replanning subtree from {failed_node.id}...")

                # 只重规划失败节点的子树，保留其余已完成工作
                # replan_subtree 的合并逻辑保留了所有已完成的工作
                # 动态性体现：DAG 的结构本身在运行过程中也是可变的——失败的子树被新生成的子树替换，而已完成的节点和结果原封不动地保留。
                # 这意味着 DAG 不是一份「执行完就扔的文本计划」，而是一个持续演进的运行时数据结构。
                dag = await self.planner.replan_subtree(
                    dag,
                    failed_node_id=failed_node.id,
                    feedback=reflection.feedback,  # 将反思反馈传给 Planner 指导改进方向
                )
                self._emit("dag_created", dag)
            else:
                logger.warning("Max re-plan attempts reached. Returning best effort.")
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

    def _emit(self, event: str, data: Any = None) -> None:
        """
        Emit an event to the UI callback.
        向 UI 回调函数发送事件。
        事件驱动 UI 更新，UI 异常不影响主流程。
        """
        try:
            self._on_event(event, data)
        except Exception:
            pass  # UI errors should never crash the pipeline / UI 异常不能影响主流程
