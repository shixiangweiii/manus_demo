#!/usr/bin/env python3
"""并发执行压力测试"""
import asyncio
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dag.graph import TaskDAG
from dag.executor import DAGExecutor
from schema import NodeType, NodeStatus, ExitCriteria, TaskNode, TaskEdge, EdgeType
from unittest.mock import AsyncMock

async def test_high_parallelism():
    """测试高并发场景 - 20 个并行 Action"""
    print("Testing High Parallelism (20 parallel actions)...")
    
    # 构建包含 20 个并行 Action 的 DAG
    nodes = {
        "goal": TaskNode(id="goal", node_type=NodeType.GOAL, description="Goal"),
        "sub": TaskNode(id="sub", node_type=NodeType.SUBGOAL, description="SubGoal", parent_id="goal"),
    }
    
    # 添加 20 个并行 Action
    for i in range(1, 21):
        nodes[f"act_{i}"] = TaskNode(
            id=f"act_{i}",
            node_type=NodeType.ACTION,
            description=f"Parallel action {i}",
            parent_id="sub",
            exit_criteria=ExitCriteria(description=f"Action {i} completed")
        )
        nodes[f"act_{i}"].status = NodeStatus.PENDING
    
    # 添加依赖边
    edges = [
        TaskEdge(source="goal", target="sub", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="sub", target="act_1", edge_type=EdgeType.DEPENDENCY),
    ]
    for i in range(2, 21):
        edges.append(TaskEdge(source="sub", target=f"act_{i}", edge_type=EdgeType.DEPENDENCY))
    
    dag = TaskDAG(task="High parallelism test", nodes=nodes, edges=edges)
    
    # Mock executor
    mock_executor_agent = AsyncMock()
    mock_executor_agent.execute_node = AsyncMock(return_value=type('StepResult', (), {
        'success': True, 'output': 'Mock result', 'tool_calls_log': []
    })())
    mock_executor_agent.tools = {"mock_tool": AsyncMock()}
    mock_executor_agent.max_iterations = 10
    mock_executor_agent.context_manager = None
    
    mock_reflector = AsyncMock()
    mock_reflector.validate_exit_criteria = AsyncMock(return_value=True)
    
    executor = DAGExecutor(
        executor_agent=mock_executor_agent,
        reflector_agent=mock_reflector,
        max_parallel=5,  # 限制每轮最多 5 个并行
    )
    
    result = await executor.execute(dag)
    
    # 验证所有节点都完成
    completed_count = sum(1 for n in dag.nodes.values() if n.status == NodeStatus.COMPLETED)
    assert completed_count == 22, f"期望 22 个节点完成，实际 {completed_count}"
    
    print(f"✓ 高并发测试通过：{completed_count} 个节点全部完成")
    print(f"✓ 结果：{result[:100]}")
    return True

async def test_medium_parallelism():
    """测试中等并发场景 - 10 个并行 Action"""
    print("\nTesting Medium Parallelism (10 parallel actions)...")
    
    nodes = {
        "goal": TaskNode(id="goal", node_type=NodeType.GOAL, description="Goal"),
        "sub1": TaskNode(id="sub1", node_type=NodeType.SUBGOAL, description="SubGoal 1", parent_id="goal"),
        "sub2": TaskNode(id="sub2", node_type=NodeType.SUBGOAL, description="SubGoal 2", parent_id="goal"),
    }
    
    # 每个 SubGoal 下 5 个并行 Action
    for i in range(1, 6):
        nodes[f"act1_{i}"] = TaskNode(
            id=f"act1_{i}",
            node_type=NodeType.ACTION,
            description=f"SubGoal1 Action {i}",
            parent_id="sub1",
            exit_criteria=ExitCriteria(description=f"Action {i} completed")
        )
        nodes[f"act1_{i}"].status = NodeStatus.PENDING
        
        nodes[f"act2_{i}"] = TaskNode(
            id=f"act2_{i}",
            node_type=NodeType.ACTION,
            description=f"SubGoal2 Action {i}",
            parent_id="sub2",
            exit_criteria=ExitCriteria(description=f"Action {i} completed")
        )
        nodes[f"act2_{i}"].status = NodeStatus.PENDING
    
    edges = [
        TaskEdge(source="goal", target="sub1", edge_type=EdgeType.DEPENDENCY),
        TaskEdge(source="goal", target="sub2", edge_type=EdgeType.DEPENDENCY),
    ]
    for i in range(1, 6):
        edges.append(TaskEdge(source="sub1", target=f"act1_{i}", edge_type=EdgeType.DEPENDENCY))
        edges.append(TaskEdge(source="sub2", target=f"act2_{i}", edge_type=EdgeType.DEPENDENCY))
    
    dag = TaskDAG(task="Medium parallelism test", nodes=nodes, edges=edges)
    
    mock_executor_agent = AsyncMock()
    mock_executor_agent.execute_node = AsyncMock(return_value=type('StepResult', (), {
        'success': True, 'output': 'Mock result', 'tool_calls_log': []
    })())
    mock_executor_agent.tools = {"mock_tool": AsyncMock()}
    mock_executor_agent.max_iterations = 10
    mock_executor_agent.context_manager = None
    
    mock_reflector = AsyncMock()
    mock_reflector.validate_exit_criteria = AsyncMock(return_value=True)
    
    executor = DAGExecutor(
        executor_agent=mock_executor_agent,
        reflector_agent=mock_reflector,
        max_parallel=3,
    )
    
    result = await executor.execute(dag)
    
    completed_count = sum(1 for n in dag.nodes.values() if n.status == NodeStatus.COMPLETED)
    assert completed_count == 12, f"期望 12 个节点完成，实际 {completed_count}"
    
    print(f"✓ 中等并发测试通过：{completed_count} 个节点全部完成")
    return True

async def main():
    """运行所有压力测试"""
    print("=" * 60)
    print("并发执行压力测试")
    print("=" * 60 + "\n")
    
    try:
        await test_high_parallelism()
        await test_medium_parallelism()
        
        print("\n" + "=" * 60)
        print("✓ 所有并发压力测试通过!")
        print("=" * 60)
        return 0
        
    except Exception as e:
        print(f"\n✗ 测试失败：{e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
