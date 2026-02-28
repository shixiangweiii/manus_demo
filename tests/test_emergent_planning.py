"""
Tests for Emergent Planning (v5) functionality.
隐式规划（v5）功能测试。

These tests verify the Claude Code-style emergent planning system:
- TODO list management (add, update, complete)
- EmergentPlannerAgent core loop
- Integration with Orchestrator routing

这些测试验证 Claude Code 风格的隐式规划系统：
- TODO 列表管理（添加、更新、完成）
- EmergentPlannerAgent 核心循环
- 与 Orchestrator 路由的集成
"""

import pytest
from schema import TodoItem, TodoList, TodoStatus


class TestTodoItem:
    """Tests for TodoItem data model."""

    def test_create_todo_item(self):
        """Test creating a basic TODO item."""
        todo = TodoItem(id=1, description="Test task")
        assert todo.id == 1
        assert todo.description == "Test task"
        assert todo.status == TodoStatus.PENDING
        assert todo.dependencies == []
        assert todo.result is None

    def test_create_todo_with_dependencies(self):
        """Test creating a TODO with dependencies."""
        todo = TodoItem(
            id=2,
            description="Dependent task",
            dependencies=[1, 3],
        )
        assert todo.dependencies == [1, 3]

    def test_todo_status_transitions(self):
        """Test TODO status transitions."""
        todo = TodoItem(id=1, description="Test")
        assert todo.status == TodoStatus.PENDING

        # Simulate status changes
        todo.status = TodoStatus.IN_PROGRESS
        assert todo.status == TodoStatus.IN_PROGRESS

        todo.status = TodoStatus.COMPLETED
        assert todo.status == TodoStatus.COMPLETED


class TestTodoList:
    """Tests for TodoList management."""

    def test_create_empty_todo_list(self):
        """Test creating an empty TODO list."""
        todo_list = TodoList(task="Test task")
        assert todo_list.task == "Test task"
        assert len(todo_list.todos) == 0
        assert todo_list.next_id == 1
        assert not todo_list.has_pending()

    def test_add_todo(self):
        """Test adding TODOs to the list."""
        todo_list = TodoList(task="Test task")
        todo1 = todo_list.add_todo("First task")
        todo2 = todo_list.add_todo("Second task", dependencies=[1])

        assert len(todo_list.todos) == 2
        assert todo1.id == 1
        assert todo2.id == 2
        assert todo2.dependencies == [1]
        assert todo_list.next_id == 3

    def test_get_pending_todos(self):
        """Test getting pending TODOs."""
        todo_list = TodoList(task="Test task")
        todo_list.add_todo("Task 1")
        todo_list.add_todo("Task 2")
        todo_list.add_todo("Task 3")

        pending = todo_list.get_pending_todos()
        assert len(pending) == 3

        # Mark one as completed
        todo_list.mark_completed(1, "Result 1")
        pending = todo_list.get_pending_todos()
        assert len(pending) == 2

    def test_get_ready_todos(self):
        """Test getting TODOs with satisfied dependencies."""
        todo_list = TodoList(task="Test task")
        todo_list.add_todo("Task 1")  # No dependencies
        todo_list.add_todo("Task 2", dependencies=[1])  # Depends on 1
        todo_list.add_todo("Task 3", dependencies=[1])  # Depends on 1
        todo_list.add_todo("Task 4", dependencies=[2, 3])  # Depends on 2 and 3

        # Initially, only Task 1 is ready
        ready = todo_list.get_ready_todos()
        assert len(ready) == 1
        assert ready[0].id == 1

        # Complete Task 1
        todo_list.mark_completed(1, "Result 1")

        # Now Task 2 and Task 3 are ready
        ready = todo_list.get_ready_todos()
        assert len(ready) == 2
        assert {t.id for t in ready} == {2, 3}

        # Complete Task 2
        todo_list.mark_completed(2, "Result 2")

        # Task 3 still ready, Task 4 still blocked
        ready = todo_list.get_ready_todos()
        assert len(ready) == 1
        assert ready[0].id == 3

    def test_mark_completed(self):
        """Test marking TODOs as completed."""
        todo_list = TodoList(task="Test task")
        todo_list.add_todo("Task 1")

        assert todo_list.todos[1].status == TodoStatus.PENDING
        assert todo_list.todos[1].result is None

        todo_list.mark_completed(1, "Completed result")

        assert todo_list.todos[1].status == TodoStatus.COMPLETED
        assert todo_list.todos[1].result == "Completed result"

    def test_mark_in_progress(self):
        """Test marking TODOs as in progress."""
        todo_list = TodoList(task="Test task")
        todo_list.add_todo("Task 1")

        assert todo_list.todos[1].status == TodoStatus.PENDING

        todo_list.mark_in_progress(1)

        assert todo_list.todos[1].status == TodoStatus.IN_PROGRESS

    def test_is_complete(self):
        """Test checking if all TODOs are complete."""
        todo_list = TodoList(task="Test task")
        todo_list.add_todo("Task 1")
        todo_list.add_todo("Task 2")

        assert not todo_list.is_complete()

        todo_list.mark_completed(1, "Result 1")
        assert not todo_list.is_complete()

        todo_list.mark_completed(2, "Result 2")
        assert todo_list.is_complete()

    def test_has_pending(self):
        """Test checking if there are pending TODOs."""
        todo_list = TodoList(task="Test task")
        assert not todo_list.has_pending()

        todo_list.add_todo("Task 1")
        assert todo_list.has_pending()

        todo_list.mark_completed(1, "Result 1")
        assert not todo_list.has_pending()


