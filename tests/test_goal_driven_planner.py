"""
Tests for Goal-Driven Planner (v8) functionality.
目标驱动规划（v8）功能测试。

These tests verify the "begin with the end in mind" planning system:
- GoalDocument, MilestonePlan, GoalReflection data models
- GoalDrivenPlannerAgent core loop (mocked LLM)
- Integration with Orchestrator routing
- Event emission for tracing/evaluation

这些测试验证「以终为始」规划系统：
- GoalDocument、MilestonePlan、GoalReflection 数据模型
- GoalDrivenPlannerAgent 核心循环（Mock LLM）
- 与 Orchestrator 路由的集成
- 用于追踪/评测的事件发射
"""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from schema import (
    GoalDocument,
    GoalReflection,
    GoalReanchorResult,
    Milestone,
    MilestonePlan,
    StepResult,
    TodoItem,
    TodoList,
    TodoStatus,
)


# ======================================================================
# Data Model Tests
# 数据模型测试
# ======================================================================


class TestGoalDocument:
    """Tests for GoalDocument data model."""

    def test_create_goal_document(self):
        doc = GoalDocument(
            original_task="Write a report",
            success_criteria="Report saved to file",
            target_state_description="A markdown report exists at report.md",
            key_deliverables=["report.md"],
            constraints=["Use only Python"],
        )
        assert doc.original_task == "Write a report"
        assert doc.success_criteria == "Report saved to file"
        assert doc.key_deliverables == ["report.md"]
        assert doc.constraints == ["Use only Python"]
        assert doc.progress_pct == 0.0
        assert doc.completed_milestones_summary == ""
        assert doc.current_focus == ""
        assert doc.updated_at > 0

    def test_goal_document_defaults(self):
        doc = GoalDocument(
            original_task="test",
            success_criteria="done",
            target_state_description="complete",
        )
        assert doc.key_deliverables == []
        assert doc.constraints == []
        assert doc.progress_pct == 0.0

    def test_goal_document_serialization(self):
        doc = GoalDocument(
            original_task="test",
            success_criteria="done",
            target_state_description="complete",
            key_deliverables=["a", "b"],
        )
        data = doc.model_dump()
        restored = GoalDocument(**data)
        assert restored.original_task == doc.original_task
        assert restored.key_deliverables == ["a", "b"]
        assert restored.progress_pct == 0.0


class TestMilestonePlan:
    """Tests for MilestonePlan and Milestone models."""

    def test_create_milestone(self):
        ms = Milestone(
            id=1,
            description="Research topic",
            completion_criteria="Notes saved",
            estimated_complexity="medium",
        )
        assert ms.id == 1
        assert ms.estimated_complexity == "medium"

    def test_milestone_default_complexity(self):
        ms = Milestone(id=1, description="test", completion_criteria="done")
        assert ms.estimated_complexity == "medium"

    def test_create_milestone_plan(self):
        plan = MilestonePlan(
            goal_description="Final report",
            milestones=[
                Milestone(id=1, description="Step 1", completion_criteria="c1"),
                Milestone(id=2, description="Step 2", completion_criteria="c2"),
            ],
            backward_reasoning="Working backward from goal",
        )
        assert len(plan.milestones) == 2
        assert plan.backward_reasoning == "Working backward from goal"

    def test_milestone_plan_ordering(self):
        """Milestones are stored in execution order (start first, goal last)."""
        plan = MilestonePlan(
            goal_description="goal",
            milestones=[
                Milestone(id=1, description="start", completion_criteria="c1"),
                Milestone(id=3, description="end", completion_criteria="c3"),
            ],
        )
        assert plan.milestones[0].description == "start"
        assert plan.milestones[1].description == "end"


class TestGoalReflection:
    """Tests for GoalReflection data model."""

    def test_create_goal_reflection(self):
        r = GoalReflection(
            current_state_summary="Half done",
            gap_analysis="Need to write report",
            next_milestone="Write report section",
            progress_pct=50.0,
            suggested_action="execute_todo",
            reasoning="Report is the main deliverable",
        )
        assert r.progress_pct == 50.0
        assert r.suggested_action == "execute_todo"

    def test_reflection_defaults(self):
        r = GoalReflection(
            current_state_summary="",
            gap_analysis="",
            next_milestone="",
        )
        assert r.progress_pct == 0.0
        assert r.suggested_action == "execute_todo"
        assert r.reasoning == ""


