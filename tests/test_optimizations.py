"""
Test suite for DAG optimizations (P0-P3).
测试 DAG 优化（P0-P3）的测试套件。

包括：
  - P0: 失败恢复增强
  - P1: 边界情况处理
  - P2: 简化 Checkpoint 机制
  - P3: 基础索引优化（懒加载缓存）
"""

import pytest
import time
from dag.graph import TaskDAG
from dag.state_machine import NodeStateMachine
from schema import DAGState, EdgeType, NodeStatus, NodeType, TaskEdge, TaskNode


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
            risk={"risk_level": "low"},
        )
        dag = TaskDAG(task="single task", nodes={"node_1": node}, edges=[])

        # 初始状态应该不是完成
        assert dag.is_complete() is False

        # 完成后应该完成
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
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.FAILED,
                risk={"risk_level": "high"},
            ),
        }
        dag = TaskDAG(task="test", nodes=nodes, edges=[])

        assert dag.has_failed_nodes() is True

        # 标记失败节点为 SKIPPED 后应该返回 False
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
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.READY,
                risk={"risk_level": "low"},
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
                status=NodeStatus.RUNNING,  # 正在运行
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,  # 等待 node_1
                risk={"risk_level": "low"},
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


class TestP2CheckpointSimplification:
    """P2: 简化 Checkpoint 机制测试"""

    def test_checkpoint_saves_periodically(self):
        """测试定期保存机制"""
        dag = TaskDAG(
            task="test",
            nodes={},
            edges=[],
            max_checkpoints=10,
            checkpoint_interval=3,
        )

        # 前两次调用不应该保存
        assert dag.save_checkpoint(force=False) is False
        assert dag.save_checkpoint(force=False) is False
        assert len(dag.checkpoints) == 0

        # 第三次应该保存
        assert dag.save_checkpoint(force=False) is True
        assert len(dag.checkpoints) == 1

    def test_checkpoint_limit(self):
        """测试 checkpoint 总数限制"""
        dag = TaskDAG(
            task="test",
            nodes={},
            edges=[],
            max_checkpoints=3,
            checkpoint_interval=1,  # 每次都保存
        )

        # 创建多个 checkpoint
        for i in range(5):
            dag.save_checkpoint(force=True)

        # 应该只有 max_checkpoints 个
        assert len(dag.checkpoints) == 3

        # 最早的应该被删除
        assert dag.checkpoints[0]["step"] == 3
        assert dag.checkpoints[-1]["step"] == 5

    def test_checkpoint_restore(self):
        """测试 checkpoint 恢复"""
        node = TaskNode(
            id="node_1",
            description="Original description",
            node_type=NodeType.ACTION,
            risk={"risk_level": "low"},
        )
        dag = TaskDAG(
            task="test",
            nodes={"node_1": node},
            edges=[],
            max_checkpoints=5,
            checkpoint_interval=1,
        )

        # 保存 checkpoint
        dag.save_checkpoint(force=True)

        # 修改节点
        node.description = "Modified description"
        node.status = NodeStatus.COMPLETED

        # 恢复到原始状态
        assert dag.restore_checkpoint(index=-1) is True
        assert dag.nodes["node_1"].description == "Original description"
        assert dag.nodes["node_1"].status == NodeStatus.PENDING

    def test_checkpoint_restore_empty(self):
        """测试空 checkpoint 列表恢复"""
        dag = TaskDAG(task="test", nodes={}, edges=[])
        assert dag.restore_checkpoint() is False

    def test_checkpoint_force_save(self):
        """测试强制保存"""
        dag = TaskDAG(
            task="test",
            nodes={},
            edges=[],
            max_checkpoints=5,
            checkpoint_interval=100,  # 大间隔
        )

        # 强制保存应该立即保存
        assert dag.save_checkpoint(force=True) is True
        assert len(dag.checkpoints) == 1


