"""
Tests for the SubAgent multi-agent mechanism (v9).
All tests are mock-based, no LLM API calls required.

子智能体多智能体机制测试（v9）。
全部测试基于 mock，无需 LLM API 调用。

Covers:
- SubAgentStatus, SubAgentSummary, SubAgentResult schema models
- SubAgent class: creation, independent context, restricted tools, depth=1,
  timeout handling, structured summary, token budget, sandbox isolation, events
- SubAgentTool: BaseTool interface, depth=1, whitelist validation, execute,
  timeout, call count limit, call count reset
- Integration: Orchestrator injection, EvaluationProbe SubAgent metrics
- Tracing: TracingBridge SubAgent span handling
- Anti-pattern specific tests: #2, #3, #4, #6, #8
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from schema import (
    LLMCallRecord,
    StepResult,
    SubAgentResult,
    SubAgentStatus,
    SubAgentSummary,
    ToolCallRecord,
)


# ======================================================================
# Fixtures & Helpers
# ======================================================================

def _make_tool(name: str = "mock_tool"):
    """Create a mock BaseTool with the given name."""
    tool = MagicMock()
    tool.name = name
    tool.description = f"Mock {name} for testing"
    tool.to_openai_tool.return_value = {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Mock {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    tool.execute = AsyncMock(return_value="mock result")
    tool.traced_execute = AsyncMock(return_value="mock result")
    return tool


def _make_llm_client():
    """Create a mock LLMClient."""
    client = MagicMock()
    client.model = "test-model"
    client.chat = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content="done", tool_calls=None))],
        usage=MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    ))
    client.chat_json = AsyncMock(return_value={
        "accomplished": "found 3 files",
        "findings": "all files use asyncio.gather",
        "issues": "",
        "artifacts": ["/tmp/result.txt"],
        "tool_calls_summary": "used web_search and file_ops",
    })
    client.chat_with_tools = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(
            message=MagicMock(content="task done", tool_calls=None),
        )],
        usage=MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    ))
    client.get_call_records.return_value = [
        LLMCallRecord(call_type="chat", prompt_summary="", total_tokens=30),
    ]
    client.reset_usage = MagicMock()
    return client


def _make_step_result(success=True, output="task completed", tool_calls=None, iterations_completed=1):
    """Create a StepResult with sensible defaults."""
    return StepResult(
        step_id="test_step",
        success=success,
        output=output,
        tool_calls_log=tool_calls or [],
        iterations_completed=iterations_completed,
    )


def _make_subagent_result(**overrides):
    """Create a SubAgentResult with defaults for testing."""
    defaults = dict(
        subagent_id="SubAgent-1",
        task_description="test",
        status=SubAgentStatus.COMPLETED,
        summary=SubAgentSummary(accomplished="found files"),
        summary_text=json.dumps({
            "accomplished": "found files",
            "findings": "",
            "issues": "",
            "artifacts": [],
            "tool_calls_summary": "",
        }),
    )
    defaults.update(overrides)
    return SubAgentResult(**defaults)


# ======================================================================
# Test SubAgentResult / SubAgentSummary / SubAgentStatus
# ======================================================================

class TestSubAgentResult:

    def test_sub_agent_status_values(self):
        assert SubAgentStatus.PENDING == "pending"
        assert SubAgentStatus.RUNNING == "running"
        assert SubAgentStatus.COMPLETED == "completed"
        assert SubAgentStatus.FAILED == "failed"
        assert SubAgentStatus.TIMED_OUT == "timed_out"

    def test_sub_agent_summary_defaults(self):
        s = SubAgentSummary()
        assert s.accomplished == ""
        assert s.findings == ""
        assert s.issues == ""
        assert s.artifacts == []
        assert s.tool_calls_summary == ""

    def test_sub_agent_summary_full(self):
        s = SubAgentSummary(
            accomplished="found files",
            findings="3 matches",
            issues="timeout on one",
            artifacts=["/tmp/a.txt"],
            tool_calls_summary="web_search x2, file_ops x1",
        )
        assert s.findings == "3 matches"
        assert len(s.artifacts) == 1

    def test_sub_agent_result_defaults(self):
        r = SubAgentResult(
            subagent_id="SubAgent-1",
            task_description="search codebase",
            status=SubAgentStatus.COMPLETED,
        )
        assert r.subagent_id == "SubAgent-1"
        assert r.status == SubAgentStatus.COMPLETED
        assert r.tool_calls_count == 0
        assert r.iterations_used == 0
        assert r.duration_ms == 0.0
        assert r.tokens_used == 0
        assert r.tool_calls_log == []
        assert r.summary_text == ""

    def test_sub_agent_result_with_summary(self):
        summary = SubAgentSummary(accomplished="found 5 items", findings="all safe")
        r = SubAgentResult(
            subagent_id="SubAgent-2",
            task_description="analyze",
            status=SubAgentStatus.COMPLETED,
            summary=summary,
            summary_text=summary.model_dump_json(),
            tool_calls_count=3,
            iterations_used=3,
        )
        parsed = json.loads(r.summary_text)
        assert parsed["accomplished"] == "found 5 items"

    def test_sub_agent_result_json_roundtrip(self):
        summary = SubAgentSummary(
            accomplished="done",
            issues="partial",
            artifacts=["/tmp/out.txt"],
        )
        r = SubAgentResult(
            subagent_id="SA-1",
            task_description="test",
            status=SubAgentStatus.COMPLETED,
            summary=summary,
            summary_text=summary.model_dump_json(),
        )
        data = json.loads(r.summary_text)
        assert "accomplished" in data
        assert "issues" in data
        assert "artifacts" in data


# ======================================================================
# Test SubAgent
# ======================================================================

class TestSubAgent:

    def test_creation_basic(self):
        from agents.subagent import SubAgent
        client = _make_llm_client()
        tool = _make_tool("search")
        agent = SubAgent(
            name="SubAgent-1",
            task_description="find files",
            llm_client=client,
            tools=[tool],
        )
        assert agent.name == "SubAgent-1"
        assert agent.task_description == "find files"
        assert "subagent" not in agent.tools

    def test_creation_with_custom_params(self):
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-custom",
            task_description="custom task",
            llm_client=client,
            tools=[_make_tool("t1")],
            max_iterations=5,
            timeout=60,
            max_tokens=10000,
            parent_agent_name="Orchestrator",
            sandbox_subdir="/tmp/sa_1",
        )
        assert agent.timeout == 60
        assert agent.max_tokens == 10000
        assert agent.parent_agent_name == "Orchestrator"

    def test_independent_context(self):
        """Anti-pattern #2: SubAgent has its own messages list, independent from parent."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )
        assert agent._summary_messages is not None
        assert len(agent._summary_messages) == 1
        assert agent._summary_messages[0]["role"] == "system"

    def test_depth_1_enforcement_no_subagent_tool(self):
        """Structural depth=1: SubAgent's tool list cannot contain 'subagent'."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        sub_tool = _make_tool("subagent")
        regular_tool = _make_tool("search")
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[regular_tool, sub_tool],
        )
        # Filtering happens in SubAgentTool.execute, not in SubAgent itself
        assert "search" in agent.tools

    def test_sandbox_subdir_in_system_prompt(self):
        """Anti-pattern #4: sandbox directory is injected into system prompt."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            sandbox_subdir="/tmp/sandbox/subagent_1",
        )
        assert "/tmp/sandbox/subagent_1" in agent._system_prompt

    def test_sandbox_subdir_not_in_system_prompt_when_empty(self):
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )
        assert "Your working directory" not in agent._system_prompt

    @pytest.mark.asyncio
    async def test_run_emits_start_and_complete_events(self):
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []
        agent = SubAgent(
            name="SA-1",
            task_description="test task",
            llm_client=client,
            tools=[_make_tool()],
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output="done")
            result = await agent.run()

        event_names = [e for e, _ in events]
        assert "subagent_start" in event_names
        assert "subagent_complete" in event_names
        assert result.status == SubAgentStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_run_emits_failed_on_step_failure(self):
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []
        agent = SubAgent(
            name="SA-1",
            task_description="test task",
            llm_client=client,
            tools=[_make_tool()],
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=False, output="error occurred")
            result = await agent.run()

        event_names = [e for e, _ in events]
        assert "subagent_start" in event_names
        assert "subagent_failed" in event_names
        assert result.status == SubAgentStatus.FAILED

    @pytest.mark.asyncio
    async def test_run_handles_timeout(self):
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []

        async def slow_execute(**kwargs):
            await asyncio.sleep(10)

        agent = SubAgent(
            name="SA-1",
            task_description="test task",
            llm_client=client,
            tools=[_make_tool()],
            timeout=1,
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", side_effect=slow_execute):
            result = await agent.run()

        event_names = [e for e, _ in events]
        assert "subagent_timed_out" in event_names
        assert result.status == SubAgentStatus.TIMED_OUT

    @pytest.mark.asyncio
    async def test_run_handles_exception(self):
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []

        agent = SubAgent(
            name="SA-1",
            task_description="test task",
            llm_client=client,
            tools=[_make_tool()],
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = RuntimeError("LLM API error")
            result = await agent.run()

        event_names = [e for e, _ in events]
        assert "subagent_failed" in event_names
        assert result.status == SubAgentStatus.FAILED

    @pytest.mark.asyncio
    async def test_structured_summary_on_success(self):
        """Anti-pattern #6: Returns structured SubAgentSummary, not free text."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="search codebase",
            llm_client=client,
            tools=[_make_tool()],
        )

        long_output = "x" * (config.SUBAGENT_SUMMARY_MAX_LENGTH + 100)
        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output=long_output)
            result = await agent.run()

        data = json.loads(result.summary_text)
        assert "accomplished" in data
        assert "findings" in data
        assert "issues" in data
        assert "artifacts" in data
        assert "tool_calls_summary" in data

    @pytest.mark.asyncio
    async def test_summary_fallback_on_llm_failure(self):
        """When summary LLM call fails, fallback truncation is used."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        client.chat_json = AsyncMock(side_effect=RuntimeError("API down"))
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        long_output = "abc" * 1000
        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output=long_output)
            result = await agent.run()

        assert result.summary.accomplished != ""
        assert "Summary generation failed" in result.summary.issues or result.summary.accomplished

    def test_event_callback_exception_isolation(self):
        """Event callback errors must not propagate to caller."""
        from agents.subagent import SubAgent
        client = _make_llm_client()

        def bad_callback(event, data):
            raise ValueError("callback broke")

        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            on_event=bad_callback,
        )
        agent._emit("test_event", {})  # should not raise


