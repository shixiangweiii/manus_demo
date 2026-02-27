"""
DAG Executor - Executes a TaskDAG using a super-step model.
DAG 执行引擎 —— 以 Super-step 模型执行 TaskDAG。

This is the core execution engine that replaces the sequential for-loop
in the original orchestrator. Inspired by LangGraph's Pregel runtime:
这是替代原 Orchestrator 顺序 for 循环的核心执行引擎，灵感来自 LangGraph 的 Pregel 运行时：

  LangGraph Pregel super-step:
    1. Find all nodes with pending messages (ready nodes)
    2. Execute them in parallel
    3. Merge outputs via reducers
    4. Repeat until no more active nodes

  LangGraph Pregel Super-step 流程：
    1. 找出所有有待处理消息的节点（就绪节点）
    2. 并行执行这些节点
    3. 通过 Reducer 合并输出到共享状态
    4. 重复直到没有活跃节点

  Our simplified version:
    1. Find all READY/PENDING nodes whose deps are COMPLETED
    2. Execute them in parallel via asyncio.gather
    3. Merge results into DAGState (simple dict write)
    4. Validate exit criteria per node
    5. Handle failures (rollback, skip subtree)
    6. Evaluate conditional edges
    7. Checkpoint, then repeat

  我们的简化版本：
    1. 找出所有依赖已满足的 READY/PENDING 节点
    2. 通过 asyncio.gather 并行执行
    3. 将结果合并到 DAGState（简单 dict 写入）
    4. 逐节点验证 exit criteria（完成判据）
    5. 处理失败（回滚 + 跳过下游子树）
    6. 评估条件边
    7. 保存 Checkpoint，然后进入下一轮

Each iteration of the while loop is one "super-step".
while 循环的每次迭代就是一个「Super-step」。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable

import config
from dag.graph import TaskDAG
from dag.state_machine import NodeStateMachine
from schema import NodeStatus, NodeType, StepResult, TaskNode

if TYPE_CHECKING:
    from agents.executor import ExecutorAgent
    from agents.reflector import ReflectorAgent

logger = logging.getLogger(__name__)


class DAGExecutor:
    """
    Executes a TaskDAG using the super-step model.
    以 Super-step 模型执行 TaskDAG。

    Each 'super-step' (term borrowed from LangGraph/Pregel):
      1. Find all ready nodes
      2. Execute them in parallel via asyncio.gather
      3. Merge results into centralized DAGState
      4. Validate exit criteria
      5. Handle failures + evaluate conditions
      6. Checkpoint state

    每个「Super-step」（借用 LangGraph/Pregel 术语）：
      1. 找出所有就绪节点
      2. 通过 asyncio.gather 并行执行
      3. 将结果合并到集中式 DAGState
      4. 验证每个节点的完成判据（exit criteria）
      5. 处理失败节点 + 评估条件边
      6. 保存状态快照（Checkpoint）

    This is LangGraph's core execution model, implemented in ~100 lines.
    这是 LangGraph 核心执行模型的简化实现，约 100 行代码。
    """

    def __init__(
        self,
        executor_agent: ExecutorAgent,
        reflector_agent: ReflectorAgent,
        max_parallel: int | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ):
        self._executor_agent = executor_agent   # ReAct 执行智能体，负责实际运行 ACTION 节点
        self._reflector = reflector_agent        # 反思智能体，负责验证 exit criteria
        self._max_parallel = max_parallel or config.MAX_PARALLEL_NODES  # 每轮最大并行节点数
        self._emit = on_event or (lambda *_: None)  # 事件回调（用于 UI 实时更新）
        self._sm = NodeStateMachine(on_transition=self._on_node_transition)  # 节点状态机

    # ------------------------------------------------------------------
    # Main execution loop
    # 主执行循环
    # ------------------------------------------------------------------

    async def execute(self, dag: TaskDAG) -> str:
        """
        Execute the full DAG and return the compiled output.
        执行完整 DAG 并返回汇总输出字符串。

        The loop runs in discrete super-steps until all nodes reach
        a terminal state (COMPLETED / SKIPPED / ROLLED_BACK).
        循环以离散 Super-step 方式运行，直到所有节点到达终态
        （COMPLETED / SKIPPED / ROLLED_BACK）。
        """
        dag.refresh_ready_states()  # 初始化：将满足条件的 PENDING 节点提升为 READY
        step = 0
        # 动态性体现：哪些节点在哪一轮执行，完全取决于当时的运行时状态——前序节点的完成情况、失败情况、跳过情况，每一轮都不一样。
        # 如果 act_1_1 意外快速完成而 act_1_2 还在跑，下一轮可能只有依赖 act_1_1 的节点就绪，而依赖两者的节点还要等。
        while not dag.is_complete():
            step += 1
            ready = dag.get_ready_nodes()
            if not ready:
                # No ready nodes but DAG not complete -> stuck (failed nodes blocking)
                # 没有就绪节点但 DAG 未完成 -> 被阻塞（通常是有失败节点阻断了下游）
                logger.warning("[DAGExecutor] No ready nodes at super-step %d. %s", step, dag.summary())
                break

            # Filter to only ACTION nodes (GOAL/SUBGOAL are structural)
            # 只执行 ACTION 节点（GOAL/SUBGOAL 是结构性分组，不直接执行）
            actionable = [n for n in ready if n.node_type == NodeType.ACTION]
            if not actionable:
                # Structural nodes with all children done: mark them completed
                # 所有就绪节点都是结构节点且其子节点已完成：直接标记为完成
                for n in ready:
                    if n.status == NodeStatus.PENDING:
                        self._sm.transition(n, NodeStatus.READY)
                    self._sm.transition(n, NodeStatus.RUNNING)
                    self._sm.transition(n, NodeStatus.COMPLETED)
                dag.refresh_ready_states()
                continue

            # Cap parallelism to MAX_PARALLEL_NODES
            # 限制每轮并行节点数，避免资源竞争
            batch = actionable[:self._max_parallel]

            self._emit("superstep", {
                "step": step,
                "nodes": [n.id for n in batch],
                "total_ready": len(actionable),
            })

            # --- Super-step: parallel execution ---
            # --- Super-step：并行执行当前批次节点 ---
            # 动态性 2：并行执行（同一 Super-step 内多节点并发）
            # v1 是严格串行：Step 1 → Step 2 → Step 3。v2 中发现多个就绪节点后，直接并行执行：
            results = await asyncio.gather(*[
                # asyncio.gather 同时发起多个 _run_node 协程
                self._run_node(node, dag) for node in batch
            ])

            # --- Merge results + validate + handle failures ---
            # --- 合并结果 + 验证完成判据 + 处理失败 ---
            for node, result in zip(batch, results):
                # Write result into centralized state (LangGraph reducer equivalent)
                # 将结果写入集中式 DAGState（等价于 LangGraph 的 Reducer）
                dag.state.merge_result(node.id, result.output)
                node.result = result.output

                if result.success:
                    # 验证 exit criteria（由 Reflector 进行 LLM 校验）
                    passed = await self._check_exit_criteria(node, result)
                    if passed:
                        self._sm.transition(node, NodeStatus.COMPLETED)
                        self._emit("node_completed", {"node": node, "result": result})
                    else:
                        # exit criteria 未通过，视为失败
                        self._sm.transition(node, NodeStatus.FAILED)
                        self._emit("node_failed", {"node": node, "result": result, "reason": "exit_criteria"})
                        await self._handle_failure(node, dag)
                else:
                    # 执行本身失败
                    self._sm.transition(node, NodeStatus.FAILED)
                    self._emit("node_failed", {"node": node, "result": result, "reason": "execution"})
                    await self._handle_failure(node, dag)

            # --- Evaluate conditional edges ---
            # --- 评估条件边，决定下游分支是否激活 ---
            self._process_conditions(dag)

            # --- Promote PENDING -> READY for next super-step ---
            # --- 为下一轮 Super-step 提升就绪节点 ---
            dag.refresh_ready_states()

            # --- Auto-complete structural parents whose children are all done ---
            # --- 自动完成所有子节点已终态的结构性父节点 ---
            self._complete_structural_nodes(dag)

            # --- Checkpoint (LangGraph-inspired) ---
            # --- 保存检查点（灵感来自 LangGraph）---
            dag.save_checkpoint()

            logger.info("[DAGExecutor] Super-step %d done. %s", step, dag.summary())

        return self._compile_output(dag)

    # ------------------------------------------------------------------
    # Node execution
    # 节点执行
    # ------------------------------------------------------------------

    async def _run_node(self, node: TaskNode, dag: TaskDAG) -> StepResult:
        """
        Execute a single ACTION node via the ReAct executor agent.
        通过 ReAct 执行智能体执行单个 ACTION 节点。

        从 DAGState 中构建节点的输入上下文（汇集依赖节点结果），
        然后委托给 ExecutorAgent 运行 ReAct 循环。
        """
        # 从集中式 DAGState 中提取该节点所需的上下文（依赖节点的结果）
        context = dag.state.get_node_context(
            node.id, dag.get_dependency_ids(node.id)
        )
        # 状态转移：PENDING -> READY -> RUNNING
        if node.status == NodeStatus.PENDING:
            self._sm.transition(node, NodeStatus.READY)
        self._sm.transition(node, NodeStatus.RUNNING)
        self._emit("node_running", {"node": node})

        return await self._executor_agent.execute_node(node, context)

    # ------------------------------------------------------------------
    # Exit criteria validation
    # 完成判据验证
    # ------------------------------------------------------------------

    async def _check_exit_criteria(self, node: TaskNode, result: StepResult) -> bool:
        """
        Validate whether a node's exit criteria are met.
        Uses the reflector for LLM-based validation when a validation_prompt is set.

        验证节点的完成判据是否满足。
        若节点设置了 validation_prompt，则委托 Reflector 进行 LLM 验证；
        否则直接以执行成功与否为判定结果。
        """
        if not node.exit_criteria.required:
            return True  # 不需要验证，直接通过
        if not node.exit_criteria.validation_prompt:
            return result.success  # 没有自定义验证 prompt，以执行结果为准

        return await self._reflector.validate_exit_criteria(node, result)

    # ------------------------------------------------------------------
    # Failure handling
    # 失败处理，动态性 4：失败感知 + 回滚 + 子树级联跳过，v1 对失败的处理是「全盘重来」。v2 的失败处理是局部的、多层次的：
    # ------------------------------------------------------------------

    async def _handle_failure(self, node: TaskNode, dag: TaskDAG) -> None:
        """
        Handle a failed node:
          1. Execute rollback nodes if any ROLLBACK edges exist
          2. Skip the failed node's downstream subtree

        处理失败节点：
          1. 若存在 ROLLBACK 边，则执行对应的回滚节点
          2. 将失败节点的下游子树全部标记为 SKIPPED
        """
        # 三层动态决策：
        # 1、检测有无 ROLLBACK 边 → 有则执行回滚节点（如清理临时文件），无则直接跳过
        # 2、将失败节点标记为 ROLLED_BACK 或 SKIPPED → 依据回滚结果动态决定
        # 3、级联跳过整个下游子树 → 不再执行任何依赖失败节点的后续操作
        rollback_targets = dag.get_rollback_targets(node.id)
        if rollback_targets:
            logger.info("[DAGExecutor] Executing rollback for node %s", node.id)
            for rb_id in rollback_targets:
                rb_node = dag.nodes.get(rb_id)
                if rb_node and rb_node.status == NodeStatus.PENDING:
                    # 执行回滚节点（通常是清理/撤销操作）
                    rb_result = await self._run_node(rb_node, dag)
                    dag.state.merge_result(rb_id, rb_result.output)
                    if rb_result.success:
                        self._sm.transition(rb_node, NodeStatus.COMPLETED)
                    else:
                        self._sm.transition(rb_node, NodeStatus.FAILED)

            # 回滚执行后将原节点标记为 ROLLED_BACK
            self._sm.transition(node, NodeStatus.ROLLED_BACK)
            self._emit("node_rollback", {"node": node})
        else:
            # 没有回滚节点，直接跳过
            self._sm.transition(node, NodeStatus.SKIPPED)

        # 无论是否回滚，都要跳过下游子树（避免在不完整状态上继续执行）
        # 子树跳过的具体实现——通过 BFS 找出所有下游节点
        dag.mark_subtree_skipped(node.id)

    # ------------------------------------------------------------------
    # Conditional edge processing
    # 条件边处理，动态性 3：条件分支（运行时结果决定执行路径）
    # ------------------------------------------------------------------

    def _process_conditions(self, dag: TaskDAG) -> None:
        """
        After nodes complete, evaluate CONDITIONAL edges.
        A conditional edge activates its target only if the condition
        keyword is found in the source node's result.

        节点完成后，评估条件边。
        条件边仅当源节点结果中包含指定条件关键词时才激活目标节点；
        否则目标节点被跳过。
        v1 完全不具备的能力——计划的执行路径不是固定的，而是根据前序节点的输出动态选择
        """
        for node in list(dag.nodes.values()):
            if node.status != NodeStatus.COMPLETED:
                continue
            for edge in dag.get_conditional_edges(node.id):
                target = dag.nodes.get(edge.target)
                if target is None or target.status != NodeStatus.PENDING:
                    continue

                condition_met = self._evaluate_condition(edge, dag)
                self._emit("condition_evaluated", {
                    "edge": edge,
                    "met": condition_met,
                })
                if not condition_met:
                    # 条件不满足：跳过目标节点及其整个下游子树
                    target.status = NodeStatus.SKIPPED
                    dag.mark_subtree_skipped(target.id)
                    logger.info(
                        "[DAGExecutor] Condition '%s' not met -> skipping %s",
                        edge.condition, target.id,
                    )

    @staticmethod
    def _evaluate_condition(edge, dag: TaskDAG) -> bool:
        """
        Simple condition evaluation: check if the condition keyword
        appears in the source node's result text.
        Production systems would use LLM-based evaluation here.

        简单条件评估：检查条件关键词是否出现在源节点的结果文本中。
        生产系统通常会在此处使用 LLM 进行语义级条件评估。
        """
        if not edge.condition:
            return True  # 无条件限制，默认激活
        source_result = dag.state.node_results.get(edge.source, "")
        # 大小写不敏感的关键词匹配
        return edge.condition.lower() in source_result.lower()

    # ------------------------------------------------------------------
    # Structural node completion
    # 结构性节点自动完成
    # ------------------------------------------------------------------

    def _complete_structural_nodes(self, dag: TaskDAG) -> None:
        """
        Auto-complete GOAL and SUBGOAL nodes when all their children
        have reached terminal states. These are structural groupings,
        not directly executable.

        当 GOAL 和 SUBGOAL 的所有子节点都到达终态时，自动将其标记为完成。
        这些节点是结构性分组，本身不直接执行，其状态由子节点决定。
        """
        for node in dag.nodes.values():
            if node.node_type == NodeType.ACTION:
                continue  # 只处理结构性节点
            if node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK):
                continue  # 已处于终态，跳过

            # 找出该节点的所有直接子节点
            children = [
                n for n in dag.nodes.values()
                if n.parent_id == node.id
            ]
            if not children:
                continue

            terminal = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}
            if all(c.status in terminal for c in children):
                any_completed = any(c.status == NodeStatus.COMPLETED for c in children)
                if any_completed:
                    # 至少有一个子节点成功完成：沿正常路径转移结构节点状态
                    if node.status == NodeStatus.PENDING:
                        self._sm.transition(node, NodeStatus.READY)
                    if node.status == NodeStatus.READY:
                        self._sm.transition(node, NodeStatus.RUNNING)
                    if node.status == NodeStatus.RUNNING:
                        self._sm.transition(node, NodeStatus.COMPLETED)
                else:
                    # 所有子节点均被跳过或回滚：结构节点也跳过
                    if node.status in (NodeStatus.PENDING, NodeStatus.READY):
                        node.status = NodeStatus.SKIPPED

    # ------------------------------------------------------------------
    # Output compilation
    # 输出汇总
    # ------------------------------------------------------------------

    @staticmethod
    def _compile_output(dag: TaskDAG) -> str:
        """
        Compile results from all completed ACTION nodes into final output.
        将所有成功完成的 ACTION 节点的结果汇总为最终输出字符串。
        """
        parts = []
        for node in dag.nodes.values():
            if node.node_type == NodeType.ACTION and node.status == NodeStatus.COMPLETED:
                if node.result:
                    parts.append(node.result)

        if not parts:
            return "No action nodes completed successfully."
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Event helpers
    # 事件辅助方法
    # ------------------------------------------------------------------

    def _on_node_transition(self, node_id: str, old: NodeStatus, new: NodeStatus) -> None:
        """
        Callback from state machine — forwarded as UI event.
        状态机的转移回调 —— 转发为 UI 事件，供前端实时展示节点状态变化。
        """
        self._emit("node_transition", {
            "node_id": node_id,
            "from": old.value,
            "to": new.value,
        })