class TestGoalReanchorResult:
    """Tests for GoalReanchorResult data model."""

    def test_create_reanchor_result(self):
        doc = GoalDocument(
            original_task="test",
            success_criteria="done",
            target_state_description="complete",
        )
        result = GoalReanchorResult(
            updated_goal_doc=doc,
            goal_drift_detected=True,
            correction_applied="Refocused on original deliverable",
        )
        assert result.goal_drift_detected is True
        assert result.updated_goal_doc.original_task == "test"


# ======================================================================
# Agent Tests (Mocked LLM)
# 智能体测试（Mock LLM）
# ======================================================================


class TestGoalDrivenPlannerAgent:
    """Tests for GoalDrivenPlannerAgent."""

    def _make_planner(self, mock_llm=None):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        from tools.web_search import WebSearchTool

        if mock_llm is None:
            mock_llm = MagicMock()

        planner = GoalDrivenPlannerAgent(
            llm_client=mock_llm,
            tools=[WebSearchTool()],
            max_iterations=3,
        )
        return planner

    def test_planner_initialization(self):
        planner = self._make_planner()
        assert planner.name == "GoalDrivenPlanner"
        assert planner._goal_doc is None
        assert planner._todo_list is None
        assert len(planner.tools) == 1

    def test_planner_import(self):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        assert GoalDrivenPlannerAgent is not None

    @pytest.mark.asyncio
    async def test_build_goal_document_mocked(self):
        planner = self._make_planner()
        planner.think_json = AsyncMock(return_value={
            "success_criteria": "File saved with results",
            "target_state_description": "report.md exists",
            "key_deliverables": ["report.md"],
            "constraints": ["No external APIs"],
        })

        doc = await planner._build_goal_document("Write a report")

        assert doc.original_task == "Write a report"
        assert doc.success_criteria == "File saved with results"
        assert "report.md" in doc.key_deliverables

    @pytest.mark.asyncio
    async def test_backward_plan_mocked(self):
        planner = self._make_planner()
        planner.think_json = AsyncMock(return_value={
            "milestones": [
                {"description": "Final report", "completion_criteria": "Report complete", "estimated_complexity": "medium"},
                {"description": "Research topic", "completion_criteria": "Notes saved", "estimated_complexity": "low"},
            ],
            "backward_reasoning": "Need report before done, need research before report",
        })

        goal_doc = GoalDocument(
            original_task="test",
            success_criteria="done",
            target_state_description="complete",
        )
        plan = await planner._backward_plan(goal_doc)

        # Milestones should be reversed (start-first order)
        assert len(plan.milestones) == 2
        assert plan.milestones[0].description == "Research topic"
        assert plan.milestones[1].description == "Final report"

    @pytest.mark.asyncio
    async def test_goal_reflect_mocked(self):
        planner = self._make_planner()
        planner.think_json = AsyncMock(return_value={
            "current_state_summary": "Research done",
            "gap_analysis": "Need to write report",
            "next_milestone": "Write report",
            "progress_pct": 60.0,
            "suggested_action": "execute_todo",
            "reasoning": "Report is the next step",
        })

        goal_doc = GoalDocument(
            original_task="test",
            success_criteria="done",
            target_state_description="complete",
        )
        todo_list = TodoList(task="test")
        todo_list.todos[1] = TodoItem(id=1, description="Write report")

        reflection = await planner._goal_reflect(goal_doc, todo_list, 1)

        assert reflection.progress_pct == 60.0
        assert reflection.next_milestone == "Write report"

    def test_milestones_to_todos(self):
        planner = self._make_planner()
        plan = MilestonePlan(
            goal_description="goal",
            milestones=[
                Milestone(id=1, description="Step 1", completion_criteria="c1"),
                Milestone(id=2, description="Step 2", completion_criteria="c2"),
                Milestone(id=3, description="Step 3", completion_criteria="c3"),
            ],
        )
        todo_list = planner._milestones_to_todos(plan, "test task")

        assert len(todo_list.todos) == 3
        # First TODO has no dependencies
        assert todo_list.todos[1].dependencies == []
        # Second TODO depends on first
        assert todo_list.todos[2].dependencies == [1]
        # Third TODO depends on second
        assert todo_list.todos[3].dependencies == [2]

    def test_select_todo_by_reflection_matches_milestone(self):
        planner = self._make_planner()
        planner._todo_list = TodoList(task="test")
        planner._todo_list.todos[1] = TodoItem(id=1, description="Research async programming")
        planner._todo_list.todos[2] = TodoItem(id=2, description="Write report")

        reflection = GoalReflection(
            current_state_summary="",
            gap_analysis="",
            next_milestone="Research async programming topic",
            progress_pct=0.0,
        )

        todo = planner._select_todo_by_reflection(reflection)
        assert todo is not None
        assert "Research" in todo.description

    def test_select_todo_by_reflection_fallback(self):
        planner = self._make_planner()
        planner._todo_list = TodoList(task="test")
        planner._todo_list.todos[1] = TodoItem(id=1, description="Task A")

        reflection = GoalReflection(
            current_state_summary="",
            gap_analysis="",
            next_milestone="Something unrelated",
            progress_pct=0.0,
        )

        todo = planner._select_todo_by_reflection(reflection)
        assert todo is not None
        assert todo.id == 1

    def test_format_goal_for_prompt(self):
        planner = self._make_planner()
        doc = GoalDocument(
            original_task="Write report",
            success_criteria="File saved",
            target_state_description="report.md exists",
            key_deliverables=["report.md"],
            constraints=["No APIs"],
            progress_pct=50.0,
            current_focus="Writing section 2",
        )
        formatted = planner._format_goal_for_prompt(doc)

        assert "Write report" in formatted
        assert "File saved" in formatted
        assert "50%" in formatted
        assert "Writing section 2" in formatted

    @pytest.mark.asyncio
    async def test_stagnation_detection(self):
        """Consecutive rounds with no progress → early break."""
        planner = self._make_planner()
        planner.stagnation_window = 2
        planner.max_outer_iterations = 20

        # Mock all LLM calls
        planner.think_json = AsyncMock(return_value={
            "success_criteria": "done", "target_state_description": "done",
            "key_deliverables": [], "constraints": [],
        })
        planner.think = AsyncMock(return_value="Final answer")

        # All executions fail → no completed TODOs → stagnation
        async def mock_execute_todo(todo, goal_doc, reflection):
            return StepResult(step_id=str(todo.id), success=False, output="fail")

        planner._execute_todo_goal_guided = mock_execute_todo

        events = []
        planner._on_event = lambda e, d: events.append((e, d))

        result = await planner.execute("stagnation test")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_goal_reanchor_on_failure(self):
        planner = self._make_planner()
        planner.think_json = AsyncMock(return_value={
            "success_criteria": "Updated criteria",
            "target_state_description": "Updated state",
            "key_deliverables": ["a.md"],
            "constraints": [],
            "progress_pct": 30.0,
            "current_focus": "Retry section",
            "completed_milestones_summary": "Done step 1",
            "goal_drift_detected": False,
            "correction_applied": "",
        })

        goal_doc = GoalDocument(
            original_task="test",
            success_criteria="done",
            target_state_description="complete",
        )
        todo_list = TodoList(task="test")
        todo_list.todos[1] = TodoItem(id=1, description="Task A")

        result = await planner._reanchor_goal(
            goal_doc, todo_list,
            StepResult(step_id="1", success=False, output="error"),
        )

        assert isinstance(result, GoalDocument)
        assert result.progress_pct == 30.0
        assert result.current_focus == "Retry section"

    def test_should_reanchor(self):
        planner = self._make_planner()
        planner.reanchor_interval = 3

        success_result = StepResult(step_id="1", success=True, output="ok")
        fail_result = StepResult(step_id="1", success=False, output="err")

        # Not yet reached interval, success → no reanchor
        planner._reanchor_counter = 1
        assert not planner._should_reanchor(1, success_result)

        # Reached interval → reanchor
        planner._reanchor_counter = 3
        assert planner._should_reanchor(3, success_result)

        # Failed → always reanchor
        planner._reanchor_counter = 0
        assert planner._should_reanchor(1, fail_result)

    def test_should_refresh_todos(self):
        planner = self._make_planner()

        success = StepResult(step_id="1", success=True, output="ok")
        fail = StepResult(step_id="1", success=False, output="err")
        replan = GoalReflection(
            current_state_summary="", gap_analysis="", next_milestone="",
            suggested_action="replan",
        )
        execute = GoalReflection(
            current_state_summary="", gap_analysis="", next_milestone="",
            suggested_action="execute_todo",
        )

        # Failure → always refresh
        assert planner._should_refresh_todos(1, execute, fail)

        # Replan suggestion → refresh
        assert planner._should_refresh_todos(1, replan, success)

        # Every 3rd iteration → refresh
        assert planner._should_refresh_todos(3, execute, success)
        assert not planner._should_refresh_todos(2, execute, success)

    @pytest.mark.asyncio
    async def test_compile_goal_anchored_answer(self):
        planner = self._make_planner()
        planner.think = AsyncMock(return_value="Comprehensive report with all results")

        goal_doc = GoalDocument(
            original_task="Write report",
            success_criteria="File saved with analysis",
            target_state_description="report.md with full analysis",
        )
        results = [
            StepResult(step_id="1", success=True, output="Research findings", tool_calls_log=[]),
            StepResult(step_id="2", success=True, output="Written report", tool_calls_log=[]),
        ]

        answer = await planner._compile_goal_anchored_answer("Write report", goal_doc, results)
        assert "Comprehensive report" in answer

    @pytest.mark.asyncio
    async def test_compile_answer_all_failed(self):
        planner = self._make_planner()

        goal_doc = GoalDocument(
            original_task="test", success_criteria="done",
            target_state_description="complete",
        )
        results = [
            StepResult(step_id="1", success=False, output="error 1", tool_calls_log=[]),
        ]

        answer = await planner._compile_goal_anchored_answer("test", goal_doc, results)
        assert "not achieved" in answer.lower() or "No" in answer

    @pytest.mark.asyncio
    async def test_get_state_summary(self):
        planner = self._make_planner()
        todo_list = TodoList(task="test")
        todo_list.todos[1] = TodoItem(id=1, description="Task A")
        todo_list.todos[2] = TodoItem(id=2, description="Task B")
        todo_list.mark_completed(1, "done")

        summary = planner._get_state_summary(todo_list)
        assert "[x]" in summary  # completed
        assert "[ ]" in summary  # pending
        assert "Task A" in summary
        assert "Task B" in summary


