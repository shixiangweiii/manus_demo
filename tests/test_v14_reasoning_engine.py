"""
Unit tests for v14 Phase 2: ReasoningEngine.
v14 Phase 2 单元测试：推理引擎。
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import config
from schema import LLMCallRecord, StepResult, TokenUsage


def _make_reasoning_engine(tools=None, max_iterations=5, max_thinking_tokens=1000):
    """Create a ReasoningEngine with mocked LLMClient for testing."""
    from react.reasoning_engine import ReasoningEngine
    from tools.router import ToolRouter

    llm_client = MagicMock(spec=["chat_with_tools", "get_call_records"])
    llm_client.get_call_records.return_value = []
    if tools is None:
        tools = {}
    tool_router = ToolRouter(available_tools=list(tools.keys()))
    return ReasoningEngine(
        llm_client=llm_client,
        tools=tools,
        max_iterations=max_iterations,
        tool_router=tool_router,
        max_thinking_tokens=max_thinking_tokens,
    )


def _setup_shared_records(engine, responses, reasoning_tokens_per_call=150):
    """Set up shared mutable records list for differential budget tracking.

    The P3 fix (differential method) requires get_call_records() to return
    a list that grows after each chat_with_tools call. This helper sets up
    that pattern with a shared mutable list.
    """
    shared_records = []
    response_queue = list(responses)

    def mock_chat(*args, **kwargs):
        shared_records.append(LLMCallRecord(
            reasoning_tokens=reasoning_tokens_per_call,
            prompt_tokens=50,
            completion_tokens=200,
            total_tokens=350,
        ))
        return response_queue.pop(0)

    engine.llm_client.chat_with_tools = AsyncMock(side_effect=mock_chat)
    engine.llm_client.get_call_records = MagicMock(return_value=shared_records)
    return shared_records


class TestThinkingRoundDoesNotIncrementIteration:
    """Pure thinking rounds (no tool_calls, no final answer) should not increment iteration."""

    @pytest.mark.asyncio
    async def test_thinking_only_round_skipped(self):
        """A response with only thinking (reasoning_content) and no tool_calls or content
        should not count as an iteration."""
        engine = _make_reasoning_engine(max_iterations=3)

        # Sequence: thinking-only → thinking-only → final answer
        responses = [
            SimpleNamespace(content="", reasoning_content="Let me think about this...", tool_calls=None),
            SimpleNamespace(content="", reasoning_content="I need to consider more...", tool_calls=None),
            SimpleNamespace(content="Here is the answer", reasoning_content="Final reasoning", tool_calls=None),
        ]
        shared_records = _setup_shared_records(engine, responses, reasoning_tokens_per_call=100)

        result = await engine.execute("test task")

        assert result.success is True
        # 2 thinking rounds + 1 final answer = 1 iteration (only final answer counts)
        assert result.iterations_completed == 1
        # P8-5A: verify chat_with_tools was called 3 times (2 thinking + 1 final)
        assert engine.llm_client.chat_with_tools.call_count == 3
        # P8-5A: verify records accumulated correctly
        assert len(shared_records) == 3

    @pytest.mark.asyncio
    async def test_tool_calls_always_increment(self):
        """A response with tool_calls always counts as an iteration."""
        engine = _make_reasoning_engine(max_iterations=5)

        tool_call = SimpleNamespace(
            id="tc1",
            function=SimpleNamespace(name="web_search", arguments='{"query": "test"}'),
        )
        responses = [
            # Round 1: thinking + tool_calls → counts as iteration
            SimpleNamespace(
                content="",
                reasoning_content="I should search for this",
                tool_calls=[tool_call],
            ),
            # Round 2: final answer
            SimpleNamespace(
                content="Found the answer",
                reasoning_content=None,
                tool_calls=None,
            ),
        ]
        engine.llm_client.chat_with_tools = AsyncMock(side_effect=responses)
        engine.llm_client.get_call_records.return_value = []

        # Mock the tool
        mock_tool = MagicMock()
        mock_tool.name = "web_search"
        mock_tool.traced_execute = AsyncMock(return_value="Search results")
        engine.tools = {"web_search": mock_tool}
        mock_tool.to_openai_tool = MagicMock(return_value={"type": "function", "function": {"name": "web_search"}})
        engine.tool_schemas = [mock_tool.to_openai_tool()]

        result = await engine.execute("test task")

        assert result.success is True
        assert result.iterations_completed == 2


class TestThinkingBudget:
    """Thinking token budget should be tracked and enforced."""

    @pytest.mark.asyncio
    async def test_thinking_budget_exceeded(self):
        """When cumulative thinking tokens exceed MAX_THINKING_TOKENS, execution should stop."""
        engine = _make_reasoning_engine(max_iterations=10, max_thinking_tokens=200)

        # Keep producing thinking-only responses
        thinking_response = SimpleNamespace(
            content="",
            reasoning_content="Thinking deeply...",
            tool_calls=None,
        )
        # Each call adds 150 reasoning tokens; budget exceeded after 2 calls (300 > 200)
        _setup_shared_records(engine, [thinking_response] * 20, reasoning_tokens_per_call=150)

        result = await engine.execute("test task")

        assert result.success is False
        assert "Thinking budget exceeded" in result.output

    @pytest.mark.asyncio
    async def test_thinking_budget_within_limit(self):
        """Thinking within budget should continue normally."""
        engine = _make_reasoning_engine(max_iterations=5, max_thinking_tokens=10000)

        responses = [
            SimpleNamespace(content="", reasoning_content="Thinking...", tool_calls=None),
            SimpleNamespace(content="The answer is 42", reasoning_content="I figured it out", tool_calls=None),
        ]
        _setup_shared_records(engine, responses, reasoning_tokens_per_call=500)

        result = await engine.execute("test task")

        assert result.success is True
        assert result.iterations_completed == 1


class TestThinkingSeparatedInMessages:
    """Thinking content should be separated from response in assistant messages."""

    @pytest.mark.asyncio
    async def test_reasoning_content_field_separated(self):
        """When reasoning_content field exists, it should be in thinking_content key
        and stripped from content."""
        engine = _make_reasoning_engine(max_iterations=3)

        responses = [
            SimpleNamespace(
                content="Final answer",
                reasoning_content="My step-by-step reasoning",
                tool_calls=None,
            ),
        ]
        engine.llm_client.chat_with_tools = AsyncMock(side_effect=responses)
        engine.llm_client.get_call_records.return_value = []

        result = await engine.execute("test task")

        assert result.success is True
        # Verify messages were built with separated thinking
        # The assistant message should be the last user-facing one
        # We can't directly access messages, but we verify the engine ran correctly

    @pytest.mark.asyncio
    async def test_think_tag_content_separated(self):
        """When <think/> tags are in content, they should be stripped."""
        from llm.client import _strip_thinking_from_content

        content = "<think\nLet me analyze\n</think\n>The final answer"
        thinking = "Let me analyze"
        result = _strip_thinking_from_content(content, thinking)
        assert result == "The final answer"

    @pytest.mark.asyncio
    async def test_strip_preserves_content_without_tags(self):
        """When thinking came from reasoning_content, content is already clean."""
        from llm.client import _strip_thinking_from_content

        content = "The final answer"
        thinking = "My reasoning process"
        result = _strip_thinking_from_content(content, thinking)
        assert result == "The final answer"

    @pytest.mark.asyncio
    async def test_strip_empty_thinking(self):
        """Empty thinking returns content unchanged."""
        from llm.client import _strip_thinking_from_content

        assert _strip_thinking_from_content("some content", "") == "some content"
        assert _strip_thinking_from_content("some content", None) == "some content"


class TestFeatureFlag:
    """ENABLE_REASONING_ENGINE feature flag should control engine selection."""

    def test_flag_defaults_to_false(self):
        """ReasoningEngine should be off by default."""
        assert config.ENABLE_REASONING_ENGINE is False

    def test_reasoning_engine_exists(self):
        """ReasoningEngine class should be importable."""
        from react.reasoning_engine import ReasoningEngine
        from react.engine import ReActEngine
        assert issubclass(ReasoningEngine, ReActEngine)


class TestP2IterationBoundaryRegression:
    """P2: After thinking-only rounds, continue_msg should be thinking-appropriate,
    not "based on tool results"."""

    @pytest.mark.asyncio
    async def test_thinking_then_tool_call_sequence(self):
        """After thinking-only rounds, next LLM call should get a thinking-appropriate prompt,
        not 'based on tool results'."""
        engine = _make_reasoning_engine(max_iterations=5)

        tool_call = SimpleNamespace(
            id="tc1",
            function=SimpleNamespace(name="web_search", arguments='{"query": "test"}'),
        )
        responses = [
            # Thinking-only (no iteration)
            SimpleNamespace(content="", reasoning_content="Thinking...", tool_calls=None),
            # More thinking (no iteration)
            SimpleNamespace(content="", reasoning_content="More thinking...", tool_calls=None),
            # Tool call after thinking (iteration 1)
            SimpleNamespace(content="", reasoning_content="", tool_calls=[tool_call]),
            # Final answer (iteration 2)
            SimpleNamespace(content="Done", reasoning_content=None, tool_calls=None),
        ]

        captured_user_msgs = []
        shared_records = []

        def mock_chat(msgs, *args, **kwargs):
            shared_records.append(LLMCallRecord(
                reasoning_tokens=100, prompt_tokens=50, completion_tokens=200, total_tokens=350,
            ))
            # Capture the last user message added by the engine
            user_msgs = [m for m in msgs if m.get("role") == "user"]
            if user_msgs:
                captured_user_msgs.append(user_msgs[-1]["content"])
            return responses.pop(0)

        engine.llm_client.chat_with_tools = AsyncMock(side_effect=mock_chat)
        engine.llm_client.get_call_records = MagicMock(return_value=shared_records)

        # Mock the tool
        mock_tool = MagicMock()
        mock_tool.name = "web_search"
        mock_tool.traced_execute = AsyncMock(return_value="Search results")
        engine.tools = {"web_search": mock_tool}
        mock_tool.to_openai_tool = MagicMock(return_value={"type": "function", "function": {"name": "web_search"}})
        engine.tool_schemas = [mock_tool.to_openai_tool()]

        result = await engine.execute("test task")

        assert result.success is True
        assert result.iterations_completed == 2
        # 1st call: initial prompt (non_system_msgs is empty)
        assert captured_user_msgs[0] == "test task"
        # 2nd call: after 1st thinking-only, should NOT say "based on tool results"
        assert "tool results" not in captured_user_msgs[1].lower()
        # 3rd call: after 2nd thinking-only, still no tool results
        assert "tool results" not in captured_user_msgs[2].lower()
        # 4th call: after tool call, should say "based on tool results"
        assert "tool results" in captured_user_msgs[3].lower()


class TestP3BudgetAccumulationRegression:
    """P3: Budget should only count records from THIS execute() call, not prior ones
    from a shared LLMClient."""

    @pytest.mark.asyncio
    async def test_budget_accumulation_with_shared_client(self):
        """Prior records from another agent using the same LLMClient should not
        be counted in this engine's thinking budget."""
        engine = _make_reasoning_engine(max_iterations=10, max_thinking_tokens=200)

        # Pre-populate with a prior call from another agent
        prior_record = LLMCallRecord(
            reasoning_tokens=500, prompt_tokens=100, completion_tokens=300, total_tokens=900,
        )
        shared_records = [prior_record]

        thinking_response = SimpleNamespace(
            content="", reasoning_content="Thinking deeply...", tool_calls=None,
        )

        def mock_chat(*args, **kwargs):
            shared_records.append(LLMCallRecord(
                reasoning_tokens=150, prompt_tokens=50, completion_tokens=200, total_tokens=400,
            ))
            return thinking_response

        engine.llm_client.chat_with_tools = AsyncMock(side_effect=mock_chat)
        engine.llm_client.get_call_records = MagicMock(return_value=shared_records)

        result = await engine.execute("test task")

        # Budget should count from records_before (1 prior record) forward,
        # so only the new records are counted: 150 + 150 = 300 > 200 → exceeded
        assert result.success is False
        assert "Thinking budget exceeded" in result.output
        # The output should show the actual accumulated tokens (300, not 800)
        assert "300" in result.output