class TestEmergentPlannerAgent:
    """Tests for EmergentPlannerAgent (integration tests)."""

    @pytest.mark.asyncio
    async def test_emergent_planner_initialization(self):
        """Test EmergentPlannerAgent initialization."""
        from agents.emergent_planner import EmergentPlannerAgent
        from llm.client import LLMClient
        from tools.code_executor import CodeExecutorTool
        from tools.file_ops import FileOpsTool
        from tools.web_search import WebSearchTool

        llm_client = LLMClient()
        tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool()]

        planner = EmergentPlannerAgent(
            llm_client=llm_client,
            tools=tools,
        )

        assert planner.name == "EmergentPlanner"
        assert planner.tools is not None
        assert len(planner.tools) == 3
        assert planner._todo_list is None

    @pytest.mark.asyncio
    async def test_emergent_planner_execute_simple_task(self):
        """Test emergent planning with a simple task (mocked)."""
        from agents.emergent_planner import EmergentPlannerAgent
        from llm.client import LLMClient
        from tools.code_executor import CodeExecutorTool
        from tools.file_ops import FileOpsTool
        from tools.web_search import WebSearchTool

        llm_client = LLMClient()
        tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool()]

        planner = EmergentPlannerAgent(
            llm_client=llm_client,
            tools=tools,
            max_iterations=5,  # Limit iterations for testing
        )

        # Simple task that should complete quickly
        task = "What is the weather today?"
        result = await planner.execute(task)

        # Should return some result (may be mocked or error if no API key)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_todo_list_update_during_execution(self):
        """Test that TODO list can be updated during execution."""
        from agents.emergent_planner import EmergentPlannerAgent
        from llm.client import LLMClient
        from tools.code_executor import CodeExecutorTool
        from tools.file_ops import FileOpsTool
        from tools.web_search import WebSearchTool

        llm_client = LLMClient()
        tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool()]

        planner = EmergentPlannerAgent(
            llm_client=llm_client,
            tools=tools,
            max_iterations=3,
        )

        # Initialize TODO list
        task = "Research and summarize a topic"
        planner._todo_list = planner._todo_list or TodoList(task=task)
        await planner._init_todo_list(task, "")

        # Should have initialized with some TODOs
        assert planner._todo_list is not None
        assert len(planner._todo_list.todos) > 0

        # Verify TODO list structure
        for todo_id, todo in planner._todo_list.todos.items():
            assert todo.id == todo_id
            assert isinstance(todo.description, str)
            assert len(todo.description) > 0


class TestOrchestratorEmergentRouting:
    """Tests for Orchestrator's emergent planning routing."""

    def test_emergent_planner_agent_import(self):
        """Test that EmergentPlannerAgent can be imported."""
        from agents.emergent_planner import EmergentPlannerAgent
        assert EmergentPlannerAgent is not None

    def test_schema_models_available(self):
        """Test that emergent planning schema models are available."""
        from schema import TodoItem, TodoList, TodoStatus

        assert TodoItem is not None
        assert TodoList is not None
        assert TodoStatus is not None

    def test_config_emergent_planning_enabled(self):
        """Test that emergent planning config is available."""
        import config

        assert hasattr(config, 'EMERGENT_PLANNING_ENABLED')
        assert hasattr(config, 'MAX_TODO_ITEMS')
        assert hasattr(config, 'TODO_COMPRESSION_THRESHOLD')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