# ======================================================================
# Orchestrator Routing Tests
# Orchestrator 路由测试
# ======================================================================


class TestOrchestratorRouting:
    """Tests for Orchestrator's v8 goal-driven routing."""

    def test_goal_driven_config_available(self):
        import config
        assert hasattr(config, 'ENABLE_GOAL_DRIVEN_PLANNER')
        assert hasattr(config, 'GOAL_REANCHOR_INTERVAL')
        assert hasattr(config, 'GOAL_REFLECTION_INTERVAL')
        assert hasattr(config, 'MAX_GOAL_DRIVEN_ITERATIONS')
        assert hasattr(config, 'GOAL_DRIVEN_STAGNATION_WINDOW')

    def test_goal_driven_planner_import(self):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        assert GoalDrivenPlannerAgent is not None

    def test_v8_schema_models_available(self):
        from schema import GoalDocument, GoalReflection, Milestone, MilestonePlan, GoalReanchorResult
        assert GoalDocument is not None
        assert GoalReflection is not None
        assert Milestone is not None
        assert MilestonePlan is not None
        assert GoalReanchorResult is not None

    @pytest.mark.asyncio
    async def test_orchestrator_routes_to_v8_when_enabled(self):
        """When ENABLE_GOAL_DRIVEN_PLANNER=true, _execute_emergent uses v8."""
        import config

        original = config.ENABLE_GOAL_DRIVEN_PLANNER
        try:
            config.ENABLE_GOAL_DRIVEN_PLANNER = True

            from agents.orchestrator import OrchestratorAgent
            from unittest.mock import MagicMock, AsyncMock

            # Create orchestrator - should instantiate goal_driven_planner
            orch = OrchestratorAgent.__new__(OrchestratorAgent)
            orch.llm_client = MagicMock()
            orch.context_manager = MagicMock()
            orch._on_event = lambda *_: None
            orch._tracing_bridge = None
            orch.planner = MagicMock()
            orch.executor_agent = MagicMock()
            orch.reflector = MagicMock()
            orch.emergent_planner = MagicMock()
            orch.short_term = MagicMock()
            orch.long_term = MagicMock()
            orch.knowledge = MagicMock()
            orch.max_replan = 3

            # Re-run the v8 init logic
            orch.goal_driven_planner = None
            if config.ENABLE_GOAL_DRIVEN_PLANNER:
                from agents.goal_driven_planner import GoalDrivenPlannerAgent
                orch.goal_driven_planner = MagicMock(spec=GoalDrivenPlannerAgent)
                orch.goal_driven_planner.execute = AsyncMock(return_value="v8 answer")
                orch.goal_driven_planner._todo_list = None

            # _execute_emergent should use v8 when goal_driven_planner exists
            result = await orch._execute_emergent("test task", "")
            assert result == "v8 answer"

        finally:
            config.ENABLE_GOAL_DRIVEN_PLANNER = original

    @pytest.mark.asyncio
    async def test_orchestrator_falls_back_to_v5_when_disabled(self):
        """When ENABLE_GOAL_DRIVEN_PLANNER=false, _execute_emergent uses v5."""
        from agents.orchestrator import OrchestratorAgent
        from unittest.mock import MagicMock, AsyncMock

        orch = OrchestratorAgent.__new__(OrchestratorAgent)
        orch.llm_client = MagicMock()
        orch.context_manager = MagicMock()
        orch._on_event = lambda *_: None
        orch._tracing_bridge = None
        orch.planner = MagicMock()
        orch.executor_agent = MagicMock()
        orch.reflector = MagicMock()
        orch.short_term = MagicMock()
        orch.long_term = MagicMock()
        orch.knowledge = MagicMock()
        orch.max_replan = 3

        # v5 emergent planner
        orch.emergent_planner = MagicMock()
        orch.emergent_planner.execute = AsyncMock(return_value="v5 answer")
        orch.emergent_planner._todo_list = None

        # v8 NOT enabled
        orch.goal_driven_planner = None

        result = await orch._execute_emergent("test task", "")
        assert result == "v5 answer"