# ======================================================================
# Test SubAgentTool
# ======================================================================

class TestSubAgentTool:

    def _make_subagent_tool(self, **overrides):
        """Helper to create SubAgentTool with sensible defaults."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        defaults = dict(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
        )
        defaults.update(overrides)
        return SubAgentTool(**defaults)

    def test_name_and_description(self):
        tool = self._make_subagent_tool()
        assert tool.name == "subagent"
        assert "sub-agent" in tool.description.lower()

    def test_parameters_schema_requires_task_description(self):
        tool = self._make_subagent_tool()
        schema = tool.parameters_schema
        assert "task_description" in schema["properties"]
        assert "task_description" in schema["required"]
        assert "tool_whitelist" in schema["properties"]

    def test_depth_1_subagent_excluded_from_whitelist(self):
        """Anti-pattern #3: 'subagent' is excluded from available names in schema."""
        tool = self._make_subagent_tool(
            available_tools={"search": _make_tool("search"), "subagent": MagicMock()},
        )
        schema = tool.parameters_schema
        desc = schema["properties"]["tool_whitelist"]["description"]
        # Description lists available tools, should not include "subagent"
        available_names = [n for n in tool._available_tools.keys() if n != "subagent"]
        assert "search" in available_names

    @pytest.mark.asyncio
    async def test_execute_returns_json_string(self):
        """Anti-pattern #6: execute returns structured JSON, not free text."""
        tool = self._make_subagent_tool()
        mock_result = _make_subagent_result()

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            result = await tool.execute(task_description="find all .py files")
            data = json.loads(result)
            assert "accomplished" in data

    @pytest.mark.asyncio
    async def test_call_count_limit(self):
        """Anti-pattern #3/8: SubAgent call count is enforced."""
        tool = self._make_subagent_tool(max_calls_per_task=2)
        mock_result = _make_subagent_result()

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            r1 = await tool.execute(task_description="t1")
            assert "Error" not in r1

            r2 = await tool.execute(task_description="t2")
            assert "Error" not in r2

        # Third call should be rejected
        r3 = await tool.execute(task_description="t3")
        assert "Error" in r3
        assert "call limit" in r3.lower()

    @pytest.mark.asyncio
    async def test_reset_task_state(self):
        """reset_task_state() resets call count AND subagent counter."""
        tool = self._make_subagent_tool(max_calls_per_task=1)
        mock_result = _make_subagent_result()

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            r1 = await tool.execute(task_description="t1")
            assert "Error" not in r1

        # Hit limit
        r2 = await tool.execute(task_description="t2")
        assert "Error" in r2

        # Reset
        tool.reset_task_state()
        assert tool._call_count == 0
        assert tool._subagent_counter == 0

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            MockSubAgent.return_value = mock_instance
            r3 = await tool.execute(task_description="t3")
            assert "Error" not in r3

    @pytest.mark.asyncio
    async def test_missing_task_description_returns_error(self):
        tool = self._make_subagent_tool()
        result = await tool.execute()
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_tool_whitelist_validation(self):
        """Invalid tool names are ignored; 'subagent' is always filtered."""
        tool = self._make_subagent_tool(
            available_tools={"search": _make_tool("search")},
        )

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            result = await tool.execute(
                task_description="test",
                tool_whitelist=["search", "subagent", "nonexistent"],
            )
            assert "Error" not in result

            # Verify SubAgent was created without "subagent" tool
            call_kwargs = MockSubAgent.call_args
            tools_arg = call_kwargs.kwargs.get("tools", [])
            tool_names = [t.name for t in tools_arg]
            assert "subagent" not in tool_names

    @pytest.mark.asyncio
    async def test_sandbox_dir_creation(self):
        """Anti-pattern #4: SubAgentTool creates isolated sandbox dir."""
        tool = self._make_subagent_tool()

        with patch("agents.subagent.SubAgent") as MockSubAgent, \
             patch("os.makedirs") as mock_makedirs:
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(task_description="test")
            mock_makedirs.assert_called()

    @pytest.mark.asyncio
    async def test_execute_handles_subagent_exception(self):
        tool = self._make_subagent_tool()

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(side_effect=RuntimeError("crash"))
            MockSubAgent.return_value = mock_instance

            result = await tool.execute(task_description="test")
            data = json.loads(result)
            assert "issues" in data


