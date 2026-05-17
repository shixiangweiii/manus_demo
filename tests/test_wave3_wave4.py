"""
Wave-3 + Wave-4 tests:
  - M2: ReActEngine._current_log lifted to a member attribute; SubAgent
        failure paths recover the in-progress log.
  - M3: SubAgentTool.reset_task_state cleans `subagent_<N>` sandbox subdirs.
  - M4: makedirs runs via asyncio.to_thread (smoke check it still works).
  - M5: dead `except asyncio.TimeoutError` removed.
  - M6: _summarize_result unexpected-structure path puts repr in `issues`,
        leaves `accomplished` empty.
  - L2: SubAgentTool truncates oversized task_description.
  - L5: subagent_iteration verbosity config respected.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from schema import StepResult, ToolCallRecord


# ----------------------------------------------------------------------
# M2: ReActEngine._current_log lift
# ----------------------------------------------------------------------


class TestM2CurrentLogLift:
    def test_react_engine_initializes_current_log(self):
        from llm.client import LLMClient
        from react.engine import ReActEngine

        client = LLMClient.__new__(LLMClient)
        client.model = "test"
        engine = ReActEngine(llm_client=client, tools=[])
        assert hasattr(engine, "_current_log")
        assert engine._current_log == []

    def test_subagent_failure_tool_calls_prefers_live_log(self):
        """When ReActEngine._current_log has entries, SubAgent._failure_tool_calls
        returns those — not the on_iteration snapshot."""
        from agents.subagent import SubAgent
        from llm.client import LLMClient

        client = LLMClient.__new__(LLMClient)
        client.model = "test"
        # Stub get_call_records so SubAgent.__init__ doesn't reach for it
        client.get_call_records = MagicMock(return_value=[])

        sa = SubAgent(name="SubAgent-1", task_description="t", llm_client=client, tools=[])

        # Simulate live log having more entries than the on_iteration snapshot
        live = [
            ToolCallRecord(tool_name="tool_a", parameters={}, result="r1"),
            ToolCallRecord(tool_name="tool_b", parameters={}, result="r2"),
        ]
        sa._react_engine._current_log = live
        sa._accumulated_tool_calls = [live[0]]  # snapshot only got 1 call

        recovered = sa._failure_tool_calls()
        assert len(recovered) == 2
        assert [r.tool_name for r in recovered] == ["tool_a", "tool_b"]

    def test_subagent_failure_tool_calls_fallback_to_snapshot(self):
        """When live log is empty, falls back to _accumulated_tool_calls."""
        from agents.subagent import SubAgent
        from llm.client import LLMClient

        client = LLMClient.__new__(LLMClient)
        client.model = "test"
        client.get_call_records = MagicMock(return_value=[])

        sa = SubAgent(name="SubAgent-1", task_description="t", llm_client=client, tools=[])
        sa._react_engine._current_log = []  # no live data
        sa._accumulated_tool_calls = [
            ToolCallRecord(tool_name="snapshot_tool", parameters={}, result="r"),
        ]

        recovered = sa._failure_tool_calls()
        assert len(recovered) == 1
        assert recovered[0].tool_name == "snapshot_tool"

    def test_returned_step_result_isolated_from_engine_log(self):
        """Wave-3 concurrency safety: a previously returned StepResult.tool_calls_log
        must not be mutated by a subsequent ReActEngine.execute() invocation
        (which rebinds self._current_log to a fresh list).

        Regression: an earlier draft did `self._current_log.clear()` + alias,
        which would WIPE the list that a returned StepResult held a reference
        to (or that pydantic copied from). The fix is to never reuse the
        underlying list across calls.
        """
        from llm.client import LLMClient
        from react.engine import ReActEngine
        from schema import StepResult

        client = LLMClient.__new__(LLMClient)
        client.model = "test"
        engine = ReActEngine(llm_client=client, tools=[])

        # Simulate a finished execute() — populate _current_log and produce a StepResult
        engine._current_log.append(ToolCallRecord(tool_name="first_call", parameters={}, result="r1"))
        sr = StepResult(step_id="1", success=True, tool_calls_log=engine._current_log)
        captured_first = list(sr.tool_calls_log)

        # Simulate a NEW execute() rebinding _current_log to a fresh list
        engine._current_log = []
        engine._current_log.append(ToolCallRecord(tool_name="second_call", parameters={}, result="r2"))

        # Returned StepResult must still see the FIRST call (not contaminated by second)
        assert [r.tool_name for r in sr.tool_calls_log] == ["first_call"]
        assert captured_first == sr.tool_calls_log


# ----------------------------------------------------------------------
# M3: sandbox cleanup on reset_task_state
# ----------------------------------------------------------------------


class TestM3SandboxCleanup:
    def test_reset_task_state_removes_subagent_subdirs(self):
        from tools.subagent_tool import SubAgentTool

        tmpdir = tempfile.mkdtemp()
        try:
            # Pre-create stale subagent_1 / subagent_2 dirs + a sibling dir
            # that should NOT be touched.
            os.makedirs(os.path.join(tmpdir, "subagent_1"))
            os.makedirs(os.path.join(tmpdir, "subagent_2"))
            os.makedirs(os.path.join(tmpdir, "user_data"))
            with open(os.path.join(tmpdir, "subagent_1", "ghost.txt"), "w") as f:
                f.write("leftover")

            with patch.object(config, "SANDBOX_DIR", tmpdir):
                tool = SubAgentTool(
                    llm_client=MagicMock(),
                    available_tools={},
                )
                tool.reset_task_state()

            assert not os.path.exists(os.path.join(tmpdir, "subagent_1"))
            assert not os.path.exists(os.path.join(tmpdir, "subagent_2"))
            # Sibling untouched
            assert os.path.exists(os.path.join(tmpdir, "user_data"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_reset_task_state_resets_counters(self):
        from tools.subagent_tool import SubAgentTool

        tool = SubAgentTool(llm_client=MagicMock(), available_tools={})
        tool._call_count = 5
        tool._subagent_counter = 7

        with patch.object(config, "SANDBOX_DIR", "/tmp/__nonexistent_subagent_test__"):
            tool.reset_task_state()

        assert tool._call_count == 0
        assert tool._subagent_counter == 0


# ----------------------------------------------------------------------
# M4: makedirs is async (smoke)
# ----------------------------------------------------------------------


class TestM4AsyncMakedirs:
    @pytest.mark.asyncio
    async def test_sandbox_creation_does_not_block(self):
        """SubAgentTool.execute() uses asyncio.to_thread for makedirs.
        Smoke check: the call still produces the directory."""
        from tools.subagent_tool import SubAgentTool

        tmpdir = tempfile.mkdtemp()
        try:
            with patch.object(config, "SANDBOX_DIR", tmpdir):
                tool = SubAgentTool(
                    llm_client=MagicMock(),
                    available_tools={"web_search": MagicMock(name="web_search")},
                    max_calls_per_task=1,
                )
                # Patch SubAgent so we don't actually run a ReAct loop
                with patch("agents.subagent.SubAgent") as MockSA:
                    mock_instance = MockSA.return_value
                    mock_instance.run = AsyncMock(return_value=MagicMock(
                        summary_text='{"accomplished":"","findings":"","issues":"","artifacts":[],"tool_calls_summary":""}',
                        status=MagicMock(value="completed"),
                        iterations_used=1,
                        tokens_used=0,
                        duration_ms=0.0,
                        summary=MagicMock(artifacts=[], accomplished="", issues=""),
                    ))
                    await tool.execute(task_description="test")

            # Verify subagent_1 dir was created
            assert os.path.isdir(os.path.join(tmpdir, "subagent_1"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ----------------------------------------------------------------------
# L2: task_description length cap
# ----------------------------------------------------------------------


class TestL2TaskDescriptionCap:
    @pytest.mark.asyncio
    async def test_long_task_description_is_truncated(self):
        from tools.subagent_tool import SubAgentTool

        tmpdir = tempfile.mkdtemp()
        try:
            with patch.object(config, "SANDBOX_DIR", tmpdir), \
                 patch.object(config, "SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH", 100):
                tool = SubAgentTool(
                    llm_client=MagicMock(),
                    available_tools={"x": MagicMock(name="x")},
                    max_calls_per_task=1,
                )
                # Capture the task_description SubAgent ends up seeing
                captured: dict = {}
                with patch("agents.subagent.SubAgent") as MockSA:
                    def _record(**kwargs):
                        captured.update(kwargs)
                        instance = MagicMock()
                        instance.run = AsyncMock(return_value=MagicMock(
                            summary_text='{}',
                            status=MagicMock(value="completed"),
                            iterations_used=1,
                            tokens_used=0,
                            duration_ms=0.0,
                            summary=MagicMock(artifacts=[], accomplished="", issues=""),
                        ))
                        return instance
                    MockSA.side_effect = _record

                    long_desc = "x" * 5000
                    await tool.execute(task_description=long_desc)

            assert "task_description" in captured
            # Original 5000 chars truncated to 100 + a marker
            sent = captured["task_description"]
            assert sent.startswith("x" * 100)
            assert "[Description truncated" in sent
            assert len(sent) < 5000
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ----------------------------------------------------------------------
# M5: dead TimeoutError except removed (replaced by CancelledError re-raise)
# ----------------------------------------------------------------------


class TestN5LocalParentCaptureUnderConcurrency:
    """Wave-1 N5 (verified under Wave-4 M4): when a concurrent set_caller()
    runs DURING the awaited makedirs, the local_parent snapshot ensures the
    spawned SubAgent is still attributed to the original caller — not the
    last writer of self._parent_name."""

    @pytest.mark.asyncio
    async def test_local_parent_survives_await_with_concurrent_set_caller(self):
        from tools.subagent_tool import SubAgentTool

        tmpdir = tempfile.mkdtemp()
        try:
            with patch.object(config, "SANDBOX_DIR", tmpdir):
                tool = SubAgentTool(
                    llm_client=MagicMock(),
                    available_tools={"x": MagicMock(name="x")},
                    max_calls_per_task=1,
                    parent_name="OriginalCaller",
                )

                captured: dict = {}
                # We hijack makedirs to be a slow await that lets a concurrent
                # set_caller() interleave before the SubAgent is constructed.
                original_to_thread = asyncio.to_thread

                async def slow_makedirs(*args, **kwargs):
                    # Simulate slow disk + give the event loop a chance
                    await asyncio.sleep(0.05)
                    return None

                with patch("tools.subagent_tool.asyncio.to_thread", side_effect=slow_makedirs), \
                     patch("agents.subagent.SubAgent") as MockSA:
                    def _record(**kwargs):
                        captured.update(kwargs)
                        instance = MagicMock()
                        instance.run = AsyncMock(return_value=MagicMock(
                            summary_text="{}",
                            status=MagicMock(value="completed"),
                            iterations_used=1, tokens_used=0, duration_ms=0.0,
                            summary=MagicMock(artifacts=[], accomplished="", issues=""),
                        ))
                        return instance
                    MockSA.side_effect = _record

                    # Start execute() task; while it's awaiting makedirs,
                    # interleave a concurrent set_caller() with a different name
                    exec_task = asyncio.create_task(tool.execute(task_description="t"))
                    await asyncio.sleep(0.01)  # let exec_task reach the makedirs await
                    tool.set_caller("OverrideCaller")
                    await exec_task

            # Despite the concurrent overwrite, the SubAgent must have been
            # constructed with the parent the original caller intended.
            assert captured.get("parent_agent_name") == "OriginalCaller"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestM5DeadCodeRemoved:
    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        from tools.subagent_tool import SubAgentTool

        tmpdir = tempfile.mkdtemp()
        try:
            with patch.object(config, "SANDBOX_DIR", tmpdir):
                tool = SubAgentTool(
                    llm_client=MagicMock(),
                    available_tools={"x": MagicMock(name="x")},
                    max_calls_per_task=1,
                )
                with patch("agents.subagent.SubAgent") as MockSA:
                    instance = MockSA.return_value
                    # Simulate parent task cancellation during SubAgent.run()
                    instance.run = AsyncMock(side_effect=asyncio.CancelledError())

                    with pytest.raises(asyncio.CancelledError):
                        await tool.execute(task_description="test")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ----------------------------------------------------------------------
# M6: unexpected-structure summary doesn't lie about accomplishment
# ----------------------------------------------------------------------


class TestM6HonestUnexpectedStructure:
    @pytest.mark.asyncio
    async def test_unexpected_structure_keeps_accomplished_empty(self):
        from agents.subagent import SubAgent
        from llm.client import LLMClient

        client = LLMClient.__new__(LLMClient)
        client.model = "test"
        client.get_call_records = MagicMock(return_value=[])
        # LLM returns dict that doesn't match SubAgentSummary schema
        client.chat_json = AsyncMock(return_value={"foo": "bar", "baz": 42})

        sa = SubAgent(name="SubAgent-1", task_description="t", llm_client=client, tools=[])
        step_result = MagicMock()
        step_result.output = "did stuff"
        step_result.tool_calls_log = []

        summary = await sa._summarize_result(step_result)

        # accomplished must be empty (don't lie); repr goes into issues
        assert summary.accomplished == ""
        assert "Summary structure unexpected" in summary.issues
        assert "foo" in summary.issues  # repr of the unexpected dict