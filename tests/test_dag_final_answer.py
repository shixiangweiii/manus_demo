"""
Regression tests for DAG final-answer synthesis.
DAG 路径最终答案合成回归测试。
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from dag.graph import TaskDAG
from schema import NodeStatus, NodeType, Reflection, StepResult, TaskNode


def _make_dag(
    task: str,
    *,
    action_status: NodeStatus = NodeStatus.COMPLETED,
    action_result: str = "59",
) -> TaskDAG:
    dag = TaskDAG(
        task=task,
        nodes={
            "goal_1": TaskNode(
                id="goal_1",
                node_type=NodeType.GOAL,
                description="Goal",
            ),
            "act_1": TaskNode(
                id="act_1",
                node_type=NodeType.ACTION,
                description="Compute final number",
                status=action_status,
                result=action_result,
            ),
        },
        edges=[],
    )
    dag.state.node_results["act_1"] = action_result
    return dag


def _make_orchestrator(*, passed: bool, compiled_answer: str):
    from agents.orchestrator import OrchestratorAgent

    orch = OrchestratorAgent.__new__(OrchestratorAgent)
    orch.executor_agent = object()
    orch.planner = types.SimpleNamespace(replan_subtree=AsyncMock())
    orch.reflector = types.SimpleNamespace(
        reflect_dag=AsyncMock(
            return_value=Reflection(
                passed=passed,
                score=1.0 if passed else 0.4,
                feedback="OK" if passed else "Needs improvement",
                suggestions=[],
            )
        )
    )
    orch.max_replan = 0
    orch._last_reflection = None
    orch._emit = MagicMock()
    orch._save_dag_checkpoint = MagicMock()
    orch._record_outcome = MagicMock()
    orch._compile_answer = AsyncMock(return_value=compiled_answer)
    return orch


def _patch_dag_executor(monkeypatch, raw_output: str):
    import agents.orchestrator as orchestrator_module

    class FakeDAGExecutor:
        def __init__(self, *args, **kwargs):
            pass

        async def execute(self, dag):
            return raw_output

    monkeypatch.setattr(orchestrator_module, "DAGExecutor", FakeDAGExecutor)


@pytest.mark.asyncio
async def test_dag_success_returns_synthesized_answer_not_action_transcript(monkeypatch):
    from agents.orchestrator import OrchestratorAgent

    raw_output = "[act_1] Compute final number:\n执行内容: 6*7 + 8+9\n计算结果: 59"
    _patch_dag_executor(monkeypatch, raw_output)
    orch = _make_orchestrator(passed=True, compiled_answer="59")
    dag = _make_dag(
        "先分别计算 6*7 和 8+9，再计算两个结果之和。最终回答必须只包含数字 59，不要解释。"
    )

    answer = await OrchestratorAgent._execute_dag_and_reflect(orch, dag)

    assert answer == "59"
    assert "[act_" not in answer
    assert "执行内容" not in answer
    orch._compile_answer.assert_awaited_once()
    task_arg, results_arg = orch._compile_answer.await_args.args
    assert task_arg == dag.state.task
    assert results_arg[0].step_id == "act_1"
    assert results_arg[0].success is True
    assert results_arg[0].output == "59"


@pytest.mark.asyncio
async def test_dag_best_effort_still_synthesizes_answer(monkeypatch):
    from agents.orchestrator import OrchestratorAgent

    raw_output = "[act_1] Compute final number:\n计算结果: 59"
    _patch_dag_executor(monkeypatch, raw_output)
    orch = _make_orchestrator(passed=False, compiled_answer="59")
    dag = _make_dag("complex task with completed action")

    answer = await OrchestratorAgent._execute_dag_and_reflect(orch, dag)

    assert answer == "59"
    assert "[act_" not in answer
    orch._record_outcome.assert_called_once()
    orch._compile_answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_compile_dag_answer_uses_failure_answer_when_no_successful_actions():
    from agents.orchestrator import OrchestratorAgent

    orch = OrchestratorAgent.__new__(OrchestratorAgent)
    orch._synthesize_failure_answer = AsyncMock(return_value="无法完成：没有成功的 DAG ACTION 结果。")
    failed_results = [
        StepResult(step_id="act_1", success=False, output="Error: failed"),
    ]

    answer = await OrchestratorAgent._compile_dag_answer(orch, "中文任务", failed_results)

    assert "无法完成" in answer
    orch._synthesize_failure_answer.assert_awaited_once_with("中文任务")
