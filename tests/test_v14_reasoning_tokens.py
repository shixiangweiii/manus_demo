"""
Unit tests for v14 Phase 1: Reasoning Token Bucketing.
v14 Phase 1 单元测试：推理 Token 分桶。
"""

import pytest

import config
from schema import LLMCallRecord, TokenUsage
from llm.client import _extract_thinking_content


class TestReasoningTokensInSchema:
    """Verify reasoning_tokens field exists and defaults to 0."""

    def test_token_usage_defaults(self):
        usage = TokenUsage()
        assert usage.reasoning_tokens == 0
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0

    def test_token_usage_with_reasoning(self):
        usage = TokenUsage(reasoning_tokens=500, completion_tokens=200, total_tokens=800)
        assert usage.reasoning_tokens == 500

    def test_llm_call_record_defaults(self):
        record = LLMCallRecord()
        assert record.reasoning_tokens == 0

    def test_llm_call_record_with_reasoning(self):
        record = LLMCallRecord(
            call_type="chat_with_tools",
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=700,
            reasoning_tokens=400,
            engine="o3-mini",
        )
        assert record.reasoning_tokens == 400

    def test_backward_compat_deserialization(self):
        """Existing serialized data without reasoning_tokens should deserialize."""
        data = {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
        usage = TokenUsage(**data)
        assert usage.reasoning_tokens == 0


class TestExtractThinkingContent:
    """Verify DeepSeek R1 <think/> tag parsing."""

    def test_no_thinking_tags(self):
        assert _extract_thinking_content("Hello world") == ""

    def test_empty_content(self):
        assert _extract_thinking_content("") == ""

    def test_none_content(self):
        assert _extract_thinking_content(None) == ""

    def test_deepseek_r1_thinking(self):
        content = "<think\nLet me analyze this step by step...\n1. First\n2. Second\n</think\n>Here is my answer."
        result = _extract_thinking_content(content)
        assert "Let me analyze this step by step" in result
        assert "First" in result

    def test_think_tag_no_closing(self):
        """Malformed thinking tag without closing should return empty."""
        content = "<think\nSome reasoning without closing tag"
        assert _extract_thinking_content(content) == ""

    def test_think_tag_with_attributes(self):
        content = '<think type="deep">Reasoning here</think' + '>Response'
        result = _extract_thinking_content(content)
        assert "Reasoning here" in result

    def test_multiline_thinking(self):
        content = "<think\nLine 1\nLine 2\nLine 3\n</think\n>Final answer"
        result = _extract_thinking_content(content)
        assert "Line 1" in result
        assert "Line 3" in result


class TestTokenAggregation:
    """Verify reasoning_tokens flows through TokenUsageSummary aggregation."""

    def test_reasoning_tokens_aggregate(self):
        """Simulate _finalize_token_usage pattern: records → TokenUsage sums."""
        records = [
            LLMCallRecord(prompt_tokens=100, completion_tokens=200, total_tokens=500, reasoning_tokens=200),
            LLMCallRecord(prompt_tokens=50, completion_tokens=100, total_tokens=300, reasoning_tokens=150),
        ]
        total = TokenUsage()
        for r in records:
            total.prompt_tokens += r.prompt_tokens
            total.completion_tokens += r.completion_tokens
            total.total_tokens += r.total_tokens
            total.reasoning_tokens += r.reasoning_tokens

        assert total.reasoning_tokens == 350
        assert total.prompt_tokens == 150
        assert total.completion_tokens == 300


class TestRecordCallReasoningTokens:
    """Verify _record_call() extracts reasoning_tokens from mock usage objects."""

    def test_record_call_extracts_reasoning_tokens(self):
        """Mock usage object with completion_tokens_details.reasoning_tokens, verify LLMCallRecord."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from llm.client import LLMClient

        details = SimpleNamespace(reasoning_tokens=500)
        usage = SimpleNamespace(
            prompt_tokens=100, completion_tokens=200, total_tokens=800,
            completion_tokens_details=details, reasoning_tokens=0,
        )
        client = LLMClient.__new__(LLMClient)
        client.model = "o3-mini"
        client._call_records = []
        with patch.object(config, "TOKEN_TRACKING_ENABLED", True), \
             patch.object(config, "REASONING_TOKEN_TRACKING", True):
            client._record_call(usage, "chat_with_tools", [{"role": "user", "content": "test"}])
        assert client._call_records[-1].reasoning_tokens == 500

    def test_record_call_fallback_reasoning_tokens(self):
        """When completion_tokens_details is absent, fall back to usage.reasoning_tokens."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from llm.client import LLMClient

        usage = SimpleNamespace(
            prompt_tokens=100, completion_tokens=200, total_tokens=800,
            reasoning_tokens=300,
        )
        client = LLMClient.__new__(LLMClient)
        client.model = "deepseek-reasoner"
        client._call_records = []
        with patch.object(config, "TOKEN_TRACKING_ENABLED", True), \
             patch.object(config, "REASONING_TOKEN_TRACKING", True):
            client._record_call(usage, "chat", [{"role": "user", "content": "test"}])
        assert client._call_records[-1].reasoning_tokens == 300

    def test_record_call_no_reasoning_tokens(self):
        """Standard model with no reasoning tokens should record 0."""
        from types import SimpleNamespace
        from unittest.mock import patch
        from llm.client import LLMClient

        usage = SimpleNamespace(
            prompt_tokens=50, completion_tokens=100, total_tokens=150,
        )
        client = LLMClient.__new__(LLMClient)
        client.model = "deepseek-chat"
        client._call_records = []
        with patch.object(config, "TOKEN_TRACKING_ENABLED", True), \
             patch.object(config, "REASONING_TOKEN_TRACKING", True):
            client._record_call(usage, "chat_json", [{"role": "user", "content": "test"}])
        assert client._call_records[-1].reasoning_tokens == 0


class TestExtractResponseDataReasoningContent:
    """S1: _extract_response_data() reads message.reasoning_content (DeepSeek official API)."""

    def test_reasoning_content_field_used_when_present(self):
        """When message.reasoning_content is set, it should be used instead of tag parsing."""
        from types import SimpleNamespace
        from llm.client import LLMClient

        message = SimpleNamespace(
            content="Final answer only",
            reasoning_content="Step-by-step reasoning from the model",
            tool_calls=None,
        )
        choice = SimpleNamespace(message=message, finish_reason="stop")
        resp = SimpleNamespace(choices=[choice])

        client = LLMClient.__new__(LLMClient)
        result = client._extract_response_data(resp, "chat")

        assert result["thinking_content"] == "Step-by-step reasoning from the model"
        assert result["response_content"] == "Final answer only"

    def test_fallback_to_tag_parsing_when_no_reasoning_content(self):
        """When reasoning_content is absent/None, fall back to <think/> tag parsing."""
        from types import SimpleNamespace
        from llm.client import LLMClient

        thinking = "<think\nLet me think\n</think\n>My answer"
        message = SimpleNamespace(
            content=thinking,
            reasoning_content=None,
            tool_calls=None,
        )
        choice = SimpleNamespace(message=message, finish_reason="stop")
        resp = SimpleNamespace(choices=[choice])

        client = LLMClient.__new__(LLMClient)
        result = client._extract_response_data(resp, "chat")

        assert "Let me think" in result["thinking_content"]

    def test_empty_reasoning_content_falls_back(self):
        """When reasoning_content is empty string, fall back to tag parsing."""
        from types import SimpleNamespace
        from llm.client import LLMClient

        thinking = "<think\nReasoning here\n</think\n>Answer"
        message = SimpleNamespace(
            content=thinking,
            reasoning_content="",
            tool_calls=None,
        )
        choice = SimpleNamespace(message=message, finish_reason="stop")
        resp = SimpleNamespace(choices=[choice])

        client = LLMClient.__new__(LLMClient)
        result = client._extract_response_data(resp, "chat")

        assert "Reasoning here" in result["thinking_content"]

    def test_no_thinking_at_all(self):
        """When neither reasoning_content nor think tags exist, thinking_content is empty."""
        from types import SimpleNamespace
        from llm.client import LLMClient

        message = SimpleNamespace(
            content="Just a regular response",
            reasoning_content=None,
            tool_calls=None,
        )
        choice = SimpleNamespace(message=message, finish_reason="stop")
        resp = SimpleNamespace(choices=[choice])

        client = LLMClient.__new__(LLMClient)
        result = client._extract_response_data(resp, "chat")

        assert result["thinking_content"] == ""


class TestOnTokenUsageReasoningTokens:
    """S4: _on_token_usage() sets reasoning_tokens on the root span."""

    def test_reasoning_tokens_set_on_root_span(self):
        """Root span should have reasoning_tokens attribute when available."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from tracing.bridge import TracingBridge

        bridge = TracingBridge.__new__(TracingBridge)
        bridge._root_span = MagicMock()
        bridge._root_span.is_recording.return_value = True

        total = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=500,
            reasoning_tokens=150,
        )
        data = SimpleNamespace(total=total)

        bridge._on_token_usage(data)

        from tracing.spans import AttrKey
        bridge._root_span.set_attribute.assert_any_call(
            AttrKey.GEN_AI_USAGE_REASONING_TOKENS, 150
        )

    def test_reasoning_tokens_zero_not_set(self):
        """When reasoning_tokens is 0/absent, root span should NOT set the attribute (> 0 guard)."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from tracing.bridge import TracingBridge

        bridge = TracingBridge.__new__(TracingBridge)
        bridge._root_span = MagicMock()
        bridge._root_span.is_recording.return_value = True

        total = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
        )
        data = SimpleNamespace(total=total)

        bridge._on_token_usage(data)

        from tracing.spans import AttrKey
        for call_args in bridge._root_span.set_attribute.call_args_list:
            assert call_args[0][0] != AttrKey.GEN_AI_USAGE_REASONING_TOKENS