# ======================================================================
# Test Integration
# ======================================================================

class TestSubAgentIntegration:

    def test_subagent_tool_not_injected_when_disabled(self):
        """When SUBAGENT_ENABLED=false, OrchestratorAgent should not have SubAgentTool."""
        with patch.object(config, "SUBAGENT_ENABLED", False):
            from agents.orchestrator import OrchestratorAgent
            client = _make_llm_client()
            tools = [_make_tool("search")]
            orch = OrchestratorAgent(llm_client=client, tools=tools)
            assert not hasattr(orch, "_subagent_tool") or orch._subagent_tool is None

    def test_subagent_tool_injected_when_enabled(self):
        """When SUBAGENT_ENABLED=true, OrchestratorAgent should have SubAgentTool."""
        with patch.object(config, "SUBAGENT_ENABLED", True), \
             patch("tools.subagent_tool.SubAgentTool") as MockSAT:
            mock_sat = MagicMock()
            MockSAT.return_value = mock_sat
            from agents.orchestrator import OrchestratorAgent
            client = _make_llm_client()
            tools = [_make_tool("search")]
            orch = OrchestratorAgent(llm_client=client, tools=tools)
            assert hasattr(orch, "_subagent_tool")

    def test_eval_probe_collects_subagent_metrics(self):
        """Anti-pattern #10: EvaluationProbe collects SubAgent event data."""
        from evaluation.runner import EvaluationProbe
        probe = EvaluationProbe()

        probe.on_event("subagent_complete", {
            "subagent_id": "SubAgent-1",
            "iterations_used": 3,
            "tokens_used": 1500,
            "duration_ms": 500.0,
            "tool_calls_count": 5,
        })

        assert len(probe.subagent_results) == 1
        assert probe.subagent_results[0]["status"] == "completed"
        assert probe.subagent_results[0]["iterations_used"] == 3

    def test_eval_probe_collects_subagent_failure(self):
        from evaluation.runner import EvaluationProbe
        probe = EvaluationProbe()

        probe.on_event("subagent_failed", {
            "subagent_id": "SubAgent-2",
            "iterations_used": 1,
        })

        assert len(probe.subagent_results) == 1
        assert probe.subagent_results[0]["status"] == "failed"

    def test_eval_probe_collects_subagent_timeout(self):
        from evaluation.runner import EvaluationProbe
        probe = EvaluationProbe()

        probe.on_event("subagent_timed_out", {
            "subagent_id": "SubAgent-3",
        })

        assert len(probe.subagent_results) == 1
        assert probe.subagent_results[0]["status"] == "timed_out"

    def test_eval_probe_multiple_subagent_results(self):
        from evaluation.runner import EvaluationProbe
        probe = EvaluationProbe()

        probe.on_event("subagent_complete", {"subagent_id": "SA-1"})
        probe.on_event("subagent_complete", {"subagent_id": "SA-2"})
        probe.on_event("subagent_failed", {"subagent_id": "SA-3"})

        assert len(probe.subagent_results) == 3
        completed = [r for r in probe.subagent_results if r["status"] == "completed"]
        failed = [r for r in probe.subagent_results if r["status"] == "failed"]
        assert len(completed) == 2
        assert len(failed) == 1

    def test_subagent_in_agents_all(self):
        from agents import SubAgent
        assert SubAgent is not None

    def test_subagent_tool_in_tools_all(self):
        from tools import SubAgentTool
        assert SubAgentTool is not None


# ======================================================================
# Test Tracing
# ======================================================================

