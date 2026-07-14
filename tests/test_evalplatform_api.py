"""
API tests for the evaluation platform server (starlette TestClient).
评测平台 API 测试（starlette TestClient，离线：heuristic 生成 + 假执行器）。
"""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

import evalplatform.server as server_module
from evalplatform.models import RunStatus
from evalplatform.server import create_app
from evalplatform.store import EvalPlatformStore
from tests.helpers.evalplatform_fixtures import SAMPLE_MD_DOC, make_result


class _FakeExecutor:
    """Instant executor producing one synthetic success per task.
    立即完成的假执行器：每个任务返回一条合成成功结果。"""

    def __init__(self, llm_client=None, tools=None):
        pass

    async def execute(self, evalset, run, on_progress=None):
        from evaluation.metrics import PlanMode, aggregate_results
        from evalplatform.executor import parse_modes

        run.status = RunStatus.RUNNING
        run.started_at = time.time()
        metrics = {}
        for mode in parse_modes(run.modes):
            results = [make_result(task_id=t.task_id, success=True, mode=mode) for t in evalset.tasks]
            aggregated = aggregate_results(results)
            aggregated.planning_mode = mode
            metrics[mode.value] = aggregated.model_dump(mode="json")
        run.metrics_by_mode = metrics
        run.status = RunStatus.COMPLETED
        run.finished_at = time.time()
        if on_progress:
            on_progress(run)
        return run


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(server_module, "EvalSetExecutor", _FakeExecutor)
    app = create_app(store=EvalPlatformStore(base_dir=str(tmp_path / "platform")))
    return TestClient(app)


