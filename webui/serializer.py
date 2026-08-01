"""
Event payload → JSON-safe serialization for the WS event stream.
事件 payload → JSON 安全序列化（供 WS 事件流）。

Zero FastAPI imports — pure functions, unit-testable.
零 FastAPI 依赖 —— 纯函数，可独立单测。

Rules / 规则:
- pydantic BaseModel → model_dump(mode="json")（失败兜底 repr）
- Enum → .value；datetime → isoformat
- dict 递归，丢弃 Future/callable/coroutine/module 值（防御性剥离
  response_future 等不可序列化对象）
- list/tuple/set 递归，上限 200 元素
- str 截断至 FIELD_MAX 字符（工具结果上游已有 2000 截断，但
  step_complete/task_complete/DAG dump 没有）
- 深度上限 12；整包 > 128KB → __oversize__ 摘要
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import json
import types
from enum import Enum
from typing import Any

try:
    from pydantic import BaseModel
except ImportError:  # pragma: no cover
    BaseModel = None  # type: ignore[assignment]

FIELD_MAX = 4000          # 单个字符串字段最大字符数 / max chars per string field
LIST_MAX = 200            # 列表最大元素数 / max list elements
DEPTH_MAX = 12            # 递归深度上限 / max recursion depth
MESSAGE_MAX_BYTES = 128 * 1024  # 整包上限 / whole-message cap
REPR_MAX = 500            # 兜底 repr 截断 / fallback repr cap


def truncate_str(text: str, limit: int = FIELD_MAX) -> tuple[str, bool]:
    """Truncate a string with a visible marker. 截断字符串并附可见标记。"""
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"…[截断 {len(text) - limit} 字符]", True


def _safe_repr(obj: Any, limit: int = REPR_MAX) -> str:
    """repr 本身可能抛异常（恶意/损坏对象），必须兜底。
    repr itself may raise (hostile/broken objects) — guard it."""
    try:
        return repr(obj)[:limit]
    except Exception:
        return f"<unreprable {type(obj).__name__}>"


class _Converter:
    """Stateful recursive converter tracking the truncated flag.
    带截断标记的递归转换器。"""

    def __init__(self) -> None:
        self.truncated = False

    def convert(self, obj: Any, depth: int = 0) -> Any:
        if depth > DEPTH_MAX:
            self.truncated = True
            return _safe_repr(obj, 200)

        if obj is None or isinstance(obj, (bool, int, float)):
            return obj

        if isinstance(obj, str):
            text, cut = truncate_str(obj)
            self.truncated = self.truncated or cut
            return text

        if isinstance(obj, Enum):
            return self.convert(obj.value, depth + 1)

        if isinstance(obj, (_dt.datetime, _dt.date)):
            return obj.isoformat()

        if BaseModel is not None and isinstance(obj, BaseModel):
            try:
                dumped = obj.model_dump(mode="json")
            except Exception:
                return {"__repr__": _safe_repr(obj)}
            return self.convert(dumped, depth + 1)

        if isinstance(obj, dict):
            result: dict[str, Any] = {}
            for key, value in obj.items():
                if self._should_drop(value):
                    continue
                result[str(key)] = self.convert(value, depth + 1)
            return result

        if isinstance(obj, (list, tuple, set, frozenset)):
            items = list(obj)
            if len(items) > LIST_MAX:
                self.truncated = True
                converted = [self.convert(v, depth + 1) for v in items[:LIST_MAX]]
                converted.append(f"…[截断 {len(items) - LIST_MAX} 项]")
                return converted
            return [self.convert(v, depth + 1) for v in items]

        # 兜底：任意对象 → repr / fallback: arbitrary object → repr
        return _safe_repr(obj)

    @staticmethod
    def _should_drop(value: Any) -> bool:
        """Drop non-serializable runtime handles (Future/callable/coroutine/module).
        丢弃不可序列化的运行时句柄（Future/可调用/协程/模块）。"""
        return (
            isinstance(value, asyncio.Future)
            or inspect.iscoroutine(value)
            or isinstance(value, types.ModuleType)
            or callable(value)
        )


def serialize_event(event: str, data: Any) -> tuple[Any, bool]:
    """Serialize one event payload → (json_safe_payload, truncated).
    序列化单个事件 payload → (JSON 安全 payload, 是否截断)。

    Never raises — any failure degrades to a repr summary.
    永不抛异常 —— 任何失败降级为 repr 摘要。
    """
    converter = _Converter()
    try:
        payload = converter.convert(data)
    except Exception as exc:  # 防御性兜底 / defensive fallback
        return {"__error__": f"serialize failed: {exc}", "__repr__": _safe_repr(data)}, True

    # 整包体积守卫 / whole-message size guard
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"__repr__": _safe_repr(payload)}, True
    if len(encoded.encode("utf-8", errors="ignore")) > MESSAGE_MAX_BYTES:
        return {"__oversize__": True, "summary": encoded[:2000]}, True

    return payload, converter.truncated


# =====================================================================
# 事件目录：统一运行时及外围适配层发布的事件。
# 仅供前端渲染器测试与文档参考；未知事件照样透传（前端 raw 兜底卡）。
# Event catalog for renderer documentation; unknown events
# still pass through (frontend raw fallback card).
# =====================================================================

KNOWN_EVENTS: frozenset[str] = frozenset({
    # lifecycle / 生命周期
    "task_started", "engine_started", "engine_completed", "task_completed", "task_failed",
    "action_started", "action_completed", "action_failed", "tool_started", "tool_completed",
    "task_start", "phase", "task_complete", "token_usage_summary",
    # plan / 计划
    "plan", "plan_created", "plan_adaptation", "step_start", "step_complete", "step_failed",
    "step_skipped", "reflection",
    # DAG
    "dag_created", "superstep", "node_running", "node_completed", "node_failed",
    "node_rollback", "node_transition", "condition_evaluated", "execution_error",
    # TODO planning（控制台未渲染 / console-dropped）
    "todo_list_initialized", "todo_start", "todo_complete", "todo_blocked",
    "todo_failed", "todo_list_update",
    # goal-driven（控制台未渲染 / console-dropped）
    "goal_anchor", "goal_reflection", "goal_drift_alert", "goal_reanchor",
    "stagnation_detected",
    # memory / 记忆
    "memory", "knowledge", "memory_stored", "memory_search_start",
    "memory_search_result", "memory_store", "memory_revoke", "memory_consolidate",
    # evolution / 自演化
    "experience_learned", "failure_lesson_stored", "avoidance_hints_injected",
    "preference_hints_injected", "preference_learned",
    # workflow
    "workflow_start", "workflow_step_start", "workflow_step_complete",
    "workflow_step_failed", "workflow_complete", "workflow_failed",
    # handoff / remote / A2A
    "handoff_start", "handoff_complete", "handoff_failed",
    "remote_subagent_start", "a2a_card_fetched", "remote_subagent_complete",
    "remote_subagent_failed",
    # guardrail / 护栏
    "guardrail_blocked", "guardrail_injection_neutralized",
    "guardrail_output_redacted", "guardrail_write_confirm", "guardrail_violation",
    # skills / 技能
    "skills_discovered", "skill_activated", "skill_activation_failed",
    "skill_content_guarded", "skill_allowed_tools_blocked", "skill_auto_created",
    "skill_optimization_applied", "skill_optimization_report",
    # subagent / 子智能体
    "subagent_start", "subagent_complete", "subagent_failed", "subagent_timed_out",
    "subagent_limit_exceeded", "subagent_iteration",
    # HITL
    "ask_user_prompt", "ask_user_response", "ask_user_timeout", "ask_user_cancelled",
    # checkpoint
    "checkpoint_saved",
    # MCP（控制台未渲染 / console-dropped）
    "mcp_tools_discovered", "mcp_tool_executed", "mcp_schema_error",
})
