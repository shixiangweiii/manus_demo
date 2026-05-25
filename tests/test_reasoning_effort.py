"""
Batch 4.2 tests: ReasoningEffort enum and effort flow from Planner → Engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schema import ReasoningEffort


class TestReasoningEffortEnum:
    def test_enum_values(self):
        assert ReasoningEffort.LOW.value == "low"
        assert ReasoningEffort.MEDIUM.value == "medium"
        assert ReasoningEffort.HIGH.value == "high"

    def test_is_str_enum(self):
        assert isinstance(ReasoningEffort.LOW, str)
        assert ReasoningEffort.LOW == "low"


class TestPlannerEffortMapping:
    def _make_planner(self):
        from agents.planner import PlannerAgent
        from context.manager import ContextManager
        from llm.client import LLMClient

        client = MagicMock(spec=LLMClient)
        cm = MagicMock(spec=ContextManager)
        return PlannerAgent(llm_client=client, context_manager=cm)

    @pytest.mark.asyncio
    async def test_classify_task_returns_tuple(self):
        planner = self._make_planner()
        with patch("config.PLAN_MODE", "simple"):
            result = await planner.classify_task("hello")
        assert isinstance(result, tuple)
        assert len(result) == 2
        complexity, effort = result
        assert complexity == "simple"
        assert isinstance(effort, ReasoningEffort)

    @pytest.mark.asyncio
    async def test_simple_task_gets_low_effort(self):
        planner = self._make_planner()
        with patch("config.PLAN_MODE", "simple"):
            _, effort = await planner.classify_task("list files")
        assert effort == ReasoningEffort.LOW

    @pytest.mark.asyncio
    async def test_emergent_task_gets_high_effort(self):
        planner = self._make_planner()
        with patch("config.PLAN_MODE", "emergent"), \
             patch("config.EMERGENT_PLANNING_ENABLED", True):
            _, effort = await planner.classify_task("explore and research")
        assert effort == ReasoningEffort.HIGH

    @pytest.mark.asyncio
    async def test_complex_task_gets_medium_effort(self):
        planner = self._make_planner()
        with patch("config.PLAN_MODE", "complex"):
            _, effort = await planner.classify_task("build pipeline")
        assert effort == ReasoningEffort.MEDIUM

    @pytest.mark.asyncio
    async def test_effort_override_low(self):
        planner = self._make_planner()
        with patch("config.PLAN_MODE", "auto"), \
             patch("config.REASONING_EFFORT", "low"), \
             patch("config.EMERGENT_PLANNING_ENABLED", True):
            _, effort = await planner.classify_task("explore deeply")
        assert effort == ReasoningEffort.LOW

    @pytest.mark.asyncio
    async def test_effort_override_high(self):
        planner = self._make_planner()
        with patch("config.PLAN_MODE", "auto"), \
             patch("config.REASONING_EFFORT", "high"), \
             patch("config.EMERGENT_PLANNING_ENABLED", True):
            _, effort = await planner.classify_task("simple task")
        assert effort == ReasoningEffort.HIGH

    @pytest.mark.asyncio
    async def test_effort_auto_uses_classifier(self):
        planner = self._make_planner()
        with patch("config.PLAN_MODE", "auto"), \
             patch("config.REASONING_EFFORT", "auto"), \
             patch("config.EMERGENT_PLANNING_ENABLED", True):
            complexity, effort = await planner.classify_task("list files")
        # Short task → rule classifier says simple → LOW
        assert complexity == "simple"
        assert effort == ReasoningEffort.LOW


class TestEngineEffortBehavior:
    def _make_engine(self):
        from react.engine import ReActEngine
        from context.manager import ContextManager
        from llm.client import LLMClient

        client = MagicMock(spec=LLMClient)
        cm = MagicMock(spec=ContextManager)
        cm.compress_if_needed = AsyncMock(side_effect=lambda msgs, llm, **kw: msgs)
        engine = ReActEngine(
            llm_client=client,
            tools=[],
            max_iterations=10,
            context_manager=cm,
        )
        return engine

    def test_low_effort_reduces_iterations(self):
        engine = self._make_engine()
        temp, max_iter = engine._apply_effort(ReasoningEffort.LOW)
        assert max_iter == 5  # max(3, 10//2)
        assert temp == 0.3

    def test_high_effort_full_iterations(self):
        engine = self._make_engine()
        temp, max_iter = engine._apply_effort(ReasoningEffort.HIGH)
        assert max_iter == 10  # full
        assert temp == 0.7

    def test_medium_effort_defaults(self):
        import config
        engine = self._make_engine()
        temp, max_iter = engine._apply_effort(ReasoningEffort.MEDIUM)
        assert max_iter == 10
        assert temp == config.REACT_TEMPERATURE

    def test_low_effort_caps_truncation(self):
        from react.engine_helpers import ToolExecutionPolicy
        policy = ToolExecutionPolicy.for_effort(ReasoningEffort.LOW)
        assert policy.truncation_limit <= 1000

    def test_high_effort_increases_truncation(self):
        from react.engine_helpers import ToolExecutionPolicy
        policy = ToolExecutionPolicy.for_effort(ReasoningEffort.HIGH)
        assert policy.truncation_limit >= 4000


class TestDAGEffortPropagation:
    """Fix 1: effort must flow from Orchestrator → DAGExecutor → execute_node."""

    def test_dag_executor_stores_effort(self):
        from dag.executor import DAGExecutor
        from agents.executor import ExecutorAgent
        from agents.reflector import ReflectorAgent

        executor = MagicMock(spec=ExecutorAgent)
        reflector = MagicMock(spec=ReflectorAgent)
        dag_ex = DAGExecutor(
            executor_agent=executor,
            reflector_agent=reflector,
            effort=ReasoningEffort.LOW,
        )
        assert dag_ex._effort == ReasoningEffort.LOW

    def test_dag_executor_none_effort_default(self):
        from dag.executor import DAGExecutor
        from agents.executor import ExecutorAgent
        from agents.reflector import ReflectorAgent

        executor = MagicMock(spec=ExecutorAgent)
        reflector = MagicMock(spec=ReflectorAgent)
        dag_ex = DAGExecutor(
            executor_agent=executor,
            reflector_agent=reflector,
        )
        assert dag_ex._effort is None

    @pytest.mark.asyncio
    async def test_run_node_passes_effort_to_execute_node(self):
        from dag.executor import DAGExecutor
        from agents.executor import ExecutorAgent
        from agents.reflector import ReflectorAgent
        from schema import StepResult, TaskNode, NodeStatus

        executor = MagicMock(spec=ExecutorAgent)
        executor.execute_node = AsyncMock(return_value=StepResult(
            step_id="n1", success=True, output="done", iterations_completed=1,
        ))
        reflector = MagicMock(spec=ReflectorAgent)

        dag_ex = DAGExecutor(
            executor_agent=executor,
            reflector_agent=reflector,
            effort=ReasoningEffort.HIGH,
        )

        node = TaskNode(id="n1", name="test", node_type="action", description="test")
        node.status = NodeStatus.READY

        from dag.graph import TaskDAG
        dag = MagicMock(spec=TaskDAG)
        dag.state = MagicMock()
        dag.state.get_node_context = MagicMock(return_value="context")
        dag.get_dependency_ids = MagicMock(return_value=[])

        with patch("config.DAG_SERIAL_EXECUTION", True):
            await dag_ex._run_node(node, dag)

        executor.execute_node.assert_called_once()
        call_kwargs = executor.execute_node.call_args
        assert call_kwargs.kwargs.get("effort") == ReasoningEffort.HIGH


class TestEmergentGoalDrivenEffortPropagation:
    """Fix 2: effort must flow from Orchestrator → EmergentPlanner / GoalDrivenPlanner."""

    @pytest.mark.asyncio
    async def test_emergent_planner_execute_receives_effort(self):
        from agents.emergent_planner import EmergentPlannerAgent
        from llm.client import LLMClient
        from context.manager import ContextManager

        client = MagicMock(spec=LLMClient)
        cm = MagicMock(spec=ContextManager)
        planner = EmergentPlannerAgent(llm_client=client, context_manager=cm, tools=[])

        # Execute with effort=HIGH — just check the effort is stored
        # (full execution would need LLM mock, but _current_effort is set early)
        with patch.object(planner, '_init_todo_list', new_callable=AsyncMock), \
             patch.object(planner, '_todo_list') as todo_list_mock:
            todo_list_mock.has_pending = MagicMock(return_value=False)
            await planner.execute("test task", effort=ReasoningEffort.HIGH)
        assert planner._current_effort == ReasoningEffort.HIGH

    @pytest.mark.asyncio
    async def test_goal_driven_planner_execute_receives_effort(self):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        from llm.client import LLMClient
        from context.manager import ContextManager

        client = MagicMock(spec=LLMClient)
        cm = MagicMock(spec=ContextManager)
        planner = GoalDrivenPlannerAgent(llm_client=client, context_manager=cm, tools=[])

        # Execute with effort=LOW — check effort is stored
        with patch.object(planner, '_build_goal_document', new_callable=AsyncMock) as mock_bg, \
             patch.object(planner, '_backward_plan', new_callable=AsyncMock), \
             patch.object(planner, '_todo_list') as todo_list_mock:
            from schema import GoalDocument
            mock_bg.return_value = GoalDocument(
                original_task="test", success_criteria="done",
                target_state_description="task is complete",
            )
            todo_list_mock.has_pending = MagicMock(return_value=False)
            await planner.execute("test task", effort=ReasoningEffort.LOW)
        assert planner._current_effort == ReasoningEffort.LOW

    @pytest.mark.asyncio
    async def test_emergent_planner_default_effort_is_medium(self):
        from agents.emergent_planner import EmergentPlannerAgent
        from llm.client import LLMClient
        from context.manager import ContextManager

        client = MagicMock(spec=LLMClient)
        cm = MagicMock(spec=ContextManager)
        planner = EmergentPlannerAgent(llm_client=client, context_manager=cm, tools=[])

        with patch.object(planner, '_init_todo_list', new_callable=AsyncMock), \
             patch.object(planner, '_todo_list') as todo_list_mock:
            todo_list_mock.has_pending = MagicMock(return_value=False)
            await planner.execute("test task")
        assert planner._current_effort == ReasoningEffort.MEDIUM