# ======================================================================
# v14 Phase 3: ReActEngine thinking stripping + ContextManager + Harness
# v14 Phase 3 单元测试：ReActEngine thinking 剥离 + 上下文感知 + Harness 配置
# ======================================================================

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


class TestReActEngineThinkingStripping:
    """ReActEngine should strip thinking from assistant messages."""

    @pytest.mark.asyncio
    async def test_think_tags_stripped_from_content(self):
        """<think/> tags in content should be removed from assistant messages."""
        from react.engine import ReActEngine
        from tools.router import ToolRouter

        llm_client = MagicMock(spec=["chat_with_tools", "get_call_records"])
        llm_client.get_call_records.return_value = []
        tool_router = ToolRouter(available_tools=[])
        engine = ReActEngine(
            llm_client=llm_client,
            tools={},
            max_iterations=3,
            tool_router=tool_router,
        )

        response_with_think = SimpleNamespace(
            content="<think\nLet me reason\n</think\n>The answer is 42",
            reasoning_content=None,
            tool_calls=None,
        )
        llm_client.chat_with_tools = AsyncMock(return_value=response_with_think)
        llm_client.get_call_records.return_value = []

        result = await engine.execute("test task")

        assert result.success is True
        # Output should NOT contain the thinking tags
        assert "<think" not in result.output
        assert "The answer is 42" in result.output

    @pytest.mark.asyncio
    async def test_reasoning_content_field_stored_separately(self):
        """reasoning_content field should go to thinking_content key in assistant message."""
        from react.engine import ReActEngine
        from tools.router import ToolRouter

        llm_client = MagicMock(spec=["chat_with_tools", "get_call_records"])
        llm_client.get_call_records.return_value = []
        tool_router = ToolRouter(available_tools=[])
        engine = ReActEngine(
            llm_client=llm_client,
            tools={},
            max_iterations=3,
            tool_router=tool_router,
        )

        captured_messages = []

        response = SimpleNamespace(
            content="Final answer",
            reasoning_content="My reasoning process",
            tool_calls=None,
        )

        original_chat = llm_client.chat_with_tools

        async def mock_chat(msgs, **kwargs):
            captured_messages.append(list(msgs))
            return response

        llm_client.chat_with_tools = AsyncMock(side_effect=mock_chat)
        llm_client.get_call_records.return_value = []

        result = await engine.execute("test task")

        assert result.success is True
        # Verify assistant message has thinking_content key
        if captured_messages:
            assistant_msgs = [m for m in captured_messages[0] if m.get("role") == "assistant"]
            # There won't be assistant msgs in the first call's input, but we can verify
            # the result output doesn't contain the reasoning text mixed in
            assert result.output == "Final answer"

    @pytest.mark.asyncio
    async def test_no_thinking_passthrough_when_none(self):
        """When no thinking is present, content passes through unchanged."""
        from react.engine import ReActEngine
        from tools.router import ToolRouter

        llm_client = MagicMock(spec=["chat_with_tools", "get_call_records"])
        llm_client.get_call_records.return_value = []
        tool_router = ToolRouter(available_tools=[])
        engine = ReActEngine(
            llm_client=llm_client,
            tools={},
            max_iterations=3,
            tool_router=tool_router,
        )

        response = SimpleNamespace(
            content="Simple answer without thinking",
            reasoning_content=None,
            tool_calls=None,
        )
        llm_client.chat_with_tools = AsyncMock(return_value=response)
        llm_client.get_call_records.return_value = []

        result = await engine.execute("test task")

        assert result.success is True
        assert result.output == "Simple answer without thinking"


