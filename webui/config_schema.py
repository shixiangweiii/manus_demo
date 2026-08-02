"""Small WebUI schema for common runtime choices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.models import Effort, EngineKind
from core.settings import AppSettings, get_settings, validate_settings


class ConfigValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__(f"configuration validation failed: {errors}")


@dataclass(frozen=True)
class Item:
    name: str
    type: str
    label: str
    description: str
    options: tuple[str, ...] = ()


GROUPS = (
    (
        "runtime",
        "运行选择",
        (
            Item("engine", "enum", "编排引擎", "选择 sequential、dag 或 agent_loop", tuple(k.value for k in EngineKind)),
            Item("effort", "enum", "运行力度", "控制计划和循环预算", tuple(k.value for k in Effort)),
        ),
    ),
    (
        "capabilities",
        "外围能力",
        (
            Item("subagent", "bool", "Subagent", "注册隔离子任务工具"),
            Item("hitl", "bool", "人机确认", "允许 Agent 在 WebUI 中提问"),
            Item("agentic_memory", "bool", "结构化记忆", "启用本地结构化记忆"),
            Item("memory_tools", "bool", "记忆工具", "允许 Agent 主动读写记忆"),
            Item("knowledge", "bool", "知识库", "检索本地 knowledge/docs 内容"),
            Item("skills", "bool", "技能", "启用技能发现与激活"),
            Item("guardrails", "bool", "Guardrails", "启用工具和输出安全钩子"),
        ),
    ),
)

_INDEX = {item.name: item for _, _, items in GROUPS for item in items}


def get_schema() -> dict[str, Any]:
    return {
        "groups": [
            {
                "id": group_id,
                "title": title,
                "items": [
                    {
                        "name": item.name,
                        "type": item.type,
                        "label": item.label,
                        "description": item.description,
                        "options": list(item.options),
                        "core": True,
                        "sensitive": False,
                        "restart_required": False,
                    }
                    for item in items
                ],
            }
            for group_id, title, items in GROUPS
        ]
    }


def get_values(settings: AppSettings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    return {
        "engine": settings.runtime.engine.value,
        "effort": settings.runtime.effort.value,
        "subagent": settings.capabilities.subagent,
        "hitl": settings.capabilities.hitl,
        "agentic_memory": settings.capabilities.agentic_memory,
        "memory_tools": settings.capabilities.memory_tools,
        "knowledge": settings.capabilities.knowledge,
        "skills": settings.capabilities.skills,
        "guardrails": settings.capabilities.guardrails,
    }


def validate(overrides: dict[str, object]) -> dict[str, object]:
    errors: dict[str, str] = {}
    result: dict[str, object] = {}
    for name, value in overrides.items():
        item = _INDEX.get(name)
        if item is None:
            errors[name] = "未知配置项"
            continue
        if item.type == "bool":
            if isinstance(value, bool):
                result[name] = value
            elif isinstance(value, str) and value.lower() in {"true", "false"}:
                result[name] = value.lower() == "true"
            else:
                errors[name] = "需要布尔值"
        else:
            candidate = str(value).lower()
            if candidate not in item.options:
                errors[name] = f"可选值：{', '.join(item.options)}"
            else:
                result[name] = candidate
    if errors:
        raise ConfigValidationError(errors)
    return result


def settings_for_session(overrides: dict[str, object]) -> tuple[AppSettings, dict[str, object]]:
    settings = get_settings().clone()
    run_overrides = {
        name: value
        for name, value in overrides.items()
        if name in {"engine", "effort"}
    }
    for name in (
        "subagent", "hitl", "agentic_memory", "memory_tools", "knowledge", "skills", "guardrails"
    ):
        if name in overrides:
            setattr(settings.capabilities, name, bool(overrides[name]))
    validate_settings(settings)
    return settings, run_overrides
