"""
Pydantic data models for the Manus Demo.
Defines the core data structures used across agents, memory, and tools.
Manus Demo 的 Pydantic 数据模型。
定义了贯穿 agents、memory、tools 各层的核心数据结构。

v2: Added DAG-based planning models (TaskNode, TaskEdge, DAGState, etc.)
    inspired by LangGraph's centralized-state pattern.
v2: 新增 DAG 规划模型（TaskNode、TaskEdge、DAGState 等），
    设计灵感来源于 LangGraph 的集中式状态管理模式。
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ======================================================================
# Legacy models (kept for backward compatibility / reference)
# 旧版模型（保留用于向后兼容 / 学习对比）
# ======================================================================

class StepStatus(str, Enum):
    # 步骤状态枚举：等待中 / 运行中 / 已完成 / 失败
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Step(BaseModel):
    # 旧版线性计划中的单个步骤
    id: int = Field(description="Unique step identifier")                                   # 步骤唯一 ID
    description: str = Field(description="What this step should accomplish")               # 步骤描述
    dependencies: list[int] = Field(default_factory=list, description="IDs of prerequisite steps")  # 前置步骤 ID 列表
    status: StepStatus = StepStatus.PENDING
    result: str | None = None                                                               # 执行结果


class Plan(BaseModel):
    # 旧版线性执行计划，包含有序步骤列表
    task: str = Field(description="The original user task")                                # 原始用户任务
    steps: list[Step] = Field(default_factory=list, description="Ordered list of steps")  # 有序步骤列表
    current_step_index: int = 0                                                            # 当前执行到的步骤索引


# ======================================================================
# DAG Planning Models (v2)
# DAG 规划模型（v2 新增）
# ======================================================================

# --- Node hierarchy: Goal -> SubGoal -> Action ---
# --- 节点层级：目标 -> 子目标 -> 可执行动作 ---

class NodeType(str, Enum):
    """
    Three-level task hierarchy for hierarchical planning.
    分层规划的三层节点类型。
    """
    GOAL = "goal"       # 顶层目标：整体任务意图
    SUBGOAL = "subgoal" # 子目标：目标的逻辑分组
    ACTION = "action"   # 可执行动作：叶节点，由 Executor 实际运行


class NodeStatus(str, Enum):
    """
    Node lifecycle states, managed by NodeStateMachine.
    节点生命周期状态，由 NodeStateMachine 强制管理合法转移。

    Transition graph:
    转移图：
        PENDING -> READY -> RUNNING -> COMPLETED
                                    -> FAILED -> ROLLED_BACK
        Any non-terminal state     -> SKIPPED
        任意非终态                  -> SKIPPED（条件不满足时跳过）
    """
    PENDING = "pending"         # 等待前置依赖完成
    READY = "ready"             # 依赖已满足，等待执行调度
    RUNNING = "running"         # 正在执行中
    COMPLETED = "completed"     # 成功完成（终态）
    FAILED = "failed"           # 执行失败
    SKIPPED = "skipped"         # 被跳过（终态，条件分支未满足或上游失败）
    ROLLED_BACK = "rolled_back" # 已回滚（终态，失败后执行了 rollback）


class EdgeType(str, Enum):
    """
    Types of relationships between nodes in the task DAG.
    任务 DAG 中节点之间的边类型。
    """
    DEPENDENCY = "dependency"   # 依赖边：B 必须等 A 完成才能执行
    CONDITIONAL = "conditional" # 条件边：B 仅在满足特定条件时才执行
    ROLLBACK = "rollback"       # 回滚边：A 失败时触发执行目标节点进行清理


# --- Per-node quality gates ---
# --- 每个节点的质量门控 ---

class ExitCriteria(BaseModel):
    """
    Defines what 'done' means for a node. Validated after execution.
    定义节点的「完成标准」，在节点执行完毕后由 Reflector 验证。
    """
    description: str = Field(description="Human-readable success condition")         # 人类可读的成功条件描述
    validation_prompt: str = Field(
        default="",
        description="LLM prompt to verify whether exit criteria are met",            # 用于 LLM 验证是否满足完成条件的提示词
    )
    required: bool = True  # 是否强制验证，False 表示跳过 LLM 校验直接通过


class RiskAssessment(BaseModel):
    """
    Risk and confidence metadata attached to each node at planning time.
    规划时附加在每个节点上的风险与置信度元数据。
    """
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="How likely this step succeeds")  # 成功概率 0~1
    risk_level: str = Field(default="low", description="low / medium / high")                             # 风险等级
    fallback_strategy: str = Field(default="", description="What to do if this step fails")               # 失败时的备选策略


# --- Core graph structures ---
# --- 核心图结构 ---

class TaskNode(BaseModel):
    """
    A single node in the task DAG. Can be a Goal, SubGoal, or Action.
    Only ACTION nodes are directly executable; GOAL and SUBGOAL are
    structural groupings whose status is derived from their children.

    任务 DAG 中的单个节点，可以是 Goal、SubGoal 或 Action。
    只有 ACTION 节点才会被 Executor 直接执行；
    GOAL 和 SUBGOAL 是结构性分组，其状态由子节点状态派生。
    """
    id: str = Field(description="Unique ID, e.g. 'goal_1', 'sub_1', 'act_1_1'")    # 节点唯一 ID
    node_type: NodeType                                                               # 节点类型（GOAL/SUBGOAL/ACTION）
    description: str                                                                  # 节点描述
    exit_criteria: ExitCriteria = Field(default_factory=lambda: ExitCriteria(description="Step completed successfully"))  # 完成判据
    risk: RiskAssessment = Field(default_factory=RiskAssessment)                     # 风险评估
    status: NodeStatus = NodeStatus.PENDING                                           # 当前状态，由状态机管理
    result: str | None = None                                                         # 执行结果文本
    parent_id: str | None = Field(default=None, description="Parent node for hierarchy tracking")         # 父节点 ID（用于层级追踪）
    rollback_action: str | None = Field(default=None, description="Description of how to undo this node") # 回滚操作描述


class TaskEdge(BaseModel):
    """
    A directed edge in the task DAG.
    任务 DAG 中的有向边。
    """
    source: str = Field(description="Source node ID")   # 起点节点 ID
    target: str = Field(description="Target node ID")   # 终点节点 ID
    edge_type: EdgeType = EdgeType.DEPENDENCY            # 边类型，默认为依赖边
    condition: str | None = Field(default=None, description="Condition expression for CONDITIONAL edges")  # 条件边的条件关键词


# --- Centralized state (inspired by LangGraph) ---
# --- 集中式状态（灵感来自 LangGraph）---

class DAGState(BaseModel):
    """
    Single source of truth for DAG execution state.
    DAG 执行状态的唯一数据源（Single Source of Truth）。

    Design note (LangGraph parallel):
    设计说明（对应 LangGraph 设计）：
      LangGraph uses typed channels + configurable reducers to manage
      how parallel node outputs merge into shared state. We keep it
      simple: each node writes to its own key in `node_results`, so
      parallel writes never conflict. This is equivalent to LangGraph's
      "LastValue" channel with unique keys per node.

      LangGraph 使用类型化 Channel + 可配置 Reducer 管理并行节点输出的合并。
      我们简化为：每个节点写入 node_results 中自己专属的 key，
      并行写入天然不会冲突，等价于 LangGraph 的 "LastValue" channel。
    """
    task: str = Field(description="Original user task")                 # 原始用户任务
    context: str = Field(default="", description="Accumulated knowledge/memory context")  # 从记忆/知识库检索到的背景上下文
    node_results: dict[str, str] = Field(
        default_factory=dict,
        description="node_id -> output text. The single source of truth for all results.",
        # node_id -> 输出文本。所有节点结果的唯一权威存储。
    )

    def get_node_context(self, node_id: str, dependency_ids: list[str]) -> str:
        """
        Build input context for a node by collecting results from its dependencies.
        This is the 'state-in' part of LangGraph's 'state-in-state-out' pattern.

        为节点构建输入上下文：汇集所有前置依赖节点的结果。
        对应 LangGraph 中「state-in → node → state-out」模式的 state-in 部分。
        """
        parts = []
        if self.context:
            parts.append(self.context)
        for dep_id in dependency_ids:
            if dep_id in self.node_results:
                parts.append(f"[Result of {dep_id}]:\n{self.node_results[dep_id]}")
        return "\n\n".join(parts)

    def merge_result(self, node_id: str, output: str) -> None:
        """
        Write a node's result into shared state.
        将节点的执行结果写入共享状态。

        LangGraph uses configurable Reducers (append / merge / overwrite) here.
        We use simple dict assignment — each node has a unique key, so parallel
        nodes naturally write to different keys without conflict.

        LangGraph 在此处使用可配置的 Reducer（追加/合并/覆盖）。
        我们直接用 dict 赋值——每个节点有唯一 key，
        并行节点写入不同 key，天然无冲突。
        """
        self.node_results[node_id] = output


# ======================================================================
# Execution Results
# 执行结果模型
# ======================================================================

class ToolCallRecord(BaseModel):
    # 记录一次工具调用的详情，用于 UI 展示和调试
    tool_name: str                                         # 工具名称
    parameters: dict[str, Any] = Field(default_factory=dict)  # 调用参数
    result: str = ""                                       # 工具返回结果（截断到 1000 字符）


class StepResult(BaseModel):
    """
    Result from executing a single step/node. Used by both legacy and DAG paths.
    单个步骤/节点执行完毕后的结果。旧版（Step）和 DAG（TaskNode）路径共用。
    """
    step_id: int | str = Field(description="Step ID (int for legacy, str for DAG nodes)")  # 步骤 ID（旧版为 int，DAG 为 str）
    success: bool                       # 是否执行成功
    output: str = ""                    # 最终输出文本
    tool_calls_log: list[ToolCallRecord] = Field(default_factory=list)  # 本次执行中所有工具调用记录


# ======================================================================
# Reflection
# 反思结果模型
# ======================================================================

class Reflection(BaseModel):
    # Reflector Agent 对整体执行结果的评估结论
    passed: bool = Field(description="Whether the task is considered complete")         # 是否通过质量门控
    score: float = Field(default=0.0, description="Quality score 0-1")                 # 质量评分 0~1
    feedback: str = Field(default="", description="Overall evaluation")                 # 整体评价文本
    suggestions: list[str] = Field(default_factory=list, description="Improvement suggestions")  # 改进建议列表


# ======================================================================
# Memory
# 记忆模型
# ======================================================================

class MemoryEntry(BaseModel):
    # 长期记忆中的一条记录，存储已完成任务的摘要和学习点
    task: str                                                    # 原始任务
    summary: str                                                 # 执行结果摘要
    learnings: list[str] = Field(default_factory=list)          # 从本次任务中提取的学习点
    timestamp: float = Field(default_factory=time.time)         # 记录时间戳


# ======================================================================
# Message
# 消息模型（OpenAI 格式）
# ======================================================================

class Message(BaseModel):
    # 与 LLM 对话的单条消息，兼容 OpenAI API 格式
    role: str = Field(description="One of: system, user, assistant, tool")  # 消息角色
    content: str = ""                                                         # 消息内容
    tool_calls: list[dict[str, Any]] | None = None                           # 工具调用列表（assistant 消息专用）
    tool_call_id: str | None = None                                           # 工具调用 ID（tool 消息专用）
    name: str | None = None                                                   # 工具名称（tool 消息专用）

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to OpenAI-compatible message dict.
        转换为 OpenAI 兼容的消息字典格式。
        """
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d
