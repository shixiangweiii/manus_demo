"""
Tests for the WS protocol (webui/ws.py) via starlette TestClient.
经 starlette TestClient 测试 WS 协议（webui/ws.py）。

覆盖：hello/state 握手、user_message → run_started → 事件 → run_finished、
hitl_response 解决挂起提问、last_seq 增量重放、坏 JSON → error。
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from webui.app import create_app
from webui.events import EventBridge


class FakeOrchestrator:
    """带可选 HITL 提问的 orchestrator 替身。
    Orchestrator stand-in with an optional HITL prompt."""

    def __init__(self, bridge: EventBridge, ask_user: bool = False):
        self._bridge = bridge
        self._ask_user = ask_user

    async def run(self, task: str) -> str:
        self._bridge.on_event("task_start", {"task": task})
        if self._ask_user:
            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            self._bridge.on_event("ask_user_prompt", {
                "question": "口味偏好？", "prompt_id": "p-test", "response_future": future,
            })
            answer = await asyncio.wait_for(future, timeout=10)
            self._bridge.on_event("ask_user_response", {"response": answer})
            return f"用户说:{answer}"
        self._bridge.on_event("task_complete", {"answer": f"答案:{task}"})
        return f"答案:{task}"

    async def resume(self, task_id: str) -> str:
        raise ValueError(f"Task {task_id} 不存在")


@pytest.fixture
def client_factory():
    """构建注入 fake 工厂的 TestClient。 Build a TestClient with a fake factory."""

    def make(ask_user: bool = False) -> TestClient:
        app = create_app()

        async def factory(bridge: EventBridge):
            return FakeOrchestrator(bridge, ask_user=ask_user), []

        app.state.session_manager._orchestrator_factory = factory
        return TestClient(app)

    return make


def _recv_until(ws, msg_type: str, limit: int = 50) -> dict:
    """收消息直到指定类型 / receive until the given message type."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"{msg_type} not received within {limit} messages")


# ---------------------------------------------------------------------
# 握手 / handshake
# ---------------------------------------------------------------------

def test_hello_returns_state_snapshot(client_factory):
    with client_factory() as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "last_seq": 0})
            state = _recv_until(ws, "state")
            assert state["session"] is None
            assert state["running"] is False
            assert state["pending_prompt"] is None


def test_ping_pong(client_factory):
    with client_factory() as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"


def test_malformed_json_yields_error(client_factory):
    with client_factory() as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text("{not json")
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "bad_message"


# ---------------------------------------------------------------------
# 运行流 / run flow
# ---------------------------------------------------------------------

def test_user_message_without_session_errors(client_factory):
    with client_factory() as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user_message", "text": "hi"})
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "no_session"


def test_full_run_flow(client_factory):
    with client_factory() as client:
        resp = client.post("/api/webui/session", json={"overrides": {"PLAN_MODE": "simple"}})
        assert resp.status_code == 200
        session_id = resp.json()["session"]["session_id"]

        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "last_seq": 0})
            _recv_until(ws, "state")
            ws.send_json({"type": "user_message", "session_id": session_id, "text": "算 2+3"})

            started = _recv_until(ws, "run_started")
            assert started["task"] == "算 2+3"
            assert started["overrides"] == {"PLAN_MODE": "simple"}

            finished = _recv_until(ws, "run_finished")
            assert finished["status"] == "completed"
            assert finished["answer"] == "答案:算 2+3"

        # 运行结束后允许换会话 / session change allowed after the run
        resp = client.post("/api/webui/session", json={"overrides": {}})
        assert resp.status_code == 200


def test_invalid_override_rejected_422(client_factory):
    with client_factory() as client:
        resp = client.post("/api/webui/session", json={"overrides": {"PLAN_MODE": "banana"}})
        assert resp.status_code == 422
        assert "PLAN_MODE" in resp.json()["errors"]


def test_resume_missing_checkpoint_reports_failed_run(client_factory):
    with client_factory() as client:
        client.post("/api/webui/session", json={"overrides": {}})
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "resume_task", "task_id": "ghost"})
            finished = _recv_until(ws, "run_finished")
            assert finished["status"] == "failed"
            assert "不存在" in finished["error"]


# ---------------------------------------------------------------------
# HITL over WS
# ---------------------------------------------------------------------

def test_hitl_prompt_and_response_over_ws(client_factory):
    with client_factory(ask_user=True) as client:
        client.post("/api/webui/session", json={"overrides": {}})
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user_message", "text": "订餐厅"})

            # 等待 ask_user_prompt 事件（data 已净化，无 Future）
            prompt = None
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "agent_event" and msg["event"] == "ask_user_prompt":
                    prompt = msg
                    break
            assert prompt is not None
            assert prompt["data"]["prompt_id"] == "p-test"
            assert "timeout_seconds" in prompt["data"]
            assert "response_future" not in prompt["data"]

            ws.send_json({"type": "hitl_response", "prompt_id": "p-test", "text": "川菜"})
            finished = _recv_until(ws, "run_finished")
            assert finished["answer"] == "用户说:川菜"


def test_hitl_response_to_unknown_prompt_errors(client_factory):
    with client_factory() as client:
        client.post("/api/webui/session", json={"overrides": {}})
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hitl_response", "prompt_id": "ghost", "text": "x"})
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "bad_message"


# ---------------------------------------------------------------------
# 重放 / replay
# ---------------------------------------------------------------------

def test_reconnect_replays_only_newer_messages(client_factory):
    with client_factory() as client:
        client.post("/api/webui/session", json={"overrides": {}})

        # 第一条连接跑完一个任务 / first connection completes a run
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "user_message", "text": "任务一"})
            first_finished = _recv_until(ws, "run_finished")
            mid_seq = first_finished["seq"]
            ws.send_json({"type": "user_message", "text": "任务二"})
            _recv_until(ws, "run_finished")

        # 以 mid_seq 重连 → 只重放任务二的消息 / reconnect at mid_seq
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "last_seq": mid_seq})
            replayed = []
            while True:
                msg = ws.receive_json()
                if msg["type"] == "state":
                    break
                replayed.append(msg)
            assert replayed, "应重放任务二的消息"
            assert all(m["seq"] > mid_seq for m in replayed)
            tasks = [m["task"] for m in replayed if m["type"] == "run_started"]
            assert tasks == ["任务二"]
