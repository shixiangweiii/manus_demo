"""
Unit tests for react.tool_call_helpers — the shared ReAct tool-call utilities
that keep ReActEngine, GoalDrivenPlanner, and EmergentPlanner legacy paths
behaviorally aligned.

These tests are intentionally focused on the helper contracts rather than the
agents that use them; integration coverage lives in test_subagent.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from react.tool_call_helpers import (
    RATE_LIMITED_MARKER,
    attribute_caller,
    classify_result,
    truncate_for_llm,
)


# ----------------------------------------------------------------------
# attribute_caller
# ----------------------------------------------------------------------


class TestAttributeCaller:
    def test_calls_set_caller_when_supported(self):
        tool = MagicMock()
        tool.name = "subagent"
        attribute_caller(tool, "ExecutorAgent")
        tool.set_caller.assert_called_once_with("ExecutorAgent")

    def test_noop_when_agent_name_empty(self):
        tool = MagicMock()
        tool.name = "subagent"
        attribute_caller(tool, "")
        tool.set_caller.assert_not_called()

    def test_noop_when_tool_lacks_set_caller(self):
        # Use a real object with a strict spec so hasattr returns False
        class ToolWithoutSetCaller:
            name = "web_search"

        tool = ToolWithoutSetCaller()
        # Must not raise
        attribute_caller(tool, "ExecutorAgent")

    def test_swallows_set_caller_exception(self):
        tool = MagicMock()
        tool.name = "subagent"
        tool.set_caller.side_effect = RuntimeError("boom")
        # Must not raise — the helper logs and continues
        attribute_caller(tool, "ExecutorAgent")
        tool.set_caller.assert_called_once_with("ExecutorAgent")


# ----------------------------------------------------------------------
# classify_result
# ----------------------------------------------------------------------


class TestClassifyResult:
    def test_normal_string_is_success(self):
        is_err, rl = classify_result("found 5 items", None)
        assert is_err is False
        assert rl is False

    def test_exception_is_hard_failure(self):
        is_err, rl = classify_result(None, RuntimeError("boom"))
        assert is_err is True
        assert rl is False

    def test_exception_takes_precedence_over_result(self):
        # Even if result looks like a normal string, an exc means hard fail
        is_err, rl = classify_result("ignored", ValueError("xxx"))
        assert is_err is True
        assert rl is False

    def test_error_prefix_is_soft_failure(self):
        is_err, rl = classify_result("Error: web_search timed out", None)
        assert is_err is True
        assert rl is False

    def test_subagent_call_limit_is_rate_limited(self):
        # The exact sentinel string used in production (SubAgentTool.execute)
        msg = f"Error: {RATE_LIMITED_MARKER} (3 per task). Please continue without spawning more sub-agents."
        is_err, rl = classify_result(msg, None)
        assert is_err is True
        assert rl is True

    def test_rate_limited_implies_error(self):
        # Rate-limited cannot exist without is_error=True per contract
        is_err, rl = classify_result(f"Error: {RATE_LIMITED_MARKER}", None)
        assert is_err and rl

    def test_non_string_result_is_success(self):
        # Defensive: tools should return str, but a dict is treated as success
        # (truncate_for_llm will pass it through unchanged)
        is_err, rl = classify_result({"result": "ok"}, None)
        assert is_err is False
        assert rl is False

    def test_error_lowercase_does_not_match(self):
        # The prefix is case-sensitive; "error:" lowercase is not classified as error
        is_err, rl = classify_result("error: lowercase shouldn't trigger", None)
        assert is_err is False
        assert rl is False


# ----------------------------------------------------------------------
# truncate_for_llm
# ----------------------------------------------------------------------


class TestTruncateForLlm:
    def test_short_success_passes_through(self):
        record, llm = truncate_for_llm("hello world", limit=2000, is_error=False)
        assert record == "hello world"
        assert llm == "hello world"

    def test_oversized_success_is_truncated_with_marker(self):
        result = "x" * 5000
        record, llm = truncate_for_llm(result, limit=2000, is_error=False)
        # Record stores bare truncation
        assert record == "x" * 2000
        assert len(record) == 2000
        # LLM message has truncation marker appended
        assert llm.startswith("x" * 2000)
        assert "[Tool output truncated at 2000 characters" in llm
        assert "original length=5000" in llm

    def test_error_keeps_full_text(self):
        # Errors are preserved in full for debugging — size usually small
        err = "Error: " + ("y" * 5000)
        record, llm = truncate_for_llm(err, limit=2000, is_error=True)
        assert record == err
        assert llm == err

    def test_non_string_passes_through(self):
        # Defensive fallback: tools should return str but if not, pass through
        record, llm = truncate_for_llm({"foo": "bar"}, limit=2000, is_error=False)
        assert record == {"foo": "bar"}
        assert llm == {"foo": "bar"}

    def test_exactly_at_limit_passes_through(self):
        result = "z" * 2000
        record, llm = truncate_for_llm(result, limit=2000, is_error=False)
        assert record == result
        assert llm == result
        assert "[Tool output truncated" not in llm

    def test_one_over_limit_triggers_truncation(self):
        result = "a" * 2001
        record, llm = truncate_for_llm(result, limit=2000, is_error=False)
        assert len(record) == 2000
        assert "[Tool output truncated" in llm
        assert "original length=2001" in llm

    def test_truncation_marker_format_matches_legacy(self):
        # Lock in the exact marker string so any change is intentional —
        # downstream code (tests, dashboards) may match on this format.
        result = "p" * 100
        _, llm = truncate_for_llm(result, limit=50, is_error=False)
        assert llm == "p" * 50 + (
            "\n\n[Tool output truncated at 50 characters "
            "to control context size; original length=100]"
        )


# ----------------------------------------------------------------------
# Combined invariants — properties the three helpers must satisfy together
# ----------------------------------------------------------------------


class TestHelperInvariants:
    def test_rate_limited_result_is_not_truncated_to_record(self):
        # Rate-limited messages are short Error: strings; truncate_for_llm with
        # is_error=True must keep the full payload so router/eval can match the
        # sentinel string after the call.
        msg = f"Error: {RATE_LIMITED_MARKER} (3 per task). Please continue."
        is_err, rl = classify_result(msg, None)
        record, llm = truncate_for_llm(msg, limit=20, is_error=is_err)
        assert is_err and rl
        assert record == msg
        assert RATE_LIMITED_MARKER in record
        assert RATE_LIMITED_MARKER in llm

    def test_success_then_truncate_pipeline(self):
        # End-to-end: classify a successful result, then truncate it
        result = "data" * 1000  # 4000 chars
        is_err, rl = classify_result(result, None)
        record, llm = truncate_for_llm(result, limit=500, is_error=is_err)
        assert not is_err and not rl
        assert len(record) == 500
        assert "[Tool output truncated" in llm
