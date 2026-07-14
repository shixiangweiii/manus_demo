"""
Tests for webui/session.py (SessionManager with a fake orchestrator factory).
webui/session.py 的单元测试（注入 fake orchestrator 工厂）。

覆盖：会话 apply/restore 生命周期、单飞锁、HITL prompt 注册/解决、
run_started/run_finished 消息流、resume ValueError 降级、trace_id 捕获。
"""

from __future__ import annotations

import asyncio

import pytest

import config
from webui.events import EventBridge
from webui.session import BusyError, NoSessionError, SessionManager


class FakeOrchestrator:
    """最小 orchestrator 替身：经 bridge 发事件，可被 gate 阻塞。
    Minimal orchestrator stand-in: emits via bridge, blockable by a gate."""

    def __init__(self, bridge: EventBridge):
        self._bridge = bridge
        self.gate: asyncio.Event | None = None
        self.resume_error: str | None = None

    async def run(self, task: str) -> str:
        self._bridge.on_event("task_start", {"task": task})
        if self.gate is not None:
            await self.gate.wait()
        self._bridge.on_event("task_complete", {"answer": f"答案:{task}"})
        return f"答案:{task}"

    async def resume(self, task_id: str) -> str:
        if self.resume_error:
            raise ValueError(self.resume_error)
        return f"恢复:{task_id}"


def _make_manager() -> tuple[SessionManager, EventBridge, list[FakeOrchestrator]]:
    bridge = EventBridge()
    created: list[FakeOrchestrator] = []

    async def factory(b: EventBridge):
        orch = FakeOrchestrator(b)
        created.append(orch)
        return orch, []

    return SessionManager(bridge, orchestrator_factory=factory), bridge, created


async def _wait_run_done(mgr: SessionManager) -> None:
    while mgr.is_running:
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------
# 会话生命周期 / session lifecycle
# ---------------------------------------------------------------------

async def test_create_session_applies_and_close_restores():
    mgr, _, _ = _make_manager()
    original = config.PLAN_MODE
    await mgr.create_session({"PLAN_MODE": "simple"})
    assert config.PLAN_MODE == "simple"
    await mgr.close_session()
    assert config.PLAN_MODE == original
    assert mgr.session is None


async def test_replace_session_restores_previous_overrides_first():
    mgr, _, _ = _make_manager()
    original = config.MAX_REACT_ITERATIONS
    await mgr.create_session({"MAX_REACT_ITERATIONS": 5})
    assert config.MAX_REACT_ITERATIONS == 5
    # 新会话覆盖另一项 → 旧覆盖必须先还原
    await mgr.create_session({"PLAN_MODE": "simple"})
    assert config.MAX_REACT_ITERATIONS == original
    assert config.PLAN_MODE == "simple"
    await mgr.close_session()


async def test_factory_failure_restores_config():
    bridge = EventBridge()

    async def broken_factory(_b):
        raise RuntimeError("factory boom")

    mgr = SessionManager(bridge, orchestrator_factory=broken_factory)
    original = config.PLAN_MODE
    with pytest.raises(RuntimeError):
        await mgr.create_session({"PLAN_MODE": "simple"})
    assert config.PLAN_MODE == original
    assert mgr.session is None


# ---------------------------------------------------------------------
# 运行 / runs
# ---------------------------------------------------------------------

async def test_run_emits_started_and_finished():
    mgr, bridge, _ = _make_manager()
    await mgr.create_session({})
    run_id = mgr.start_run("算 1+1")
    await _wait_run_done(mgr)

    messages = bridge.replay(0)
    types = [m["type"] for m in messages]
    assert types[0] == "run_started"
    assert types[-1] == "run_finished"
    started = messages[0]
    finished = messages[-1]
    assert started["run_id"] == run_id and started["kind"] == "run"
    assert finished["status"] == "completed"
    assert finished["answer"] == "答案:算 1+1"
    # agent_event 带 run_id / agent events carry the run_id
    agent_events = [m for m in messages if m["type"] == "agent_event"]
    assert agent_events and all(m["run_id"] == run_id for m in agent_events)
    await mgr.close_session()


