"""
Node State Machine - Validates and enforces node lifecycle transitions.
节点状态机 —— 校验并强制执行节点生命周期的合法状态转移。

The transition table is the single source of truth for what state changes
are legal. Any invalid transition raises InvalidTransitionError, preventing
the DAG from entering an inconsistent state.
转移表是合法状态变化的唯一权威来源。
任何非法转移都会抛出 InvalidTransitionError，防止 DAG 进入不一致状态。

Transition graph:
转移图：
    PENDING ──> READY ──> RUNNING ──> COMPLETED   (happy path / 正常路径)
                                  ──> FAILED ──> ROLLED_BACK
    Any non-terminal ──────────────> SKIPPED       (conditional branch not taken / 条件分支未满足)
"""

from __future__ import annotations

import logging
from typing import Callable

from schema import NodeStatus, TaskNode

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    """
    Raised when an illegal state transition is attempted.
    当尝试非法状态转移时抛出此异常。
    """
    pass


# The full transition table — readable at a glance.
# 完整的状态转移表——一目了然地看清所有合法转移路径。
# 动态性 6：状态机强制合法转移
# v1 的 step.status 只是一个普通枚举字段，代码可以随意赋值。v2 通过 NodeStateMachine 严格管控每次转移：
VALID_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    NodeStatus.PENDING:     {NodeStatus.READY, NodeStatus.SKIPPED},
    NodeStatus.READY:       {NodeStatus.RUNNING, NodeStatus.SKIPPED},
    NodeStatus.RUNNING:     {NodeStatus.COMPLETED, NodeStatus.FAILED},
    NodeStatus.FAILED:      {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED},
    # Terminal states — no further transitions allowed
    # 终态——不允许任何进一步转移
    NodeStatus.COMPLETED:   set(),
    NodeStatus.SKIPPED:     set(),
    NodeStatus.ROLLED_BACK: set(),
}


class NodeStateMachine:
    """
    Validates and applies node state transitions.
    校验并应用节点状态转移。

    Provides a single `transition()` method that:
      1. Checks the VALID_TRANSITIONS table
      2. Applies the change to the node
      3. Fires an optional callback for UI/logging

    提供唯一的 `transition()` 方法，该方法：
      1. 查询 VALID_TRANSITIONS 表校验合法性
      2. 将状态变更应用到节点对象
      3. 触发可选回调函数（用于 UI 更新或日志）
    """

    def __init__(self, on_transition: Callable[[str, NodeStatus, NodeStatus], None] | None = None):
        """
        Args:
            on_transition: Optional callback(node_id, old_status, new_status)
                           for event-driven UI updates.
            on_transition: 可选回调 callback(node_id, 旧状态, 新状态)
                           用于事件驱动的 UI 实时更新。
        """
        self._on_transition = on_transition

    def can_transition(self, node: TaskNode, new_status: NodeStatus) -> bool:
        """
        Check whether transitioning `node` to `new_status` is legal.
        检查将 `node` 转移到 `new_status` 是否合法。
        """
        return new_status in VALID_TRANSITIONS.get(node.status, set())

    def transition(self, node: TaskNode, new_status: NodeStatus) -> None:
        """
        Apply a state transition. Raises InvalidTransitionError if illegal.
        应用状态转移。若转移非法则抛出 InvalidTransitionError。

        This is our equivalent of what LangGraph handles internally in its
        Pregel runtime — ensuring nodes only move through valid states.
        这等价于 LangGraph 内部 Pregel 运行时所做的事——
        确保节点只能经过合法状态路径。
        """
        if not self.can_transition(node, new_status):
            raise InvalidTransitionError(
                f"Node '{node.id}': cannot transition from {node.status.value} to {new_status.value}. "
                f"Valid targets: {sorted(s.value for s in VALID_TRANSITIONS.get(node.status, set()))}"
            )

        old_status = node.status
        node.status = new_status  # 应用状态变更

        logger.debug("[SM] %s: %s -> %s", node.id, old_status.value, new_status.value)

        if self._on_transition:
            try:
                self._on_transition(node.id, old_status, new_status)
            except Exception:
                pass  # UI errors should never crash the pipeline / UI 异常不能影响主流程
