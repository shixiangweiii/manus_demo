"""
Tests for the eval set executor (fake EvaluationRunner, no LLM).
评测集执行器测试（假 EvaluationRunner，不调 LLM）。
"""

from __future__ import annotations

import os

import pytest

import config
from evaluation.metrics import PlanMode
from evalplatform.executor import EvalSetExecutor, clamp_repeat, parse_modes
from evalplatform.generator import generate_heuristic_tasks
from evalplatform.models import DocumentRecord, EvalRunRecord, GeneratedEvalSet, RunStatus
from tests.helpers.evalplatform_fixtures import SAMPLE_MD_DOC, make_result


class TestClampRepeat:
    def test_clamps(self):
        assert clamp_repeat(0) == 1
        assert clamp_repeat(500) == 5
        assert clamp_repeat(3) == 3
        assert clamp_repeat("bad") == 1
        assert clamp_repeat(None) == 1


class TestParseModes:
    def test_default_simple(self):
        assert parse_modes(None) == [PlanMode.SIMPLE]
        assert parse_modes([]) == [PlanMode.SIMPLE]

    def test_dedup_and_case(self):
        assert parse_modes(["Simple", "simple", "complex"]) == [PlanMode.SIMPLE, PlanMode.COMPLEX]

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="未知的规划模式"):
            parse_modes(["auto"])


def _evalset(num_tasks: int = 2) -> GeneratedEvalSet:
    doc = DocumentRecord(doc_id="doc_e", filename="g.md", content=SAMPLE_MD_DOC,
                         char_count=len(SAMPLE_MD_DOC))
    return GeneratedEvalSet(
        evalset_id="es_e", name="es", doc_id="doc_e",
        tasks=generate_heuristic_tasks(doc, num_tasks),
    )


@pytest.fixture
def executor(monkeypatch) -> EvalSetExecutor:
    """Executor whose runner returns synthetic results. 返回合成结果的执行器。"""
    async def fake_evaluate_task(self, task, mode, _shared_memory_dir=None):
        return make_result(task_id=task.task_id, success=True, mode=mode)

    from evaluation.runner import EvaluationRunner
    monkeypatch.setattr(EvaluationRunner, "evaluate_task", fake_evaluate_task)
    ex = EvalSetExecutor()
    ex._runner.llm_client = type("L", (), {"model": "fake-model"})()
    return ex


class TestExecutor:
    async def test_single_mode_run(self, executor):
        evalset = _evalset(2)
        run = EvalRunRecord(evalset_id="es_e", modes=["simple"])
        await executor.execute(evalset, run)
        assert run.status == RunStatus.COMPLETED
        assert run.llm_model == "fake-model"
        metrics = run.metrics_by_mode["simple"]
        assert metrics["total_tasks"] == len(evalset.tasks)
        assert metrics["task_success_rate"] == 1.0
        assert run.progress.completed_units == run.progress.total_units
        assert run.finished_at >= run.started_at

    async def test_multi_mode_and_progress_callback(self, executor):
        evalset = _evalset(2)
        run = EvalRunRecord(evalset_id="es_e", modes=["simple", "complex"])
        snapshots: list[int] = []
        await executor.execute(evalset, run, on_progress=lambda r: snapshots.append(r.progress.completed_units))
        assert set(run.metrics_by_mode) == {"simple", "complex"}
        assert run.progress.total_units == len(evalset.tasks) * 2
        assert snapshots and snapshots[-1] == run.progress.total_units

    async def test_repeat_computes_pass_at_k(self, executor):
        evalset = _evalset(1)
        run = EvalRunRecord(evalset_id="es_e", modes=["simple"], repeat=3)
        await executor.execute(evalset, run)
        result = run.metrics_by_mode["simple"]["results"][0]
        assert result["trial_count"] == 3
        assert result["pass_at_k"] == 1.0
        assert run.progress.total_units == 3

    async def test_empty_evalset_fails(self, executor):
        evalset = _evalset(1)
        evalset.tasks = []
        run = EvalRunRecord(evalset_id="es_e", modes=["simple"])
        await executor.execute(evalset, run)
        assert run.status == RunStatus.FAILED
        assert "没有任务" in run.error

    async def test_infra_error_marks_failed(self, monkeypatch):
        async def broken_evaluate_task(self, task, mode, _shared_memory_dir=None):
            raise RuntimeError("infra exploded")

        from evaluation.runner import EvaluationRunner
        monkeypatch.setattr(EvaluationRunner, "evaluate_task", broken_evaluate_task)
        executor = EvalSetExecutor()
        run = EvalRunRecord(evalset_id="es_e", modes=["simple"])
        await executor.execute(_evalset(1), run)
        assert run.status == RunStatus.FAILED
        assert "infra exploded" in run.error
        assert run.progress.current_task_id == ""


class TestSandboxIsolation:
    async def test_each_run_uses_fresh_sandbox_and_restores(self, monkeypatch):
        # review V1: the run executes in a temp sandbox (not the persistent
        # ~/.manus_demo/sandbox), and config.SANDBOX_DIR is restored afterward
        seen: list[str] = []

        async def capturing_evaluate_task(self, task, mode, _shared_memory_dir=None):
            seen.append(config.SANDBOX_DIR)
            return make_result(task_id=task.task_id, success=True, mode=mode)

        from evaluation.runner import EvaluationRunner
        monkeypatch.setattr(EvaluationRunner, "evaluate_task", capturing_evaluate_task)

        original = config.SANDBOX_DIR
        executor = EvalSetExecutor()
        run = EvalRunRecord(evalset_id="es_e", modes=["simple"])
        await executor.execute(_evalset(2), run)

        # every task ran under the SAME per-run temp sandbox, not the global one
        assert seen and all(s != original for s in seen)
        assert len(set(seen)) == 1
        run_sandbox = seen[0]
        assert "manus_evalplatform_sandbox_" in run_sandbox
        # restored and cleaned up after the run
        assert config.SANDBOX_DIR == original
        assert not os.path.exists(run_sandbox)

    async def test_two_runs_get_distinct_sandboxes(self, monkeypatch):
        seen: list[str] = []

        async def capturing_evaluate_task(self, task, mode, _shared_memory_dir=None):
            seen.append(config.SANDBOX_DIR)
            return make_result(task_id=task.task_id, success=True, mode=mode)

        from evaluation.runner import EvaluationRunner
        monkeypatch.setattr(EvaluationRunner, "evaluate_task", capturing_evaluate_task)

        executor = EvalSetExecutor()
        await executor.execute(_evalset(1), EvalRunRecord(evalset_id="es_e", modes=["simple"]))
        await executor.execute(_evalset(1), EvalRunRecord(evalset_id="es_e", modes=["simple"]))
        assert len(set(seen)) == 2  # 两次运行拿到不同沙箱，杜绝跨运行文件残留