class TestP4BudgetExceededPartialWork:
    """P4: Budget-exceeded output should include partial work (tool summary + last response)."""

    @pytest.mark.asyncio
    async def test_budget_exceeded_includes_partial_work(self):
        """When budget is exceeded after some tool calls, output should mention
        executed tools. Budget exceeded only occurs during pure-thinking rounds
        (no tool_calls, no final answer), so response_text is always empty there."""
        engine = _make_reasoning_engine(max_iterations=10, max_thinking_tokens=200)

        tool_call = SimpleNamespace(
            id="tc1",
            function=SimpleNamespace(name="web_search", arguments='{"query": "test"}'),
        )
        responses = [
            # Tool call (iteration 1)
            SimpleNamespace(content="", reasoning_content="", tool_calls=[tool_call]),
            # Thinking-only after tool call (within budget)
            SimpleNamespace(content="", reasoning_content="Thinking more...", tool_calls=None),
            # Thinking-only (budget exceeded: 150*3=450 > 200)
            SimpleNamespace(content="", reasoning_content="Still thinking...", tool_calls=None),
        ]

        shared_records = []

        def mock_chat(*args, **kwargs):
            shared_records.append(LLMCallRecord(
                reasoning_tokens=150, prompt_tokens=50, completion_tokens=200, total_tokens=400,
            ))
            return responses.pop(0)

        engine.llm_client.chat_with_tools = AsyncMock(side_effect=mock_chat)
        engine.llm_client.get_call_records = MagicMock(return_value=shared_records)

        # Mock the tool
        mock_tool = MagicMock()
        mock_tool.name = "web_search"
        mock_tool.traced_execute = AsyncMock(return_value="Search results")
        engine.tools = {"web_search": mock_tool}
        mock_tool.to_openai_tool = MagicMock(return_value={"type": "function", "function": {"name": "web_search"}})
        engine.tool_schemas = [mock_tool.to_openai_tool()]

        result = await engine.execute("test task")

        assert result.success is False
        assert "Thinking budget exceeded" in result.output
        # Tool calls made before budget exceeded should be in output
        assert "Tools executed: [web_search]" in result.output
        # Pure thinking round has empty response_text
        assert "Last partial response: (empty)" in result.output