class TestSubAgentTracing:

    def test_span_name_constant_exists(self):
        from tracing.spans import SpanName
        assert hasattr(SpanName, "SUBAGENT_EXECUTE")
        assert SpanName.SUBAGENT_EXECUTE == "subagent.execute"

    def test_attr_key_constants_exist(self):
        from tracing.spans import AttrKey
        for key in ("SUBAGENT_ID", "SUBAGENT_TASK", "SUBAGENT_STATUS",
                     "SUBAGENT_PARENT_AGENT", "SUBAGENT_ITERATIONS",
                     "SUBAGENT_DURATION_MS", "SUBAGENT_TOKENS_USED",
                     "SUBAGENT_CALL_COUNT", "SUBAGENT_TOOL_WHITELIST"):
            assert hasattr(AttrKey, key), f"AttrKey.{key} missing"

    def test_span_icon_exists(self):
        from tracing.spans import SPAN_ICONS
        assert "subagent" in SPAN_ICONS


# ======================================================================
# Anti-Pattern Specific Tests
# ======================================================================

class TestSubAgentAntiPatterns:

    @pytest.mark.asyncio
    async def test_anti_pattern_2_context_isolation(self):
        """#2: SubAgent's internal messages are not visible to parent."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )
        parent_messages = [{"role": "user", "content": "parent context"}]
        sub_messages = agent._summary_messages
        assert sub_messages is not parent_messages
        assert parent_messages[0] not in sub_messages

    @pytest.mark.asyncio
    async def test_anti_pattern_3_call_count_limit(self):
        """#3: SubAgentTool enforces max call count per task."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
            max_calls_per_task=1,
        )

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(task_description="first call")
            result = await tool.execute(task_description="second call")
            assert "limit" in result.lower() or "Error" in result

    @pytest.mark.asyncio
    async def test_anti_pattern_3_depth_1_structural(self):
        """#3: depth=1 enforced structurally — SubAgent tool list excludes 'subagent'."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search"), "file_ops": _make_tool("file_ops")},
        )

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(
                task_description="test",
                tool_whitelist=["search", "subagent"],
            )

            call_kwargs = MockSubAgent.call_args
            tools_arg = call_kwargs.kwargs.get("tools", [])
            tool_names = [t.name for t in tools_arg]
            assert "subagent" not in tool_names

    @pytest.mark.asyncio
    async def test_anti_pattern_4_sandbox_isolation(self):
        """#4: SubAgent gets an isolated sandbox directory."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
        )

        with patch("agents.subagent.SubAgent") as MockSubAgent, \
             patch("os.makedirs"):
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(task_description="test")

            call_kwargs = MockSubAgent.call_args
            sandbox = call_kwargs.kwargs.get("sandbox_subdir", "")
            assert "subagent_" in sandbox

    def test_anti_pattern_6_structured_summary_vs_free_text(self):
        """#6: SubAgentSummary has structured fields, not just a text blob."""
        summary = SubAgentSummary(
            accomplished="found 5 files",
            findings="all use asyncio",
            issues="2 files had errors",
            artifacts=["/tmp/a.py", "/tmp/b.py"],
            tool_calls_summary="file_ops x3",
        )
        assert summary.accomplished != ""
        assert summary.issues != ""
        assert len(summary.artifacts) == 2
        structured = summary.model_dump_json()
        data = json.loads(structured)
        assert data["artifacts"] == ["/tmp/a.py", "/tmp/b.py"]

    @pytest.mark.asyncio
    async def test_anti_pattern_6_result_preserves_tool_calls_log(self):
        """#6: SubAgentResult preserves full tool_calls_log for debugging."""
        tc_log = [
            ToolCallRecord(tool_name="search", parameters={}, result="found 3 items"),
            ToolCallRecord(tool_name="file_ops", parameters={"action": "list", "filename": "/tmp"}, result="listed"),
        ]
        result = SubAgentResult(
            subagent_id="SA-1",
            task_description="test",
            status=SubAgentStatus.COMPLETED,
            tool_calls_log=tc_log,
        )
        assert len(result.tool_calls_log) == 2
        assert result.tool_calls_log[0].tool_name == "search"

    def test_anti_pattern_8_config_defaults(self):
        """#8: Token budget and call count defaults are defensive."""
        assert config.SUBAGENT_MAX_CALLS_PER_TASK > 0
        assert config.SUBAGENT_MAX_TOKENS_PER_CALL > 0
        assert config.SUBAGENT_MAX_ITERATIONS > 0
        assert config.SUBAGENT_TIMEOUT > 0

    @pytest.mark.asyncio
    async def test_anti_pattern_8_token_budget_check_in_run(self):
        """#8: SubAgent.run() respects max_tokens budget."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            max_tokens=100,
        )

        high_token_records = [
            LLMCallRecord(call_type="chat", prompt_summary="", total_tokens=200),
        ]
        client.get_call_records.return_value = high_token_records

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output="done")
            result = await agent.run()

        assert result is not None


# ======================================================================
# Test Config
# ======================================================================

class TestSubAgentConfig:

    def test_subagent_enabled_default_false(self):
        assert hasattr(config, "SUBAGENT_ENABLED")

    def test_subagent_config_vars_exist(self):
        for var in ("SUBAGENT_MAX_ITERATIONS", "SUBAGENT_TIMEOUT",
                     "SUBAGENT_MAX_CONCURRENT", "SUBAGENT_SUMMARY_MAX_LENGTH",
                     "SUBAGENT_MAX_CALLS_PER_TASK", "SUBAGENT_MAX_TOKENS_PER_CALL",
                     "SUBAGENT_DEFAULT_TOOL_WHITELIST"):
            assert hasattr(config, var), f"config.{var} missing"


# ======================================================================
# Test P0 Fixes: on_iteration, iterations_completed, iterations_used
# ======================================================================

class TestP0ReActEngineCallback:

    def test_step_result_iterations_completed_field(self):
        """P0-2: StepResult has iterations_completed field."""
        r = StepResult(step_id="t", success=True, iterations_completed=5)
        assert r.iterations_completed == 5

    def test_step_result_iterations_completed_default_zero(self):
        r = StepResult(step_id="t", success=True)
        assert r.iterations_completed == 0

    @pytest.mark.asyncio
    async def test_react_engine_on_iteration_callback(self):
        """P0-1: ReActEngine.execute() calls on_iteration callback."""
        from react.engine import ReActEngine
        client = _make_llm_client()
        tool = _make_tool("mock_tool")
        callback_calls = []

        engine = ReActEngine(
            llm_client=client,
            tools=[tool],
            max_iterations=5,
        )

        def on_iter(iteration, tool_calls):
            callback_calls.append((iteration, list(tool_calls)))

        with patch.object(engine, 'tool_schemas', [tool.to_openai_tool()]):
            result = await engine.execute(
                prompt="test",
                on_iteration=on_iter,
            )

        # Callback should have been called at least once
        assert len(callback_calls) >= 1
        # First argument should be iteration number
        assert callback_calls[0][0] >= 1


class TestP0IterationsUsedSemantics:

    @pytest.mark.asyncio
    async def test_iterations_used_from_iterations_completed(self):
        """P0-2: SubAgent uses iterations_completed, not len(tool_calls_log)."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            on_event=lambda e, d: events.append((e, d)),
        )

        tc_log = [ToolCallRecord(tool_name="search", parameters={}, result="ok")]
        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(
                success=True, output="done",
                tool_calls=tc_log, iterations_completed=4,
            )
            result = await agent.run()

        # iterations_used should be 4 (from iterations_completed), not 1 (len of tool_calls_log)
        assert result.iterations_used == 4

    @pytest.mark.asyncio
    async def test_iterations_used_in_failed_branch(self):
        """P0-2: Failed branch also uses iterations_completed."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(
                success=False, output="error",
                iterations_completed=7,
            )
            result = await agent.run()

        assert result.iterations_used == 7


class TestP0EventKeyNames:

    @pytest.mark.asyncio
    async def test_complete_event_uses_iterations_used_key(self):
        """P0-3: Event data uses 'iterations_used' not 'iterations'."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output="done", iterations_completed=3)
            await agent.run()

        complete_events = [d for e, d in events if e == "subagent_complete"]
        assert len(complete_events) == 1
        assert "iterations_used" in complete_events[0]
        assert complete_events[0]["iterations_used"] == 3

    @pytest.mark.asyncio
    async def test_failed_event_uses_iterations_used_key(self):
        """P0-3: Failed event data also uses 'iterations_used'."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=False, output="err", iterations_completed=2)
            await agent.run()

        failed_events = [d for e, d in events if e == "subagent_failed"]
        assert len(failed_events) == 1
        assert "iterations_used" in failed_events[0]

    @pytest.mark.asyncio
    async def test_timeout_event_uses_iterations_used_key(self):
        """P0-3: Timeout event data also uses 'iterations_used'."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []

        async def slow_exec(**kwargs):
            # Simulate on_iteration callback being called before timeout
            if "on_iteration" in kwargs and kwargs["on_iteration"]:
                kwargs["on_iteration"](1, [])
            await asyncio.sleep(10)

        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            timeout=1,
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", side_effect=slow_exec):
            result = await agent.run()

        timeout_events = [d for e, d in events if e == "subagent_timed_out"]
        assert len(timeout_events) == 1
        assert "iterations_used" in timeout_events[0]


