"""
Tests for webui/config_schema.py.
webui/config_schema.py 的单元测试。

覆盖：schema 与 config.py 一致性、类型强转/拒绝、apply/restore 往返、
敏感项永不出值、env_passthrough 同步。
"""

from __future__ import annotations

import json
import os

import pytest

import config
from webui import config_schema
from webui.config_schema import ConfigValidationError, GROUPS, _ITEM_INDEX


# ---------------------------------------------------------------------
# schema 一致性 / schema consistency
# ---------------------------------------------------------------------

def test_every_item_exists_on_config():
    """每个条目必须是 config.py 的真实属性（防 schema 漂移）。"""
    missing = [name for name in _ITEM_INDEX if not hasattr(config, name)]
    assert missing == [], f"schema 中的条目在 config.py 不存在: {missing}"


def test_no_duplicate_item_names():
    names = [item.name for group in GROUPS for item in group.items]
    assert len(names) == len(set(names))


def test_enum_defaults_are_valid_options():
    """enum 条目的当前 config 值必须在 options 里。"""
    for item in _ITEM_INDEX.values():
        if item.type == "enum" and not item.sensitive:
            value = getattr(config, item.name)
            assert value in item.options, f"{item.name}={value!r} 不在 {item.options}"


def test_type_declarations_match_config_values():
    """声明类型与 config 当前值的 Python 类型一致。"""
    type_map = {"bool": bool, "int": int, "float": float, "str": str, "enum": str}
    for item in _ITEM_INDEX.values():
        value = getattr(config, item.name)
        expected = type_map[item.type]
        if item.type == "float":
            assert isinstance(value, (int, float)) and not isinstance(value, bool), item.name
        else:
            assert isinstance(value, expected), (
                f"{item.name} 声明为 {item.type} 但 config 值是 {type(value).__name__}"
            )
        if item.type == "int":
            assert not isinstance(value, bool), item.name


# ---------------------------------------------------------------------
# 校验 / validate
# ---------------------------------------------------------------------

def test_validate_coerces_types():
    coerced = config_schema.validate({
        "SUBAGENT_ENABLED": "true",
        "MAX_REACT_ITERATIONS": "12",
        "MEMORY_MIN_CONFIDENCE": "0.5",
        "PLAN_MODE": "EMERGENT",
        "LLM_MODEL": "deepseek-chat",
    })
    assert coerced["SUBAGENT_ENABLED"] is True
    assert coerced["MAX_REACT_ITERATIONS"] == 12
    assert coerced["MEMORY_MIN_CONFIDENCE"] == 0.5
    assert coerced["PLAN_MODE"] == "emergent"  # enum 归一化小写


def test_validate_rejects_unknown_key():
    with pytest.raises(ConfigValidationError) as exc_info:
        config_schema.validate({"NOT_A_REAL_KEY": 1})
    assert "NOT_A_REAL_KEY" in exc_info.value.errors


def test_validate_rejects_bad_values():
    with pytest.raises(ConfigValidationError) as exc_info:
        config_schema.validate({
            "MAX_REACT_ITERATIONS": "abc",     # 非整数
            "PLAN_MODE": "banana",             # 非法枚举
            "SUBAGENT_ENABLED": "yes",         # 非 true/false
        })
    errors = exc_info.value.errors
    assert set(errors) == {"MAX_REACT_ITERATIONS", "PLAN_MODE", "SUBAGENT_ENABLED"}


def test_validate_rejects_sensitive_and_restart_required():
    with pytest.raises(ConfigValidationError) as exc_info:
        config_schema.validate({
            "LLM_API_KEY": "sk-hack",          # 敏感项
            "TRACING_BACKEND": "file",         # restart_required
        })
    errors = exc_info.value.errors
    assert "LLM_API_KEY" in errors
    assert "TRACING_BACKEND" in errors


def test_validate_rejects_bool_passed_as_int():
    with pytest.raises(ConfigValidationError):
        config_schema.validate({"MAX_REACT_ITERATIONS": True})


# ---------------------------------------------------------------------
# apply / restore
# ---------------------------------------------------------------------

def test_apply_restore_roundtrip():
    """apply 后 config 生效，restore 后完全还原。"""
    before = {name: getattr(config, name) for name in _ITEM_INDEX}
    overrides = config_schema.validate({
        "PLAN_MODE": "simple",
        "SUBAGENT_ENABLED": "true",
        "MAX_REACT_ITERATIONS": "7",
    })
    originals = config_schema.apply(overrides)
    try:
        assert config.PLAN_MODE == "simple"
        assert config.SUBAGENT_ENABLED is True
        assert config.MAX_REACT_ITERATIONS == 7
    finally:
        config_schema.restore(originals)
    after = {name: getattr(config, name) for name in _ITEM_INDEX}
    assert before == after


def test_env_passthrough_user_location():
    """USER_LOCATION 需同步写 os.environ（工具直读 env）。"""
    original_env = os.environ.get("USER_LOCATION")
    originals = config_schema.apply({"USER_LOCATION": "Beijing"})
    try:
        assert os.environ.get("USER_LOCATION") == "Beijing"
        assert config.USER_LOCATION == "Beijing"
    finally:
        config_schema.restore(originals)
    # 恢复后 env 回到原状（原来无值 → 被移除）
    assert os.environ.get("USER_LOCATION") == original_env


# ---------------------------------------------------------------------
# 敏感项 / sensitive items
# ---------------------------------------------------------------------

def test_schema_never_leaks_sensitive_values():
    """schema/values 输出里绝不包含敏感项的值。"""
    sentinel = "sk-super-secret-value-for-test"
    original = config.LLM_API_KEY
    config.LLM_API_KEY = sentinel
    try:
        schema_json = json.dumps(config_schema.get_schema(), ensure_ascii=False)
        values_json = json.dumps(config_schema.get_values(), ensure_ascii=False, default=str)
        assert sentinel not in schema_json
        assert sentinel not in values_json
        assert "LLM_API_KEY" not in config_schema.get_values()
    finally:
        config.LLM_API_KEY = original


def test_schema_sensitive_items_carry_configured_flag():
    schema = config_schema.get_schema()
    flat = [it for g in schema["groups"] for it in g["items"]]
    sensitive = [it for it in flat if it["sensitive"]]
    assert sensitive, "至少应有一个敏感项（LLM_API_KEY 等）"
    for it in sensitive:
        assert "configured" in it
        assert isinstance(it["configured"], bool)
