"""
v20.4 Checkpoint skill persistence unit tests.
Checkpoint 技能状态持久化单元测试。
"""

import json

import pytest

from checkpoint.models import (
    CHECKPOINT_VERSION,
    DAGPathState,
    EmergentPathState,
    GoalDrivenPathState,
    SimplePathState,
    TaskCheckpoint,
)
from schema import TaskRunState


# ======================================================================
# PathState active_skills fields
# ======================================================================

class TestPathStateActiveSkills:
    """Test active_skills and skill_activation_count on all PathState models."""

    def test_simple_default(self):
        state = SimplePathState(plan={}, all_results=[], attempt=0, current_step_index=0)
        assert state.active_skills == []
        assert state.skill_activation_count == 0

    def test_simple_set_values(self):
        state = SimplePathState(
            plan={}, all_results=[], attempt=0, current_step_index=0,
            active_skills=["hello-world", "data-analysis"],
            skill_activation_count=2,
        )
        assert state.active_skills == ["hello-world", "data-analysis"]
        assert state.skill_activation_count == 2

    def test_dag_default(self):
        state = DAGPathState(dag={}, results=[], attempt=0)
        assert state.active_skills == []
        assert state.skill_activation_count == 0

    def test_dag_set_values(self):
        state = DAGPathState(
            dag={}, results=[], attempt=0,
            active_skills=["web-research"],
            skill_activation_count=1,
        )
        assert state.active_skills == ["web-research"]
        assert state.skill_activation_count == 1

    def test_emergent_default(self):
        state = EmergentPathState(
            todo_list={}, all_results=[], iteration=0, stagnation_state={},
        )
        assert state.active_skills == []
        assert state.skill_activation_count == 0

    def test_emergent_set_values(self):
        state = EmergentPathState(
            todo_list={}, all_results=[], iteration=0, stagnation_state={},
            active_skills=["hello-world"],
            skill_activation_count=1,
        )
        assert state.active_skills == ["hello-world"]

    def test_goal_driven_default(self):
        state = GoalDrivenPathState(
            todo_list={}, all_results=[], iteration=0,
            stagnation_state={}, goal_doc={},
        )
        assert state.active_skills == []
        assert state.skill_activation_count == 0

    def test_goal_driven_set_values(self):
        state = GoalDrivenPathState(
            todo_list={}, all_results=[], iteration=0,
            stagnation_state={}, goal_doc={},
            active_skills=["data-analysis"],
            skill_activation_count=1,
        )
        assert state.active_skills == ["data-analysis"]


# ======================================================================
# Checkpoint version
# ======================================================================

class TestCheckpointVersion:
    """Test CHECKPOINT_VERSION is bumped for v20.4."""

    def test_version_is_2(self):
        assert CHECKPOINT_VERSION == 2


# ======================================================================
# Backward compatibility
# ======================================================================

class TestCheckpointBackwardCompat:
    """Test that old checkpoint JSON (version 1) still deserializes correctly."""

    def test_old_simple_checkpoint_without_skill_fields(self):
        """Version 1 checkpoint JSON without active_skills should load fine."""
        old_json = {
            "task_id": "test_old_001",
            "task": "old task",
            "context": "",
            "complexity": "simple",
            "effort": "medium",
            "state": "running",
            "created_at": 1000.0,
            "updated_at": 1000.0,
            "checkpoint_version": 1,
            "simple_state": {
                "plan": {"steps": []},
                "all_results": [],
                "attempt": 0,
                "current_step_index": 0,
            },
        }
        checkpoint = TaskCheckpoint(**old_json)
        assert checkpoint.simple_state is not None
        assert checkpoint.simple_state.active_skills == []
        assert checkpoint.simple_state.skill_activation_count == 0

    def test_old_dag_checkpoint_without_skill_fields(self):
        old_json = {
            "task_id": "test_old_002",
            "task": "old dag task",
            "context": "",
            "complexity": "complex",
            "effort": "medium",
            "state": "running",
            "created_at": 1000.0,
            "updated_at": 1000.0,
            "checkpoint_version": 1,
            "dag_state": {
                "dag": {"nodes": {}, "edges": []},
                "results": [],
                "attempt": 0,
            },
        }
        checkpoint = TaskCheckpoint(**old_json)
        assert checkpoint.dag_state is not None
        assert checkpoint.dag_state.active_skills == []
        assert checkpoint.dag_state.skill_activation_count == 0

    def test_old_emergent_checkpoint_without_skill_fields(self):
        old_json = {
            "task_id": "test_old_003",
            "task": "old emergent task",
            "context": "",
            "complexity": "emergent",
            "effort": "medium",
            "state": "running",
            "created_at": 1000.0,
            "updated_at": 1000.0,
            "checkpoint_version": 1,
            "emergent_state": {
                "todo_list": {"items": []},
                "all_results": [],
                "iteration": 0,
                "stagnation_state": {},
            },
        }
        checkpoint = TaskCheckpoint(**old_json)
        assert checkpoint.emergent_state is not None
        assert checkpoint.emergent_state.active_skills == []

    def test_old_goal_driven_checkpoint_without_skill_fields(self):
        old_json = {
            "task_id": "test_old_004",
            "task": "old goal-driven task",
            "context": "",
            "complexity": "goal_driven",
            "effort": "medium",
            "state": "running",
            "created_at": 1000.0,
            "updated_at": 1000.0,
            "checkpoint_version": 1,
            "goal_driven_state": {
                "todo_list": {"items": []},
                "all_results": [],
                "iteration": 0,
                "stagnation_state": {},
                "goal_doc": {"text": "goal"},
                "reanchor_counter": 0,
            },
        }
        checkpoint = TaskCheckpoint(**old_json)
        assert checkpoint.goal_driven_state is not None
        assert checkpoint.goal_driven_state.active_skills == []


# ======================================================================
# New checkpoint with skill fields
# ======================================================================

class TestNewCheckpointWithSkills:
    """Test that new checkpoint with skill fields roundtrips correctly."""

    def test_simple_roundtrip(self):
        state = SimplePathState(
            plan={"steps": []},
            all_results=[],
            attempt=0,
            current_step_index=1,
            active_skills=["hello-world"],
            skill_activation_count=1,
        )
        dumped = state.model_dump(mode="json")
        restored = SimplePathState(**dumped)
        assert restored.active_skills == ["hello-world"]
        assert restored.skill_activation_count == 1

    def test_full_checkpoint_roundtrip(self):
        checkpoint = TaskCheckpoint(
            task_id="test_skill_ckpt",
            task="test task",
            context="",
            complexity="simple",
            effort="medium",
            state=TaskRunState.RUNNING,
            created_at=1000.0,
            updated_at=1000.0,
            simple_state=SimplePathState(
                plan={"steps": []},
                all_results=[],
                attempt=0,
                current_step_index=2,
                active_skills=["hello-world", "data-analysis"],
                skill_activation_count=2,
            ),
        )
        json_str = checkpoint.model_dump_json()
        restored = TaskCheckpoint.model_validate_json(json_str)
        assert restored.simple_state.active_skills == ["hello-world", "data-analysis"]
        assert restored.simple_state.skill_activation_count == 2
