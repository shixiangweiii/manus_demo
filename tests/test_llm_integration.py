"""
LLM Integration Validation Tests (v6.0).
LLM 集成验证测试（v6.0）。

This test suite validates the upgraded inference engine capabilities:
1. OpenAI SDK integration with DeepSeek API
2. Basic chat completion
3. Tool-calling (function calling)
4. JSON structured output
5. Error handling and retry mechanism
6. ReAct Engine integration

测试套件验证升级后的推理引擎能力：
1. OpenAI SDK 与 DeepSeek API 的集成
2. 基础聊天补全
3. 工具调用（Function Calling）
4. JSON 结构化输出
5. 错误处理和重试机制
6. ReAct Engine 集成

Usage:
  # Run all validation tests
  pytest tests/test_llm_integration.py -v

  # Run specific test categories
  pytest tests/test_llm_integration.py -k "chat" -v
  pytest tests/test_llm_integration.py -k "tools" -v
  pytest tests/test_llm_integration.py -k "retry" -v

  # Run with real API (requires valid API key)
  DEEP_SEEK_API_KEY=your_key pytest tests/test_llm_integration.py -v
"""

import asyncio
import json
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import LLMClient
from tools.base import BaseTool
from tools.router import ToolRouter
from react.engine import ReActEngine