class TestP3CacheOptimization:
    """P3: 基础索引优化（懒加载缓存）测试"""

    def test_cache_lazy_loading(self):
        """测试懒加载缓存"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)

        # 初始状态缓存应该无效
        assert dag._cache_valid is False

        # 第一次查询应该触发缓存构建
        deps = dag.get_dependency_ids("node_2")
        assert dag._cache_valid is True
        assert deps == ["node_1"]

        # 后续查询应该使用缓存
        deps = dag.get_dependency_ids("node_2")
        assert deps == ["node_1"]

    def test_cache_invalidation_on_edge_add(self):
        """测试添加边时缓存失效"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)

        # 触发缓存构建
        dag.get_dependency_ids("node_2")
        assert dag._cache_valid is True

        # 添加新边应该使缓存失效
        new_edge = TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.CONDITIONAL, condition="test")
        dag.add_dynamic_edge(new_edge)
        assert dag._cache_valid is False

    def test_cache_invalidation_on_node_remove(self):
        """测试移除节点时缓存失效"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)

        # 触发缓存构建
        dag.get_dependency_ids("node_2")
        assert dag._cache_valid is True

        # 移除节点应该使缓存失效
        dag.remove_pending_node("node_1")
        assert dag._cache_valid is False

    def test_cache_conditional_edges(self):
        """测试条件边缓存"""
        nodes = {
            "node_1": TaskNode(
                id="node_1",
                description="Task 1",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                risk={"risk_level": "low"},
            ),
        }
        edges = [
            TaskEdge(
                source="node_1",
                target="node_2",
                edge_type=EdgeType.CONDITIONAL,
                condition="success",
            ),
        ]
        dag = TaskDAG(task="test", nodes=nodes, edges=edges)

        # 触发缓存构建
        cond_edges = dag.get_conditional_edges("node_1")
        assert len(cond_edges) == 1
        assert cond_edges[0].condition == "success"


class TestP0FailureRecovery:
    """P0: 失败恢复增强测试"""

    def test_try_recover_blocked_nodes_no_deps(self):
        """测试无依赖节点的恢复"""
        node = TaskNode(
            id="node_1",
            description="Task 1",
            node_type=NodeType.ACTION,
            status=NodeStatus.PENDING,
            risk={"risk_level": "low"},
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
                status=NodeStatus.COMPLETED,  # 终态
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,  # 等待 node_1
                risk={"risk_level": "low"},
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
                status=NodeStatus.RUNNING,  # 非终态
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,  # 等待 node_1
                risk={"risk_level": "low"},
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
                status=NodeStatus.SKIPPED,  # 终态
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,  # 等待 node_1
                risk={"risk_level": "low"},
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
                status=NodeStatus.COMPLETED,  # 终态
                risk={"risk_level": "low"},
            ),
            "node_2": TaskNode(
                id="node_2",
                description="Task 2",
                node_type=NodeType.ACTION,
                status=NodeStatus.PENDING,  # 等待 node_1
                risk={"risk_level": "medium"},
            ),
        }
        edges = [
            TaskEdge(source="node_1", target="node_2", edge_type=EdgeType.DEPENDENCY),
        ]
        dag = TaskDAG(
            task="integration test",
            nodes=nodes,
            edges=edges,
            max_checkpoints=5,
            checkpoint_interval=2,
        )

        # P3: 使用缓存查询
        deps = dag.get_dependency_ids("node_2")
        assert deps == ["node_1"]

        # P2: 保存 checkpoint
        dag.save_checkpoint(force=True)
        assert len(dag.checkpoints) == 1

        # P1: 获取阻塞报告 - node_2 依赖 node_1 (COMPLETED)，所以应该被识别为可以恢复
        report = dag.get_blockage_report()
        assert report["has_blockage"] is False  # node_1 已完成，所以没有真正阻塞
        assert len(report["stuck_nodes"]) == 0

        # P0: 尝试恢复 - node_1 已完成，node_2 应该被恢复
        recovered = dag.try_recover_blocked_nodes()
        assert recovered == 1
        assert nodes["node_2"].status == NodeStatus.READY

        # 再次 checkpoint
        dag.save_checkpoint(force=True)
        assert len(dag.checkpoints) == 2

        # P1: 检查是否有失败节点
        assert dag.has_failed_nodes() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
