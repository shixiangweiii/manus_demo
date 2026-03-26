#!/usr/bin/env python3
"""循环依赖检测测试"""
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dag.graph import TaskDAG
from schema import NodeType, TaskNode, TaskEdge, EdgeType

def test_cycle_detection():
    """验证环检测是否生效"""
    print("Testing Cycle Detection...")
    try:
        nodes = {
            "a": TaskNode(id="a", node_type=NodeType.ACTION, description="A"),
            "b": TaskNode(id="b", node_type=NodeType.ACTION, description="B"),
            "c": TaskNode(id="c", node_type=NodeType.ACTION, description="C"),
        }
        # 制造环：a -> b -> c -> a
        edges = [
            TaskEdge(source="a", target="b", edge_type=EdgeType.DEPENDENCY),
            TaskEdge(source="b", target="c", edge_type=EdgeType.DEPENDENCY),
            TaskEdge(source="c", target="a", edge_type=EdgeType.DEPENDENCY),
        ]
        
        dag = TaskDAG(task="Cycle test", nodes=nodes, edges=edges)
        print("✗ 环检测失败：应该抛出 ValueError")
        return False
    except ValueError as e:
        if "Cycle detected" in str(e):
            print(f"✓ 环检测通过：{e}")
            return True
        else:
            print(f"✗ 异常类型错误：{e}")
            return False

def test_no_cycle():
    """验证无环 DAG 正常创建"""
    print("\nTesting No Cycle DAG...")
    try:
        nodes = {
            "a": TaskNode(id="a", node_type=NodeType.ACTION, description="A"),
            "b": TaskNode(id="b", node_type=NodeType.ACTION, description="B"),
            "c": TaskNode(id="c", node_type=NodeType.ACTION, description="C"),
        }
        # 正常顺序：a -> b -> c (无环)
        edges = [
            TaskEdge(source="a", target="b", edge_type=EdgeType.DEPENDENCY),
            TaskEdge(source="b", target="c", edge_type=EdgeType.DEPENDENCY),
        ]
        
        dag = TaskDAG(task="No cycle test", nodes=nodes, edges=edges)
        print(f"✓ 无环 DAG 创建成功，包含 {len(nodes)} 个节点")
        return True
    except Exception as e:
        print(f"✗ 无环 DAG 创建失败：{e}")
        return False

def main():
    """运行所有环检测测试"""
    print("=" * 60)
    print("循环依赖检测测试")
    print("=" * 60 + "\n")
    
    try:
        result1 = test_cycle_detection()
        result2 = test_no_cycle()
        
        if result1 and result2:
            print("\n" + "=" * 60)
            print("✓ 所有循环依赖检测测试通过!")
            print("=" * 60)
            return 0
        else:
            print("\n" + "=" * 60)
            print("✗ 部分测试失败")
            print("=" * 60)
            return 1
        
    except Exception as e:
        print(f"\n✗ 测试失败：{e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
