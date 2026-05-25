"""
Tests for v14.5 Task Resume — checkpoint models and store.
"""

import asyncio
import json
import os
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from schema import (
    GoalDocument,
    GoalReflection,
    GoalAction,
    Milestone,
    MilestonePlan,
    NodeStatus,
    NodeType,
    Plan,
    Reflection,
    ReasoningEffort,
    Step,
    StepResult,
    StepStatus,
    TaskEdge,
    TaskNode,
    TaskRunState,
    TodoItem,
    TodoList,
    TodoStatus,
    ToolCallRecord,
)
from checkpoint.models import (
    CHECKPOINT_VERSION,
    CheckpointCorruptedError,
    CheckpointValidationError,
    CheckpointVersionMismatchError,
    DAGPathState,
    EmergentPathState,
    GoalDrivenPathState,
    SimplePathState,
    TaskCheckpoint,
    TaskCheckpointSummary,
)
from checkpoint.store import TaskStateStore
import config


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def store(tmp_path):
    """Create a TaskStateStore with a temp directory."""
    return TaskStateStore(checkpoint_dir=str(tmp_path / "checkpoints"))


@pytest.fixture
def fake_openai(monkeypatch):
    """Install minimal optional-dependency modules for offline Orchestrator imports."""
    module = sys.modules.get("openai") or types.ModuleType("openai")

    class DummyAsyncOpenAI:
        def __init__(self, *args, **kwargs):
            pass

    module.AsyncOpenAI = DummyAsyncOpenAI
    for name in ("RateLimitError", "APIError", "APITimeoutError", "BadRequestError"):
        setattr(module, name, type(name, (Exception,), {}))
    monkeypatch.setitem(sys.modules, "openai", module)

    mcp_module = types.ModuleType("mcp")
    mcp_client_module = types.ModuleType("mcp.client")
    mcp_stream_module = types.ModuleType("mcp.client.streamable_http")
    mcp_session_module = types.ModuleType("mcp.client.session")
    mcp_types_module = types.ModuleType("mcp.types")

    def streamablehttp_client(*args, **kwargs):
        raise RuntimeError("mcp SDK is not installed in offline tests")

    class DummyClientSession:
        def __init__(self, *args, **kwargs):
            pass

    class DummyTextContent:
        def __init__(self, text=""):
            self.text = text

    class DummyCallToolResult:
        isError = False
        content = []

    mcp_stream_module.streamablehttp_client = streamablehttp_client
    mcp_session_module.ClientSession = DummyClientSession
    mcp_types_module.CallToolResult = DummyCallToolResult
    mcp_types_module.TextContent = DummyTextContent

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", mcp_client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", mcp_stream_module)
    monkeypatch.setitem(sys.modules, "mcp.client.session", mcp_session_module)
    monkeypatch.setitem(sys.modules, "mcp.types", mcp_types_module)


@pytest.fixture
def sample_plan():
    return Plan(
        task="Test task",
        steps=[
            Step(id=1, description="Step 1", status=StepStatus.COMPLETED, result="done"),
            Step(id=2, description="Step 2", status=StepStatus.PENDING),
            Step(id=3, description="Step 3", status=StepStatus.PENDING),
        ],
    )


@pytest.fixture
def sample_step_results():
    return [
        StepResult(step_id=1, success=True, output="Step 1 output"),
    ]


@pytest.fixture
def sample_todo_list():
    return TodoList(
        task="Test task",
        todos={
            1: TodoItem(id=1, description="TODO 1", status=TodoStatus.COMPLETED, result="done"),
            2: TodoItem(id=2, description="TODO 2", status=TodoStatus.PENDING),
        },
        next_id=3,
    )


@pytest.fixture
def sample_goal_doc():
    return GoalDocument(
        original_task="Test task",
        success_criteria="Task is complete",
        target_state_description="Everything done",
        updated_at=time.time(),
    )


