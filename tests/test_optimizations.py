"""
Test suite for DAG optimizations.
测试 DAG 优化的测试套件。

包括：
  - P0: 失败恢复增强 (try_recover_blocked_nodes)
  - P1: 边界情况处理
  - P2: Checkpoint 机制
  - P3: 邻接表正确性
"""

import pytest
from dag.graph import TaskDAG
from dag.state_machine import NodeStateMachine
from schema import EdgeType, NodeStatus, NodeType, TaskEdge, TaskNode


class TestP1BoundaryConditions:
    """P1: 边界情况处理测试"""

    def test_empty_dag_is_complete(self):
        """测试空 DAG 的 is_complete()"""
        dag = TaskDAG(task="empty task", nodes={}, edges=[])
        assert dag.is_complete() is True

    def test_single_node_dag(self):
        """测试单节点 DAG"""
        node = TaskNode(
            id="node_1",
            description="Single task",
            node_type=NodeType.ACTION,
        )
        dag = TaskDAG(task="single task", nodes={"node_1": node}, edges=[])

        assert dag.is_complete() is False

        node.status = NodeStatus.COMPLETED
        assert dag.is_complete() is True

    def test_has_failed_nodes(self):
        """测试 has_failed_nodes()"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                status=NodeStatus.COMPLETED,
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.FAILED,
            ),
        }
        dag = TaskDAG(task="test", nodes=nodes, edges=[])

        assert dag.has_failed_nodes() is True

        nodes["node_2"].status = NodeStatus.SKIPPED
        assert dag.has_failed_nodes() is False

    def test_get_blockage_report_empty_dag(self):
        """测试空 DAG 的阻塞报告"""
        dag = TaskDAG(task="empty", nodes={}, edges=[])
        report = dag.get_blockage_report()

        assert report["total_nodes"] == 0
        assert report["status_counts"] == {}
        assert report["stuck_nodes"] == []
        assert report["has_blockage"] is False

    def test_get_blockage_report_no_blockage(self):
        """测试无阻塞的 DAG"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                status=NodeStatus.COMPLETED,
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.READY,
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)
        report = dag.get_blockage_report()

        assert report["has_blockage"] is False
        assert len(report["stuck_nodes"]) == 0

    def test_get_blockage_report_with_blockage(self):
        """测试有阻塞的 DAG"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                status=NodeStatus.RUNNING,
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)
        report = dag.get_blockage_report()

        assert report["has_blockage"] is True
        assert len(report["stuck_nodes"]) == 1
        assert report["stuck_nodes"][0]["node_id"] == "node_2"
        assert "node_1" in report["stuck_nodes"][0]["blocked_by"]


class TestP2CheckpointMechanism:
    """P2: Checkpoint 机制测试（匹配实际 API）"""

    def test_checkpoint_saves_state(self):
        """测试 save_checkpoint 捕获当前状态"""
        dag = TaskDAG(task="test", nodes={}, edges=[])
        dag.save_checkpoint()
        assert len(dag.checkpoints) == 1

    def test_checkpoint_limit(self):
        """测试 checkpoint 数量受 MAX_CHECKPOINTS 限制"""
        dag = TaskDAG(task="test", nodes={}, edges=[])
        for _ in range(20):
            dag.save_checkpoint()
        assert len(dag.checkpoints) <= 10  # Default MAX_CHECKPOINTS from config

    def test_checkpoint_content(self):
        """测试 checkpoint 包含预期的序列化数据"""
        node = TaskNode(id="node_1", description="Task", node_type=NodeType.ACTION)
        dag = TaskDAG(task="test", nodes={"node_1": node}, edges=[])
        dag.save_checkpoint()
        assert dag.checkpoints[0]["task"] == "test"
        assert "node_1" in dag.checkpoints[0]["nodes"]

    def test_checkpoint_readonly(self):
        """测试 checkpoints 属性返回只读副本"""
        dag = TaskDAG(task="test", nodes={}, edges=[])
        dag.save_checkpoint()
        cp = dag.checkpoints
        assert isinstance(cp, list)
        # Modifying the returned list shouldn't affect internal state
        cp.clear()
        assert len(dag.checkpoints) == 1


class TestP3AdjacencyOptimization:
    """P3: 邻接表正确性测试"""

    def test_dependency_lookup_via_adjacency(self):
        """测试 get_dependency_ids 使用预构建的反向邻接表"""
        nodes = {
            "node_1": TaskNode(id="node_1", description="T1", node_type=NodeType.ACTION),
            "node_2": TaskNode(id="node_2", description="T2", node_type=NodeType.ACTION),
        }
        edges = [TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY)]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)
        assert dag.get_dependency_ids("node_2") == ["node_1"]

    def test_adjacency_updated_on_node_remove(self):
        """测试移除节点后邻接表正确更新"""
        nodes = {
            "node_1": TaskNode(id="node_1", description="T1", node_type=NodeType.ACTION),
            "node_2": TaskNode(id="node_2", description="T2", node_type=NodeType.ACTION),
        }
        edges = [TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY)]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)
        dag.remove_pending_node("node_1")
        assert dag.get_dependency_ids("node_2") == []

    def test_adjacency_updated_on_edge_add(self):
        """测试动态添加边后邻接表正确更新"""
        nodes = {
            "node_1": TaskNode(id="node_1", description="T1", node_type=NodeType.ACTION),
            "node_2": TaskNode(id="node_2", description="T2", node_type=NodeType.ACTION),
        }
        dag = TaskDAG(task="test", nodes=nodes, edges=[])
        edge = TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY)
        dag.add_dynamic_edge(edge)
        assert dag.get_dependency_ids("node_2") == ["node_1"]

    def test_conditional_edges_not_in_dep_adjacency(self):
        """测试 CONDITIONAL 边不影响依赖邻接表"""
        nodes = {
            "node_1": TaskNode(id="node_1", description="T1", node_type=NodeType.ACTION),
            "node_2": TaskNode(id="node_2", description="T2", node_type=NodeType.ACTION),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.CONDITIONAL, condition="success"),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)
        # Conditional edge should NOT appear in dependency lookup
        assert dag.get_dependency_ids("node_2") == []
        # But should appear in conditional edge query
        cond_edges = dag.get_conditional_edges("node_1")
        assert len(cond_edges) == 1


class TestP0FailureRecovery:
    """P0: 失败恢复增强测试"""

    def test_try_recover_blocked_nodes_no_deps(self):
        """测试无依赖节点的恢复"""
        node = TaskNode(
            id="node_1",
            description="Task 1",
            node_type=NodeType.ACTION,
            status=NodeStatus.PENDING,
        )
        dag = TaskDAG(task="test", nodes={"node_1": node}, edges=[])

        recovered = dag.try_recover_blocked_nodes()
        assert recovered == 1
        assert node.status == NodeStatus.READY

    def test_try_recover_blocked_nodes_all_deps_terminal(self):
        """测试所有依赖都已终态的节点恢复"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                status=NodeStatus.COMPLETED,
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)

        recovered = dag.try_recover_blocked_nodes()
        assert recovered == 1
        assert nodes["node_2"].status == NodeStatus.READY

    def test_try_recover_blocked_nodes_non_terminal_deps(self):
        """测试有非终态依赖时不应恢复"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                status=NodeStatus.RUNNING,
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)

        recovered = dag.try_recover_blocked_nodes()
        assert recovered == 0
        assert nodes["node_2"].status == NodeStatus.PENDING

    def test_try_recover_blocked_nodes_skipped_deps(self):
        """测试依赖被跳过时应该恢复"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                status=NodeStatus.SKIPPED,
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)

        recovered = dag.try_recover_blocked_nodes()
        assert recovered == 1
        assert nodes["node_2"].status == NodeStatus.READY


class TestIntegration:
    """集成测试"""

    def test_all_optimizations_work_together(self):
        """测试所有优化协同工作"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                status=NodeStatus.COMPLETED,
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="integration test", nodes=nodes, edges=edges)

        # P3: 使用邻接表查询依赖
        deps = dag.get_dependency_ids("node_2")
        assert deps == ["node_1"]

        # P2: 保存 checkpoint
        dag.save_checkpoint()
        assert len(dag.checkpoints) == 1

        # P1: 获取阻塞报告
        report = dag.get_blockage_report()
        assert report["has_blockage"] is False
        assert len(report["stuck_nodes"]) == 0

        # P0: 恢复被阻塞的节点
        recovered = dag.try_recover_blocked_nodes()
        assert recovered == 1
        assert nodes["node_2"].status == NodeStatus.READY

        # 再次 checkpoint
        dag.save_checkpoint()
        assert len(dag.checkpoints) == 2

        # P1: 检查是否有失败节点
        assert dag.has_failed_nodes() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
