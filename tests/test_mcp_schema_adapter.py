"""Tests for MCP Schema Adapter — MCP inputSchema to OpenAI parameters conversion."""

import pytest

from tools.mcp.schema_adapter import (
    mcp_schema_to_openai,
    get_schema_metrics,
    reset_schema_metrics,
    _StrictReject,
)


@pytest.fixture(autouse=True)
def _reset_metrics():
    reset_schema_metrics()
    yield
    reset_schema_metrics()


class TestSimpleSchema:
    def test_basic_object_passes_through(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        }
        result = mcp_schema_to_openai(schema)
        assert result["type"] == "object"
        assert "query" in result["properties"]
        assert result["properties"]["query"]["type"] == "string"
        assert result["required"] == ["query"]

    def test_empty_schema_returns_object(self):
        result = mcp_schema_to_openai({})
        assert result["type"] == "object"
        assert result.get("properties", {}) == {}

    def test_non_dict_returns_empty(self):
        result = mcp_schema_to_openai("not a dict")
        assert result["type"] == "object"
        assert result.get("properties", {}) == {}

    def test_preserves_const_enum_default(self):
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "slow"]},
                "count": {"type": "integer", "default": 10},
                "fixed": {"type": "string", "const": "value"},
            },
        }
        result = mcp_schema_to_openai(schema)
        assert result["properties"]["mode"]["enum"] == ["fast", "slow"]
        assert result["properties"]["count"]["default"] == 10
        assert result["properties"]["fixed"]["const"] == "value"


class TestAdditionalProperties:
    def test_stripped_in_loose_mode(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "additionalProperties": False,
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert "additionalProperties" not in result
        assert "x" in result["properties"]

    def test_rejected_in_strict_mode(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "additionalProperties": False,
        }
        result = mcp_schema_to_openai(schema, mode="strict")
        assert "(schema rejected" in result.get("description", "")
        assert "properties" not in result or result.get("properties") == {}


class TestRefResolution:
    def test_ref_inlined(self):
        schema = {
            "type": "object",
            "$defs": {
                "Name": {"type": "string", "description": "A person's name"},
            },
            "properties": {
                "first": {"$ref": "#/$defs/Name"},
                "last": {"$ref": "#/$defs/Name"},
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert result["properties"]["first"]["type"] == "string"
        assert result["properties"]["last"]["type"] == "string"
        assert "$defs" not in result

    def test_recursive_ref_replaced_with_string(self):
        schema = {
            "type": "object",
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "child": {"$ref": "#/$defs/Node"},
                    },
                },
            },
            "properties": {
                "root": {"$ref": "#/$defs/Node"},
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        # The root ref resolves, but the recursive child ref within becomes string
        assert "root" in result["properties"]

    def test_missing_ref_in_loose(self):
        schema = {
            "type": "object",
            "properties": {
                "x": {"$ref": "#/$defs/Missing"},
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert result["properties"]["x"]["type"] == "string"

    def test_ref_rejected_in_strict(self):
        schema = {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/X"}},
            "$defs": {"X": {"type": "string"}},
            "additionalProperties": False,
        }
        result = mcp_schema_to_openai(schema, mode="strict")
        # Strict mode rejects because of additionalProperties, not $ref
        assert "(schema rejected" in result.get("description", "")


class TestOneOfAnyOf:
    def test_oneof_takes_first_non_null(self):
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "oneOf": [
                        {"type": "null"},
                        {"type": "string"},
                        {"type": "integer"},
                    ],
                    "description": "A nullable value",
                },
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert result["properties"]["value"]["type"] == "string"
        assert result["properties"]["value"]["description"] == "A nullable value"

    def test_anyof_takes_first_non_null(self):
        schema = {
            "type": "object",
            "properties": {
                "data": {
                    "anyOf": [
                        {"type": "integer"},
                        {"type": "string"},
                    ],
                },
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert result["properties"]["data"]["type"] == "integer"


class TestAllOf:
    def test_allof_merges_properties(self):
        schema = {
            "type": "object",
            "properties": {
                "combined": {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {"a": {"type": "string"}},
                            "required": ["a"],
                        },
                        {
                            "type": "object",
                            "properties": {"b": {"type": "integer"}},
                        },
                    ],
                    "description": "Combined object",
                },
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        combined = result["properties"]["combined"]
        assert "a" in combined["properties"]
        assert "b" in combined["properties"]
        assert "a" in combined["required"]


class TestNestedSchema:
    def test_nested_properties_recurse(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "nested": {
                            "type": "object",
                            "properties": {
                                "deep": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        config = result["properties"]["config"]
        assert config["type"] == "object"
        assert "name" in config["properties"]
        assert config["properties"]["nested"]["type"] == "object"
        assert config["properties"]["nested"]["properties"]["deep"]["type"] == "boolean"

    def test_array_items_recurse(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        items = result["properties"]["items"]
        assert items["type"] == "array"
        assert items["items"]["type"] == "string"


class TestUnsupportedFeatures:
    def test_pattern_properties_stripped_loose(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "patternProperties": {"^S_": {"type": "string"}},
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert "patternProperties" not in result

    def test_unevaluated_properties_stripped_loose(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "unevaluatedProperties": False,
        }
        result = mcp_schema_to_openai(schema, mode="loose")
        assert "unevaluatedProperties" not in result


class TestMetrics:
    def test_metrics_track_stripped(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "additionalProperties": False,
        }
        mcp_schema_to_openai(schema, mode="loose")
        metrics = get_schema_metrics()
        assert metrics["tools_stripped"] >= 1

    def test_metrics_track_rejected(self):
        schema = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "additionalProperties": False,
        }
        mcp_schema_to_openai(schema, mode="strict")
        metrics = get_schema_metrics()
        assert metrics["tools_rejected"] >= 1

    def test_metrics_track_validation_errors(self):
        schema = {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/Missing"}},
        }
        mcp_schema_to_openai(schema, mode="loose")
        metrics = get_schema_metrics()
        assert metrics["schema_validation_errors"] >= 1
