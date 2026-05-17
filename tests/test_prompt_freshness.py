"""
Wave-2 tests: system prompts are built per-instance in __init__,
not at module import time.

Lock in:
  - The auto-injected date is fresh on each agent instantiation (not frozen
    at import — survives processes that span midnight).
  - HITL guidance is governed by `_HITL_RUNTIME_OVERRIDE`, not the snapshot
    of `config.HITL_ENABLED` at import. This is what makes the v13
    double-gating contract actually work for run_single mode.
  - SubAgent's system prompt explicitly excludes HITL guidance even when
    HITL is otherwise active (because SubAgentTool structurally excludes
    `ask_user` from the SubAgent's tool whitelist).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import config


# ----------------------------------------------------------------------
# Helper: minimal agent factory
# ----------------------------------------------------------------------


def _make_llm_client():
    """Stub LLMClient — agents only need .model in __init__ for some paths."""
    client = MagicMock()
    client.model = "test-model"
    return client


def _make_tool(name: str = "tool_a"):
    tool = MagicMock()
    tool.name = name
    tool.description = "test"
    tool.to_openai_tool.return_value = {
        "type": "function",
        "function": {"name": name, "description": "", "parameters": {"type": "object"}},
    }
    return tool


# ----------------------------------------------------------------------
# Date freshness — system prompts built at __init__ pick up "today"
# ----------------------------------------------------------------------


class TestDateFreshness:
    def test_executor_system_prompt_contains_current_date(self):
        from agents.executor import ExecutorAgent
        agent = ExecutorAgent(llm_client=_make_llm_client(), tools=[_make_tool()])

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        # The auto-injected "Current Context" section quotes the date as "Today's date: YYYY-MM-DD"
        assert today in agent.system_prompt
        assert "Today's date" in agent.system_prompt

    def test_emergent_planner_system_prompt_contains_current_date(self):
        from agents.emergent_planner import EmergentPlannerAgent
        agent = EmergentPlannerAgent(llm_client=_make_llm_client(), tools=[_make_tool()])

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in agent.system_prompt

    def test_goal_driven_planner_system_prompt_contains_current_date(self):
        from agents.goal_driven_planner import GoalDrivenPlannerAgent
        agent = GoalDrivenPlannerAgent(llm_client=_make_llm_client(), tools=[_make_tool()])

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in agent.system_prompt

    def test_planner_system_prompt_contains_current_date(self):
        from agents.planner import PlannerAgent
        agent = PlannerAgent(llm_client=_make_llm_client())

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in agent.system_prompt

    def test_late_instantiation_picks_up_new_date(self):
        """Two agents instantiated at different simulated dates pick up
        different dates in their system prompts."""
        from agents.prompt_utils import build_context_injection

        # Patch datetime.now to simulate "yesterday" and "today"
        # (build_context_injection imports datetime locally)
        import agents.prompt_utils as prompt_utils

        class _FakeDatetime:
            @classmethod
            def now(cls):
                return cls._now

        # First call: simulated "2026-05-01"
        from datetime import datetime as real_datetime
        fake_today = real_datetime(2026, 5, 1, 9, 0, 0)
        _FakeDatetime._now = fake_today
        with patch.object(prompt_utils, "datetime", _FakeDatetime):
            text_today = build_context_injection()

        # Second call: simulated "2026-05-02" (process spans midnight)
        fake_tomorrow = real_datetime(2026, 5, 2, 9, 0, 0)
        _FakeDatetime._now = fake_tomorrow
        with patch.object(prompt_utils, "datetime", _FakeDatetime):
            text_tomorrow = build_context_injection()

        assert "2026-05-01" in text_today
        assert "2026-05-02" in text_tomorrow
        # And confirm both are *newly built* (not cached)
        assert "2026-05-01" not in text_tomorrow
        assert "2026-05-02" not in text_today


# ----------------------------------------------------------------------
# HITL double-gating: runtime override, not import-time snapshot
# ----------------------------------------------------------------------


class TestHitlDoubleGating:
    """Wave-2 H4 fix: HITL guidance respects the runtime override set by
    OrchestratorAgent.__init__, even when the agent is built later in the
    same process. Module-level evaluation would have frozen the wrong
    value at import time.
    """

    def test_hitl_guidance_in_executor_when_override_true(self):
        from agents.executor import ExecutorAgent
        from agents.prompt_utils import set_hitl_runtime_enabled

        set_hitl_runtime_enabled(True)
        try:
            agent = ExecutorAgent(llm_client=_make_llm_client(), tools=[_make_tool()])
            assert "ask_user" in agent.system_prompt
        finally:
            set_hitl_runtime_enabled(None)  # reset to fall back to config

    def test_hitl_guidance_suppressed_in_executor_when_override_false(self):
        """Even with config.HITL_ENABLED=True, an explicit runtime override
        of False (set by OrchestratorAgent in non-interactive mode) suppresses
        the guidance — the v13 double-gating contract."""
        from agents.executor import ExecutorAgent
        from agents.prompt_utils import set_hitl_runtime_enabled

        set_hitl_runtime_enabled(False)
        try:
            with patch.object(config, "HITL_ENABLED", True):
                agent = ExecutorAgent(llm_client=_make_llm_client(), tools=[_make_tool()])
                assert "ask_user" not in agent.system_prompt
        finally:
            set_hitl_runtime_enabled(None)

    def test_hitl_falls_back_to_config_when_override_unset(self):
        from agents.executor import ExecutorAgent
        from agents.prompt_utils import set_hitl_runtime_enabled

        set_hitl_runtime_enabled(None)  # explicit fallback path
        with patch.object(config, "HITL_ENABLED", True):
            agent = ExecutorAgent(llm_client=_make_llm_client(), tools=[_make_tool()])
            assert "ask_user" in agent.system_prompt

        set_hitl_runtime_enabled(None)
        with patch.object(config, "HITL_ENABLED", False):
            agent = ExecutorAgent(llm_client=_make_llm_client(), tools=[_make_tool()])
            assert "ask_user" not in agent.system_prompt


# ----------------------------------------------------------------------
# SubAgent system prompt: HITL guidance always suppressed (M1)
# ----------------------------------------------------------------------


class TestSubAgentHitlGuidanceSuppression:
    """Wave-2 M1: SubAgent's system prompt must NOT include ask_user guidance
    even when HITL is globally active, because SubAgentTool structurally
    excludes ask_user from the SubAgent's tool whitelist. Including the
    guidance would have the LLM call a tool that isn't there."""

    def _make_subagent(self, hitl_active: bool):
        from agents.prompt_utils import set_hitl_runtime_enabled
        from agents.subagent import SubAgent

        set_hitl_runtime_enabled(hitl_active)
        try:
            agent = SubAgent(
                name="SubAgent-test",
                task_description="test",
                llm_client=_make_llm_client(),
                tools=[_make_tool()],
            )
            return agent._system_prompt
        finally:
            set_hitl_runtime_enabled(None)

    def test_subagent_prompt_excludes_hitl_when_active(self):
        prompt = self._make_subagent(hitl_active=True)
        assert "ask_user" not in prompt

    def test_subagent_prompt_excludes_hitl_when_inactive(self):
        prompt = self._make_subagent(hitl_active=False)
        assert "ask_user" not in prompt

    def test_subagent_prompt_excludes_subagent_guidance(self):
        """Sanity: depth=1 — SubAgent prompt also has no nested-subagent guidance."""
        from agents.prompt_utils import set_hitl_runtime_enabled
        from agents.subagent import SubAgent

        set_hitl_runtime_enabled(False)
        try:
            with patch.object(config, "SUBAGENT_ENABLED", True):
                agent = SubAgent(
                    name="SubAgent-test",
                    task_description="test",
                    llm_client=_make_llm_client(),
                    tools=[_make_tool()],
                )
                # The "subagent" guidance section header is "When to Use the \"subagent\" Tool"
                assert "When to Use the \"subagent\" Tool" not in agent._system_prompt
        finally:
            set_hitl_runtime_enabled(None)
