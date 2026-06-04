"""
Batch 4.1 tests: react.engine_helpers.execute_tool_calls.

Covers the shared tool-execution path extracted from ReActEngine,
ReasoningEngine, EmergentPlannerAgent, and GoalDrivenPlannerAgent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from react.engine_helpers import ToolExecutionPolicy, execute_tool_calls
from schema import ReasoningEffort, ToolCallRecord
from tools.router import ToolRouter


def _make_tc(name: str, args: str = "{}", tc_id: str = "tc_1"):
    """Build a lightweight tool-call object matching OpenAI shape."""
    return SimpleNamespace(
        id=tc_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


def _make_tool(name: str, return_value: str = "ok", *, side_effect=None):
    """Build a mock tool with traced_execute."""
    t = MagicMock()
    t.name = name
    if side_effect:
        t.traced_execute = AsyncMock(side_effect=side_effect)
    else:
        t.traced_execute = AsyncMock(return_value=return_value)
    return t


class TestBasicExecution:
    @pytest.mark.asyncio
    async def test_single_tool_success(self):
        tool = _make_tool("search", "found it")
        tools = {"search": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        msgs = await execute_tool_calls(
            [_make_tc("search")],
            tools,
            router,
            node_id="n1",
            agent_name="TestAgent",
            truncation_limit=2000,
            tool_calls_log=log,
            log_prefix="Test",
        )

        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "tc_1"
        assert msgs[0]["content"] == "found it"
        assert len(log) == 1
        assert log[0].tool_name == "search"
        assert log[0].result == "found it"

    @pytest.mark.asyncio
    async def test_parallel_tool_calls(self):
        tool_a = _make_tool("a", "result_a")
        tool_b = _make_tool("b", "result_b")
        tools = {"a": tool_a, "b": tool_b}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        msgs = await execute_tool_calls(
            [_make_tc("a", tc_id="tc_1"), _make_tc("b", tc_id="tc_2")],
            tools,
            router,
            node_id="n1",
            agent_name="TestAgent",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        assert len(msgs) == 2
        assert msgs[0]["tool_call_id"] == "tc_1"
        assert msgs[0]["content"] == "result_a"
        assert msgs[1]["tool_call_id"] == "tc_2"
        assert msgs[1]["content"] == "result_b"


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        tools = {}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        msgs = await execute_tool_calls(
            [_make_tc("nonexistent")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        assert "[TOOL ERROR]" in msgs[0]["content"]
        assert "Unknown tool" in msgs[0]["content"]
        assert log[0].result.startswith("Error:")


class TestToolException:
    @pytest.mark.asyncio
    async def test_tool_exception_captured(self):
        tool = _make_tool("boom", side_effect=RuntimeError("kaboom"))
        tools = {"boom": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        msgs = await execute_tool_calls(
            [_make_tc("boom")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        assert "[TOOL ERROR]" in msgs[0]["content"]
        assert "kaboom" in msgs[0]["content"]
        assert log[0].result.startswith("Error:")


class TestToolRouterAccounting:
    @pytest.mark.asyncio
    async def test_success_recorded(self):
        tool = _make_tool("ok_tool", "done")
        tools = {"ok_tool": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        await execute_tool_calls(
            [_make_tc("ok_tool")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        summary = router.get_node_summary("n1")
        assert "ok_tool" in summary
        assert summary["ok_tool"]["calls"] == 1
        assert summary["ok_tool"]["failures"] == 0

    @pytest.mark.asyncio
    async def test_failure_recorded(self):
        tool = _make_tool("bad_tool", "Error: something broke")
        tools = {"bad_tool": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        await execute_tool_calls(
            [_make_tc("bad_tool")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        summary = router.get_node_summary("n1")
        assert summary["bad_tool"]["failures"] == 1

    @pytest.mark.asyncio
    async def test_rate_limited_recorded(self):
        from react.tool_call_helpers import RATE_LIMITED_MARKER

        tool = _make_tool("sub", f"Error: {RATE_LIMITED_MARKER}")
        tools = {"sub": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        await execute_tool_calls(
            [_make_tc("sub")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        summary = router.get_node_summary("n1")
        assert summary["sub"]["rate_limited"] == 1
        assert summary["sub"]["failures"] == 0

    @pytest.mark.asyncio
    async def test_429_error_recorded_as_rate_limited(self):
        tool = _make_tool("fetch_url", "Error: fetch_url failed: RuntimeError: 429 Too Many Requests")
        tools = {"fetch_url": tool}
        router = ToolRouter(available_tools=["fetch_url", "web_search"])
        log: list[ToolCallRecord] = []

        await execute_tool_calls(
            [_make_tc("fetch_url")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        summary = router.get_node_summary("n1")
        assert summary["fetch_url"]["rate_limited"] == 1
        assert summary["fetch_url"]["failures"] == 0


class TestTruncation:
    @pytest.mark.asyncio
    async def test_truncation_applied_to_success(self):
        long_result = "x" * 5000
        tool = _make_tool("big", long_result)
        tools = {"big": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        msgs = await execute_tool_calls(
            [_make_tc("big")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=1000,
            tool_calls_log=log,
        )

        # LLM message should have truncation marker
        assert "truncated" in msgs[0]["content"]
        # Record result is truncated but without marker
        assert len(log[0].result) <= 1000

    @pytest.mark.asyncio
    async def test_no_truncation_on_error(self):
        long_error = "Error: " + "x" * 5000
        tool = _make_tool("fail", long_error)
        tools = {"fail": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        msgs = await execute_tool_calls(
            [_make_tc("fail")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=100,
            tool_calls_log=log,
        )

        # Error results are NOT truncated
        assert len(log[0].result) > 100


class TestToolCallsLogAppend:
    @pytest.mark.asyncio
    async def test_log_appended_in_place(self):
        tool = _make_tool("t", "ok")
        tools = {"t": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        await execute_tool_calls(
            [_make_tc("t")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        # Same list object, appended to
        assert len(log) == 1
        assert log[0].tool_name == "t"


class TestErrorPrefix:
    @pytest.mark.asyncio
    async def test_error_has_tool_error_prefix(self):
        tool = _make_tool("bad", "Error: timeout")
        tools = {"bad": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        msgs = await execute_tool_calls(
            [_make_tc("bad")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        assert msgs[0]["content"].startswith("[TOOL ERROR]")


class TestCallerAttribution:
    @pytest.mark.asyncio
    async def test_attribute_caller_called_with_agent_name(self):
        tool = _make_tool("sub", "ok")
        tool.set_caller = MagicMock()
        tools = {"sub": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        await execute_tool_calls(
            [_make_tc("sub")],
            tools,
            router,
            node_id="n1",
            agent_name="MyAgent",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        tool.set_caller.assert_called_once_with("MyAgent")

    @pytest.mark.asyncio
    async def test_no_caller_when_empty(self):
        tool = _make_tool("t", "ok")
        tool.set_caller = MagicMock()
        tools = {"t": tool}
        router = ToolRouter(available_tools=["search", "a", "b"])
        log: list[ToolCallRecord] = []

        await execute_tool_calls(
            [_make_tc("t")],
            tools,
            router,
            node_id="n1",
            agent_name="",
            truncation_limit=2000,
            tool_calls_log=log,
        )

        tool.set_caller.assert_not_called()


class TestToolExecutionPolicy:
    def test_default_policy_reads_config(self):
        import config as config_module
        policy = ToolExecutionPolicy.default()
        assert policy.truncation_limit == config_module.TOOL_RESULT_TRUNCATION_LIMIT
        assert policy.error_prefix == "[TOOL ERROR]"
        assert policy.include_alternatives_hint is True
        assert "IMPORTANT" in policy.error_retry_guidance

    def test_default_policy_respects_config_override(self):
        import config as config_module
        original = config_module.TOOL_RESULT_TRUNCATION_LIMIT
        try:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = 7777
            policy = ToolExecutionPolicy.default()
            assert policy.truncation_limit == 7777
        finally:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = original

    def test_custom_policy_overrides_defaults(self):
        policy = ToolExecutionPolicy(
            truncation_limit=500,
            error_prefix="[ERR]",
            include_alternatives_hint=False,
        )
        assert policy.truncation_limit == 500
        assert policy.error_prefix == "[ERR]"
        assert policy.include_alternatives_hint is False

    @pytest.mark.asyncio
    async def test_custom_error_prefix_applied(self):
        tool = _make_tool("bad", "Error: fail")
        tools = {"bad": tool}
        router = ToolRouter(available_tools=["bad"])
        log: list[ToolCallRecord] = []

        policy = ToolExecutionPolicy(error_prefix="[CUSTOM]")
        msgs = await execute_tool_calls(
            [_make_tc("bad")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
            policy=policy,
        )
        assert msgs[0]["content"].startswith("[CUSTOM]")

    @pytest.mark.asyncio
    async def test_no_alternatives_hint(self):
        tool = _make_tool("bad", "Error: fail")
        tools = {"bad": tool}
        router = ToolRouter(available_tools=["bad"])
        log: list[ToolCallRecord] = []

        policy = ToolExecutionPolicy(include_alternatives_hint=False)
        msgs = await execute_tool_calls(
            [_make_tc("bad")],
            tools,
            router,
            node_id="n1",
            agent_name="Test",
            truncation_limit=2000,
            tool_calls_log=log,
            policy=policy,
        )
        assert "IMPORTANT" not in msgs[0]["content"]

    def test_for_effort_low_relative_to_config(self):
        import config as config_module
        original = config_module.TOOL_RESULT_TRUNCATION_LIMIT
        try:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = 3000
            policy = ToolExecutionPolicy.for_effort(ReasoningEffort.LOW)
            assert policy.truncation_limit == 1500  # base // 2
        finally:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = original

    def test_for_effort_high_relative_to_config(self):
        import config as config_module
        original = config_module.TOOL_RESULT_TRUNCATION_LIMIT
        try:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = 3000
            policy = ToolExecutionPolicy.for_effort(ReasoningEffort.HIGH)
            assert policy.truncation_limit == 6000  # base * 2
        finally:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = original

    def test_for_effort_low_minimum_floor(self):
        import config as config_module
        original = config_module.TOOL_RESULT_TRUNCATION_LIMIT
        try:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = 500
            policy = ToolExecutionPolicy.for_effort(ReasoningEffort.LOW)
            assert policy.truncation_limit >= 500  # max(500, 250)
        finally:
            config_module.TOOL_RESULT_TRUNCATION_LIMIT = original

    def test_for_effort_medium_is_default(self):
        policy = ToolExecutionPolicy.for_effort(ReasoningEffort.MEDIUM)
        default = ToolExecutionPolicy.default()
        assert policy.truncation_limit == default.truncation_limit


class TestParseArgs:
    """Fix 4: execute_tool_calls parse_args parameter for fenced JSON."""

    @pytest.mark.asyncio
    async def test_fenced_json_with_custom_parser(self):
        from llm.client import LLMClient

        tool = _make_tool("search", "found it")
        tools = {"search": tool}
        router = ToolRouter(available_tools=["search"])
        log: list[ToolCallRecord] = []

        # Arguments wrapped in markdown fence — json.loads would fail
        tc = _make_tc("search", args='```json\n{"query": "test"}\n```')
        msgs = await execute_tool_calls(
            [tc], tools, router,
            node_id="n1", agent_name="Test", truncation_limit=2000,
            tool_calls_log=log,
            parse_args=LLMClient.parse_json,
        )
        # Tool should receive the parsed args
        tool.traced_execute.assert_called_once_with(query="test")

    @pytest.mark.asyncio
    async def test_default_parser_rejects_fenced_json(self):
        tool = _make_tool("search", "found it")
        tools = {"search": tool}
        router = ToolRouter(available_tools=["search"])
        log: list[ToolCallRecord] = []

        # Default json.loads can't parse fenced JSON → falls back to {}
        tc = _make_tc("search", args='```json\n{"query": "test"}\n```')
        msgs = await execute_tool_calls(
            [tc], tools, router,
            node_id="n1", agent_name="Test", truncation_limit=2000,
            tool_calls_log=log,
        )
        # Tool called with empty args (fallback {})
        tool.traced_execute.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_parse_args_returns_non_dict_falls_back(self):
        tool = _make_tool("search", "found it")
        tools = {"search": tool}
        router = ToolRouter(available_tools=["search"])
        log: list[ToolCallRecord] = []

        # Custom parser returns a list, not dict → fallback to {}
        tc = _make_tc("search", args='[1, 2, 3]')
        msgs = await execute_tool_calls(
            [tc], tools, router,
            node_id="n1", agent_name="Test", truncation_limit=2000,
            tool_calls_log=log,
            parse_args=json.loads,
        )
        tool.traced_execute.assert_called_once_with()