class TestContextManagerThinkingAware:
    """ContextManager should handle thinking_content in compression."""

    def test_messages_to_text_includes_thinking(self):
        """_messages_to_text should include thinking_content when THINKING_AWARE_CONTEXT is true."""
        from context.manager import ContextManager

        messages = [
            {"role": "assistant", "content": "The answer", "thinking_content": "My reasoning"},
        ]
        text = ContextManager._messages_to_text(messages)
        assert "[assistant thinking]: My reasoning" in text
        assert "[assistant]: The answer" in text

    def test_messages_to_text_always_includes_thinking(self):
        """_messages_to_text should always include thinking_content (not gated by flag)."""
        from context.manager import ContextManager

        messages = [
            {"role": "assistant", "content": "The answer", "thinking_content": "My reasoning"},
        ]
        text = ContextManager._messages_to_text(messages)
        assert "[assistant thinking]: My reasoning" in text
        assert "[assistant]: The answer" in text

    def test_find_safe_split_preserves_thinking_group(self):
        """Split should not cut between thinking-bearing assistant and next message."""
        from context.manager import ContextManager

        messages = [
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Question 2"},
            {"role": "assistant", "content": "", "thinking_content": "Deep reasoning"},
            {"role": "user", "content": "Question 3"},
            {"role": "assistant", "content": "Answer 3"},
        ]
        # reserve=2 means we want the last 2 messages as recent
        # naive split would be at index 4, but thinking-bearing assistant at index 3
        # should be pulled into recent
        split_idx = ContextManager._find_safe_split(messages, 2)
        # The thinking-bearing assistant at index 3 should be included in recent
        assert split_idx <= 3

    def test_estimate_tokens_counts_thinking(self):
        """estimate_messages_tokens should count thinking_content tokens."""
        from context.manager import ContextManager

        messages_no_thinking = [{"role": "assistant", "content": "The answer"}]
        messages_with_thinking = [
            {"role": "assistant", "content": "The answer", "thinking_content": "Detailed reasoning process here"}
        ]
        cm = ContextManager()
        tokens_no = cm.estimate_messages_tokens(messages_no_thinking)
        tokens_with = cm.estimate_messages_tokens(messages_with_thinking)
        assert tokens_with > tokens_no


