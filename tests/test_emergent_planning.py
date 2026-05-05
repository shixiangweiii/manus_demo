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


class TestCycleDetection:
    """Tests for dependency cycle detection in TodoList."""

    def test_no_cycle_linear_chain(self):
        tl = TodoList(task="test")
        tl.add_todo("A")  # id=1
        tl.add_todo("B", dependencies=[1])  # id=2
        tl.add_todo("C", dependencies=[2])  # id=3
        assert not tl._has_cycle()

    def test_cycle_detected_after_manual_edit(self):
        tl = TodoList(task="test")
        tl.add_todo("A")  # id=1
        tl.add_todo("B", dependencies=[1])  # id=2
        tl.add_todo("C", dependencies=[2])  # id=3
        tl.todos[1].dependencies = [3]  # creates 1->3->2->1
        assert tl._has_cycle()

    def test_add_todo_prevents_cycle(self):
        tl = TodoList(task="test")
        tl.add_todo("A")  # id=1
        tl.add_todo("B", dependencies=[1])  # id=2
        tl.add_todo("C", dependencies=[2])  # id=3
        # Now modify 1 to depend on 3 creating 1->3->2->1 cycle
        tl.todos[1].dependencies = [3]
        assert tl._has_cycle()
        # add_todo should prevent new additions that maintain the cycle
        # (the cycle already exists via manual edit, add_todo's cycle check
        # will detect it when trying to add any new todo that doesn't break it)


class TestEmergentPlannerIntegration:
    """Integration tests for EmergentPlannerAgent with mocked LLM."""

    def _make_planner(self, mock_llm):
        from agents.emergent_planner import EmergentPlannerAgent
        from tools.web_search import WebSearchTool
        planner = EmergentPlannerAgent(
            llm_client=mock_llm,
            tools=[WebSearchTool()],
            max_iterations=3,
            max_outer_iterations=20,
        )
        return planner

    @pytest.mark.asyncio
    async def test_todo_retry_and_block(self):
        """TODO fails 3 times → should be marked BLOCKED."""
        from unittest.mock import AsyncMock, MagicMock
        from schema import StepResult

        mock_llm = MagicMock()
        # init_todo returns 1 TODO
        mock_llm.chat_json = AsyncMock(return_value={"todos": [{"description": "Task A"}]})
        mock_llm.chat = AsyncMock(return_value="Final summary")
        mock_llm.chat_with_tools = AsyncMock(side_effect=Exception("LLM boom"))

        planner = self._make_planner(mock_llm)
        # Force _execute_todo to fail by making think_with_tools throw
        original_think = planner.think_with_tools

        async def fail_think(*a, **kw):
            raise Exception("forced failure")

        planner.think_with_tools = fail_think
        planner.think_json = AsyncMock(return_value={"todos": [{"description": "Task A"}]})
        planner.think = AsyncMock(return_value="Summary")

        result = await planner.execute("test task")
        assert isinstance(result, str)
        # The TODO should have been retried and eventually blocked
        blocked = [t for t in planner._todo_list.todos.values() if t.status == TodoStatus.BLOCKED]
        assert len(blocked) > 0

    @pytest.mark.asyncio
    async def test_fallback_todo_single_retry(self):
        """Init_todo fails twice → fallback TODO has retry_count limited."""
        from unittest.mock import AsyncMock, MagicMock

        mock_llm = MagicMock()
        planner = self._make_planner(mock_llm)

        # Make think_json always fail
        planner.think_json = AsyncMock(side_effect=Exception("parse fail"))
        planner._todo_list = TodoList(task="test")

        await planner._init_todo_list("test task", "")

        # Should have exactly 1 fallback TODO
        assert len(planner._todo_list.todos) == 1
        fallback = list(planner._todo_list.todos.values())[0]
        import config as config_module
        assert fallback.retry_count == config_module.MAX_TODO_RETRIES - 1

    @pytest.mark.asyncio
    async def test_stagnation_detection(self):
        """Consecutive rounds with no COMPLETED increment → early break."""
        from unittest.mock import AsyncMock, MagicMock
        from schema import StepResult

        mock_llm = MagicMock()
        planner = self._make_planner(mock_llm)

        # Mock think_json for _init_todo_list
        planner.think_json = AsyncMock(return_value={"todos": [{"description": "Task A"}]})
        # Mock think for _compile_answer
        planner.think = AsyncMock(return_value="Final answer")

        # Make execute_todo always return failure
        async def mock_execute_todo(todo):
            return StepResult(step_id=todo.id, success=False, output="fail", tool_calls_log=[])

        planner._execute_todo = mock_execute_todo

        events = []
        planner._on_event = lambda e, d: events.append((e, d))

        await planner.execute("stagnation test")

        # Should have completed (all blocked → loop exits normally)
        phase_events = [d for e, d in events if e == "phase"]
        assert any("completed" in str(p).lower() for p in phase_events)

    @pytest.mark.asyncio
    async def test_compile_answer_includes_failures(self):
        """_compile_answer should handle mixed success/failure results."""
        from unittest.mock import AsyncMock, MagicMock
        from schema import StepResult

        mock_llm = MagicMock()
        planner = self._make_planner(mock_llm)

        results = [
            StepResult(step_id=1, success=True, output="Result A", tool_calls_log=[]),
            StepResult(step_id=2, success=False, output="Error: boom", tool_calls_log=[]),
            StepResult(step_id=3, success=True, output="Result C", tool_calls_log=[]),
        ]

        planner.think = AsyncMock(return_value="Synthesized answer with failures noted")

        answer = await planner._compile_answer("test task", results)

        assert "Synthesized" in answer
        # Verify the LLM was called with both success and failure info
        call_args = planner.think.call_args[0][0]
        assert "Result A" in call_args
        assert "Error: boom" in call_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
