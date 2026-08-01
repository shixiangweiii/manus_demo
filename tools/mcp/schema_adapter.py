"""
MCP Schema Adapter — convert MCP tool inputSchema to OpenAI function-calling parameters.

MCP Schema 转换器 —— 将 MCP 工具的 inputSchema 转换为 OpenAI function-calling 的 parameters 格式。

MCP JSON Schema 和 OpenAI function-calling 格式高度相似，但有以下关键差异：
  1. additionalProperties: OpenAI 忽略；MCP 可能设为 false
  2. $defs / definitions + $ref: OpenAI 不总是支持 $ref
  3. oneOf / anyOf / allOf: OpenAI 支持有限
  4. patternProperties / unevaluatedProperties / prefixItems: OpenAI 不支持

提供两种模式：
  - loose: 去除不支持特性，记录警告，尽力转换
  - strict: 遇到不支持特性则拒绝，返回空 schema
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Schema conversion metrics for evaluation reporting
_schema_metrics: dict[str, int] = {
    "tool_parameter_errors": 0,
    "schema_validation_errors": 0,
    "tools_stripped": 0,
    "tools_rejected": 0,
}


def get_schema_metrics() -> dict[str, int]:
    """Return copy of schema conversion metrics for evaluation reporting.
    返回 schema 转换指标的副本（用于评测报告）。"""
    return dict(_schema_metrics)


def reset_schema_metrics() -> None:
    """Reset all schema conversion metrics (for testing).
    重置所有 schema 转换指标（测试用）。"""
    for key in _schema_metrics:
        _schema_metrics[key] = 0


def mcp_schema_to_openai(
    mcp_schema: dict[str, Any],
    mode: str = "loose",
) -> dict[str, Any]:
    """
    Convert an MCP tool's inputSchema to OpenAI function-calling parameters format.

    将 MCP 工具的 inputSchema 转换为 OpenAI function-calling 的 parameters 格式。

    Args:
        mcp_schema: MCP tool inputSchema (JSON Schema dict)
        mode: "loose" (strip unsupported, keep going) or "strict" (reject on unsupported)

    Returns:
        Valid OpenAI function-calling parameters dict.
    """
    if not isinstance(mcp_schema, dict):
        _schema_metrics["tool_parameter_errors"] += 1
        logger.warning("[SchemaAdapter] Non-dict inputSchema received")
        return {"type": "object", "properties": {}}

    schema = copy.deepcopy(mcp_schema)

    # Ensure base structure
    if schema.get("type") != "object":
        schema["type"] = "object"

    try:
        result = _convert_schema(schema, mode, path="", visited=frozenset())
    except _StrictReject as exc:
        _schema_metrics["tools_rejected"] += 1
        logger.warning("[SchemaAdapter] Strict mode rejected schema: %s", exc)
        return {
            "type": "object",
            "properties": {},
            "description": f"(schema rejected: {exc})",
        }

    return result


class _StrictReject(Exception):
    """Raised when strict mode encounters an unsupported schema feature.
    strict 模式遇到不支持的 schema 特性时抛出。"""


def _convert_schema(
    schema: dict[str, Any],
    mode: str,
    path: str,
    visited: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Core recursive conversion logic.
    核心递归转换逻辑。"""
    result: dict[str, Any] = {}

    # Preserve type
    if "type" in schema:
        result["type"] = schema["type"]

    # Preserve description
    if "description" in schema:
        result["description"] = schema["description"]

    # Preserve const, enum, default
    for key in ("const", "enum", "default"):
        if key in schema:
            result[key] = schema[key]

    # Handle additionalProperties — strip in loose, reject in strict
    if "additionalProperties" in schema:
        if mode == "strict":
            raise _StrictReject(f"{path}.additionalProperties")
        _schema_metrics["tools_stripped"] += 1
        logger.debug("[SchemaAdapter] Stripped additionalProperties at %s", path or "(root)")

    # Handle patternProperties, unevaluatedProperties, prefixItems — strip/reject
    for unsupported_key in ("patternProperties", "unevaluatedProperties", "prefixItems"):
        if unsupported_key in schema:
            if mode == "strict":
                raise _StrictReject(f"{path}.{unsupported_key}")
            _schema_metrics["tools_stripped"] += 1
            logger.debug("[SchemaAdapter] Stripped %s at %s", unsupported_key, path or "(root)")

    # Resolve $defs/definitions and inline $ref
    defs = schema.get("$defs", schema.get("definitions", {}))

    # Process properties
    if "properties" in schema:
        result["properties"] = {}
        for prop_name, prop_schema in schema["properties"].items():
            prop_path = f"{path}.{prop_name}" if path else prop_name
            result["properties"][prop_name] = _convert_property(
                prop_schema, defs, mode, prop_path, visited,
            )

    # Preserve required
    if "required" in schema:
        result["required"] = schema["required"]

    # Process items (for array types)
    if "items" in schema:
        items_path = f"{path}[*]" if path else "[*]"
        if isinstance(schema["items"], dict):
            result["items"] = _convert_property(
                schema["items"], defs, mode, items_path, visited,
            )
        elif isinstance(schema["items"], list):
            result["items"] = [
                _convert_property(item, defs, mode, f"{items_path}[{i}]", visited)
                if isinstance(item, dict) else item
                for i, item in enumerate(schema["items"])
            ]

    # Preserve format (OpenAI ignores it but it's harmless)
    if "format" in schema:
        result["format"] = schema["format"]

    # Infer a type when conversion leaves it unspecified.
    if "type" not in result:
        if "properties" in result:
            result["type"] = "object"
        elif "items" in result:
            result["type"] = "array"

    return result


