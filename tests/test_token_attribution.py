"""
Wave-6 tests: per-caller token attribution.

Locks in the contract:
  - LLMCallRecord schema accepts (and back-compat-loads) caller_tag.
  - LLMClient._record_call writes caller_tag through to the record.
  - BaseAgent.think_* defaults caller_tag to self.name; explicit kwargs wins.
  - Orchestrator._finalize_token_usage builds a by_caller view that sums to
    the same totals as by_engine.
  - Records without caller_tag fall into the "unknown" bucket (visibility
    over silent loss).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from schema import LLMCallRecord, TokenUsage, TokenUsageSummary


# ----------------------------------------------------------------------
# LLMCallRecord schema: backward compatibility
# ----------------------------------------------------------------------


class TestLLMCallRecordSchema:
    def test_default_caller_tag_is_empty(self):
        record = LLMCallRecord(call_type="chat", total_tokens=30, engine="m")
        assert record.caller_tag == ""

    def test_explicit_caller_tag_is_preserved(self):
        record = LLMCallRecord(
            call_type="chat", total_tokens=30, engine="m",
            caller_tag="SubAgent-1",
        )
        assert record.caller_tag == "SubAgent-1"

    def test_old_json_without_caller_tag_validates(self):
        """Old trace JSON files written before Wave-6 must still load."""
        old_payload = {
            "call_type": "chat_with_tools",
            "prompt_summary": "test",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "engine": "deepseek-chat",
        }
        record = LLMCallRecord.model_validate(old_payload)
        assert record.caller_tag == ""
        assert record.total_tokens == 150

    def test_token_usage_summary_default_by_caller_is_empty(self):
        summary = TokenUsageSummary()
        assert summary.by_caller == {}
        # by_engine still defaults the same
        assert summary.by_engine == {}


# ----------------------------------------------------------------------
# LLMClient._record_call wires caller_tag through
# ----------------------------------------------------------------------


class TestRecordCallWiring:
    def _make_usage(self, prompt=10, completion=20, total=30):
        u = MagicMock()
        u.prompt_tokens = prompt
        u.completion_tokens = completion
        u.total_tokens = total
        return u

    def test_record_call_writes_caller_tag(self):
        from llm.client import LLMClient

        client = LLMClient.__new__(LLMClient)  # bypass __init__ (no API key needed)
        client.model = "test-model"
        client._call_records = []

        with patch.object(config, "TOKEN_TRACKING_ENABLED", True):
            client._record_call(
                self._make_usage(),
                "chat_with_tools",
                [{"role": "user", "content": "hi"}],
                caller_tag="ExecutorAgent",
            )

        assert len(client._call_records) == 1
        rec = client._call_records[0]
        assert rec.caller_tag == "ExecutorAgent"
        assert rec.engine == "test-model"
        assert rec.total_tokens == 30

    def test_record_call_default_empty_caller_tag(self):
        from llm.client import LLMClient

        client = LLMClient.__new__(LLMClient)
        client.model = "test-model"
        client._call_records = []

        with patch.object(config, "TOKEN_TRACKING_ENABLED", True):
            client._record_call(
                self._make_usage(),
                "chat",
                [{"role": "user", "content": "hi"}],
            )

        assert client._call_records[0].caller_tag == ""


# ----------------------------------------------------------------------
# BaseAgent.think_* auto-tags with self.name
# ----------------------------------------------------------------------


class TestBaseAgentAutoTagging:
    def _make_agent(self, name: str):
        from agents.base import BaseAgent
        from context.manager import ContextManager

        client = MagicMock()
        client.chat = AsyncMock(return_value="ok")
        client.chat_json = AsyncMock(return_value={"a": 1})
        # think_with_tools needs a response with tool_calls attribute
        resp = MagicMock()
        resp.content = "ok"
        resp.tool_calls = None
        client.chat_with_tools = AsyncMock(return_value=resp)

        # ContextManager.compress_if_needed returns messages unchanged
        cm = ContextManager()
        cm.compress_if_needed = AsyncMock(side_effect=lambda msgs, _: msgs)

        agent = BaseAgent(
            name=name,
            system_prompt="sys",
            llm_client=client,
            context_manager=cm,
        )
        return agent, client

    @pytest.mark.asyncio
    async def test_think_defaults_caller_tag_to_self_name(self):
        agent, client = self._make_agent("Reflector")
        await agent.think("hi")
        client.chat.assert_called_once()
        kwargs = client.chat.call_args.kwargs
        assert kwargs.get("caller_tag") == "Reflector"

    @pytest.mark.asyncio
    async def test_think_json_defaults_caller_tag_to_self_name(self):
        agent, client = self._make_agent("PlannerAgent")
        await agent.think_json("hi")
        kwargs = client.chat_json.call_args.kwargs
        assert kwargs.get("caller_tag") == "PlannerAgent"

    @pytest.mark.asyncio
    async def test_think_with_tools_defaults_caller_tag_to_self_name(self):
        agent, client = self._make_agent("EmergentPlanner")
        await agent.think_with_tools("hi", tools=[])
        kwargs = client.chat_with_tools.call_args.kwargs
        assert kwargs.get("caller_tag") == "EmergentPlanner"

    @pytest.mark.asyncio
    async def test_explicit_kwarg_caller_tag_overrides_default(self):
        agent, client = self._make_agent("PlannerAgent")
        await agent.think("hi", caller_tag="CustomCaller")
        kwargs = client.chat.call_args.kwargs
        assert kwargs.get("caller_tag") == "CustomCaller"


# ----------------------------------------------------------------------
# Orchestrator._finalize_token_usage builds by_caller view
# ----------------------------------------------------------------------


class TestByCallerAggregation:
    def _make_orchestrator_with_records(self, records: list[LLMCallRecord]):
        """Construct a minimal Orchestrator + stub LLMClient for aggregation."""
        from agents.orchestrator import OrchestratorAgent

        orch = OrchestratorAgent.__new__(OrchestratorAgent)
        client = MagicMock()
        client.model = "deepseek-chat"
        client.get_call_records.return_value = records
        orch.llm_client = client
        return orch

    def test_by_caller_separates_buckets(self):
        records = [
            LLMCallRecord(call_type="chat", total_tokens=100, prompt_tokens=80,
                          completion_tokens=20, engine="m", caller_tag="ExecutorAgent"),
            LLMCallRecord(call_type="chat", total_tokens=50, prompt_tokens=40,
                          completion_tokens=10, engine="m", caller_tag="SubAgent-1"),
            LLMCallRecord(call_type="chat", total_tokens=30, prompt_tokens=25,
                          completion_tokens=5, engine="m", caller_tag="SubAgent-1"),
        ]
        orch = self._make_orchestrator_with_records(records)
        summary = orch._finalize_token_usage()

        assert "ExecutorAgent" in summary.by_caller
        assert "SubAgent-1" in summary.by_caller
        # SubAgent-1 should sum its two calls
        assert summary.by_caller["SubAgent-1"].total_tokens == 80
        assert summary.by_caller["ExecutorAgent"].total_tokens == 100

    def test_untagged_records_fall_into_unknown_bucket(self):
        records = [
            LLMCallRecord(call_type="chat", total_tokens=100, engine="m", caller_tag=""),
            LLMCallRecord(call_type="chat", total_tokens=50, engine="m", caller_tag="Reflector"),
        ]
        orch = self._make_orchestrator_with_records(records)
        summary = orch._finalize_token_usage()

        assert summary.by_caller["unknown"].total_tokens == 100
        assert summary.by_caller["Reflector"].total_tokens == 50

    def test_by_caller_total_equals_by_engine_total(self):
        """Sum across by_caller must equal sum across by_engine — same source."""
        records = [
            LLMCallRecord(call_type="chat", total_tokens=100, prompt_tokens=80,
                          completion_tokens=20, engine="m", caller_tag="A"),
            LLMCallRecord(call_type="chat", total_tokens=50, prompt_tokens=40,
                          completion_tokens=10, engine="m", caller_tag="B"),
            LLMCallRecord(call_type="chat", total_tokens=70, prompt_tokens=50,
                          completion_tokens=20, engine="m", caller_tag=""),
        ]
        orch = self._make_orchestrator_with_records(records)
        summary = orch._finalize_token_usage()

        engine_total = sum(u.total_tokens for u in summary.by_engine.values())
        caller_total = sum(u.total_tokens for u in summary.by_caller.values())
        assert engine_total == caller_total == summary.total.total_tokens == 220

    def test_by_engine_view_unchanged_by_wave_6(self):
        """Wave-6 must not change existing by_engine semantics."""
        records = [
            LLMCallRecord(call_type="chat", total_tokens=100, engine="deepseek-chat", caller_tag="X"),
            LLMCallRecord(call_type="chat", total_tokens=50, engine="qwen-72b", caller_tag="Y"),
        ]
        orch = self._make_orchestrator_with_records(records)
        summary = orch._finalize_token_usage()

        assert summary.by_engine["deepseek-chat"].total_tokens == 100
        assert summary.by_engine["qwen-72b"].total_tokens == 50
