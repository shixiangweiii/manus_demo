"""
TaskDAG - Directed Acyclic Graph for hierarchical task planning.
TaskDAG —— 用于分层任务规划的有向无环图。

The TaskDAG holds:
  - nodes: dict of TaskNode (goals, subgoals, actions)
  - edges: list of TaskEdge (dependency, conditional, rollback)
  - state: DAGState (centralized shared state, inspired by LangGraph)
  - checkpoints: list of state snapshots (inspired by LangGraph's persistence)

TaskDAG 包含：
  - nodes:       TaskNode 字典（目标、子目标、动作节点）
  - edges:       TaskEdge 列表（依赖边、条件边、回滚边）
  - state:       DAGState（集中式共享状态，灵感来自 LangGraph）
  - checkpoints: 状态快照列表（灵感来自 LangGraph 的持久化机制）

Key operations:
  - get_ready_nodes(): find nodes ready for parallel execution
  - topological_sort(): Kahn's algorithm for execution ordering
  - mark_subtree_skipped(): cascade skip on condition failure
  - is_complete(): check if DAG execution is done

核心操作：
  - get_ready_nodes():       找出所有可并行执行的就绪节点
  - topological_sort():      Kahn 算法确定合法执行顺序
  - mark_subtree_skipped():  条件不满足时级联跳过下游子树
  - is_complete():           检查 DAG 是否全部执行完毕
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from schema import DAGState, EdgeType, NodeStatus, NodeType, TaskEdge, TaskNode

logger = logging.getLogger(__name__)


class TaskDAG:
    """
    Directed Acyclic Graph of task nodes with centralized state.
    带集中式状态的任务节点有向无环图。

    This is our lightweight equivalent of LangGraph's StateGraph:
    - LangGraph: nodes are functions, edges are conditional routers, state is TypedDict
    - Ours: nodes are TaskNode data objects, edges are TaskEdge, state is DAGState

    这是我们对 LangGraph StateGraph 的轻量化等价实现：
    - LangGraph：节点是函数，边是条件路由器，状态是 TypedDict
    - 我们的实现：节点是 TaskNode 数据对象，边是 TaskEdge，状态是 DAGState
    """

    def __init__(
        self,
        task: str,
        nodes: dict[str, TaskNode],
        edges: list[TaskEdge],
        context: str = "",
        max_checkpoints: int = 20,
        checkpoint_interval: int = 5,
    ):
        """
        初始化 TaskDAG。

        Args:
            task: 任务描述
            nodes: 初始节点字典
            edges: 初始边列表
            context: 额外的上下文信息
            max_checkpoints: 最大 checkpoint 数量（P2 优化），超过后删除最旧的
            checkpoint_interval: checkpoint 保存间隔（P2 优化），每 N 次调用保存一次
        """
        self.nodes = nodes    # 所有节点，key 为节点 ID
        self.edges = edges    # 所有边
        self.state = DAGState(task=task, context=context)  # 集中式共享状态

        # 简化的 Checkpoint 配置（P2 优化）
        self._max_checkpoints = max_checkpoints
        self._checkpoint_interval = checkpoint_interval
        self._checkpoint_counter = 0

        # 懒加载索引缓存（P3 优化）
        self._dep_cache: dict[str, list[str]] = {}  # 依赖关系缓存
        self._children_cache: dict[str, list[str]] = {}  # 子节点缓存
        self._conditional_edges_cache: dict[str, list[TaskEdge]] = {}  # 条件边缓存
        self._cache_valid: bool = False

        # LangGraph snapshots state at every super-step for time-travel debugging.
        # We keep a simple list of serialized snapshots for the same purpose.
        # LangGraph 在每个 Super-step 快照状态，以支持时间旅行调试。
        # 我们用简单的序列化 dict 列表实现同样目的。
        self._checkpoints: list[dict[str, Any]] = []

        self._validate_dag()  # 构造时做基础校验

    # ------------------------------------------------------------------
    # Cache management (P3 optimization)
    # 缓存管理（P3 优化）
    # ------------------------------------------------------------------

    def _ensure_cache_valid(self) -> None:
        """
        确保索引缓存有效（P3 优化）。

        使用懒加载模式：只有在缓存失效时才重建。
        这样可以避免在 DAG 结构不变时重复构建索引。
        """
        if self._cache_valid:
            return

        self._dep_cache.clear()
        self._children_cache.clear()
        self._conditional_edges_cache.clear()

        for e in self.edges:
            if e.edge_type == EdgeType.DEPENDENCY:
                # 依赖关系缓存：target -> [sources]
                self._dep_cache.setdefault(e.target, []).append(e.source)
                # 子节点缓存：source -> [targets]
                self._children_cache.setdefault(e.source, []).append(e.target)
            elif e.edge_type == EdgeType.CONDITIONAL:
                # 条件边缓存：source -> [edges]
                self._conditional_edges_cache.setdefault(e.source, []).append(e)

        self._cache_valid = True
        logger.debug(
            "[DAG] Cache rebuilt: %d deps, %d children, %d conditional edges",
            len(self._dep_cache),
            len(self._children_cache),
            len(self._conditional_edges_cache)
        )

    def _invalidate_cache(self) -> None:
        """
        使缓存失效（P3 优化）。

        在 DAG 结构变更后调用，标记缓存需要重建。
        """
        self._cache_valid = False

    # ------------------------------------------------------------------
    # Node queries
    # 节点查询方法，动态性 1：运行时就绪发现（而非预定义执行序列）
    # ------------------------------------------------------------------

    def get_ready_nodes(self) -> list[TaskNode]:
        """
        Return nodes that can execute now: PENDING or READY with all
        DEPENDENCY predecessors COMPLETED.

        返回当前可以执行的节点：状态为 PENDING 或 READY，
        且所有 DEPENDENCY 类型的前置节点均已 COMPLETED。

        In LangGraph terms, these are the nodes that would run in the
        next "super-step" — a round of parallel execution.
        在 LangGraph 的术语中，这些节点将在下一个「Super-step」（并行执行轮次）中运行。
        """
        eligible = {NodeStatus.PENDING, NodeStatus.READY}  # 可被调度的状态集合
        ready = []
        for node in self.nodes.values():
            if node.status not in eligible:
                continue
            # 检查所有依赖是否都已完成
            # 核心逻辑是：不查看任何预定义的执行顺序表，而是在运行时扫描当前所有节点状态，发现谁的依赖已经全部满足。
            deps = self.get_dependency_ids(node.id)
            if all(self.nodes[d].status == NodeStatus.COMPLETED for d in deps):
                ready.append(node)
        return ready

    def get_dependency_ids(self, node_id: str) -> list[str]:
        """
        Return IDs of nodes that `node_id` depends on (DEPENDENCY edges only).
        返回 `node_id` 所依赖的所有节点 ID（仅考虑 DEPENDENCY 类型的边）。

        优化：使用缓存避免重复遍历边列表（P3 优化）。

        时间复杂度：
          - 缓存有效时：O(k)，k = 依赖数量
          - 缓存失效时：O(n+m)，n = 节点数，m = 边数（首次构建）
        """
        self._ensure_cache_valid()
        return self._dep_cache.get(node_id, [])

    def get_conditional_edges(self, source_id: str) -> list[TaskEdge]:
        """
        Return CONDITIONAL edges originating from `source_id`.
        返回从 `source_id` 出发的所有 CONDITIONAL 条件边。

        优化：使用缓存避免重复遍历边列表（P3 优化）。

        时间复杂度：
          - 缓存有效时：O(k)，k = 该节点的条件边数量
          - 缓存失效时：O(m)，m = 边数（首次构建）
        """
        self._ensure_cache_valid()
        return self._conditional_edges_cache.get(source_id, [])

    def get_rollback_targets(self, node_id: str) -> list[str]:
        """
        Return node IDs connected via ROLLBACK edges from `node_id`.
        返回通过 ROLLBACK 边与 `node_id` 相连的目标节点 ID 列表。
        """
        return [
            e.target for e in self.edges
            if e.source == node_id and e.edge_type == EdgeType.ROLLBACK
        ]

    def get_downstream(self, node_id: str) -> list[str]:
        """
        Return all node IDs downstream of `node_id` via BFS on DEPENDENCY edges.
        通过 BFS 遍历 DEPENDENCY 边，返回 `node_id` 所有下游节点 ID。
        用于失败时级联跳过整个子树。
        """
        visited: set[str] = set()
        queue: deque[str] = deque()

        # 先找直接子节点
        # 注意: 仅遍历DEPENDENCY边,这是设计决策
        # - CONDITIONAL边代表动态分支,不构成执行先后顺序
        # - ROLLBACK边仅在失败时触发,不参与正常执行路径
        # - mark_subtree_skipped用于级联跳过,只应影响依赖路径上的节点
        children = [
            e.target for e in self.edges
            if e.source == node_id and e.edge_type == EdgeType.DEPENDENCY
        ]
        queue.extend(children)

        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            for e in self.edges:
                if e.source == nid and e.edge_type == EdgeType.DEPENDENCY:
                    queue.append(e.target)

        return list(visited)

    # ------------------------------------------------------------------
    # State mutations
    # 状态变更方法
    # ------------------------------------------------------------------

    def mark_subtree_skipped(
        self,
        node_id: str,
        state_machine=None,
    ) -> None:
        """
        Mark all downstream nodes as SKIPPED (e.g., when a condition fails).
        将所有下游节点标记为 SKIPPED（例如条件分支未满足，或上游节点失败时）。

        Args:
            node_id: The node ID that triggered the cascade skip
            state_machine: Optional NodeStateMachine instance for valid state transitions.
                          If provided, uses transition(); otherwise uses direct assignment.
        """
        downstream = self.get_downstream(node_id)
        for nid in downstream:
            node = self.nodes[nid]
            if node.status in (NodeStatus.PENDING, NodeStatus.READY):
                if state_machine is not None:
                    state_machine.transition(node, NodeStatus.SKIPPED)
                else:
                    node.status = NodeStatus.SKIPPED
                logger.info("[DAG] Node %s SKIPPED (downstream of %s)", nid, node_id)

    def refresh_ready_states(self) -> None:
        """
        Promote PENDING nodes to READY if all their dependencies are COMPLETED.
        Called after each super-step to prepare the next round.

        将依赖全部满足的 PENDING 节点提升为 READY 状态。
        每个 Super-step 结束后调用，为下一轮执行做准备。
        """
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            deps = self.get_dependency_ids(node.id)
            if all(self.nodes[d].status == NodeStatus.COMPLETED for d in deps):
                node.status = NodeStatus.READY

    # ------------------------------------------------------------------
    # Graph algorithms
    # 图算法
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        """
        Kahn's algorithm — returns node IDs in a valid execution order.
        Only considers DEPENDENCY edges.

        Kahn 算法 —— 返回节点 ID 的合法拓扑执行顺序。
        仅考虑 DEPENDENCY 类型的边。
        保证每个节点在其所有前置依赖之后出现。
        """
        # 统计每个节点的入度（有多少 DEPENDENCY 边指向它）
        in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        for e in self.edges:
            if e.edge_type == EdgeType.DEPENDENCY:
                in_degree[e.target] = in_degree.get(e.target, 0) + 1

        # 将入度为 0 的节点（无前置依赖）加入队列
        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        result: list[str] = []

        while queue:
            nid = queue.popleft()
            result.append(nid)
            # 将以该节点为源点的 DEPENDENCY 边目标节点的入度减 1
            for e in self.edges:
                if e.source == nid and e.edge_type == EdgeType.DEPENDENCY:
                    in_degree[e.target] -= 1
                    if in_degree[e.target] == 0:
                        queue.append(e.target)

        if len(result) != len(self.nodes):
            logger.warning("[DAG] Cycle detected! Topological sort incomplete.")
        return result

    def is_complete(self) -> bool:
        """
        True if every node has reached a terminal state
        (COMPLETED, SKIPPED, or ROLLED_BACK).

        当所有节点都到达终态（COMPLETED、SKIPPED 或 ROLLED_BACK）时返回 True。
        DAGExecutor 的主循环以此为退出条件。
        """
        terminal = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}
        return all(n.status in terminal for n in self.nodes.values())

    def has_failed_nodes(self) -> bool:
        """
        检查是否存在处于 FAILED 状态的节点（未被回滚或跳过）。
        """
        return any(n.status == NodeStatus.FAILED for n in self.nodes.values())

    # ------------------------------------------------------------------
    # Dynamic graph mutation (v3 - Adaptive Planning)
    # 动态图变更（v3 - 自适应规划新增）
    # ------------------------------------------------------------------

    def add_dynamic_node(self, node: TaskNode) -> bool:
        """
        Add a new node to the DAG at runtime.
        运行时向 DAG 动态添加新节点。

        Only succeeds if the node ID doesn't already exist.
        Returns True on success, False if ID conflicts.

        仅在节点 ID 不存在时成功添加，返回 True；ID 冲突则返回 False。
        """
        if node.id in self.nodes:
            logger.warning("[DAG] Cannot add node '%s': ID already exists", node.id)
            return False

        self.nodes[node.id] = node
        logger.info("[DAG] Dynamic node added: %s (%s) - %s", node.id, node.node_type.value, node.description[:60])
        return True

    def add_dynamic_edge(self, edge: TaskEdge) -> bool:
        """
        Add a new edge to the DAG at runtime.
        运行时向 DAG 动态添加新边。

        Validates that both source and target exist.
        校验源节点和目标节点是否存在。

        For DEPENDENCY edges, also validates that adding the edge won't create a cycle.
        对于DEPENDENCY类型的边，还会验证添加该边不会引入环。

        P3 优化：使缓存失效以确保索引一致性。
        """
        if edge.source not in self.nodes:
            logger.warning("[DAG] Cannot add edge: source '%s' not found", edge.source)
            return False
        if edge.target not in self.nodes:
            logger.warning("[DAG] Cannot add edge: target '%s' not found", edge.target)
            return False

        key = (edge.source, edge.target, edge.edge_type.value)
        for existing in self.edges:
            if (existing.source, existing.target, existing.edge_type.value) == key:
                logger.debug("[DAG] Edge %s->%s (%s) already exists, skipping", edge.source, edge.target, edge.edge_type.value)
                return False

        # 修复 H4: 对于DEPENDENCY边，检查是否引入环
        if edge.edge_type == EdgeType.DEPENDENCY:
            # 临时添加边并检查拓扑排序
            self.edges.append(edge)
            try:
                sorted_nodes = self.topological_sort()
                if len(sorted_nodes) != len(self.nodes):
                    # 环检测失败：回滚
                    self.edges.pop()
                    logger.warning(
                        "[DAG] Adding edge %s -> %s would create a cycle",
                        edge.source, edge.target
                    )
                    return False
            except Exception:
                # 拓扑排序异常，回滚
                self.edges.pop()
                logger.warning(
                    "[DAG] Adding edge %s -> %s caused topological sort error",
                    edge.source, edge.target
                )
                return False
            # 无环：临时添加的边保留，不需要再次append
        else:
            # 非DEPENDENCY边直接添加
            self.edges.append(edge)

        self._invalidate_cache()  # P3 优化：使缓存失效
        logger.info("[DAG] Dynamic edge added: %s -> %s (%s)", edge.source, edge.target, edge.edge_type.value)
        return True

    def remove_pending_node(self, node_id: str) -> bool:
        """
        Remove a PENDING node and all its connected edges.
        移除一个 PENDING 状态的节点及其所有关联边。

        Only PENDING nodes can be removed (running/completed nodes cannot).
        Returns True on success.

        只有 PENDING 状态的节点可以被移除（运行中/已完成的不行）。

        P3 优化：使缓存失效以确保索引一致性。
        """
        node = self.nodes.get(node_id)
        if node is None:
            logger.warning("[DAG] Cannot remove node '%s': not found", node_id)
            return False
        if node.status != NodeStatus.PENDING:
            logger.warning("[DAG] Cannot remove node '%s': status is %s (must be PENDING)", node_id, node.status.value)
            return False

        del self.nodes[node_id]
        self.edges = [e for e in self.edges if e.source != node_id and e.target != node_id]
        if node_id in self.state.node_results:
            del self.state.node_results[node_id]

        self._invalidate_cache()  # P3 优化：使缓存失效
        logger.info("[DAG] Node removed: %s", node_id)
        return True

    def modify_node(self, node_id: str, description: str | None = None, exit_criteria_desc: str | None = None) -> bool:
        """
        Modify a PENDING node's description and/or exit criteria.
        修改 PENDING 节点的描述和/或完成判据。

        只有 PENDING 状态的节点可被修改。
        """
        node = self.nodes.get(node_id)
        if node is None:
            logger.warning("[DAG] Cannot modify node '%s': not found", node_id)
            return False
        if node.status not in (NodeStatus.PENDING, NodeStatus.READY):
            logger.warning("[DAG] Cannot modify node '%s': status is %s", node_id, node.status.value)
            return False

        if description is not None:
            old_desc = node.description
            node.description = description
            logger.info("[DAG] Node %s description updated: '%s' -> '%s'", node_id, old_desc[:40], description[:40])

        if exit_criteria_desc is not None:
            node.exit_criteria.description = exit_criteria_desc
            node.exit_criteria.validation_prompt = f"Has this been achieved? {exit_criteria_desc}"

        return True

    def get_pending_action_nodes(self) -> list[TaskNode]:
        """
        Return all ACTION nodes still in PENDING or READY state.
        返回所有仍处于 PENDING 或 READY 状态的 ACTION 节点。
        用于自适应规划评估哪些节点可能需要调整。
        """
        return [
            n for n in self.nodes.values()
            if n.node_type == NodeType.ACTION and n.status in (NodeStatus.PENDING, NodeStatus.READY)
        ]

    def get_completed_action_count(self) -> int:
        """
        Count completed ACTION nodes.
        统计已完成的 ACTION 节点数量。
        """
        return sum(
            1 for n in self.nodes.values()
            if n.node_type == NodeType.ACTION and n.status == NodeStatus.COMPLETED
        )

    def get_blockage_report(self) -> dict[str, Any]:
        """
        生成阻塞报告，帮助诊断 DAG 执行中的问题（P1 优化）。

        报告内容包括：
          - 各状态节点数量统计
          - 处于运行状态的节点列表
          - 可能被阻塞的节点及其阻塞原因

        Returns:
            包含阻塞诊断信息的字典。

        Example:
            >>> dag.get_blockage_report()
            {
                "total_nodes": 10,
                "status_counts": {"PENDING": 3, "COMPLETED": 5, "RUNNING": 2},
                "stuck_nodes": [
                    {"node_id": "node_3", "blocked_by": ["node_1"]}
                ],
                "has_blockage": True
            }
        """
        report: dict[str, Any] = {
            "total_nodes": len(self.nodes),
            "status_counts": {},
            "stuck_nodes": [],
            "has_blockage": False,
        }

        # 统计各状态数量
        for node in self.nodes.values():
            status = node.status.value
            report["status_counts"][status] = report["status_counts"].get(status, 0) + 1

        # 找出可能被阻塞的节点
        terminal_statuses = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}
        running_statuses = {NodeStatus.PENDING, NodeStatus.READY, NodeStatus.RUNNING}

        for node in self.nodes.values():
            if node.status in running_statuses:
                deps = self.get_dependency_ids(node.id)

                # 找出所有非终态的依赖
                non_terminal_deps = []
                for dep_id in deps:
                    dep_node = self.nodes.get(dep_id)
                    if dep_node is None:
                        # 依赖节点不存在，可能是 dangling 引用
                        continue
                    if dep_node.status not in terminal_statuses:
                        non_terminal_deps.append(dep_id)

                if non_terminal_deps:
                    report["stuck_nodes"].append({
                        "node_id": node.id,
                        "blocked_by": non_terminal_deps,
                        "current_status": node.status.value,
                    })
                    report["has_blockage"] = True

        return report

    def has_failed_nodes(self) -> bool:
        """
        检查是否存在未处理的失败节点（P1 优化）。

        Returns:
            如果存在 FAILED 状态的节点返回 True。
        """
        return any(n.status == NodeStatus.FAILED for n in self.nodes.values())

    def try_recover_blocked_nodes(self) -> int:
        """
        尝试恢复被阻塞的节点（P0 优化）。

        当 DAG 执行中出现"假性阻塞"（条件边被错误跳过导致依赖链断裂）时，
        此方法尝试强制推进所有 PENDING 节点变为 READY，以便继续执行。

        区分"假性阻塞"和"真性阻塞"：
          - 假性阻塞：所有依赖节点都已到达终态（COMPLETED/SKIPPED/ROLLED_BACK）
          - 真性阻塞：至少有一个依赖节点仍在运行中（RUNNING/PENDING/READY）

        增强点（P0 优化）：
          - 区分真性阻塞和假性阻塞
          - 添加详细的恢复报告
          - 更严格的恢复条件检查

        Returns:
            恢复的 PENDING 节点数量。
        """
        recovered_count = 0
        blocked_report = self.get_blockage_report()

        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue

            deps = self.get_dependency_ids(node.id)

            # 无依赖的节点可以直接恢复
            if not deps:
                node.status = NodeStatus.READY
                recovered_count += 1
                logger.info("[DAG] Recovered unblocked node (no deps): %s", node.id)
                continue

            # 检查是否所有依赖节点都已到达终态（COMPLETED/SKIPPED/ROLLED_BACK）
            terminal_statuses = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}
            dep_statuses = {dep_id: self.nodes[dep_id].status for dep_id in deps if dep_id in self.nodes}

            all_deps_terminal = all(
                status in terminal_statuses for status in dep_statuses.values()
            )

            # 只有所有依赖都到达终态时才恢复（避免在依赖未完成时强制推进）
            if all_deps_terminal:
                node.status = NodeStatus.READY
                recovered_count += 1
                logger.info(
                    "[DAG] Recovered blocked node: %s (deps: %s -> %s)",
                    node.id,
                    deps,
                    [s.value for s in dep_statuses.values()]
                )
            else:
                # 真性阻塞 - 有依赖仍在运行中
                non_terminal_deps = [
                    dep_id for dep_id, status in dep_statuses.items()
                    if status not in terminal_statuses
                ]
                logger.debug(
                    "[DAG] Node %s is truly blocked by non-terminal deps: %s",
                    node.id,
                    non_terminal_deps
                )

        if recovered_count > 0:
            logger.info("[DAG] Recovered %d blocked nodes out of %d stuck", recovered_count, len(blocked_report["stuck_nodes"]))
        else:
            logger.warning("[DAG] No nodes recovered. Stuck nodes: %s", blocked_report["stuck_nodes"])

        return recovered_count

    # ------------------------------------------------------------------
    # Checkpointing (LangGraph-inspired)
    # 检查点（灵感来自 LangGraph）
    # ------------------------------------------------------------------

    def save_checkpoint(self, force: bool = False) -> bool:
        """
        简化的 Checkpoint 保存（P2 优化）。

        实现策略：
          1. 定期保存：每 N 次调用才实际保存一次（_checkpoint_interval）
          2. 限制总数：最多保留 N 个 checkpoint（_max_checkpoints），超出时删除最旧的
          3. 支持强制保存：用于关键操作后的强制 checkpoint

        这两个优化显著减少了：
          - 内存占用（从无限增长到有限空间）
          - checkpoint 保存时间（从每次都保存变为定期保存）

        Args:
            force: 是否强制保存（忽略间隔限制）

        Returns:
            是否实际保存了 checkpoint。
        """
        self._checkpoint_counter += 1

        # 定期保存：只有达到间隔才保存
        if not force and self._checkpoint_counter % self._checkpoint_interval != 0:
            return False

        # 限制总数：删除最旧的 checkpoint
        if len(self._checkpoints) >= self._max_checkpoints:
            discarded = self._checkpoints.pop(0)
            logger.debug(
                "[DAG] Checkpoint limit reached (%d), discarding oldest (step %s)",
                self._max_checkpoints,
                str(discarded.get("step", "unknown"))[:50]
            )

        # 创建 snapshot
        snapshot = {
            "step": self._checkpoint_counter,
            "timestamp": __import__("time").time(),
            "data": self.to_dict(),
        }
        self._checkpoints.append(snapshot)

        logger.debug(
            "[DAG] Checkpoint saved (step %d, total %d checkpoints)",
            self._checkpoint_counter,
            len(self._checkpoints)
        )
        return True

    def restore_checkpoint(self, index: int = -1) -> bool:
        """
        从 checkpoint 恢复 DAG 状态（P2 优化）。

        支持恢复到任意 checkpoint，但实际使用中建议使用 index=-1 恢复到最近的一个。

        Args:
            index: checkpoint 索引，-1 表示最近一个，0 表示最旧的一个

        Returns:
            是否成功恢复。

        Raises:
            IndexError: 如果索引超出范围
        """
        if not self._checkpoints:
            logger.warning("[DAG] No checkpoints to restore")
            return False

        try:
            checkpoint = self._checkpoints[index]
        except IndexError:
            logger.error("[DAG] Checkpoint index %d out of range (0-%d)", index, len(self._checkpoints) - 1)
            return False

        data = checkpoint["data"]

        # 重建 DAG 状态
        restored = TaskDAG.from_dict(data)

        # 替换当前状态
        self.nodes = restored.nodes
        self.edges = restored.edges
        self.state = restored.state

        # 修复 C3: 恢复检查点计数器
        self._checkpoint_counter = checkpoint["step"]

        # 修复 C4: 使缓存失效以确保索引一致性
        self._invalidate_cache()

        logger.info(
            "[DAG] Checkpoint restored (step %d, recovered %d nodes)",
            checkpoint["step"],
            len(self.nodes)
        )
        return True

    @property
    def checkpoints(self) -> list[dict[str, Any]]:
        """返回所有 checkpoint 快照的只读副本。"""
        return list(self._checkpoints)

    # ------------------------------------------------------------------
    # Serialization
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize the full DAG (structure + state) to a dict.
        将完整 DAG（图结构 + 运行状态）序列化为 dict，用于 checkpoint 或持久化。
        """
        return {
            "task": self.state.task,
            "context": self.state.context,
            "node_results": dict(self.state.node_results),
            "nodes": {nid: n.model_dump() for nid, n in self.nodes.items()},
            "edges": [e.model_dump() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskDAG:
        """
        Reconstruct a TaskDAG from a serialized dict.
        从序列化 dict 重建 TaskDAG，用于从 checkpoint 恢复。
        """
        nodes = {nid: TaskNode(**ndata) for nid, ndata in data["nodes"].items()}
        edges = [TaskEdge(**edata) for edata in data["edges"]]
        dag = cls(
            task=data["task"],
            nodes=nodes,
            edges=edges,
            context=data.get("context", ""),
        )
        dag.state.node_results = data.get("node_results", {})
        return dag

    # ------------------------------------------------------------------
    # Validation
    # 校验
    # ------------------------------------------------------------------

    def _validate_dag(self) -> None:
        """
        Basic validation: check edges reference existing nodes and detect cycles.
        基础校验：检查所有边的端点都存在于 nodes 中，并检测环。
        修复 Medium #8: 添加无环检测，防止 DAG 中存在循环依赖导致死锁。
        """
        node_ids = set(self.nodes.keys())
        for e in self.edges:
            if e.source not in node_ids:
                logger.warning("[DAG] Edge source '%s' not found in nodes", e.source)
            if e.target not in node_ids:
                logger.warning("[DAG] Edge target '%s' not found in nodes", e.target)

        # 修复 Medium #8: 使用 Kahn 算法检测 DEPENDENCY 边中的环
        in_degree = {nid: 0 for nid in node_ids}
        for e in self.edges:
            if e.edge_type == EdgeType.DEPENDENCY:
                in_degree[e.target] = in_degree.get(e.target, 0) + 1

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        visited = 0
        while queue:
            nid = queue.popleft()
            visited += 1
            for e in self.edges:
                if e.source == nid and e.edge_type == EdgeType.DEPENDENCY:
                    in_degree[e.target] -= 1
                    if in_degree[e.target] == 0:
                        queue.append(e.target)

        if visited != len(node_ids):
            unvisited = [nid for nid in node_ids if in_degree.get(nid, 0) > 0]
            logger.error("[DAG] Cycle detected! Unvisited nodes: %s", unvisited)
            raise ValueError(f"Cycle detected in DAG! Nodes involved: {unvisited}")

    # ------------------------------------------------------------------
    # Display helpers
    # 展示辅助方法
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """
        One-line summary for logging.
        生成单行状态摘要，用于日志输出，如：DAG[5 nodes: 2 completed, 1 running, 2 pending]
        """
        status_counts: dict[str, int] = {}
        for n in self.nodes.values():
            status_counts[n.status.value] = status_counts.get(n.status.value, 0) + 1
        parts = [f"{v} {k}" for k, v in status_counts.items()]
        return f"DAG[{len(self.nodes)} nodes: {', '.join(parts)}]"

    def get_action_nodes(self) -> list[TaskNode]:
        """
        Return only ACTION-type nodes (the executable leaf nodes).
        返回所有 ACTION 类型的节点（可执行的叶节点，由 Executor 实际运行）。
        """
        return [n for n in self.nodes.values() if n.node_type == NodeType.ACTION]
