"""
Orchestrator Agent - Central coordinator for the multi-agent pipeline.
Orchestrator 智能体 —— 多智能体流水线的中央协调者。

The orchestrator manages the full lifecycle of a task:
  1. Retrieve relevant memories and knowledge
  2. Delegate to Planner for DAG creation (hierarchical planning)
  3. Delegate to DAGExecutor for parallel execution (super-step model)
  4. Delegate to Reflector for result validation
  5. Handle partial re-planning if reflection fails
  6. Store learnings in long-term memory

Orchestrator 管理任务的完整生命周期：
  1. 检索相关记忆和知识
  2. 委托 Planner 创建 DAG（分层规划）
  3. 委托 DAGExecutor 并行执行（Super-step 模型）
  4. 委托 Reflector 验证结果
  5. 若反思失败则处理局部重规划
  6. 将学习成果存入长期记忆

v2: Upgraded from sequential Plan execution to DAG-based execution.
    The core loop is now: Plan DAG -> Execute DAG -> Reflect -> Partial Replan.
v2: 从顺序计划执行升级为 DAG 驱动执行。
    核心循环变为：Plan DAG -> Execute DAG -> Reflect -> Partial Replan。
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
from schema import MemoryEntry, NodeStatus
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    Top-level agent that orchestrates the DAG-based Plan-Execute-Reflect pipeline.
    顶层智能体，编排整个基于 DAG 的「规划-执行-反思」流水线。

    v2 Architecture:
    v2 架构：
        User Task
           |
           v
        [Orchestrator] ── retrieves memory & knowledge（检索记忆和知识）
           |
           v
        [Planner]      ── creates hierarchical TaskDAG (Goal -> SubGoals -> Actions)
                          （创建分层 TaskDAG：目标 -> 子目标 -> 动作）
           |
           v
        [DAGExecutor]  ── super-step parallel execution with state machine
                          （Super-step 并行执行 + 状态机管理节点生命周期）
           |                (each node validated against exit criteria)
           |                （每个节点执行后验证完成判据）
           v
        [Reflector]    ── validates full DAG results, may trigger partial replan
                          （验证完整 DAG 结果，必要时触发局部重规划）
           |
           v
        Final Answer   ── stored in long-term memory（存入长期记忆）
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
        Execute a user task through the full DAG-based multi-agent pipeline.
        通过完整的 DAG 多智能体流水线执行用户任务。

        Flow:
          1. Gather context (memory + knowledge)
          2. Planner creates a hierarchical TaskDAG
          3. DAGExecutor runs the DAG (parallel super-steps)
          4. Reflector validates results
          5. Partial replan if needed
          6. Store in memory

        流程：
          1. 收集上下文（记忆 + 知识）
          2. Planner 创建分层 TaskDAG
          3. DAGExecutor 执行 DAG（并行 Super-steps）
          4. Reflector 验证结果
          5. 若需要则局部重规划
          6. 存入长期记忆
        """
        self._emit("task_start", {"task": task})

        # --- Phase 1: Gather context ---
        # --- 阶段 1：收集上下文 ---
        self._emit("phase", "Gathering context...")
        combined_context = await self._gather_context(task)

        # --- Phase 2: Plan (create DAG) ---
        # --- 阶段 2：规划（创建 DAG）---
        self._emit("phase", "Planning (building task DAG)...")
        dag = await self.planner.create_dag(task, combined_context)
        self._emit("dag_created", dag)

        # --- Phase 3: Execute & Reflect (with partial re-planning) ---
        # --- 阶段 3：执行 & 反思（含局部重规划）---
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
    # DAG Execute-Reflect loop
    # DAG 执行-反思循环
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