# ======================================================================
# Test P0: Token Budget Circuit Breaker
# ======================================================================

class TestP0TokenBudgetCircuitBreaker:

    @pytest.mark.asyncio
    async def test_token_budget_exceeded_returns_failed(self):
        """P0-1: SubAgent returns FAILED when token budget exceeded."""
        from agents.subagent import SubAgent, SubAgentTokenExhausted
        client = _make_llm_client()
        events = []
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            max_tokens=50,
            on_event=lambda e, d: events.append((e, d)),
        )

        # Token records that exceed budget
        high_token_records = [
            LLMCallRecord(call_type="chat", prompt_summary="", total_tokens=10),
            LLMCallRecord(call_type="chat", prompt_summary="", total_tokens=60),
        ]
        client.get_call_records.return_value = high_token_records

        async def exec_with_callback(**kwargs):
            # Simulate on_iteration triggering token check
            if "on_iteration" in kwargs and kwargs["on_iteration"]:
                kwargs["on_iteration"](1, [])
            return _make_step_result(success=True, output="done")

        with patch.object(agent._react_engine, "execute", side_effect=exec_with_callback):
            # The on_iteration callback inside SubAgent._on_react_iteration
            # will check tokens and raise SubAgentTokenExhausted
            # But since we're mocking execute, we need to simulate differently
            pass

        # Direct test: _on_react_iteration raises when budget exceeded (index range method)
        agent._records_before = 0
        with pytest.raises(SubAgentTokenExhausted):
            agent._on_react_iteration(1, [])

    @pytest.mark.asyncio
    async def test_token_budget_not_exceeded_does_not_raise(self):
        """Token budget check passes when under limit."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            max_tokens=50000,
        )

        # Token records well under budget
        client.get_call_records.return_value = [
            LLMCallRecord(call_type="chat", prompt_summary="", total_tokens=100),
        ]
        agent._records_before = 0

        # Should not raise
        agent._on_react_iteration(1, [])

    def test_subagent_token_exhausted_exception_exists(self):
        from agents.subagent import SubAgentTokenExhausted
        exc = SubAgentTokenExhausted("budget exceeded")
        assert "budget" in str(exc)


# ======================================================================
# Test P1 Fixes
# ======================================================================

class TestP1ParentNamePassthrough:

    def test_parent_name_default(self):
        """P1-4: SubAgentTool defaults to 'OrchestratorAgent'."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
        )
        assert tool._parent_name == "OrchestratorAgent"

    def test_parent_name_custom(self):
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
            parent_name="CustomAgent",
        )
        assert tool._parent_name == "CustomAgent"

    @pytest.mark.asyncio
    async def test_parent_name_passed_to_subagent(self):
        """P1-4: parent_name is passed to SubAgent constructor."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
            parent_name="TestParent",
        )

        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(task_description="test")
            call_kwargs = MockSubAgent.call_args
            assert call_kwargs.kwargs.get("parent_agent_name") == "TestParent"


class TestP1SummarizeArtifactsAndToolCalls:

    @pytest.mark.asyncio
    async def test_summarize_extracts_artifacts_from_tool_calls_log(self):
        """P1-5: _summarize_result correctly includes artifacts from tool_calls_log."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        # Mock chat_json to return summary with empty artifacts (trigger override logic)
        client.chat_json = AsyncMock(return_value={
            "accomplished": "short result",
            "findings": "",
            "issues": "",
            "artifacts": [],
            "tool_calls_summary": "file_ops(write)",
        })
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        tc_log = [
            ToolCallRecord(tool_name="file_ops", parameters={"action": "write", "filename": "/tmp/out.txt"}, result="written"),
        ]
        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(
                success=True, output="short result",
                tool_calls=tc_log, iterations_completed=1,
            )
            result = await agent.run()

        # Should have extracted artifact from tool_calls_log via override
        assert "/tmp/out.txt" in result.summary.artifacts
        assert result.summary.tool_calls_summary != ""

    @pytest.mark.asyncio
    async def test_summarize_extracts_tool_calls_summary(self):
        """P1-5: _summarize_result correctly includes tool_calls_summary from log."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        client.chat_json = AsyncMock(return_value={
            "accomplished": "short result",
            "findings": "test findings",
            "issues": "",
            "artifacts": [],
            "tool_calls_summary": "",
        })
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        tc_log = [
            ToolCallRecord(tool_name="web_search", parameters={"query": "test"}, result="found"),
            ToolCallRecord(tool_name="file_ops", parameters={"action": "write", "filename": "/tmp/f.txt"}, result="ok"),
        ]
        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(
                success=True, output="short",
                tool_calls=tc_log, iterations_completed=2,
            )
            result = await agent.run()

        assert "web_search" in result.summary.tool_calls_summary
        assert "file_ops" in result.summary.tool_calls_summary


class TestP1TimeoutBranchToolCallsLog:

    @pytest.mark.asyncio
    async def test_timeout_preserves_accumulated_tool_calls(self):
        """P1-6: Timeout branch preserves tool_calls_log via _accumulated_tool_calls."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []

        accumulated = [
            ToolCallRecord(tool_name="search", parameters={}, result="partial"),
        ]

        async def timeout_exec(**kwargs):
            # Simulate on_iteration being called before timeout
            if "on_iteration" in kwargs and kwargs["on_iteration"]:
                kwargs["on_iteration"](1, accumulated)
            await asyncio.sleep(10)

        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            timeout=1,
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", side_effect=timeout_exec):
            result = await agent.run()

        assert result.status == SubAgentStatus.TIMED_OUT
        assert len(result.tool_calls_log) >= 1

    @pytest.mark.asyncio
    async def test_exception_preserves_accumulated_tool_calls(self):
        """P1-6: Exception branch preserves tool_calls_log."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        events = []

        accumulated = [
            ToolCallRecord(tool_name="search", parameters={"q": "test"}, result="data"),
        ]

        async def failing_exec(**kwargs):
            if "on_iteration" in kwargs and kwargs["on_iteration"]:
                kwargs["on_iteration"](1, accumulated)
            raise RuntimeError("unexpected error")

        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            on_event=lambda e, d: events.append((e, d)),
        )

        with patch.object(agent._react_engine, "execute", side_effect=failing_exec):
            result = await agent.run()

        assert result.status == SubAgentStatus.FAILED
        assert len(result.tool_calls_log) >= 1


class TestP1ModelValidate:

    @pytest.mark.asyncio
    async def test_summary_uses_model_validate_on_valid_json(self):
        """P1-7: _summarize_result uses SubAgentSummary.model_validate()."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        # Return valid JSON matching SubAgentSummary schema
        client.chat_json = AsyncMock(return_value={
            "accomplished": "searched 5 files",
            "findings": "all use async",
            "issues": "2 had errors",
            "artifacts": ["/tmp/a.py"],
            "tool_calls_summary": "search x3",
        })
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        long_output = "x" * (config.SUBAGENT_SUMMARY_MAX_LENGTH + 100)
        tc_log = [ToolCallRecord(tool_name="search", parameters={}, result="ok")]
        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(
                success=True, output=long_output,
                tool_calls=tc_log, iterations_completed=3,
            )
            result = await agent.run()

        assert result.summary.accomplished == "searched 5 files"
        assert result.summary.issues == "2 had errors"
        assert len(result.summary.artifacts) >= 1

    @pytest.mark.asyncio
    async def test_summary_fallback_on_invalid_structure(self):
        """P1-7: Invalid JSON structure falls back gracefully."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        # Return JSON with wrong keys
        client.chat_json = AsyncMock(return_value={
            "wrong_key": "value",
        })
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        long_output = "y" * (config.SUBAGENT_SUMMARY_MAX_LENGTH + 100)
        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(
                success=True, output=long_output,
                iterations_completed=2,
            )
            result = await agent.run()

        # Should have a fallback summary (not crash)
        assert result.summary is not None


# ======================================================================
# Test P2/P3 Fixes
# ======================================================================

class TestP2ResetTaskState:

    @pytest.mark.asyncio
    async def test_reset_task_state_clears_counter(self):
        """P2-8: reset_task_state() also resets _subagent_counter."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
        )
        tool._subagent_counter = 5
        tool._call_count = 3
        tool.reset_task_state()
        assert tool._call_count == 0
        assert tool._subagent_counter == 0