async def test_single_flight_second_run_rejected():
    mgr, _, created = _make_manager()
    await mgr.create_session({})
    created[0].gate = asyncio.Event()  # 阻塞首个 run / block the first run
    mgr.start_run("长任务")
    await asyncio.sleep(0.01)
    with pytest.raises(BusyError):
        mgr.start_run("第二个")
    with pytest.raises(BusyError):
        await mgr.create_session({})  # 运行中也不能换会话
    created[0].gate.set()
    await _wait_run_done(mgr)
    await mgr.close_session()


async def test_run_without_session_raises():
    mgr, _, _ = _make_manager()
    with pytest.raises(NoSessionError):
        mgr.start_run("任务")


async def test_resume_value_error_becomes_failed_run():
    mgr, bridge, created = _make_manager()
    await mgr.create_session({})
    created[0].resume_error = "Task abc 不存在"
    mgr.start_resume("abc")
    await _wait_run_done(mgr)
    finished = bridge.replay(0)[-1]
    assert finished["type"] == "run_finished"
    assert finished["status"] == "failed"
    assert "不存在" in finished["error"]
    await mgr.close_session()


# ---------------------------------------------------------------------
# HITL
# ---------------------------------------------------------------------

async def test_prompt_register_and_resolve_once():
    mgr, bridge, _ = _make_manager()
    await mgr.create_session({})
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    # 模拟 orchestrator 发出 ask_user_prompt（Future 在序列化前注册）
    bridge.on_event("ask_user_prompt", {
        "question": "你喜欢什么颜色？", "prompt_id": "p1", "response_future": future,
    })
    pending = mgr.pending_prompt()
    assert pending and pending["prompt_id"] == "p1"

    assert mgr.resolve_prompt("p1", "蓝色") is True
    assert future.result() == "蓝色"
    assert mgr.resolve_prompt("p1", "红色") is False  # 只解决一次 / once only
    await asyncio.sleep(0)  # 让 done_callback 清理 / let cleanup run
    assert mgr.pending_prompt() is None
    # 序列化后的事件不含 Future / serialized event has no Future
    prompt_event = [m for m in bridge.replay(0) if m.get("event") == "ask_user_prompt"][0]
    assert "response_future" not in prompt_event["data"]
    assert prompt_event["data"]["question"] == "你喜欢什么颜色？"
    await mgr.close_session()


async def test_unknown_prompt_resolve_returns_false():
    mgr, _, _ = _make_manager()
    await mgr.create_session({})
    assert mgr.resolve_prompt("nope", "x") is False
    await mgr.close_session()


# ---------------------------------------------------------------------
# trace 捕获 / trace capture
# ---------------------------------------------------------------------

class _StubSpanContext:
    trace_id = 0x0AF3_0000_0000_0000_0000_0000_0000_BEEF


class _StubSpan:
    def get_span_context(self):
        return _StubSpanContext()


class _StubTracingBridge:
    _root_span = _StubSpan()


async def test_trace_id_captured_from_stub_bridge(monkeypatch):
    mgr, bridge, created = _make_manager()
    await mgr.create_session({})
    created[0]._tracing_bridge = _StubTracingBridge()  # 注入替身 / inject stub
    monkeypatch.setattr(config, "TRACING_ENABLED", True)
    monkeypatch.setattr(config, "TRACING_BACKEND", "file")

    mgr.start_run("追踪任务")
    await _wait_run_done(mgr)
    finished = bridge.replay(0)[-1]
    assert finished["trace"] is not None
    assert finished["trace"]["trace_id"] == format(_StubSpanContext.trace_id, "032x")
    assert finished["trace"]["url"].startswith("/traces/")
    await mgr.close_session()


async def test_trace_ref_none_when_tracing_disabled(monkeypatch):
    mgr, bridge, created = _make_manager()
    await mgr.create_session({})
    created[0]._tracing_bridge = _StubTracingBridge()
    monkeypatch.setattr(config, "TRACING_ENABLED", False)
    mgr.start_run("无追踪任务")
    await _wait_run_done(mgr)
    finished = bridge.replay(0)[-1]
    assert finished["trace"] is None
    await mgr.close_session()
