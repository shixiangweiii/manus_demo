"""
Tool Router - Intelligent tool selection and failure-based switching.
工具路由器 —— 智能工具选择与基于失败的自动切换。

v3 feature: When a tool fails consecutively, the router suggests
alternative tools to the LLM, preventing it from being stuck in a
tool-failure loop.

v3 特性：当某工具连续失败时，路由器向 LLM 建议替代工具，
避免陷入工具失败的死循环。

The router tracks per-node tool usage statistics and provides:
  - Failure counting per tool per node
  - Alternative tool suggestions when threshold is exceeded
  - Usage statistics for observability

路由器追踪每个节点的工具使用统计，提供：
  - 每个工具在每个节点中的失败计数
  - 超过阈值时的替代工具建议
  - 用于可观测性的使用统计
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import config

logger = logging.getLogger(__name__)


@dataclass
class ToolStats:
    """Per-tool usage statistics within a single node execution.
    单个节点执行中某工具的使用统计。"""
    calls: int = 0              # 总调用次数
    failures: int = 0           # 失败次数
    consecutive_failures: int = 0  # 连续失败次数（成功后重置）

    @property
    def success_rate(self) -> float:
        return (self.calls - self.failures) / self.calls if self.calls > 0 else 1.0


class ToolRouter:
    """
    Tracks tool usage and suggests alternatives on repeated failures.
    追踪工具使用情况，在连续失败时建议替代工具。

    Usage in ReAct loop:
      1. Before tool execution: router might inject a hint
      2. After success: router.record_success(node_id, tool_name)
      3. After failure: router.record_failure(node_id, tool_name)
      4. Before next LLM call: router.get_hint(node_id) for context

    在 ReAct 循环中的用法：
      1. 工具执行前：路由器可能注入建议提示
      2. 成功后：router.record_success(node_id, tool_name)
      3. 失败后：router.record_failure(node_id, tool_name)
      4. 下次 LLM 调用前：router.get_hint(node_id) 获取上下文建议
    """

    def __init__(
        self,
        available_tools: list[str],
        failure_threshold: int | None = None,
    ):
        self._available_tools = list(available_tools)
        self._threshold = failure_threshold or config.TOOL_FAILURE_THRESHOLD
        # node_id -> tool_name -> ToolStats
        self._stats: dict[str, dict[str, ToolStats]] = {}

    def _get_stats(self, node_id: str, tool_name: str) -> ToolStats:
        if node_id not in self._stats:
            self._stats[node_id] = {}
        if tool_name not in self._stats[node_id]:
            self._stats[node_id][tool_name] = ToolStats()
        return self._stats[node_id][tool_name]

    def record_success(self, node_id: str, tool_name: str) -> None:
        """Record a successful tool call. Resets consecutive failure count.
        记录一次成功的工具调用，重置连续失败计数。"""
        stats = self._get_stats(node_id, tool_name)
        stats.calls += 1
        stats.consecutive_failures = 0
        logger.debug("[ToolRouter] %s/%s: success (total: %d calls, %d failures)",
                     node_id, tool_name, stats.calls, stats.failures)

    def record_failure(self, node_id: str, tool_name: str) -> None:
        """Record a failed tool call.
        记录一次失败的工具调用。"""
        stats = self._get_stats(node_id, tool_name)
        stats.calls += 1
        stats.failures += 1
        stats.consecutive_failures += 1
        logger.info("[ToolRouter] %s/%s: failure #%d (consecutive: %d, threshold: %d)",
                    node_id, tool_name, stats.failures, stats.consecutive_failures, self._threshold)

    def should_suggest_alternative(self, node_id: str, tool_name: str) -> bool:
        """Check if consecutive failures have exceeded the threshold.
        检查连续失败次数是否超过阈值。"""
        stats = self._get_stats(node_id, tool_name)
        return stats.consecutive_failures >= self._threshold

    def get_failing_tools(self, node_id: str) -> list[str]:
        """Return tool names that have exceeded the failure threshold for this node.
        返回在该节点中超过失败阈值的工具名称列表。"""
        if node_id not in self._stats:
            return []
        return [
            tool_name for tool_name, stats in self._stats[node_id].items()
            if stats.consecutive_failures >= self._threshold
        ]

    def get_alternative_tools(self, node_id: str, failed_tool: str) -> list[str]:
        """Suggest tools that haven't been failing for this node.
        建议在该节点中尚未连续失败的工具。"""
        failing = set(self.get_failing_tools(node_id))
        return [t for t in self._available_tools if t not in failing and t != failed_tool]

    def get_hint(self, node_id: str) -> str:
        """
        Generate a hint string for the LLM based on tool failure patterns.
        Returns empty string if no hint is needed.

        基于工具失败模式为 LLM 生成建议提示字符串。
        无需建议时返回空字符串。
        """
        failing_tools = self.get_failing_tools(node_id)
        if not failing_tools:
            return ""

        hints = []
        for tool_name in failing_tools:
            alternatives = self.get_alternative_tools(node_id, tool_name)
            stats = self._get_stats(node_id, tool_name)
            hint = (
                f"Tool '{tool_name}' has failed {stats.consecutive_failures} times consecutively. "
                f"Consider using a different approach."
            )
            if alternatives:
                hint += f" Available alternatives: {', '.join(alternatives)}."
            hints.append(hint)

        return "\n".join(hints)

    def get_node_summary(self, node_id: str) -> dict[str, Any]:
        """Return usage summary for a node (for observability).
        返回节点的工具使用摘要（用于可观测性）。"""
        if node_id not in self._stats:
            return {}
        return {
            tool_name: {
                "calls": s.calls,
                "failures": s.failures,
                "consecutive_failures": s.consecutive_failures,
                "success_rate": f"{s.success_rate:.0%}",
            }
            for tool_name, s in self._stats[node_id].items()
        }

    def reset_node(self, node_id: str) -> None:
        """Clear stats for a node (e.g. on retry).
        清除节点的统计数据（如重试时）。"""
        self._stats.pop(node_id, None)