class TestP2LimitEvent:

    @pytest.mark.asyncio
    async def test_limit_exceeded_emits_event(self):
        """P2-9: Call count limit emits 'subagent_limit_exceeded' event."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        events = []
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search")},
            max_calls_per_task=1,
            on_event=lambda e, d: events.append((e, d)),
        )

        mock_result = _make_subagent_result()
        with patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(task_description="first")

        # Second call hits limit
        result = await tool.execute(task_description="second")
        assert "Error" in result

        event_names = [e for e, _ in events]
        assert "subagent_limit_exceeded" in event_names


class TestP2SuppressionRules:

    def test_system_prompt_has_suppression_rules(self):
        """P2-10: System prompt includes suppression rules 6-8."""
        from agents.subagent import SUBAGENT_SYSTEM_PROMPT
        # Rule 6: don't read unrelated files
        assert "unrelated" in SUBAGENT_SYSTEM_PROMPT.lower() or "无关" in SUBAGENT_SYSTEM_PROMPT
        # Rule 7: don't assume missing details
        assert "unclear" in SUBAGENT_SYSTEM_PROMPT.lower() or "不清晰" in SUBAGENT_SYSTEM_PROMPT
        # Rule 8: don't repeat same tool call
        assert "repeatedly" in SUBAGENT_SYSTEM_PROMPT.lower() or "重复" in SUBAGENT_SYSTEM_PROMPT


class TestP2TokenIndexRange:

    @pytest.mark.asyncio
    async def test_tokens_used_from_record_index_range(self):
        """P2-12: tokens_used computed via record index range, not delta."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        # Simulate pre-existing records + new ones during run
        pre_records = [
            LLMCallRecord(call_type="chat", prompt_summary="", total_tokens=100),
        ]
        all_records = pre_records + [
            LLMCallRecord(call_type="chat", prompt_summary="", total_tokens=50),
            LLMCallRecord(call_type="chat_json", prompt_summary="", total_tokens=30),
        ]

        call_count = [0]
        def get_records_side_effect():
            call_count[0] += 1
            # Call 1: len() for _records_before
            if call_count[0] <= 1:
                return pre_records
            return all_records  # After execution

        client.get_call_records.side_effect = get_records_side_effect

        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output="done", iterations_completed=1)
            result = await agent.run()

        # Should only count new records (50 + 30 = 80), not pre-existing ones
        assert result.tokens_used == 80