def _make_checkpoint(complexity="simple", **overrides) -> TaskCheckpoint:
    now = time.time()
    defaults = dict(
        task_id="test1234",
        task="Test task",
        context="Some context",
        complexity=complexity,
        effort="medium",
        state=TaskRunState.RUNNING,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return TaskCheckpoint(**defaults)


# ======================================================================
# Data Model Roundtrip Tests
# ======================================================================

class TestSimplePathStateRoundtrip:
    def test_plan_preserves_step_statuses(self, sample_plan, sample_step_results):
        state = SimplePathState(
            plan=sample_plan.model_dump(),
            all_results=[r.model_dump() for r in sample_step_results],
            attempt=0,
            current_step_index=1,
        )
        data = state.model_dump(mode="json")
        restored = SimplePathState(**data)
        assert restored.current_step_index == 1
        restored_plan = Plan(**restored.plan)
        assert restored_plan.steps[0].status == StepStatus.COMPLETED
        assert restored_plan.steps[1].status == StepStatus.PENDING

    def test_reflection_preserved(self, sample_plan):
        reflection = Reflection(passed=False, score=0.5, feedback="Not good enough")
        state = SimplePathState(
            plan=sample_plan.model_dump(),
            all_results=[],
            attempt=1,
            reflection=reflection.model_dump(),
        )
        data = state.model_dump(mode="json")
        restored = SimplePathState(**data)
        assert restored.reflection is not None
        r = Reflection(**restored.reflection)
        assert r.passed is False
        assert r.score == 0.5


class TestDAGPathStateRoundtrip:
    def test_dag_preserves_node_statuses(self):
        from dag.graph import TaskDAG
        from dag.state_machine import NodeStateMachine

        dag = TaskDAG(
            task="Test task",
            nodes={
                "goal_1": TaskNode(id="goal_1", node_type=NodeType.GOAL, description="Goal"),
                "act_1": TaskNode(id="act_1", node_type=NodeType.ACTION, description="Action 1",
                                  status=NodeStatus.COMPLETED, result="done"),
                "act_2": TaskNode(id="act_2", node_type=NodeType.ACTION, description="Action 2"),
            },
            edges=[
                TaskEdge(source="goal_1", target="act_1"),
                TaskEdge(source="goal_1", target="act_2"),
            ],
        )
        dag_dict = dag.to_dict()
        state = DAGPathState(dag=dag_dict, results=[], attempt=0)
        data = state.model_dump(mode="json")
        restored = DAGPathState(**data)
        sm = NodeStateMachine()
        restored_dag = TaskDAG.from_dict(restored.dag, sm)
        assert restored_dag.nodes["act_1"].status == NodeStatus.COMPLETED
        assert restored_dag.nodes["act_2"].status == NodeStatus.PENDING


class TestEmergentPathStateRoundtrip:
    def test_todo_list_preserves_statuses(self, sample_todo_list, sample_step_results):
        state = EmergentPathState(
            todo_list=sample_todo_list.model_dump(),
            all_results=[r.model_dump() for r in sample_step_results],
            iteration=3,
            stagnation_state={"prev_completed": 1, "stagnation_rounds": 0},
        )
        data = state.model_dump(mode="json")
        restored = EmergentPathState(**data)
        restored_todos = TodoList(**restored.todo_list)
        assert restored_todos.todos[1].status == TodoStatus.COMPLETED
        assert restored_todos.todos[2].status == TodoStatus.PENDING
        assert restored.iteration == 3


class TestGoalDrivenPathStateRoundtrip:
    def test_goal_doc_preserved(self, sample_todo_list, sample_goal_doc):
        state = GoalDrivenPathState(
            todo_list=sample_todo_list.model_dump(),
            all_results=[],
            iteration=2,
            stagnation_state={"prev_completed": 0, "stagnation_rounds": 0},
            goal_doc=sample_goal_doc.model_dump(),
            reanchor_counter=1,
        )
        data = state.model_dump(mode="json")
        restored = GoalDrivenPathState(**data)
        restored_goal = GoalDocument(**restored.goal_doc)
        assert restored_goal.original_task == "Test task"
        assert restored.reanchor_counter == 1

    def test_milestone_and_reflection(self, sample_todo_list, sample_goal_doc):
        milestone_plan = MilestonePlan(
            goal_description="Done",
            milestones=[Milestone(id=1, description="M1", completion_criteria="c1")],
        )
        goal_reflection = GoalReflection(
            current_state_summary="Starting",
            gap_analysis="Much to do",
            next_milestone="M1",
            suggested_action=GoalAction.EXECUTE_TODO,
        )
        state = GoalDrivenPathState(
            todo_list=sample_todo_list.model_dump(),
            all_results=[],
            iteration=1,
            stagnation_state={},
            goal_doc=sample_goal_doc.model_dump(),
            milestone_plan=milestone_plan.model_dump(),
            last_reflection=goal_reflection.model_dump(),
            reanchor_counter=0,
        )
        data = state.model_dump(mode="json")
        restored = GoalDrivenPathState(**data)
        assert restored.milestone_plan is not None
        mp = MilestonePlan(**restored.milestone_plan)
        assert len(mp.milestones) == 1
        assert restored.last_reflection is not None
        gr = GoalReflection(**restored.last_reflection)
        assert gr.suggested_action == GoalAction.EXECUTE_TODO


# ======================================================================
# TaskCheckpoint Roundtrip
# ======================================================================

class TestTaskCheckpointRoundtrip:
    def test_simple_checkpoint_roundtrip(self, sample_plan, sample_step_results):
        cp = _make_checkpoint(
            complexity="simple",
            simple_state=SimplePathState(
                plan=sample_plan.model_dump(),
                all_results=[r.model_dump() for r in sample_step_results],
                attempt=0,
                current_step_index=1,
            ),
        )
        data = cp.model_dump(mode="json")
        restored = TaskCheckpoint(**data)
        assert restored.task_id == "test1234"
        assert restored.simple_state is not None
        assert restored.simple_state.current_step_index == 1

    def test_emergent_checkpoint_roundtrip(self, sample_todo_list):
        cp = _make_checkpoint(
            complexity="emergent",
            emergent_state=EmergentPathState(
                todo_list=sample_todo_list.model_dump(),
                all_results=[],
                iteration=5,
                stagnation_state={"prev_completed": 2, "stagnation_rounds": 1},
            ),
        )
        data = cp.model_dump(mode="json")
        restored = TaskCheckpoint(**data)
        assert restored.emergent_state is not None
        assert restored.emergent_state.iteration == 5


# ======================================================================
# TaskStateStore Tests
# ======================================================================

class TestTaskStateStore:
    def test_save_and_load(self, store, sample_plan, sample_step_results):
        cp = _make_checkpoint(
            complexity="simple",
            simple_state=SimplePathState(
                plan=sample_plan.model_dump(),
                all_results=[r.model_dump() for r in sample_step_results],
            ),
        )
        filepath = store.save(cp)
        assert os.path.exists(filepath)

        loaded = store.load("test1234")
        assert loaded is not None
        assert loaded.task_id == "test1234"
        assert loaded.complexity == "simple"
        assert loaded.simple_state is not None

    def test_load_nonexistent_returns_none(self, store):
        assert store.load("nonexistent") is None

    def test_list_tasks(self, store):
        for i in range(3):
            cp = _make_checkpoint(task_id=f"task{i}", task=f"Task {i}")
            store.save(cp)
        summaries = store.list_tasks()
        assert len(summaries) == 3
        task_ids = {s.task_id for s in summaries}
        assert task_ids == {"task0", "task1", "task2"}

    def test_mark_completed(self, store):
        cp = _make_checkpoint()
        store.save(cp)
        store.mark_completed("test1234")
        loaded = store.load("test1234")
        assert loaded is not None
        assert loaded.state == TaskRunState.COMPLETED

    def test_mark_failed(self, store):
        cp = _make_checkpoint()
        store.save(cp)
        store.mark_failed("test1234")
        loaded = store.load("test1234")
        assert loaded is not None
        assert loaded.state == TaskRunState.FAILED

    def test_delete(self, store):
        cp = _make_checkpoint()
        store.save(cp)
        store.delete("test1234")
        assert store.load("test1234") is None

    def test_corrupted_file_raises(self, store):
        cp = _make_checkpoint()
        store.save(cp)
        # Overwrite with garbage
        latest = store._latest_file_for("test1234")
        assert latest is not None
        with open(latest, "w") as f:
            f.write("NOT VALID JSON {{{")
        with pytest.raises(CheckpointCorruptedError):
            store.load("test1234")

    def test_version_mismatch_raises(self, store):
        cp = _make_checkpoint()
        store.save(cp)
        latest = store._latest_file_for("test1234")
        assert latest is not None
        with open(latest, "r") as f:
            data = json.load(f)
        data["checkpoint_version"] = 999
        with open(latest, "w") as f:
            json.dump(data, f)
        with pytest.raises((CheckpointCorruptedError, CheckpointVersionMismatchError)):
            store.load("test1234")

    def test_prune_old_checkpoints(self, store):
        cp = _make_checkpoint()
        # Save more than CHECKPOINT_MAX_PER_TASK
        for _ in range(7):
            store.save(cp)
        from pathlib import Path as _Path
        files = list(_Path(store._dir).glob("test1234_*.json"))
        assert len(files) <= config.CHECKPOINT_MAX_PER_TASK

    def test_atomic_write_no_tmp_left(self, store):
        cp = _make_checkpoint()
        store.save(cp)
        from pathlib import Path as _Path
        tmp_files = list(_Path(store._dir).glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_latest_loads_most_recent(self, store):
        now = time.time()
        # Save an older checkpoint first
        cp_old = _make_checkpoint(updated_at=now - 100)
        store.save(cp_old)
        # Save a newer checkpoint
        cp_new = _make_checkpoint(updated_at=now, state=TaskRunState.PAUSED_WAITING_USER)
        store.save(cp_new)
        loaded = store.load("test1234")
        assert loaded is not None
        assert loaded.state == TaskRunState.PAUSED_WAITING_USER


# ======================================================================
# Integration Tests — Orchestrator Resume
# ======================================================================

def _can_import_agents():
    """Check if agent modules can be imported (requires openai package)."""
    try:
        import openai  # noqa: F401
        return True
    except ModuleNotFoundError:
        return False


requires_openai = pytest.mark.skipif(
    not _can_import_agents(),
    reason="openai package not installed — agent integration tests skipped",
)


def _make_offline_orchestrator(store, fake_openai, monkeypatch):
    monkeypatch.setattr(config, "TASK_RESUME_ENABLED", True)
    monkeypatch.setattr(config, "TRACING_ENABLED", False)

    from agents.orchestrator import OrchestratorAgent

    llm_client = MagicMock()
    llm_client.model = "test-model"
    llm_client.get_call_records = MagicMock(return_value=[])
    llm_client.reset_usage = MagicMock()
    llm_client.chat = AsyncMock(return_value="final answer")
    llm_client.chat_with_tools = AsyncMock()
    llm_client.chat_json = AsyncMock(return_value={})

    orch = OrchestratorAgent(
        llm_client=llm_client,
        tools=[],
        on_event=lambda e, d: None,
        interactive=False,
        task_state_store=store,
    )
    orch._store_memory = MagicMock()
    return orch


class TestOrchestratorCheckpointLifecycle:
    """Regression tests for Orchestrator-level checkpoint lifecycle."""

    def test_run_marks_task_completed(self, store, fake_openai, monkeypatch):
        orch = _make_offline_orchestrator(store, fake_openai, monkeypatch)
        plan = Plan(task="Test task", steps=[])

        orch._gather_context = AsyncMock(return_value="ctx")
        orch.planner.classify_task = AsyncMock(return_value=("simple", ReasoningEffort.MEDIUM))
        orch.planner.create_plan = AsyncMock(return_value=plan)
        orch._execute_and_reflect_simple = AsyncMock(return_value="answer")

        answer = asyncio.run(orch.run("Test task"))

        assert answer == "answer"
        summaries = store.list_tasks()
        assert len(summaries) == 1
        assert summaries[0].state == TaskRunState.COMPLETED
        loaded = store.load(summaries[0].task_id)
        assert loaded is not None
        assert loaded.task == "Test task"
        assert loaded.resume_metadata["boundary"] == "task_complete"

    def test_resume_completed_task_uses_injected_store(self, store, fake_openai, monkeypatch, sample_plan):
        cp = _make_checkpoint(
            complexity="simple",
            state=TaskRunState.COMPLETED,
            simple_state=SimplePathState(
                plan=sample_plan.model_dump(),
                all_results=[],
            ),
        )
        store.save(cp)
        orch = _make_offline_orchestrator(store, fake_openai, monkeypatch)

        with pytest.raises(ValueError, match="already completed"):
            asyncio.run(orch.resume("test1234"))

    def test_resume_preserves_task_when_saving_followup_checkpoint(
        self,
        store,
        fake_openai,
        monkeypatch,
        sample_plan,
    ):
        cp = _make_checkpoint(
            complexity="simple",
            task="Original task text",
            simple_state=SimplePathState(
                plan=sample_plan.model_dump(),
                all_results=[],
            ),
        )
        store.save(cp)
        orch = _make_offline_orchestrator(store, fake_openai, monkeypatch)

        async def fake_resume_simple(checkpoint):
            orch._save_checkpoint(TaskRunState.RUNNING)
            return "answer"

        orch._resume_simple = fake_resume_simple

        asyncio.run(orch.resume("test1234"))

        loaded = store.load("test1234")
        assert loaded is not None
        assert loaded.state == TaskRunState.COMPLETED
        assert loaded.task == "Original task text"

    def test_hitl_prompt_saves_paused_checkpoint_metadata(self, store, fake_openai, monkeypatch):
        orch = _make_offline_orchestrator(store, fake_openai, monkeypatch)
        orch._current_task_id = "hitl1234"
        orch._checkpoint_task = "Need user input"
        orch._active_complexity = "simple"
        orch._checkpoint_context = "ctx"
        orch._checkpoint_effort = ReasoningEffort.MEDIUM
        orch._checkpoint_created_at = time.time()

        async def invoke():
            future = asyncio.get_running_loop().create_future()
            orch._handle_user_prompt("Which city?", "prompt01", future)

        asyncio.run(invoke())

        loaded = store.load("hitl1234")
        assert loaded is not None
        assert loaded.state == TaskRunState.PAUSED_WAITING_USER
        assert loaded.resume_metadata["boundary"] == "hitl_prompt"
        assert loaded.resume_metadata["prompt_id"] == "prompt01"
        assert loaded.resume_metadata["question"] == "Which city?"

    def test_emergent_progress_callback_saves_path_state(
        self,
        store,
        fake_openai,
        monkeypatch,
        sample_todo_list,
        sample_step_results,
    ):
        orch = _make_offline_orchestrator(store, fake_openai, monkeypatch)
        orch._current_task_id = "emrg1234"
        orch._checkpoint_task = "Emergent task"
        orch._active_complexity = "emergent"
        orch._checkpoint_effort = ReasoningEffort.MEDIUM
        orch._checkpoint_created_at = time.time()

        orch._checkpoint_emergent_progress({
            "boundary": "after_todo",
            "committed_ids": ["1"],
            "todo_list": sample_todo_list.model_dump(),
            "all_results": [r.model_dump() for r in sample_step_results],
            "iteration": 1,
            "stagnation_state": {"prev_completed": 1, "stagnation_rounds": 0},
        })

        loaded = store.load("emrg1234")
        assert loaded is not None
        assert loaded.emergent_state is not None
        assert loaded.emergent_state.iteration == 1
        assert loaded.resume_metadata["committed_ids"] == ["1"]

    def test_goal_driven_progress_callback_saves_path_state(
        self,
        store,
        fake_openai,
        monkeypatch,
        sample_todo_list,
        sample_step_results,
        sample_goal_doc,
    ):
        orch = _make_offline_orchestrator(store, fake_openai, monkeypatch)
        orch._current_task_id = "goal1234"
        orch._checkpoint_task = "Goal task"
        orch._active_complexity = "goal_driven"
        orch._checkpoint_effort = ReasoningEffort.MEDIUM
        orch._checkpoint_created_at = time.time()

        orch._checkpoint_goal_driven_progress({
            "boundary": "after_todo",
            "committed_ids": ["1"],
            "todo_list": sample_todo_list.model_dump(),
            "all_results": [r.model_dump() for r in sample_step_results],
            "iteration": 1,
            "stagnation_state": {"prev_completed": 1, "stagnation_rounds": 0},
            "goal_doc": sample_goal_doc.model_dump(),
            "last_reflection": None,
            "reanchor_counter": 0,
        })

        loaded = store.load("goal1234")
        assert loaded is not None
        assert loaded.goal_driven_state is not None
        assert loaded.goal_driven_state.iteration == 1
        assert loaded.resume_metadata["boundary"] == "after_todo"


class TestSimplePathResume:
    """Test simple path checkpoint + resume with mocked LLM."""

    @pytest.fixture
    def orchestrator(self):
        from agents.orchestrator import OrchestratorAgent
        from llm.client import LLMClient
        from unittest.mock import AsyncMock, MagicMock

        llm_client = MagicMock(spec=LLMClient)
        llm_client.model = "test-model"
        llm_client.get_call_records = MagicMock(return_value=[])
        llm_client.reset_usage = MagicMock()

        # Mock chat to return simple JSON plan
        plan_json = json.dumps({
            "task": "test",
            "steps": [
                {"id": 1, "description": "Step 1"},
                {"id": 2, "description": "Step 2"},
                {"id": 3, "description": "Step 3"},
            ],
            "current_step_index": 0,
        })
        llm_client.chat = AsyncMock(return_value=plan_json)
        llm_client.chat_with_tools = AsyncMock(return_value=MagicMock(
            content="Done", tool_calls=None,
        ))
        llm_client.chat_json = AsyncMock(return_value={
            "passed": True, "score": 1.0, "feedback": "OK", "suggestions": [],
        })

        # Minimal tool set (empty)
        orch = OrchestratorAgent(
            llm_client=llm_client,
            tools=[],
            on_event=lambda e, d: None,
            interactive=False,
        )
        return orch

    @requires_openai
    def test_simple_checkpoint_has_task_id(self, orchestrator):
        """Verify that run() assigns a task_id and can build checkpoints."""
        orchestrator._current_task_id = "abc12345"
        orchestrator._checkpoint_task = "Test task"
        orchestrator._active_complexity = "simple"
        orchestrator._checkpoint_effort = ReasoningEffort.MEDIUM
        orchestrator._checkpoint_context = "ctx"
        orchestrator._checkpoint_created_at = time.time()
        cp = orchestrator._build_checkpoint(TaskRunState.RUNNING)
        assert cp.task_id == "abc12345"
        assert cp.task == "Test task"

    @requires_openai
    def test_resume_completed_task_raises(self, store, sample_plan):
        """Resuming a completed task should raise ValueError."""
        cp = _make_checkpoint(
            complexity="simple",
            state=TaskRunState.COMPLETED,
            simple_state=SimplePathState(
                plan=sample_plan.model_dump(),
                all_results=[],
            ),
        )
        store.save(cp)
        from agents.orchestrator import OrchestratorAgent
        from llm.client import LLMClient
        from unittest.mock import MagicMock
        import asyncio
        orch = OrchestratorAgent(
            llm_client=MagicMock(spec=LLMClient),
            tools=[],
            on_event=lambda e, d: None,
            task_state_store=store,
        )
        with pytest.raises(ValueError, match="already completed"):
            asyncio.run(orch.resume("test1234"))

    @requires_openai
    def test_resume_nonexistent_task_raises(self):
        """Resuming a nonexistent task_id should raise ValueError."""
        from agents.orchestrator import OrchestratorAgent
        from llm.client import LLMClient
        from unittest.mock import MagicMock
        import asyncio
        orch = OrchestratorAgent(
            llm_client=MagicMock(spec=LLMClient),
            tools=[],
            on_event=lambda e, d: None,
        )
        with pytest.raises(ValueError, match="No checkpoint found"):
            asyncio.run(orch.resume("nonexistent"))


class TestDAGPathResume:
    """Test DAG path checkpoint + resume."""

    def test_dag_checkpoint_from_orchestrator(self):
        """Verify DAG checkpoint is built correctly from a DAG."""
        from dag.graph import TaskDAG
        from dag.state_machine import NodeStateMachine

        dag = TaskDAG(
            task="Test task",
            nodes={
                "goal_1": TaskNode(id="goal_1", node_type=NodeType.GOAL, description="Goal"),
                "act_1": TaskNode(id="act_1", node_type=NodeType.ACTION, description="Action 1",
                                  status=NodeStatus.COMPLETED, result="done"),
                "act_2": TaskNode(id="act_2", node_type=NodeType.ACTION, description="Action 2"),
            },
            edges=[
                TaskEdge(source="goal_1", target="act_1"),
                TaskEdge(source="goal_1", target="act_2"),
            ],
        )
        dag_state = DAGPathState(
            dag=dag.to_dict(),
            results=[StepResult(step_id="act_1", success=True, output="done").model_dump()],
        )
        cp = _make_checkpoint(complexity="complex", dag_state=dag_state)
        data = cp.model_dump(mode="json")
        restored = TaskCheckpoint(**data)
        assert restored.dag_state is not None
        sm = NodeStateMachine()
        restored_dag = TaskDAG.from_dict(restored.dag_state.dag, sm)
        assert restored_dag.nodes["act_1"].status == NodeStatus.COMPLETED
        assert restored_dag.nodes["act_2"].status == NodeStatus.PENDING


class TestEmergentPathResume:
    """Test emergent path checkpoint + resume data model."""

    def test_emergent_state_roundtrip_via_checkpoint(self, sample_todo_list):
        cp = _make_checkpoint(
            complexity="emergent",
            emergent_state=EmergentPathState(
                todo_list=sample_todo_list.model_dump(),
                all_results=[],
                iteration=5,
                stagnation_state={"prev_completed": 1, "stagnation_rounds": 0},
            ),
        )
        data = cp.model_dump(mode="json")
        restored = TaskCheckpoint(**data)
        assert restored.emergent_state is not None
        assert restored.emergent_state.iteration == 5
        todos = TodoList(**restored.emergent_state.todo_list)
        assert todos.todos[1].status == TodoStatus.COMPLETED


class TestGoalDrivenPathResume:
    """Test goal-driven path checkpoint + resume data model."""

    def test_goal_driven_state_roundtrip(self, sample_todo_list, sample_goal_doc):
        cp = _make_checkpoint(
            complexity="goal_driven",
            goal_driven_state=GoalDrivenPathState(
                todo_list=sample_todo_list.model_dump(),
                all_results=[],
                iteration=3,
                stagnation_state={"prev_completed": 0, "stagnation_rounds": 0},
                goal_doc=sample_goal_doc.model_dump(),
                reanchor_counter=2,
            ),
        )
        data = cp.model_dump(mode="json")
        restored = TaskCheckpoint(**data)
        assert restored.goal_driven_state is not None
        assert restored.goal_driven_state.reanchor_counter == 2
        goal = GoalDocument(**restored.goal_driven_state.goal_doc)
        assert goal.original_task == "Test task"


class TestHITLPauseCheckpoint:
    """Test HITL pause checkpoint state."""

    def test_paused_waiting_user_state(self):
        cp = _make_checkpoint(state=TaskRunState.PAUSED_WAITING_USER)
        assert cp.state == TaskRunState.PAUSED_WAITING_USER
        data = cp.model_dump(mode="json")
        restored = TaskCheckpoint(**data)
        assert restored.state == TaskRunState.PAUSED_WAITING_USER

    def test_hitl_prompt_count_preserved(self):
        cp = _make_checkpoint(hitl_prompt_count=3)
        assert cp.hitl_prompt_count == 3
        data = cp.model_dump(mode="json")
        restored = TaskCheckpoint(**data)
        assert restored.hitl_prompt_count == 3


class TestPlannerResumeMethods:
    """Test that EmergentPlanner and GoalDrivenPlanner have resume_execute."""

    @requires_openai
    def test_emergent_planner_has_resume_execute(self):
        from agents.emergent_planner import EmergentPlannerAgent
        assert hasattr(EmergentPlannerAgent, "resume_execute")

    @requires_openai
    def test_goal_driven_planner_has_resume_execute(self):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        assert hasattr(GoalDrivenPlannerAgent, "resume_execute")

    @requires_openai
    def test_emergent_has_run_emergent_loop(self):
        from agents.emergent_planner import EmergentPlannerAgent
        assert hasattr(EmergentPlannerAgent, "_run_emergent_loop")

    @requires_openai
    def test_goal_driven_has_run_goal_driven_loop(self):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        assert hasattr(GoalDrivenPlannerAgent, "_run_goal_driven_loop")