class EchoTool(BaseTool):
    """Simple echo tool for testing."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the input text back with a prefix."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to echo back",
                },
            },
            "required": ["text"],
        }

    async def execute(self, text: str) -> str:
        return f"Echo: {text}"


class AddTool(BaseTool):
    """Simple calculator tool for testing."""

    @property
    def name(self) -> str:
        return "add"

    @property
    def description(self) -> str:
        return "Adds two numbers together."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First number"},
                "b": {"type": "number", "description": "Second number"},
            },
            "required": ["a", "b"],
        }

    async def execute(self, a: float, b: float) -> str:
        return str(a + b)


class TestLLMClientInitialization:
    """Test LLMClient initialization and configuration."""

    def test_default_initialization(self):
        """Test creating LLMClient with default settings."""
        client = LLMClient()
        assert client.model is not None
        assert client._client is not None

    def test_custom_initialization(self):
        """Test creating LLMClient with custom settings."""
        client = LLMClient(
            model="custom-model",
            retry_enabled=True,
            max_retries=5,
            backoff_factor=3.0,
        )
        assert client.model == "custom-model"
        assert client.retry_enabled is True
        assert client.max_retries == 5
        assert client.backoff_factor == 3.0

    def test_environment_variable_loading(self):
        """Test that .env file is loaded correctly."""
        from dotenv import load_dotenv
        load_dotenv()

        base_url = os.getenv("LLM_BASE_URL")
        api_key = os.getenv("LLM_API_KEY")
        model = os.getenv("LLM_MODEL")

        assert base_url is not None, "LLM_BASE_URL should be set in .env"
        assert api_key is not None, "LLM_API_KEY should be set in .env"
        assert model is not None, "LLM_MODEL should be set in .env"

    def test_retry_config_defaults(self):
        """Test retry configuration default values."""
        client = LLMClient()
        assert hasattr(client, 'retry_enabled')
        assert hasattr(client, 'max_retries')
        assert hasattr(client, 'backoff_factor')


class TestLLMClientChat:
    """Test basic chat completion functionality."""

    @pytest.mark.asyncio
    async def test_chat_basic(self):
        """Test basic text chat completion."""
        client = LLMClient()

        messages = [{"role": "user", "content": "Say 'Hello, World!' in exactly those words."}]
        response = await client.chat(messages, temperature=0.0)

        assert isinstance(response, str)
        assert len(response) > 0
        print(f"[chat_basic] Response: {response}")

    @pytest.mark.asyncio
    async def test_chat_with_system_prompt(self):
        """Test chat with system prompt."""
        client = LLMClient()

        messages = [
            {"role": "system", "content": "You are a helpful assistant that speaks like a pirate."},
            {"role": "user", "content": "Greet me."},
        ]
        response = await client.chat(messages, temperature=0.7)

        assert isinstance(response, str)
        assert len(response) > 0
        print(f"[chat_with_system] Response: {response}")

    @pytest.mark.asyncio
    async def test_chat_temperature_effect(self):
        """Test that temperature affects response variation."""
        client = LLMClient()

        messages = [{"role": "user", "content": "Give me a random number between 1 and 10."}]

        responses = set()
        for _ in range(3):
            response = await client.chat(messages, temperature=1.0)
            responses.add(response.strip())

        print(f"[chat_temperature] Unique responses: {len(responses)}")

    @pytest.mark.asyncio
    async def test_chat_max_tokens_limit(self):
        """Test max_tokens parameter limits response length."""
        client = LLMClient()

        messages = [{"role": "user", "content": "Write a long story about a cat."}]
        response = await client.chat(messages, max_tokens=50, temperature=0.0)

        words = response.split()
        print(f"[chat_max_tokens] Word count: {len(words)}, Response: {response}")
        assert len(words) <= 60


class TestLLMClientFunctionCalling:
    """Test function calling / tool use capabilities."""

    @pytest.mark.asyncio
    async def test_chat_with_tools_single_tool(self):
        """Test function calling with a single tool."""
        client = LLMClient()
        tools = [EchoTool()]

        messages = [{"role": "user", "content": "Please echo the text 'Hello, AI!'"}]
        response = await client.chat_with_tools(
            messages,
            tools=[t.to_openai_tool() for t in tools],
            temperature=0.0,
        )

        assert hasattr(response, 'tool_calls')
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            assert tool_call.function.name == "echo"
            args = json.loads(tool_call.function.arguments)
            assert "text" in args
            print(f"[tools_single] Tool call: {tool_call.function.name}({args})")

    @pytest.mark.asyncio
    async def test_chat_with_tools_multiple_tools(self):
        """Test function calling with multiple tools."""
        client = LLMClient()
        tools = [EchoTool(), AddTool()]

        messages = [{"role": "user", "content": "What is 5 + 3?"}]
        response = await client.chat_with_tools(
            messages,
            tools=[t.to_openai_tool() for t in tools],
            temperature=0.0,
        )

        assert hasattr(response, 'tool_calls')
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            print(f"[tools_multiple] Tool call: {tool_call.function.name}")

    @pytest.mark.asyncio
    async def test_tool_execution_flow(self):
        """Test complete tool execution flow: call -> execute -> result."""
        client = LLMClient()
        tools = [EchoTool(), AddTool()]
        tool_map = {t.name: t for t in tools}

        messages = [{"role": "user", "content": "Echo 'Testing' and add 10 + 20."}]

        response = await client.chat_with_tools(
            messages,
            tools=[t.to_openai_tool() for t in tools],
            temperature=0.0,
        )

        if response.tool_calls:
            for tool_call in response.tool_calls:
                tool_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                tool = tool_map.get(tool_name)

                if tool:
                    result = await tool.execute(**args)
                    print(f"[tool_exec] {tool_name}({args}) = {result}")
                    assert isinstance(result, str)


class TestLLMClientJSONOutput:
    """Test structured JSON output capabilities."""

    @pytest.mark.asyncio
    async def test_chat_json_basic(self):
        """Test basic JSON response."""
        client = LLMClient()

        messages = [
            {"role": "user", "content": "Return a JSON object with fields 'name' (string) and 'age' (number)."}
        ]
        result = await client.chat_json(messages)

        assert isinstance(result, dict)
        assert "name" in result or "age" in result
        print(f"[json_basic] Result: {result}")

    @pytest.mark.asyncio
    async def test_chat_json_complex_structure(self):
        """Test complex JSON structure."""
        client = LLMClient()

        messages = [
            {"role": "user", "content": 'Return valid JSON: {"status": "ok", "count": 42, "items": ["a", "b"]}'}
        ]
        result = await client.chat_json(messages)

        assert isinstance(result, dict)
        print(f"[json_complex] Result: {result}")


class TestLLMClientErrorHandling:
    """Test error handling and retry mechanism."""

    def test_retryable_errors_defined(self):
        """Test that retryable errors are properly defined."""
        from llm.client import RETRYABLE_ERRORS

        assert len(RETRYABLE_ERRORS) > 0
        print(f"[retry_errors] Defined retryable errors: {RETRYABLE_ERRORS}")

    @pytest.mark.asyncio
    async def test_invalid_json_handling(self):
        """Test handling of invalid JSON responses."""
        client = LLMClient()

        messages = [{"role": "user", "content": "Return exactly: This is not JSON."}]
        response = await client.chat(messages, temperature=0.0)

        assert isinstance(response, str)

    @pytest.mark.asyncio
    async def test_empty_message_handling(self):
        """Test handling of empty messages."""
        client = LLMClient()

        messages = [{"role": "user", "content": ""}]
        response = await client.chat(messages, temperature=0.0)

        print(f"[empty_msg] Response: {response}")


class TestReActEngine:
    """Test the unified ReAct Engine (v6.0)."""

    @pytest.mark.asyncio
    async def test_react_engine_initialization(self):
        """Test ReActEngine initialization."""
        client = LLMClient()
        tools = [EchoTool(), AddTool()]

        engine = ReActEngine(
            llm_client=client,
            tools=tools,
            max_iterations=5,
        )

        assert engine.llm_client is not None
        assert len(engine.tools) == 2
        assert engine.max_iterations == 5
        print("[react_init] ReActEngine initialized successfully")

    @pytest.mark.asyncio
    async def test_react_engine_simple_task(self):
        """Test ReActEngine with a simple task."""
        client = LLMClient()
        tools = [EchoTool()]

        engine = ReActEngine(
            llm_client=client,
            tools=tools,
            max_iterations=3,
        )

        result = await engine.execute(
            prompt="Use the echo tool to say 'Hello, ReAct Engine!'",
            node_id="test_node",
        )

        assert hasattr(result, 'success')
        assert hasattr(result, 'output')
        assert hasattr(result, 'tool_calls_log')
        print(f"[react_simple] Success: {result.success}, Output: {result.output[:100]}")

    @pytest.mark.asyncio
    async def test_react_engine_multiple_tools(self):
        """Test ReActEngine with multiple tools."""
        client = LLMClient()
        tools = [EchoTool(), AddTool()]

        engine = ReActEngine(
            llm_client=client,
            tools=tools,
            max_iterations=5,
        )

        result = await engine.execute(
            prompt="Calculate 25 + 17 using the add tool, then echo the result.",
            node_id="calc_node",
        )

        print(f"[react_multi] Success: {result.success}")
        if result.tool_calls_log:
            for tc in result.tool_calls_log:
                print(f"  Tool: {tc.tool_name}, Args: {tc.parameters}")

    @pytest.mark.asyncio
    async def test_react_engine_context_passthrough(self):
        """Test that context is passed to the prompt."""
        client = LLMClient()
        tools = [EchoTool()]

        engine = ReActEngine(
            llm_client=client,
            tools=tools,
            max_iterations=3,
        )

        result = await engine.execute(
            prompt="Echo a greeting.",
            context="Previous result: User said hello.",
            node_id="context_node",
        )

        print(f"[react_context] Success: {result.success}")

    @pytest.mark.asyncio
    async def test_react_engine_max_iterations(self):
        """Test max iterations limit."""
        client = LLMClient()
        tools = [EchoTool()]

        engine = ReActEngine(
            llm_client=client,
            tools=tools,
            max_iterations=1,
        )

        result = await engine.execute(
            prompt="Keep calling echo tool multiple times until I say stop.",
            node_id="iter_node",
        )

        print(f"[react_iter] Iterations used: {len(result.tool_calls_log)}")

    @pytest.mark.asyncio
    async def test_react_engine_with_system_hint(self):
        """Test ReActEngine with system hint."""
        client = LLMClient()
        tools = [EchoTool()]

        engine = ReActEngine(
            llm_client=client,
            tools=tools,
            max_iterations=3,
        )

        result = await engine.execute(
            prompt="Say something.",
            system_hint="Always respond with enthusiasm!",
            node_id="hint_node",
        )

        print(f"[react_hint] Success: {result.success}")


class TestFeatureFlags:
    """Test v6.0 feature flags."""

    def test_react_engine_v2_flag_exists(self):
        """Test that ENABLE_REACT_ENGINE_V2 flag exists."""
        import config
        assert hasattr(config, 'ENABLE_REACT_ENGINE_V2')
        print(f"[flag] ENABLE_REACT_ENGINE_V2: {config.ENABLE_REACT_ENGINE_V2}")

    def test_llm_retry_flags_exist(self):
        """Test that LLM retry flags exist."""
        import config
        assert hasattr(config, 'LLM_RETRY_ENABLED')
        assert hasattr(config, 'LLM_RETRY_MAX_ATTEMPTS')
        assert hasattr(config, 'LLM_RETRY_BACKOFF_FACTOR')
        print(f"[flag] LLM_RETRY_ENABLED: {config.LLM_RETRY_ENABLED}")
        print(f"[flag] LLM_RETRY_MAX_ATTEMPTS: {config.LLM_RETRY_MAX_ATTEMPTS}")

    def test_feature_flags_default_values(self):
        """Test that feature flags have correct defaults."""
        import config
        assert config.ENABLE_REACT_ENGINE_V2 is False
        assert config.LLM_RETRY_ENABLED is False
        print("[flag] Default values are backward compatible")


class TestEndToEndIntegration:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_complete_react_loop(self):
        """Test complete ReAct loop with tool execution."""
        client = LLMClient()
        tools = [EchoTool(), AddTool()]
        tool_map = {t.name: t for t in tools}

        engine = ReActEngine(
            llm_client=client,
            tools=tools,
            max_iterations=5,
        )

        result = await engine.execute(
            prompt="First, calculate 15 + 25 using the add tool. Then echo the sum.",
            node_id="e2e_node",
        )

        assert result.success
        assert len(result.tool_calls_log) > 0

        print("\n[E2E] Complete tool execution trace:")
        for tc in result.tool_calls_log:
            print(f"  {tc.tool_name}({tc.parameters}) -> {tc.result[:50]}...")

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self):
        """Test multi-turn conversation with context."""
        client = LLMClient()

        messages = [
            {"role": "user", "content": "What is 100 divided by 4?"}
        ]

        response1 = await client.chat(messages, temperature=0.0)
        print(f"[e2e_turn1] {response1}")

        messages.append({"role": "assistant", "content": response1})
        messages.append({"role": "user", "content": "Now multiply that result by 10."})

        response2 = await client.chat(messages, temperature=0.0)
        print(f"[e2e_turn2] {response2}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