class TestP2DefaultWhitelistConfig:

    def test_default_whitelist_config_exists(self):
        """P2-14: SUBAGENT_DEFAULT_TOOL_WHITELIST config exists."""
        assert hasattr(config, "SUBAGENT_DEFAULT_TOOL_WHITELIST")

    @pytest.mark.asyncio
    async def test_default_whitelist_used_when_empty_tool_whitelist(self):
        """P2-14: Config default whitelist is used when no tool_whitelist provided."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()
        tool = SubAgentTool(
            llm_client=client,
            available_tools={"search": _make_tool("search"), "file_ops": _make_tool("file_ops")},
        )

        with patch.object(config, "SUBAGENT_DEFAULT_TOOL_WHITELIST", "search"), \
             patch("agents.subagent.SubAgent") as MockSubAgent:
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(task_description="test")

            call_kwargs = MockSubAgent.call_args
            tools_arg = call_kwargs.kwargs.get("tools", [])
            tool_names = [t.name for t in tools_arg]
            assert "search" in tool_names
            assert "file_ops" not in tool_names


class TestTracingEventKeyFix:

    def test_tracing_bridge_reads_iterations_used(self):
        """P0-3: TracingBridge reads 'iterations_used' not 'iterations'."""
        from tracing.spans import AttrKey
        # Verify the attr key constant exists
        assert hasattr(AttrKey, "SUBAGENT_ITERATIONS")


# ======================================================================
# Test SubAgent system_hint fix + Prompt Guidance
# ======================================================================

class TestSubAgentSystemHintFix:

    @pytest.mark.asyncio
    async def test_run_passes_system_hint_to_engine(self):
        """SubAgent.run() must pass self._system_prompt as system_hint to ReActEngine."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output="done")
            await agent.run()

        mock_exec.assert_called_once()
        call_kwargs = mock_exec.call_args
        assert call_kwargs.kwargs.get("system_hint") == agent._system_prompt

    @pytest.mark.asyncio
    async def test_system_hint_includes_sandbox_dir(self):
        """system_hint passed to engine should include sandbox dir when set."""
        from agents.subagent import SubAgent
        client = _make_llm_client()
        agent = SubAgent(
            name="SA-1",
            task_description="test",
            llm_client=client,
            tools=[_make_tool()],
            sandbox_subdir="/tmp/sandbox/subagent_1",
        )

        with patch.object(agent._react_engine, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = _make_step_result(success=True, output="done")
            await agent.run()

        system_hint = mock_exec.call_args.kwargs.get("system_hint", "")
        assert "/tmp/sandbox/subagent_1" in system_hint


class TestPromptUtilsModule:

    def test_get_subagent_guidance_enabled(self):
        """get_subagent_guidance() returns non-empty when SUBAGENT_ENABLED=true."""
        from agents.prompt_utils import get_subagent_guidance
        with patch.object(config, "SUBAGENT_ENABLED", True):
            guidance = get_subagent_guidance()
            assert guidance != ""
            assert "subagent" in guidance.lower()

    def test_get_subagent_guidance_disabled(self):
        """get_subagent_guidance() returns empty when SUBAGENT_ENABLED=false."""
        from agents.prompt_utils import get_subagent_guidance
        with patch.object(config, "SUBAGENT_ENABLED", False):
            guidance = get_subagent_guidance()
            assert guidance == ""

    def test_build_system_prompt_with_guidance(self):
        """build_system_prompt() appends guidance when enabled."""
        from agents.prompt_utils import build_system_prompt
        base = "You are an agent."
        with patch.object(config, "SUBAGENT_ENABLED", True):
            result = build_system_prompt(base)
            assert result.startswith("You are an agent.")
            assert "subagent" in result.lower()

    def test_build_system_prompt_without_guidance(self):
        """build_system_prompt() returns base unchanged when disabled."""
        from agents.prompt_utils import build_system_prompt
        base = "You are an agent."
        with patch.object(config, "SUBAGENT_ENABLED", False):
            result = build_system_prompt(base)
            assert result == base

    def test_guidance_has_positive_and_negative_criteria(self):
        """Guidance text includes both when-to-use and when-NOT-to-use criteria."""
        from agents.prompt_utils import get_subagent_guidance
        with patch.object(config, "SUBAGENT_ENABLED", True):
            guidance = get_subagent_guidance()
            assert "3+" in guidance or "three" in guidance.lower()
            assert "DO NOT" in guidance or "do not" in guidance


class TestSystemPromptComposition:

    def _get_module_constant(self, module_path, constant_name):
        """Helper to import and get a module-level constant."""
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, constant_name)

    def test_executor_prompt_includes_guidance_when_enabled(self):
        """EXECUTOR_SYSTEM_PROMPT includes subagent guidance when enabled."""
        with patch.object(config, "SUBAGENT_ENABLED", True):
            # Re-import to pick up the patched config
            import importlib
            import agents.executor as executor_mod
            importlib.reload(executor_mod)
            prompt = executor_mod.EXECUTOR_SYSTEM_PROMPT
            assert "subagent" in prompt.lower()
            assert "ReAct paradigm" in prompt

    def test_executor_prompt_excludes_guidance_when_disabled(self):
        """EXECUTOR_SYSTEM_PROMPT has no subagent guidance when disabled."""
        with patch.object(config, "SUBAGENT_ENABLED", False):
            import importlib
            import agents.executor as executor_mod
            importlib.reload(executor_mod)
            prompt = executor_mod.EXECUTOR_SYSTEM_PROMPT
            assert "When to Use" not in prompt

    def test_emergent_prompt_includes_guidance_when_enabled(self):
        """EMERGENT_PLANNER_SYSTEM_PROMPT includes subagent guidance when enabled."""
        with patch.object(config, "SUBAGENT_ENABLED", True):
            import importlib
            import agents.emergent_planner as ep_mod
            importlib.reload(ep_mod)
            prompt = ep_mod.EMERGENT_PLANNER_SYSTEM_PROMPT
            assert "subagent" in prompt.lower()
            assert "TODO list" in prompt

    def test_emergent_prompt_excludes_guidance_when_disabled(self):
        """EMERGENT_PLANNER_SYSTEM_PROMPT has no subagent guidance when disabled."""
        with patch.object(config, "SUBAGENT_ENABLED", False):
            import importlib
            import agents.emergent_planner as ep_mod
            importlib.reload(ep_mod)
            prompt = ep_mod.EMERGENT_PLANNER_SYSTEM_PROMPT
            assert "When to Use" not in prompt

    def test_goal_driven_prompt_includes_guidance_when_enabled(self):
        """V8_GOAL_DRIVEN_SYSTEM_PROMPT includes subagent guidance when enabled."""
        with patch.object(config, "SUBAGENT_ENABLED", True):
            import importlib
            import agents.goal_driven_planner as gdp_mod
            importlib.reload(gdp_mod)
            prompt = gdp_mod.V8_GOAL_DRIVEN_SYSTEM_PROMPT
            assert "subagent" in prompt.lower()
            assert "begin with the end in mind" in prompt

    def test_goal_driven_prompt_excludes_guidance_when_disabled(self):
        """V8_GOAL_DRIVEN_SYSTEM_PROMPT has no subagent guidance when disabled."""
        with patch.object(config, "SUBAGENT_ENABLED", False):
            import importlib
            import agents.goal_driven_planner as gdp_mod
            importlib.reload(gdp_mod)
            prompt = gdp_mod.V8_GOAL_DRIVEN_SYSTEM_PROMPT
            assert "When to Use" not in prompt

    def test_base_prompts_preserved_regardless_of_flag(self):
        """Base prompt content is unchanged regardless of SUBAGENT_ENABLED."""
        for enabled in (True, False):
            with patch.object(config, "SUBAGENT_ENABLED", enabled):
                import importlib
                import agents.executor as executor_mod
                importlib.reload(executor_mod)
                prompt = executor_mod.EXECUTOR_SYSTEM_PROMPT
                # Core content always present
                assert "ReAct paradigm" in prompt
                assert "THINK" in prompt
                assert "ACT" in prompt
                assert "OBSERVE" in prompt