def _poll(client: TestClient, path: str, key: str, want: str, timeout: float = 5.0) -> dict:
    """Poll a detail endpoint until its status reaches `want`. 轮询状态直至到位。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(path).json()
        record = data[list(data.keys())[0]]
        if record[key] == want:
            return record
        time.sleep(0.05)
    raise AssertionError(f"{path} 未在 {timeout}s 内达到 {key}={want}: {record}")


def _upload_doc(client: TestClient) -> str:
    resp = client.post("/api/evalplatform/documents", json={
        "filename": "guide.md", "content": SAMPLE_MD_DOC,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["document"]["doc_id"]


def _create_ready_evalset(client: TestClient) -> str:
    doc_id = _upload_doc(client)
    resp = client.post("/api/evalplatform/evalsets", json={
        "doc_id": doc_id, "num_tasks": 3, "use_llm": False,
    })
    assert resp.status_code == 200, resp.text
    evalset_id = resp.json()["evalset"]["evalset_id"]
    _poll(client, f"/api/evalplatform/evalsets/{evalset_id}", "status", "ready")
    return evalset_id


def _run_to_completion(client: TestClient, evalset_id: str, modes=None) -> str:
    resp = client.post("/api/evalplatform/runs", json={
        "evalset_id": evalset_id, "modes": modes or ["simple"],
    })
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run"]["run_id"]
    _poll(client, f"/api/evalplatform/runs/{run_id}", "status", "completed")
    return run_id


class TestDocuments:
    def test_upload_and_overview(self, client):
        doc_id = _upload_doc(client)
        overview = client.get("/api/evalplatform/overview").json()
        assert [d["doc_id"] for d in overview["documents"]] == [doc_id]

    def test_upload_missing_fields(self, client):
        resp = client.post("/api/evalplatform/documents", json={"filename": ""})
        assert resp.status_code == 422

    def test_upload_binary_rejected(self, client):
        resp = client.post("/api/evalplatform/documents", json={
            "filename": "x.pdf", "content": "not really pdf",
        })
        assert resp.status_code == 422
        assert "二进制" in resp.json()["message"]

    def test_get_document_404(self, client):
        assert client.get("/api/evalplatform/documents/nope").status_code == 404


class TestEvalsets:
    def test_generate_heuristic(self, client):
        evalset_id = _create_ready_evalset(client)
        detail = client.get(f"/api/evalplatform/evalsets/{evalset_id}").json()["evalset"]
        assert detail["generator"] == "heuristic"
        assert 1 <= len(detail["tasks"]) <= 3
        assert detail["tasks"][0]["task_description"]

    def test_generate_unknown_doc_404(self, client):
        resp = client.post("/api/evalplatform/evalsets", json={"doc_id": "nope"})
        assert resp.status_code == 404


class TestRuns:
    def test_run_lifecycle_and_report(self, client):
        evalset_id = _create_ready_evalset(client)
        run_id = _run_to_completion(client, evalset_id)

        run = client.get(f"/api/evalplatform/runs/{run_id}").json()["run"]
        assert run["metrics_by_mode"]["simple"]["task_success_rate"] == 1.0

        # 报告：markdown 端点 + HTML 页面
        md = client.get(f"/api/evalplatform/runs/{run_id}/report.md")
        assert md.status_code == 200 and "# 评测报告" in md.text
        page = client.get(f"/reports/{run_id}")
        assert page.status_code == 200 and "评测报告" in page.text

        # 概览含成功率
        overview = client.get("/api/evalplatform/overview").json()
        assert overview["runs"][0]["success_rate"] == 1.0

    def test_run_not_ready_evalset_409(self, client):
        doc_id = _upload_doc(client)
        # 手工造一个 generating 状态的评测集
        from evalplatform.models import GeneratedEvalSet
        store: EvalPlatformStore = client.app.state.store
        evalset = GeneratedEvalSet(doc_id=doc_id)
        store.save_evalset(evalset)
        resp = client.post("/api/evalplatform/runs", json={"evalset_id": evalset.evalset_id})
        assert resp.status_code == 409

    def test_run_invalid_modes_422(self, client):
        evalset_id = _create_ready_evalset(client)
        resp = client.post("/api/evalplatform/runs", json={
            "evalset_id": evalset_id, "modes": ["auto"],
        })
        assert resp.status_code == 422

    def test_report_missing_404(self, client):
        assert client.get("/api/evalplatform/runs/nope/report.md").status_code == 404

    def test_repeat_clamped_to_max(self, client):
        # review V6: server clamps repeat to [1,5]
        evalset_id = _create_ready_evalset(client)
        resp = client.post("/api/evalplatform/runs", json={
            "evalset_id": evalset_id, "modes": ["simple"], "repeat": 500,
        })
        assert resp.status_code == 200
        run_id = resp.json()["run"]["run_id"]
        _poll(client, f"/api/evalplatform/runs/{run_id}", "status", "completed")
        run = client.get(f"/api/evalplatform/runs/{run_id}").json()["run"]
        assert run["repeat"] == 5


class TestAnalyses:
    def test_no_completed_runs_422(self, client):
        resp = client.post("/api/evalplatform/analyses", json={})
        assert resp.status_code == 422

    def test_analysis_lifecycle(self, client):
        evalset_id = _create_ready_evalset(client)
        run_id = _run_to_completion(client, evalset_id)

        resp = client.post("/api/evalplatform/analyses", json={"run_ids": [run_id]})
        assert resp.status_code == 200, resp.text
        analysis_id = resp.json()["analysis"]["analysis_id"]

        detail = client.get(f"/api/evalplatform/analyses/{analysis_id}").json()["analysis"]
        assert detail["run_ids"] == [run_id]
        assert detail["suggestions"]
        page = client.get(f"/analyses/{analysis_id}")
        assert page.status_code == 200 and "聚合分析" in page.text

    def test_analysis_unknown_run_404(self, client):
        resp = client.post("/api/evalplatform/analyses", json={"run_ids": ["nope"]})
        assert resp.status_code == 404

    def test_analysis_rejects_incomplete_run(self, client):
        # review V5: explicit run_ids must be COMPLETED, like the default branch
        from evalplatform.models import EvalRunRecord, RunStatus
        store: EvalPlatformStore = client.app.state.store
        running = EvalRunRecord(evalset_id="es", modes=["simple"], status=RunStatus.RUNNING)
        store.save_run(running)
        resp = client.post("/api/evalplatform/analyses", json={"run_ids": [running.run_id]})
        assert resp.status_code == 422
        assert "未完成" in resp.json()["message"]

    def test_analysis_defaults_to_all_completed(self, client):
        evalset_id = _create_ready_evalset(client)
        _run_to_completion(client, evalset_id)
        resp = client.post("/api/evalplatform/analyses", json={})
        assert resp.status_code == 200
        assert len(resp.json()["analysis"]["run_ids"]) == 1


class TestFrontend:
    def test_index_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "评测平台" in resp.text
