"""
Tests for webui/serializer.py.
webui/serializer.py 的单元测试。

覆盖：pydantic 模型往返、Future/callable 剥离、Enum→value、字符串截断、
不可序列化对象兜底、超大包摘要、深度守卫。
"""

from __future__ import annotations

import asyncio
import json

from schema import Plan, Reflection, Step, StepStatus
from webui.serializer import (
    FIELD_MAX,
    KNOWN_EVENTS,
    MESSAGE_MAX_BYTES,
    serialize_event,
    truncate_str,
)


def _json_roundtrip(payload):
    """序列化结果必须可被 json.dumps 完整编码。"""
    return json.loads(json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------------
# pydantic 模型 / pydantic models
# ---------------------------------------------------------------------

def test_plan_model_serializes():
    plan = Plan(task="测试任务", steps=[
        Step(id=1, description="第一步", dependencies=[]),
        Step(id=2, description="第二步", dependencies=[1]),
    ])
    payload, truncated = serialize_event("plan", plan)
    payload = _json_roundtrip(payload)
    assert truncated is False
    assert payload["task"] == "测试任务"
    assert len(payload["steps"]) == 2
    assert payload["steps"][1]["dependencies"] == [1]


def test_reflection_model_serializes():
    reflection = Reflection(passed=True, score=0.9, feedback="良好", suggestions=[])
    payload, _ = serialize_event("reflection", reflection)
    payload = _json_roundtrip(payload)
    assert payload["passed"] is True
    assert payload["score"] == 0.9


def test_enum_becomes_value():
    payload, _ = serialize_event("x", {"status": StepStatus.COMPLETED})
    assert payload["status"] == StepStatus.COMPLETED.value


# ---------------------------------------------------------------------
# 不可序列化对象剥离 / non-serializable stripping
# ---------------------------------------------------------------------

def test_future_and_callable_are_dropped():
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        payload, _ = serialize_event("ask_user_prompt", {
            "question": "你的口味偏好？",
            "prompt_id": "ab12cd34",
            "response_future": future,
            "callback": lambda: None,
        })
        assert "response_future" not in payload
        assert "callback" not in payload
        assert payload["question"] == "你的口味偏好？"
        assert payload["prompt_id"] == "ab12cd34"
        _json_roundtrip(payload)
    finally:
        loop.close()


def test_arbitrary_object_falls_back_to_repr():
    class Weird:
        def __repr__(self):
            return "<Weird instance>"

    payload, _ = serialize_event("x", {"obj": Weird()})
    assert payload["obj"] == "<Weird instance>"


def test_serialize_never_raises_on_hostile_repr():
    class Hostile:
        def __repr__(self):
            raise RuntimeError("boom")

    payload, truncated = serialize_event("x", {"obj": Hostile()})
    # 不抛异常即可；降级为错误摘要 / must not raise; degrades to a summary
    assert truncated is True or isinstance(payload, dict)


# ---------------------------------------------------------------------
# 截断 / truncation
# ---------------------------------------------------------------------

def test_long_string_truncated_with_flag():
    text = "呀" * (FIELD_MAX + 500)
    payload, truncated = serialize_event("x", {"content": text})
    assert truncated is True
    assert len(payload["content"]) < len(text)
    assert "截断" in payload["content"]


def test_long_list_truncated():
    payload, truncated = serialize_event("x", {"items": list(range(500))})
    assert truncated is True
    assert len(payload["items"]) == 201  # 200 + 截断标记


def test_oversize_message_summarized():
    # 多字段组合超过整包上限（单字段截断不够时）
    data = {f"k{i}": "x" * FIELD_MAX for i in range(60)}
    payload, truncated = serialize_event("x", data)
    assert truncated is True
    assert payload.get("__oversize__") is True
    assert len(json.dumps(payload)) < MESSAGE_MAX_BYTES


def test_deep_nesting_guarded():
    data: dict = {"v": 1}
    for _ in range(30):
        data = {"nested": data}
    payload, truncated = serialize_event("x", data)
    assert truncated is True
    _json_roundtrip(payload)


# ---------------------------------------------------------------------
# 其他 / misc
# ---------------------------------------------------------------------

def test_truncate_str_helper():
    text, cut = truncate_str("short")
    assert text == "short" and cut is False
    text, cut = truncate_str("a" * 10, limit=5)
    assert cut is True and text.startswith("aaaaa")


def test_known_events_catalog_sane():
    assert "task_start" in KNOWN_EVENTS
    assert "todo_complete" in KNOWN_EVENTS       # 控制台丢弃、web 要渲染
    assert "guardrail_blocked" in KNOWN_EVENTS
    assert len(KNOWN_EVENTS) >= 80