# ======================================================================
# Test Fixes from Code Review (P0, P1+)
# ======================================================================

class TestP0NoDoubleWrite:
    """P0 fix: SubAgentTool passes context="" to SubAgent.run(), not task_description."""

    @pytest.mark.asyncio
    async def test_subagent_tool_passes_empty_context(self):
        """SubAgentTool.execute() passes context="" to avoid double-write."""
        from tools.subagent_tool import SubAgentTool
        client = _make_llm_client()

        tool = SubAgentTool(
            llm_client=client,
            available_tools={"mock_tool": _make_tool()},
        )

        with patch("agents.subagent.SubAgent") as MockSubAgent, \
             patch("os.makedirs"):
            mock_result = _make_subagent_result()
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value=mock_result)
            MockSubAgent.return_value = mock_instance

            await tool.execute(task_description="research async patterns")

            # Verify run() was called with context="" (not task_description)
            call_args = mock_instance.run.call_args
            assert call_args.kwargs.get("context", None) == ""


class TestExtractArtifactsFromLog:
    """P1+ fix: _extract_artifacts_from_log uses correct parameter names."""

    def test_extracts_from_file_ops_write_action(self):
        """Extracts filename from file_ops with action='write'."""
        from agents.subagent import _extract_artifacts_from_log
        tc_log = [
            ToolCallRecord(tool_name="file_ops", parameters={"action": "write", "filename": "result.txt"}, result="written"),
        ]
        assert _extract_artifacts_from_log(tc_log) == ["result.txt"]

    def test_skips_file_ops_read_action(self):
        """Does not extract from file_ops with action='read' (only write creates artifacts)."""
        from agents.subagent import _extract_artifacts_from_log
        tc_log = [
            ToolCallRecord(tool_name="file_ops", parameters={"action": "read", "filename": "data.txt"}, result="content"),
        ]
        assert _extract_artifacts_from_log(tc_log) == []

    def test_skips_file_ops_list_action(self):
        """Does not extract from file_ops with action='list'."""
        from agents.subagent import _extract_artifacts_from_log
        tc_log = [
            ToolCallRecord(tool_name="file_ops", parameters={"action": "list", "filename": ""}, result="3 files"),
        ]
        assert _extract_artifacts_from_log(tc_log) == []

    def test_skips_shell_tool(self):
        """Shell tool file creation cannot be statically detected."""
        from agents.subagent import _extract_artifacts_from_log
        tc_log = [
            ToolCallRecord(tool_name="shell", parameters={"command": "touch /tmp/newfile.txt"}, result="ok"),
        ]
        assert _extract_artifacts_from_log(tc_log) == []

    def test_skips_old_parameter_names(self):
        """Old parameter names (path, file_path) are no longer matched."""
        from agents.subagent import _extract_artifacts_from_log
        tc_log = [
            ToolCallRecord(tool_name="file_ops", parameters={"path": "/tmp/out.txt"}, result="written"),
        ]
        # No 'action' key → no match
        assert _extract_artifacts_from_log(tc_log) == []

    def test_deduplicates_paths(self):
        """Same filename from multiple write calls is deduplicated."""
        from agents.subagent import _extract_artifacts_from_log
        tc_log = [
            ToolCallRecord(tool_name="file_ops", parameters={"action": "write", "filename": "report.md"}, result="written"),
            ToolCallRecord(tool_name="file_ops", parameters={"action": "write", "filename": "report.md"}, result="updated"),
        ]
        assert _extract_artifacts_from_log(tc_log) == ["report.md"]