def _convert_property(
    prop: dict[str, Any],
    defs: dict[str, Any],
    mode: str,
    path: str,
    visited: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Convert a single property schema.
    转换单个属性 schema。"""
    if not isinstance(prop, dict):
        return {"type": "string"}

    # Handle $ref — resolve and inline
    if "$ref" in prop:
        return _resolve_ref(prop, defs, mode, path, visited)

    # Handle oneOf / anyOf — take first non-null option
    for composite_key in ("oneOf", "anyOf"):
        if composite_key in prop:
            return _handle_oneof_anyof(prop, composite_key, defs, mode, path, visited)

    # Handle allOf — merge properties
    if "allOf" in prop:
        return _handle_allof(prop, defs, mode, path, visited)

    # Regular property — recurse
    return _convert_schema(prop, mode, path, visited)


def _resolve_ref(
    prop: dict[str, Any],
    defs: dict[str, Any],
    mode: str,
    path: str,
    visited: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Resolve a $ref reference against $defs.
    解析 $ref 引用。Uses visited set for circular ref detection."""
    ref = prop.get("$ref", "")
    # Circular $ref detection via visited set
    if ref in visited:
        _schema_metrics["schema_validation_errors"] += 1
        if mode == "strict":
            raise _StrictReject(f"{path}.$ref (circular: {ref})")
        return {"type": "string", "description": f"(circular ref: {ref})"}
    visited = visited | {ref}

    # Expect format: "#/$defs/Name" or "#/definitions/Name"
    if not ref.startswith("#/"):
        _schema_metrics["schema_validation_errors"] += 1
        if mode == "strict":
            raise _StrictReject(f"{path}.$ref (external: {ref})")
        return {"type": "string", "description": f"(unresolved ref: {ref})"}

    parts = ref.split("/")
    # Navigate to the definition: #/$defs/Name -> defs["Name"]
    def_name = parts[-1] if len(parts) >= 2 else ""

    if not def_name or def_name not in defs:
        _schema_metrics["schema_validation_errors"] += 1
        if mode == "strict":
            raise _StrictReject(f"{path}.$ref (not found: {ref})")
        return {"type": "string", "description": f"(missing ref: {ref})"}

    resolved = defs[def_name]
    if not isinstance(resolved, dict):
        return {"type": "string"}

    # Merge resolved properties with prop's own overrides (e.g. description)
    result = _convert_schema(resolved, mode, path, visited)
    if "description" in prop and "description" not in result:
        result["description"] = prop["description"]

    return result


def _handle_oneof_anyof(
    prop: dict[str, Any],
    key: str,
    defs: dict[str, Any],
    mode: str,
    path: str,
    visited: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Handle oneOf/anyOf by taking the first non-null option.
    处理 oneOf/anyOf —— 取第一个非 null 选项。"""
    options = prop.get(key, [])
    if not options:
        return {"type": "string"}

    # Find first non-null option
    for opt in options:
        if isinstance(opt, dict) and opt.get("type") != "null":
            result = _convert_property(opt, defs, mode, f"{path}({key})", visited)
            # Preserve description from parent
            if "description" in prop and "description" not in result:
                result["description"] = prop["description"]
            return result

    _schema_metrics["schema_validation_errors"] += 1
    if mode == "strict":
        raise _StrictReject(f"{path}.{key} (no non-null option)")
    return {"type": "string"}


def _handle_allof(
    prop: dict[str, Any],
    defs: dict[str, Any],
    mode: str,
    path: str,
    visited: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Handle allOf by merging properties from all options.
    处理 allOf —— 合并所有选项的属性。
    Non-object allOf items (e.g. string+constraints) are not merged —
    first non-null option is used directly."""
    options = prop.get("allOf", [])
    if not options:
        return {"type": "string"}

    # Check if any option is an object type (including $ref that may resolve to object)
    has_object = any(
        isinstance(o, dict) and (
            o.get("type") == "object" or "properties" in o or "$ref" in o
        )
        for o in options
    )
    if not has_object:
        # Non-object allOf: take first non-null option
        for opt in options:
            if isinstance(opt, dict) and opt.get("type") != "null":
                result = _convert_property(opt, defs, mode, f"{path}(allOf)", visited)
                if "description" in prop and "description" not in result:
                    result["description"] = prop["description"]
                return result
        return {"type": "string"}

    merged: dict[str, Any] = {"type": "object", "properties": {}}
    merged_required: list[str] = []

    for opt in options:
        if not isinstance(opt, dict):
            continue
        converted = _convert_property(opt, defs, mode, f"{path}(allOf)", visited)
        if "properties" in converted:
            merged["properties"].update(converted["properties"])
        if "required" in converted:
            merged_required.extend(converted["required"])

    if merged_required:
        merged["required"] = merged_required

    if "description" in prop:
        merged["description"] = prop["description"]

    return merged