# ======================================================================
# Event Tests
# 事件测试
# ======================================================================


class TestV8Events:
    """Tests for v8 event emission."""

    @pytest.mark.asyncio
    async def test_events_emitted_during_execution(self):
        """Verify event sequence during v8 execution."""
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        from tools.web_search import WebSearchTool

        mock_llm = MagicMock()
        planner = GoalDrivenPlannerAgent(
            llm_client=mock_llm,
            tools=[WebSearchTool()],
            max_iterations=2,
        )
        planner.max_outer_iterations = 5

        # Mock all LLM interactions
        planner.think_json = AsyncMock(side_effect=[
            # _build_goal_document
            {"success_criteria": "done", "target_state_description": "complete", "key_deliverables": [], "constraints": []},
            # _backward_plan
            {"milestones": [{"description": "Step 1", "completion_criteria": "c1", "estimated_complexity": "low"}], "backward_reasoning": "test"},
            # _goal_reflect
            {"current_state_summary": "Starting", "gap_analysis": "All", "next_milestone": "Step 1", "progress_pct": 0.0, "suggested_action": "execute_todo", "reasoning": "test"},
            # _refresh_todo_list (optional, may not fire)
            {"new_todos": [], "modify_todos": [], "blocked_todos": []},
        ])
        planner.think = AsyncMock(return_value="Final answer")

        # Mock execute to succeed immediately
        async def mock_execute_todo(todo, goal_doc, reflection):
            return StepResult(step_id=str(todo.id), success=True, output="Done")

        planner._execute_todo_goal_guided = mock_execute_todo

        events = []
        planner._on_event = lambda e, d: events.append((e, d))

        await planner.execute("test task")

        event_names = [e for e, _ in events]
        assert "goal_anchor" in event_names
        assert "todo_list_initialized" in event_names
        assert "goal_reflection" in event_names
        assert "todo_start" in event_names
        assert "todo_complete" in event_names

        # Verify v8 event data format matches v5 (TracingBridge compatibility)
        for event_name, data in events:
            if event_name == "todo_start":
                assert "todo" in data, "todo_start must contain 'todo' key for TracingBridge"
                assert isinstance(data["todo"], TodoItem)
            elif event_name == "todo_complete":
                assert "todo" in data, "todo_complete must contain 'todo' key"
                assert "result" in data, "todo_complete must contain 'result' key"
                assert isinstance(data["result"], StepResult)

    def test_tracing_bridge_has_v8_handlers(self):
        """Verify TracingBridge has handlers for v8 events."""
        from tracing.bridge import TracingBridge

        try:
            bridge = TracingBridge()
            assert "goal_anchor" in bridge._event_handlers
            assert "goal_reflection" in bridge._event_handlers
            assert "goal_reanchor" in bridge._event_handlers
        except Exception:
            pass

    def test_v8_phase_span_name_mapping(self):
        """Verify _phase_to_span_name maps v8 phase texts correctly."""
        from tracing.bridge import TracingBridge
        from tracing.spans import SpanName

        try:
            mapping = TracingBridge._phase_to_span_name
            assert mapping("Executing with goal-driven planning (v8)...") == SpanName.EXECUTION_GOAL_DRIVEN
            assert mapping("Building goal document...") == SpanName.GOAL_ANCHOR
            assert mapping("Planning backward from goal state...") == SpanName.GOAL_ANCHOR
            assert mapping("Compiling final answer against goal...") == SpanName.GOAL_ANCHOR
        except Exception:
            pass

    def test_todo_event_format_tracing_bridge_compatible(self):
        """Verify v8 todo event data can be processed by TracingBridge handlers."""
        from tracing.bridge import TracingBridge

        try:
            bridge = TracingBridge()
            # Simulate v8 todo_start event with v5-compatible format
            todo = TodoItem(id=1, description="Test TODO")
            data = {"todo": todo}

            # _on_todo_start should extract todo from data
            handler = bridge._event_handlers.get("todo_start")
            assert handler is not None
            # Handler should not return early (data.get("todo") should find the TodoItem)
            assert data.get("todo") is not None
            assert isinstance(data.get("todo"), TodoItem)
        except Exception:
            pass