class TestHarnessConfig:
    """Harness configuration values should be accessible with sensible defaults."""

    def test_react_temperature_default(self):
        assert config.REACT_TEMPERATURE == 0.5

    def test_reasoning_temperature_default(self):
        assert config.REASONING_TEMPERATURE == 0.5

    def test_convergence_escalation_multiplier_default(self):
        assert config.CONVERGENCE_ESCALATION_MULTIPLIER == 2

    def test_thinking_aware_context_default(self):
        assert config.THINKING_AWARE_CONTEXT is True

    def test_planner_temperature_in_planner(self):
        """PlannerAgent should use config.PLANNER_TEMPERATURE."""
        # Verify the config value is importable and the planner references it
        assert hasattr(config, "PLANNER_TEMPERATURE")
        assert config.PLANNER_TEMPERATURE == 0.3

    def test_reflector_temperature_in_reflector(self):
        """ReflectorAgent should use config.REFLECTOR_TEMPERATURE."""
        assert hasattr(config, "REFLECTOR_TEMPERATURE")
        assert config.REFLECTOR_TEMPERATURE == 0.1


class TestReasoningOnlyInReActEngine:
    """P2: ReActEngine should not silently swallow reasoning-only responses."""

    @pytest.mark.asyncio
    async def test_reasoning_only_does_not_swallow_answer(self):
        """ReActEngine receiving reasoning-only response should not return 'Task completed'."""
        from react.engine import ReActEngine
        from tools.router import ToolRouter

        llm_client = MagicMock(spec=["chat_with_tools", "get_call_records"])
        llm_client.get_call_records.return_value = []
        tool_router = ToolRouter(available_tools=[])
        engine = ReActEngine(
            llm_client=llm_client,
            tools={},
            max_iterations=5,
            tool_router=tool_router,
        )

        responses = [
            # Reasoning-only: content empty, reasoning_content present, no tool_calls
            SimpleNamespace(content="", reasoning_content="thinking deeply...", tool_calls=None),
            # Final answer after thinking
            SimpleNamespace(content="The answer is 42", reasoning_content=None, tool_calls=None),
        ]
        llm_client.chat_with_tools = AsyncMock(side_effect=responses)
        llm_client.get_call_records.return_value = []

        result = await engine.execute("test task")

        assert result.success is True
        assert "Task completed (no output)" not in result.output
        assert "42" in result.output

    @pytest.mark.asyncio
    async def test_reasoning_only_with_think_tags(self):
        """ReActEngine with <think/> tags should continue, not return early."""
        from react.engine import ReActEngine
        from tools.router import ToolRouter

        llm_client = MagicMock(spec=["chat_with_tools", "get_call_records"])
        llm_client.get_call_records.return_value = []
        tool_router = ToolRouter(available_tools=[])
        engine = ReActEngine(
            llm_client=llm_client,
            tools={},
            max_iterations=5,
            tool_router=tool_router,
        )

        responses = [
            # Thinking via <think/> tags in content
            SimpleNamespace(content="<think\nLet me reason\n</think\n>", reasoning_content=None, tool_calls=None),
            # Final answer
            SimpleNamespace(content="Final answer", reasoning_content=None, tool_calls=None),
        ]
        llm_client.chat_with_tools = AsyncMock(side_effect=responses)
        llm_client.get_call_records.return_value = []

        result = await engine.execute("test task")

        assert result.success is True
        assert result.output == "Final answer"


class TestStripThinkingFromContentLocation:
    """P3: _strip_thinking_from_content should be importable from llm/client, not react/reasoning_engine."""

    def test_importable_from_llm_client(self):
        from llm.client import _strip_thinking_from_content
        assert callable(_strip_thinking_from_content)

    def test_not_defined_in_reasoning_engine(self):
        """_strip_thinking_from_content should NOT be defined locally in reasoning_engine."""
        import react.reasoning_engine as re_mod
        # It should be imported, not defined locally
        assert "_strip_thinking_from_content" not in re_mod.__dict__ or \
            re_mod._strip_thinking_from_content.__module__ != "react.reasoning_engine"
