"""
DAG 能力演示测试 — 测试分别体现：
  1. 自主分层规划能力 (Hierarchical Planning)
  2. 并行执行 + 外部工具调用 (Parallel Execution with Tool Calls)
  3. 条件分支 + 失败回滚 (Conditional Branching & Rollback)
  4. (v3) 动态 DAG 变更 (Dynamic DAG Mutation)
  5. (v3) 工具路由器 (Tool Router)
  6. (v3) 自适应规划集成 (Adaptive Planning Integration)

运行方式:
    .venv/bin/python -m pytest tests/test_dag_capabilities.py -v

所有测试均不依赖真实 LLM API，通过 Mock 模拟 LLM 响应，
但使用真实的 DAG 基础设施（TaskDAG、NodeStateMachine、DAGExecutor）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from dag.graph import TaskDAG
from dag.executor import DAGExecutor
from dag.state_machine import NodeStateMachine, InvalidTransitionError
from schema import (
    DAGState,
    EdgeType,
    ExitCriteria,
    NodeStatus,
    NodeType,
    RiskAssessment,
    StepResult,
    TaskEdge,
    TaskNode,
    ToolCallRecord,
)


# ======================================================================
# Helper: 构建一个 "搜索 Python 信息并生成报告" 的三层 DAG
# ======================================================================


def _build_research_dag() -> TaskDAG:
    """
    模拟 Planner 自主生成的分层计划:

        goal_1 (Goal: 调研 Python 并生成报告)
        ├── sub_1 (SubGoal: 收集信息)
        │   ├── act_1_1 (Action: 搜索 Python 基础信息)  ← web_search
        │   └── act_1_2 (Action: 获取 Python 版本)      ← execute_python
        └── sub_2 (SubGoal: 整理输出)
            └── act_2_1 (Action: 将结果写入报告)        ← file_ops

    act_1_1 和 act_1_2 可以**并行**执行 (无相互依赖)。
    act_2_1 依赖 act_1_1 和 act_1_2 的结果。
    """
    nodes: dict[str, TaskNode] = {
        "goal_1": TaskNode(
            id="goal_1",
            node_type=NodeType.GOAL,
            description="调研 Python 语言并生成简要报告",
            exit_criteria=ExitCriteria(description="报告文件已生成"),
            risk=RiskAssessment(confidence=0.9, risk_level="low"),
        ),
        "sub_1": TaskNode(
            id="sub_1",
            node_type=NodeType.SUBGOAL,
            description="收集 Python 相关信息",
            parent_id="goal_1",
            exit_criteria=ExitCriteria(description="至少获得 2 条信息"),
            risk=RiskAssessment(confidence=0.85, risk_level="low"),
        ),
        "sub_2": TaskNode(
            id="sub_2",
            node_type=NodeType.SUBGOAL,
            description="整理信息并输出报告",
            parent_id="goal_1",
            exit_criteria=ExitCriteria(description="报告已写入文件"),
            risk=RiskAssessment(confidence=0.8, risk_level="medium"),
        ),
        "act_1_1": TaskNode(
            id="act_1_1",
            node_type=NodeType.ACTION,
            description="使用 web_search 工具搜索 Python 基础信息",
            parent_id="sub_1",
            exit_criteria=ExitCriteria(description="返回搜索结果"),
            risk=RiskAssessment(confidence=0.95, risk_level="low"),
            rollback_action="无需回滚，搜索为只读操作",
        ),
        "act_1_2": TaskNode(
            id="act_1_2",
            node_type=NodeType.ACTION,
            description="使用 execute_python 工具获取当前 Python 版本号",
            parent_id="sub_1",
            exit_criteria=ExitCriteria(description="成功输出版本号"),
            risk=RiskAssessment(confidence=0.9, risk_level="low"),
            rollback_action="无需回滚",
        ),
        "act_2_1": TaskNode(
            id="act_2_1",
            node_type=NodeType.ACTION,
            description="使用 file_ops 工具将收集到的信息写入 report.md",
            parent_id="sub_2",
            exit_criteria=ExitCriteria(description="文件写入成功"),
            risk=RiskAssessment(confidence=0.85, risk_level="medium", fallback_strategy="重试写入"),
        ),
    }

    edges: list[TaskEdge] = [
        TaskEdge(source="goal_1", target="sub_1", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="goal_1", target="sub_2", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="sub_1", target="act_1_1", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="sub_1", target="act_1_2", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="act_1_1", target="act_2_1", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="act_1_2", target="act_2_1", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="sub_2", target="act_2_1", edge_type=EdgeType.DEPENDENCY),
    ]

    return TaskDAG(
        task="调研 Python 语言并生成简要报告",
        nodes=nodes,
        edges=edges,
    )


# ======================================================================
# Test 1: 自主分层规划能力
# ======================================================================


class TestHierarchicalPlanning:
    """
    验证 DAG 的分层结构和自主规划特性:
    - Goal → SubGoal → Action 三层分解
    - 拓扑排序保证执行顺序
    - 并行节点识别
    - 每个节点都附带 exit_criteria 和 risk_assessment
    """

    def test_hierarchy_structure(self):
        """验证三层层级: 1 Goal, 2 SubGoals, 3 Actions."""
        dag = _build_research_dag()

        goals = [n for n in dag.nodes.values() if n.node_type == NodeType.GOAL]
        subgoals = [n for n in dag.nodes.values() if n.node_type == NodeType.SUBGOAL]
        actions = [n for n in dag.nodes.values() if n.node_type == NodeType.ACTION]

        assert len(goals) == 1, "应有 1 个顶层 Goal"
        assert len(subgoals) == 2, "应有 2 个 SubGoal"
        assert len(actions) == 3, "应有 3 个 Action (可执行叶节点)"

        for sg in subgoals:
            assert sg.parent_id == "goal_1", f"SubGoal {sg.id} 的 parent 应为 goal_1"
        for act in actions:
            assert act.parent_id in ("sub_1", "sub_2"), f"Action {act.id} 应挂载在某个 SubGoal 下"

    def test_topological_order(self):
        """验证拓扑排序: 父节点总是排在子节点之前."""
        dag = _build_research_dag()
        order = dag.topological_sort()
        idx = {nid: i for i, nid in enumerate(order)}

        assert idx["goal_1"] < idx["sub_1"], "goal_1 必须在 sub_1 之前"
        assert idx["sub_1"] < idx["act_1_1"], "sub_1 必须在 act_1_1 之前"
        assert idx["sub_1"] < idx["act_1_2"], "sub_1 必须在 act_1_2 之前"
        assert idx["act_1_1"] < idx["act_2_1"], "act_1_1 必须在 act_2_1 之前"
        assert idx["act_1_2"] < idx["act_2_1"], "act_1_2 必须在 act_2_1 之前"

    def test_parallel_ready_detection(self):
        """
        验证并行就绪检测: 当 sub_1 完成后，act_1_1 和 act_1_2
        应同时变为 READY，体现 DAG 驱动的并行能力.
        """
        dag = _build_research_dag()

        # 初始状态: goal_1 是唯一的就绪节点 (无前置依赖)
        dag.refresh_ready_states()
        ready_ids = {n.id for n in dag.get_ready_nodes()}
        assert "goal_1" in ready_ids

        # 模拟 goal_1 完成
        dag.nodes["goal_1"].status = NodeStatus.COMPLETED
        dag.refresh_ready_states()
        ready_ids = {n.id for n in dag.get_ready_nodes()}
        assert "sub_1" in ready_ids and "sub_2" in ready_ids, "goal_1 完成后 sub_1, sub_2 应并行就绪"

        # 模拟 sub_1 完成 (sub_2 也完成以满足 act_2_1 的依赖)
        dag.nodes["sub_1"].status = NodeStatus.COMPLETED
        dag.refresh_ready_states()
        ready_ids = {n.id for n in dag.get_ready_nodes()}
        assert "act_1_1" in ready_ids and "act_1_2" in ready_ids, (
            "sub_1 完成后 act_1_1 和 act_1_2 应**并行**就绪 — 这是 DAG 并行的核心"
        )
        assert "act_2_1" not in ready_ids, "act_2_1 还在等待 act_1_1 和 act_1_2 完成"

    def test_exit_criteria_and_risk(self):
        """验证每个节点都携带完成判据和风险评估."""
        dag = _build_research_dag()
        for node in dag.nodes.values():
            assert node.exit_criteria is not None, f"{node.id} 缺少 exit_criteria"
            assert node.exit_criteria.description, f"{node.id} 的 exit_criteria.description 为空"
            assert node.risk is not None, f"{node.id} 缺少 risk"
            assert 0.0 <= node.risk.confidence <= 1.0, f"{node.id} 的 confidence 不合法"
            assert node.risk.risk_level in ("low", "medium", "high"), f"{node.id} 的 risk_level 不合法"


# ======================================================================
# Test 2: 并行执行 + 外部工具调用
# ======================================================================


class TestParallelExecutionWithTools:
    """
    验证 DAGExecutor 的并行执行机制和工具调用链路:
    - Mock ExecutorAgent.execute_node() 模拟 ReAct 工具调用
    - 真实运行 DAGExecutor 的 super-step 循环
    - 验证并行执行、状态合并、工具调用记录、checkpoint
    """

    @pytest.mark.asyncio
    async def test_superstep_parallel_with_tools(self):
        """
        场景: act_1_1(web_search) 和 act_1_2(execute_python) 并行执行,
              然后 act_2_1(file_ops) 串行执行.

        验证:
          1. 工具调用记录 (ToolCallRecord) 正确反映每个 Action 调用的工具
          2. 并行节点的结果正确合并到 DAGState
          3. 每个 super-step 都产生 checkpoint
        """
        dag = _build_research_dag()

        # --- 预先将结构节点标记完成，聚焦测试 ACTION 节点 ---
        dag.nodes["goal_1"].status = NodeStatus.COMPLETED
        dag.nodes["sub_1"].status = NodeStatus.COMPLETED
        dag.nodes["sub_2"].status = NodeStatus.COMPLETED

        # --- Mock: 模拟 ExecutorAgent 的 ReAct 工具调用 ---
        mock_executor_agent = AsyncMock()

        async def fake_execute_node(node: TaskNode, context: str = "") -> StepResult:
            """根据节点 ID 返回不同的工具调用结果，模拟 ReAct 循环."""
            tool_map = {
                "act_1_1": StepResult(
                    step_id="act_1_1",
                    success=True,
                    output="Python 是高级编程语言，支持多种编程范式。",
                    tool_calls_log=[
                        ToolCallRecord(
                            tool_name="web_search",
                            parameters={"query": "Python 编程语言"},
                            result="Search results for: 'Python 编程语言'...",
                        )
                    ],
                ),
                "act_1_2": StepResult(
                    step_id="act_1_2",
                    success=True,
                    output="当前 Python 版本: 3.12.0",
                    tool_calls_log=[
                        ToolCallRecord(
                            tool_name="execute_python",
                            parameters={"code": "import sys; print(sys.version)"},
                            result="Output:\n3.12.0",
                        )
                    ],
                ),
                "act_2_1": StepResult(
                    step_id="act_2_1",
                    success=True,
                    output="报告已写入 report.md (156 字符)",
                    tool_calls_log=[
                        ToolCallRecord(
                            tool_name="file_ops",
                            parameters={"action": "write", "filename": "report.md", "content": "..."},
                            result="Successfully wrote 156 characters to report.md",
                        )
                    ],
                ),
            }
            return tool_map[node.id]

        mock_executor_agent.execute_node = AsyncMock(side_effect=fake_execute_node)

        # Mock ReflectorAgent (exit criteria 全部通过)
        mock_reflector = AsyncMock()
        mock_reflector.validate_exit_criteria = AsyncMock(return_value=True)

        # --- 收集事件用于验证 ---
        events: list[tuple[str, dict]] = []

        dag_executor = DAGExecutor(
            executor_agent=mock_executor_agent,
            reflector_agent=mock_reflector,
            max_parallel=3,
            on_event=lambda etype, data: events.append((etype, data)),
        )

        # --- 执行 ---
        output = await dag_executor.execute(dag)

        # --- 验证 1: 所有 ACTION 节点执行成功 ---
        for nid in ("act_1_1", "act_1_2", "act_2_1"):
            assert dag.nodes[nid].status == NodeStatus.COMPLETED, f"{nid} 应为 COMPLETED"

        # --- 验证 2: 工具调用记录 ---
        call_args = [c.args for c in mock_executor_agent.execute_node.call_args_list]
        called_node_ids = {args[0].id for args in call_args}
        assert called_node_ids == {"act_1_1", "act_1_2", "act_2_1"}, "三个 Action 节点都应被调用"

        # --- 验证 3: DAGState 中结果正确合并 ---
        assert "act_1_1" in dag.state.node_results
        assert "Python" in dag.state.node_results["act_1_1"]
        assert "act_1_2" in dag.state.node_results
        assert "3.12" in dag.state.node_results["act_1_2"]
        assert "act_2_1" in dag.state.node_results
        assert "report.md" in dag.state.node_results["act_2_1"]

        # --- 验证 4: 并行执行 — act_1_1 和 act_1_2 应在同一个 super-step ---
        superstep_events = [e for e in events if e[0] == "superstep"]
        assert len(superstep_events) >= 1, "至少有 1 个 superstep 事件"
        first_batch_nodes = superstep_events[0][1]["nodes"]
        assert "act_1_1" in first_batch_nodes and "act_1_2" in first_batch_nodes, (
            "act_1_1 和 act_1_2 应在同一个 super-step 中并行执行"
        )

        # --- 验证 5: Checkpoint 已保存 ---
        assert len(dag.checkpoints) >= 2, "至少 2 个 checkpoint (每个 super-step 一个)"

        # --- 验证 6: 最终输出包含所有工具产出 ---
        assert "Python" in output
        assert "report.md" in output


# ======================================================================
# Test 3: 条件分支 + 失败回滚
# ======================================================================


class TestConditionalBranchAndRollback:
    """
    验证 DAG 的动态决策能力:
    - 条件分支: 根据节点执行结果动态启用/跳过下游路径
    - 失败回滚: 节点失败时触发 rollback 节点并跳过下游子树
    - 状态机: 全程通过 NodeStateMachine 强制合法状态转移
    """

    @pytest.mark.asyncio
    async def test_conditional_branch_and_rollback(self):
        """
        场景 (DAG 拓扑):

            act_check ──(DEPENDENCY)──────────> act_write
                      ──(DEPENDENCY)──────────> act_deep_search
                      ──(CONDITIONAL:"需要深入")> act_deep_search
            act_risky ──(DEPENDENCY)──────────> act_final
                      ──(DEPENDENCY)──────────> act_cleanup   ← 同时作为 rollback
                      ──(ROLLBACK)────────────> act_cleanup

        关键设计:
          - act_deep_search 有 DEPENDENCY + CONDITIONAL 双边:
            DEPENDENCY 确保它等待 act_check 完成;
            CONDITIONAL 在 act_check 完成后评估条件 — 若不满足则跳过。
          - act_cleanup 有 DEPENDENCY on act_risky:
            正常路径下 act_risky 需 COMPLETED 才能触发 act_cleanup;
            act_risky 失败时，rollback handler 直接拉起 PENDING 状态的 act_cleanup。

        流程:
          1. Super-step 1: act_check 和 act_risky 并行执行
             - act_check 成功，结果包含 "需要深入" → 条件满足，act_deep_search 保留
             - act_risky 失败 → 触发 rollback(act_cleanup)，下游 act_final 跳过
          2. Super-step 2: act_write 和 act_deep_search 并行执行 (均依赖 act_check)
        """
        nodes: dict[str, TaskNode] = {
            "act_check": TaskNode(
                id="act_check",
                node_type=NodeType.ACTION,
                description="检查是否需要深入搜索",
                exit_criteria=ExitCriteria(description="返回检查结果", required=False),
            ),
            "act_deep_search": TaskNode(
                id="act_deep_search",
                node_type=NodeType.ACTION,
                description="执行深入搜索 (条件分支)",
                exit_criteria=ExitCriteria(description="返回搜索结果", required=False),
            ),
            "act_write": TaskNode(
                id="act_write",
                node_type=NodeType.ACTION,
                description="写入初步结果",
                exit_criteria=ExitCriteria(description="写入成功", required=False),
            ),
            "act_risky": TaskNode(
                id="act_risky",
                node_type=NodeType.ACTION,
                description="执行高风险操作 (模拟失败)",
                risk=RiskAssessment(confidence=0.3, risk_level="high", fallback_strategy="执行清理"),
                exit_criteria=ExitCriteria(description="操作完成", required=False),
            ),
            "act_cleanup": TaskNode(
                id="act_cleanup",
                node_type=NodeType.ACTION,
                description="回滚清理操作",
                exit_criteria=ExitCriteria(description="清理完成", required=False),
            ),
            "act_final": TaskNode(
                id="act_final",
                node_type=NodeType.ACTION,
                description="最终汇总 (依赖 act_risky)",
                exit_criteria=ExitCriteria(description="汇总完成", required=False),
            ),
        }

        edges: list[TaskEdge] = [
            # act_check → act_write (普通依赖)
            TaskEdge(source="act_check", target="act_write", edge_type=EdgeType.DEPENDENCY),
            # act_check → act_deep_search (DEPENDENCY 保证等待 + CONDITIONAL 控制是否跳过)
            TaskEdge(source="act_check", target="act_deep_search", edge_type=EdgeType.DEPENDENCY),
            TaskEdge(source="act_check", target="act_deep_search",
                     edge_type=EdgeType.CONDITIONAL, condition="需要深入"),
            # act_risky → act_final (依赖)
            TaskEdge(source="act_risky", target="act_final", edge_type=EdgeType.DEPENDENCY),
            # act_risky → act_cleanup (DEPENDENCY 防止自动执行 + ROLLBACK 支持失败回滚)
            TaskEdge(source="act_risky", target="act_cleanup", edge_type=EdgeType.DEPENDENCY),
            TaskEdge(source="act_risky", target="act_cleanup", edge_type=EdgeType.ROLLBACK),
        ]

        dag = TaskDAG(task="条件分支与回滚演示", nodes=nodes, edges=edges)

        # --- Mock ExecutorAgent ---
        mock_executor = AsyncMock()

        async def fake_execute(node: TaskNode, context: str = "") -> StepResult:
            results = {
                "act_check": StepResult(
                    step_id="act_check", success=True,
                    output="分析完成。结论: 需要深入研究 Python 的并发模型。",
                    tool_calls_log=[
                        ToolCallRecord(tool_name="web_search",
                                       parameters={"query": "Python concurrency"},
                                       result="Search results...")
                    ],
                ),
                "act_deep_search": StepResult(
                    step_id="act_deep_search", success=True,
                    output="深入搜索完成: asyncio, threading, multiprocessing 三种模型",
                    tool_calls_log=[
                        ToolCallRecord(tool_name="web_search",
                                       parameters={"query": "Python asyncio vs threading"},
                                       result="Detailed results...")
                    ],
                ),
                "act_write": StepResult(
                    step_id="act_write", success=True,
                    output="初步结果已写入 draft.md",
                    tool_calls_log=[
                        ToolCallRecord(tool_name="file_ops",
                                       parameters={"action": "write", "filename": "draft.md"},
                                       result="Written successfully")
                    ],
                ),
                "act_risky": StepResult(
                    step_id="act_risky", success=False,
                    output="Error: 操作超时，执行失败",
                ),
                "act_cleanup": StepResult(
                    step_id="act_cleanup", success=True,
                    output="回滚清理完成: 已删除临时文件",
                    tool_calls_log=[
                        ToolCallRecord(tool_name="file_ops",
                                       parameters={"action": "list"},
                                       result="Sandbox cleaned")
                    ],
                ),
            }
            return results[node.id]

        mock_executor.execute_node = AsyncMock(side_effect=fake_execute)
        mock_reflector = AsyncMock()
        mock_reflector.validate_exit_criteria = AsyncMock(return_value=True)

        events: list[tuple[str, dict]] = []

        dag_executor = DAGExecutor(
            executor_agent=mock_executor,
            reflector_agent=mock_reflector,
            on_event=lambda etype, data: events.append((etype, data)),
        )

        await dag_executor.execute(dag)

        # --- 验证 1: 条件分支被正确触发 ---
        condition_events = [e for e in events if e[0] == "condition_evaluated"]
        assert len(condition_events) >= 1, "应有条件评估事件"
        assert condition_events[0][1]["met"] is True, "条件 '需要深入' 应被判定为满足"
        assert dag.nodes["act_deep_search"].status == NodeStatus.COMPLETED, (
            "条件满足时 act_deep_search 应正常执行完成"
        )

        # --- 验证 2: 失败触发回滚 ---
        assert dag.nodes["act_risky"].status in (NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED), (
            "act_risky 失败后应被标记为 ROLLED_BACK 或 SKIPPED"
        )
        assert dag.nodes["act_cleanup"].status == NodeStatus.COMPLETED, (
            "回滚节点 act_cleanup 应被 rollback handler 执行成功"
        )

        # --- 验证 3: 下游子树被跳过 ---
        assert dag.nodes["act_final"].status == NodeStatus.SKIPPED, (
            "act_risky 失败后，其下游 act_final 应被自动跳过"
        )

        # --- 验证 4: 正常路径节点均完成 ---
        assert dag.nodes["act_check"].status == NodeStatus.COMPLETED
        assert dag.nodes["act_write"].status == NodeStatus.COMPLETED

        # --- 验证 5: 状态机合法性 — 终态节点不可再转移 ---
        sm = NodeStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(dag.nodes["act_check"], NodeStatus.RUNNING)

        # --- 验证 6: 工具调用贯穿全流程 ---
        called_ids = {c.args[0].id for c in mock_executor.execute_node.call_args_list}
        assert "act_check" in called_ids, "act_check 应调用了 web_search"
        assert "act_write" in called_ids, "act_write 应调用了 file_ops"
        assert "act_cleanup" in called_ids, "act_cleanup (回滚) 应调用了 file_ops"


# ======================================================================
# Test 4 (v3): 动态 DAG 变更
# ======================================================================


class TestDynamicDAGMutation:
    """
    验证 v3 新增的动态 DAG 变更能力:
    - 运行时添加/移除/修改节点
    - 动态添加边
    - 变更后 DAG 的就绪检测仍正确
    """

    def test_add_dynamic_node(self):
        """验证运行时可以添加新的 ACTION 节点."""
        dag = _build_research_dag()
        original_count = len(dag.nodes)

        new_node = TaskNode(
            id="act_dynamic_1",
            node_type=NodeType.ACTION,
            description="动态添加的搜索节点",
            parent_id="sub_1",
        )
        assert dag.add_dynamic_node(new_node), "添加新节点应成功"
        assert len(dag.nodes) == original_count + 1
        assert "act_dynamic_1" in dag.nodes

        assert not dag.add_dynamic_node(new_node), "重复添加同 ID 节点应失败"

    def test_add_dynamic_edge(self):
        """验证运行时可以添加新的边."""
        dag = _build_research_dag()
        original_edge_count = len(dag.edges)

        new_node = TaskNode(
            id="act_new",
            node_type=NodeType.ACTION,
            description="新节点",
            parent_id="sub_1",
        )
        dag.add_dynamic_node(new_node)

        edge = TaskEdge(source="act_1_1", target="act_new", edge_type=EdgeType.DEPENDENCY)
        assert dag.add_dynamic_edge(edge), "添加新边应成功"
        assert len(dag.edges) == original_edge_count + 1

        assert not dag.add_dynamic_edge(edge), "重复添加同一边应失败（去重）"

        bad_edge = TaskEdge(source="nonexistent", target="act_new", edge_type=EdgeType.DEPENDENCY)
        assert not dag.add_dynamic_edge(bad_edge), "源节点不存在时添加应失败"

    def test_remove_pending_node(self):
        """验证可以移除 PENDING 状态的节点."""
        dag = _build_research_dag()
        original_count = len(dag.nodes)

        assert dag.remove_pending_node("act_2_1"), "移除 PENDING 节点应成功"
        assert len(dag.nodes) == original_count - 1
        assert "act_2_1" not in dag.nodes
        assert all(e.source != "act_2_1" and e.target != "act_2_1" for e in dag.edges), \
            "关联的边也应被移除"

    def test_cannot_remove_completed_node(self):
        """验证不能移除已完成的节点."""
        dag = _build_research_dag()
        dag.nodes["act_1_1"].status = NodeStatus.COMPLETED
        assert not dag.remove_pending_node("act_1_1"), "已完成节点不可移除"

    def test_modify_node(self):
        """验证可以修改 PENDING 节点的描述和完成判据."""
        dag = _build_research_dag()
        assert dag.modify_node("act_1_1", description="改为搜索 Go 语言信息")
        assert dag.nodes["act_1_1"].description == "改为搜索 Go 语言信息"

        assert dag.modify_node("act_1_2", exit_criteria_desc="返回 Go 版本号")
        assert dag.nodes["act_1_2"].exit_criteria.description == "返回 Go 版本号"

    def test_dynamic_node_readiness(self):
        """验证动态添加的节点在依赖满足后能正确变为 READY."""
        dag = _build_research_dag()

        # 添加一个依赖 act_1_1 的动态节点
        new_node = TaskNode(
            id="act_dynamic_extra",
            node_type=NodeType.ACTION,
            description="基于搜索结果的额外分析",
            parent_id="sub_1",
        )
        dag.add_dynamic_node(new_node)
        dag.add_dynamic_edge(TaskEdge(source="sub_1", target="act_dynamic_extra", edge_type=EdgeType.DEPENDENCY))
        dag.add_dynamic_edge(TaskEdge(source="act_1_1", target="act_dynamic_extra", edge_type=EdgeType.DEPENDENCY))

        # 模拟 sub_1 和 act_1_1 完成
        dag.nodes["goal_1"].status = NodeStatus.COMPLETED
        dag.nodes["sub_1"].status = NodeStatus.COMPLETED
        dag.nodes["act_1_1"].status = NodeStatus.COMPLETED
        dag.refresh_ready_states()

        ready_ids = {n.id for n in dag.get_ready_nodes()}
        assert "act_dynamic_extra" in ready_ids, "动态节点在依赖满足后应变为 READY"

    def test_get_pending_action_nodes(self):
        """验证 get_pending_action_nodes 正确返回."""
        dag = _build_research_dag()
        pending = dag.get_pending_action_nodes()
        assert len(pending) == 3, "初始应有 3 个 PENDING ACTION 节点"

        dag.nodes["act_1_1"].status = NodeStatus.COMPLETED
        pending = dag.get_pending_action_nodes()
        assert len(pending) == 2, "完成一个后应剩 2 个"


# ======================================================================
# Test 5 (v3): 工具路由器
# ======================================================================


class TestToolRouter:
    """
    验证 v3 工具路由器的失败追踪和替代建议能力:
    - 成功/失败记录
    - 连续失败阈值触发
    - 替代工具建议
    - 上下文提示生成
    """

    def test_success_resets_consecutive_failures(self):
        """验证成功调用会重置连续失败计数."""
        from tools.router import ToolRouter

        router = ToolRouter(available_tools=["web_search", "execute_python", "file_ops"], failure_threshold=2)
        router.record_failure("node_1", "web_search")
        router.record_failure("node_1", "web_search")
        assert router.should_suggest_alternative("node_1", "web_search"), "连续失败 2 次应触发建议"

        router.record_success("node_1", "web_search")
        assert not router.should_suggest_alternative("node_1", "web_search"), "成功后应重置"

    def test_failure_threshold(self):
        """验证连续失败达到阈值后触发建议."""
        from tools.router import ToolRouter

        router = ToolRouter(available_tools=["web_search", "execute_python"], failure_threshold=3)
        for _ in range(2):
            router.record_failure("node_1", "web_search")
        assert not router.should_suggest_alternative("node_1", "web_search"), "未达阈值不应触发"

        router.record_failure("node_1", "web_search")
        assert router.should_suggest_alternative("node_1", "web_search"), "达到阈值应触发"

    def test_alternative_suggestion(self):
        """验证替代工具建议排除了失败工具."""
        from tools.router import ToolRouter

        router = ToolRouter(available_tools=["web_search", "execute_python", "file_ops"], failure_threshold=2)
        router.record_failure("node_1", "web_search")
        router.record_failure("node_1", "web_search")

        alternatives = router.get_alternative_tools("node_1", "web_search")
        assert "web_search" not in alternatives
        assert "execute_python" in alternatives
        assert "file_ops" in alternatives

    def test_hint_generation(self):
        """验证提示信息生成."""
        from tools.router import ToolRouter

        router = ToolRouter(available_tools=["web_search", "execute_python"], failure_threshold=2)
        assert router.get_hint("node_1") == "", "无失败时应返回空提示"

        router.record_failure("node_1", "web_search")
        router.record_failure("node_1", "web_search")
        hint = router.get_hint("node_1")
        assert "web_search" in hint, "提示中应包含失败工具名"
        assert "failed" in hint.lower(), "提示中应提及失败"

    def test_node_isolation(self):
        """验证不同节点的统计互相隔离."""
        from tools.router import ToolRouter

        router = ToolRouter(available_tools=["web_search"], failure_threshold=2)
        router.record_failure("node_1", "web_search")
        router.record_failure("node_1", "web_search")
        assert router.should_suggest_alternative("node_1", "web_search")
        assert not router.should_suggest_alternative("node_2", "web_search"), "node_2 无失败记录"


# ======================================================================
# Test 6 (v3): 自适应规划集成 — DAGExecutor 中超步间调整
# ======================================================================


class TestAdaptivePlanningIntegration:
    """
    验证 v3 自适应规划在 DAGExecutor 中的集成:
    - DAGExecutor 在超步间调用 Planner.adapt_plan()
    - 调整操作（REMOVE/MODIFY/ADD）正确应用到 DAG
    - 事件正确发出
    """

    @pytest.mark.asyncio
    async def test_adaptive_remove_node(self):
        """
        场景: 执行完 act_1_1 后，自适应规划判断 act_2_1 不再需要，将其移除。
        """
        from schema import AdaptAction, AdaptationResult, PlanAdaptation

        dag = _build_research_dag()
        dag.nodes["goal_1"].status = NodeStatus.COMPLETED
        dag.nodes["sub_1"].status = NodeStatus.COMPLETED
        dag.nodes["sub_2"].status = NodeStatus.COMPLETED

        mock_executor_agent = AsyncMock()

        call_count = 0

        async def fake_execute(node: TaskNode, context: str = "") -> StepResult:
            nonlocal call_count
            call_count += 1
            return StepResult(step_id=node.id, success=True, output=f"Result of {node.id}")

        mock_executor_agent.execute_node = AsyncMock(side_effect=fake_execute)

        mock_reflector = AsyncMock()
        mock_reflector.validate_exit_criteria = AsyncMock(return_value=True)

        # Mock Planner with adaptive planning
        mock_planner = AsyncMock()
        adapt_call_count = 0

        async def fake_adapt(d):
            nonlocal adapt_call_count
            adapt_call_count += 1
            if adapt_call_count == 1:
                return AdaptationResult(
                    should_adapt=True,
                    reasoning="Based on act_1_1 results, act_2_1 is no longer needed.",
                    adaptations=[
                        PlanAdaptation(
                            action=AdaptAction.REMOVE,
                            target_node_id="act_2_1",
                            reason="已不需要写报告",
                        )
                    ],
                )
            return AdaptationResult(should_adapt=False, reasoning="No further changes needed.")

        mock_planner.adapt_plan = AsyncMock(side_effect=fake_adapt)

        def fake_apply(d, adaptations):
            changes = []
            for a in adaptations:
                if a.action == AdaptAction.REMOVE:
                    if d.remove_pending_node(a.target_node_id):
                        changes.append(f"Removed '{a.target_node_id}': {a.reason}")
            return changes

        mock_planner.apply_adaptations = MagicMock(side_effect=fake_apply)

        events = []
        dag_executor = DAGExecutor(
            executor_agent=mock_executor_agent,
            reflector_agent=mock_reflector,
            planner_agent=mock_planner,
            max_parallel=3,
            on_event=lambda etype, data: events.append((etype, data)),
        )

        # Ensure adaptive is enabled
        import config as cfg
        original_enabled = cfg.ADAPTIVE_PLANNING_ENABLED
        original_min = cfg.ADAPT_PLAN_MIN_COMPLETED
        cfg.ADAPTIVE_PLANNING_ENABLED = True
        cfg.ADAPT_PLAN_MIN_COMPLETED = 1

        try:
            output = await dag_executor.execute(dag)
        finally:
            cfg.ADAPTIVE_PLANNING_ENABLED = original_enabled
            cfg.ADAPT_PLAN_MIN_COMPLETED = original_min

        # act_2_1 should have been removed
        assert "act_2_1" not in dag.nodes, "act_2_1 应已被自适应规划移除"

        # act_1_1 and act_1_2 should have completed
        assert dag.nodes["act_1_1"].status == NodeStatus.COMPLETED
        assert dag.nodes["act_1_2"].status == NodeStatus.COMPLETED

        # Verify adaptation event was emitted
        adaptation_events = [e for e in events if e[0] == "plan_adaptation"]
        assert any(e[1].get("adapted") for e in adaptation_events), "应有 adapted=True 的事件"