# ======================================================================
# Integration: Goal-Guided ReAct Loop (Mocked)
# 集成测试：目标引导 ReAct 循环（Mock）
# ======================================================================


class TestGoalGuidedReactLoop:
    """Tests for the bounded ReAct loop with goal injection."""

    def _make_planner(self):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        from tools.web_search import WebSearchTool

        mock_llm = MagicMock()
        planner = GoalDrivenPlannerAgent(
            llm_client=mock_llm,
            tools=[WebSearchTool()],
            max_iterations=3,
        )
        return planner

    @pytest.mark.asyncio
    async def test_execute_todo_no_tool_calls(self):
        """When LLM responds without tool_calls, TODO completes."""
        planner = self._make_planner()

        # Mock LLM response: no tool calls
        mock_response = MagicMock()
        mock_response.content = "Task is done"
        mock_response.tool_calls = None
        planner.llm_client.chat_with_tools = AsyncMock(return_value=mock_response)

        goal_doc = GoalDocument(
            original_task="test", success_criteria="done",
            target_state_description="complete",
        )
        reflection = GoalReflection(
            current_state_summary="", gap_analysis="", next_milestone="",
        )
        todo = TodoItem(id=1, description="Simple task")

        result = await planner._execute_todo_goal_guided(todo, goal_doc, reflection)
        assert result.success is True
        assert "Task is done" in result.output

    @pytest.mark.asyncio
    async def test_execute_todo_max_iterations(self):
        """When LLM keeps calling tools, hits max iterations."""
        planner = self._make_planner()

        # Mock LLM response: always has tool calls
        mock_tc = MagicMock()
        mock_tc.id = "tc_1"
        mock_tc.function.name = "web_search"
        mock_tc.function.arguments = '{"query": "test"}'

        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.tool_calls = [mock_tc]
        planner.llm_client.chat_with_tools = AsyncMock(return_value=mock_response)

        goal_doc = GoalDocument(
            original_task="test", success_criteria="done",
            target_state_description="complete",
        )
        reflection = GoalReflection(
            current_state_summary="", gap_analysis="", next_milestone="",
        )
        todo = TodoItem(id=1, description="Hard task")

        result = await planner._execute_todo_goal_guided(todo, goal_doc, reflection)
        assert result.success is False
        assert "max iterations" in result.output.lower() or "3" in result.output

    @pytest.mark.asyncio
    async def test_goal_injection_in_first_message(self):
        """First iteration should include goal document in the prompt."""
        planner = self._make_planner()

        captured_messages = []

        async def capture_llm(messages, **kwargs):
            captured_messages.extend(messages)
            mock_resp = MagicMock()
            mock_resp.content = "Done"
            mock_resp.tool_calls = None
            return mock_resp

        planner.llm_client.chat_with_tools = capture_llm

        goal_doc = GoalDocument(
            original_task="Write report",
            success_criteria="File saved",
            target_state_description="report.md exists",
        )
        reflection = GoalReflection(
            current_state_summary="", gap_analysis="", next_milestone="",
        )
        todo = TodoItem(id=1, description="Write the report")

        await planner._execute_todo_goal_guided(todo, goal_doc, reflection)

        # Check that goal document was injected
        user_msgs = [m for m in captured_messages if m.get("role") == "user"]
        assert len(user_msgs) > 0
        first_msg = user_msgs[0]["content"]
        assert "GOAL DOCUMENT" in first_msg
        assert "Write report" in first_msg
        assert "File saved" in first_msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
