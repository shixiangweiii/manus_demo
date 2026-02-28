#!/usr/bin/env python3
"""
Simple test script for emergent planning (v5) functionality.
隐式规划（v5）功能简单测试脚本。
"""

import sys
sys.path.insert(0, '/Users/shixiangweii/PycharmProjects/manus_demo')

from schema import TodoItem, TodoList, TodoStatus

def test_todo_item():
    """Test TodoItem creation."""
    print("Testing TodoItem...")
    todo = TodoItem(id=1, description="Test task")
    assert todo.id == 1
    assert todo.description == "Test task"
    assert todo.status == TodoStatus.PENDING
    assert todo.dependencies == []
    print("✓ TodoItem creation passed")

def test_todo_list():
    """Test TodoList management."""
    print("\nTesting TodoList...")
    todo_list = TodoList(task="Test task")
    
    # Add TODOs
    todo1 = todo_list.add_todo("First task")
    todo2 = todo_list.add_todo("Second task", dependencies=[1])
    
    assert len(todo_list.todos) == 2
    assert todo1.id == 1
    assert todo2.id == 2
    assert todo2.dependencies == [1]
    print("✓ Add TODOs passed")
    
    # Test get_ready_todos
    ready = todo_list.get_ready_todos()
    assert len(ready) == 1
    assert ready[0].id == 1
    print("✓ Get ready TODOs passed")
    
    # Mark first as completed
    todo_list.mark_completed(1, "Result 1")
    assert todo_list.todos[1].status == TodoStatus.COMPLETED
    
    # Now second should be ready
    ready = todo_list.get_ready_todos()
    assert len(ready) == 1
    assert ready[0].id == 2
    print("✓ TODO dependencies passed")
    
    # Test is_complete
    assert not todo_list.is_complete()
    todo_list.mark_completed(2, "Result 2")
    assert todo_list.is_complete()
    print("✓ Is complete check passed")

def test_config():
    """Test config values."""
    print("\nTesting config...")
    import config
    
    assert hasattr(config, 'EMERGENT_PLANNING_ENABLED')
    assert hasattr(config, 'MAX_TODO_ITEMS')
    assert hasattr(config, 'TODO_COMPRESSION_THRESHOLD')
    
    print(f"  EMERGENT_PLANNING_ENABLED = {config.EMERGENT_PLANNING_ENABLED}")
    print(f"  MAX_TODO_ITEMS = {config.MAX_TODO_ITEMS}")
    print(f"  TODO_COMPRESSION_THRESHOLD = {config.TODO_COMPRESSION_THRESHOLD}")
    print("✓ Config values passed")

def test_emergent_planner_import():
    """Test that EmergentPlannerAgent can be imported."""
    print("\nTesting EmergentPlannerAgent import...")
    from agents.emergent_planner import EmergentPlannerAgent
    
    assert EmergentPlannerAgent is not None
    print("✓ EmergentPlannerAgent import passed")

def test_orchestrator_import():
    """Test that Orchestrator has emergent planner."""
    print("\nTesting Orchestrator import...")
    from agents.orchestrator import OrchestratorAgent
    from agents.emergent_planner import EmergentPlannerAgent
    from llm.client import LLMClient
    from tools.web_search import WebSearchTool
    from tools.code_executor import CodeExecutorTool
    from tools.file_ops import FileOpsTool
    
    llm_client = LLMClient()
    tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool()]
    
    orchestrator = OrchestratorAgent(
        llm_client=llm_client,
        tools=tools,
    )
    
    assert hasattr(orchestrator, 'emergent_planner')
    assert isinstance(orchestrator.emergent_planner, EmergentPlannerAgent)
    print("✓ Orchestrator emergent_planner attribute passed")

if __name__ == "__main__":
    print("=" * 60)
    print("Emergent Planning (v5) - Simple Tests")
    print("=" * 60)
    
    try:
        test_todo_item()
        test_todo_list()
        test_config()
        test_emergent_planner_import()
        test_orchestrator_import()
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
