"""Tests for API message sanitizer and chat_json fallback scope.

Verifies that internal fields (thinking_content) are stripped before
sending messages to the OpenAI-compatible API, and that chat_json only
falls back on BadRequestError related to response_format.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.client import _sanitize_messages_for_api, _INTERNAL_MESSAGE_KEYS


# ---------------------------------------------------------------------------
# _sanitize_messages_for_api unit tests
# ---------------------------------------------------------------------------

class TestSanitizeMessages:
    """Verify the sanitizer strips internal keys and preserves standard ones."""

    def test_strips_thinking_content(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "thinking_content": "deep thoughts"},
        ]
        result = _sanitize_messages_for_api(msgs)
        assert all("thinking_content" not in m for m in result)

    def test_preserves_standard_keys(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "foo", "arguments": "{}"}}]},
            {"role": "tool", "content": "result", "tool_call_id": "tc1", "name": "foo"},
        ]
        result = _sanitize_messages_for_api(msgs)
        assert len(result) == 4
        assert result[0] == {"role": "system", "content": "You are helpful."}
        assert result[1] == {"role": "user", "content": "hello"}
        assert "tool_calls" in result[2]
        assert result[3]["tool_call_id"] == "tc1"
        assert result[3]["name"] == "foo"

    def test_does_not_mutate_original(self):
        msgs = [
            {"role": "assistant", "content": "hi", "thinking_content": "deep"},
        ]
        _sanitize_messages_for_api(msgs)
        assert msgs[0]["thinking_content"] == "deep"

    def test_empty_list_returns_empty(self):
        assert _sanitize_messages_for_api([]) == []

    def test_messages_without_internal_keys_pass_through(self):
        msgs = [{"role": "user", "content": "hello"}]
        result = _sanitize_messages_for_api(msgs)
        assert result == msgs

    def test_internal_keys_constant_contains_thinking_content(self):
        assert "thinking_content" in _INTERNAL_MESSAGE_KEYS


# ---------------------------------------------------------------------------
# Integration-style tests: verify LLMClient methods sanitize before API call
# ---------------------------------------------------------------------------

def _make_mock_client():
    """Create an LLMClient with a mocked AsyncOpenAI that captures API args."""
    with patch("llm.client.AsyncOpenAI"):
        from llm.client import LLMClient
        client = LLMClient.__new__(LLMClient)
        client.model = "test-model"
        client._client = AsyncMock()
        client.retry_enabled = False
        client.max_retries = 0
        client.backoff_factor = 1.0
        client._call_records = []
        return client


def _make_response(content="hello", tool_calls=None, usage=None):
    """Build a minimal mock OpenAI response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = ""
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


@pytest.fixture
def captured_messages():
    """Return a list that will be populated with the messages arg to create()."""
    return []


def _setup_chat_mock(client, captured, content="hello"):
    resp = _make_response(content=content)
    async def capture_create(**kwargs):
        captured.append(kwargs.get("messages", []))
        return resp
    client._client.chat.completions.create = capture_create


@pytest.mark.asyncio
async def test_chat_strips_internal_keys(captured_messages):
    client = _make_mock_client()
    _setup_chat_mock(client, captured_messages)

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "answer", "thinking_content": "thinking..."},
    ]
    await client.chat(messages)

    assert len(captured_messages) == 1
    api_msgs = captured_messages[0]
    assert all("thinking_content" not in m for m in api_msgs)
    # Original messages unchanged
    assert messages[1]["thinking_content"] == "thinking..."


@pytest.mark.asyncio
async def test_chat_with_tools_strips_internal_keys(captured_messages):
    client = _make_mock_client()
    resp = _make_response()
    async def capture_create(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        return resp
    client._client.chat.completions.create = capture_create

    messages = [
        {"role": "user", "content": "search weather"},
        {"role": "assistant", "content": "", "thinking_content": "planning search", "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "weather"}'}}]},
        {"role": "tool", "content": "sunny", "tool_call_id": "tc1", "name": "web_search"},
    ]
    await client.chat_with_tools(messages, tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}])

    assert len(captured_messages) == 1
    api_msgs = captured_messages[0]
    assert all("thinking_content" not in m for m in api_msgs)


@pytest.mark.asyncio
async def test_chat_json_strips_internal_keys(captured_messages):
    client = _make_mock_client()
    resp = _make_response(content='{"plan": "step1"}')
    async def capture_create(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        return resp
    client._client.chat.completions.create = capture_create

    messages = [
        {"role": "user", "content": "plan this"},
        {"role": "assistant", "content": "thinking about it", "thinking_content": "deep thoughts"},
    ]
    await client.chat_json(messages)

    assert len(captured_messages) == 1
    api_msgs = captured_messages[0]
    assert all("thinking_content" not in m for m in api_msgs)


# ---------------------------------------------------------------------------
# chat_json fallback scope tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_json_fallback_on_response_format_bad_request():
    """chat_json should fall back to plain text only on BadRequestError about response_format."""
    from openai import BadRequestError
    client = _make_mock_client()

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: raise BadRequestError about response_format
            raise BadRequestError(
                message="Unsupported parameter: response_format",
                response=MagicMock(status_code=400),
                body=None,
            )
        # Second call (fallback via chat): return normal response
        resp = _make_response(content='{"result": "ok"}')
        return resp

    client._client.chat.completions.create = mock_create

    result = await client.chat_json([{"role": "user", "content": "test"}])
    assert result == {"result": "ok"}
    assert call_count == 2  # JSON mode attempt + fallback


@pytest.mark.asyncio
async def test_chat_json_propagates_non_response_format_bad_request():
    """chat_json should propagate BadRequestError NOT about response_format."""
    from openai import BadRequestError
    client = _make_mock_client()

    async def mock_create(**kwargs):
        raise BadRequestError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body=None,
        )

    client._client.chat.completions.create = mock_create

    with pytest.raises(BadRequestError, match="Invalid API key"):
        await client.chat_json([{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_chat_json_propagates_rate_limit():
    """chat_json should NOT fall back on rate limit errors."""
    from openai import RateLimitError
    client = _make_mock_client()

    async def mock_create(**kwargs):
        raise RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(status_code=429),
            body=None,
        )

    client._client.chat.completions.create = mock_create

    with pytest.raises(RateLimitError):
        await client.chat_json([{"role": "user", "content": "test"}])


@pytest.mark.asyncio
async def test_chat_json_propagates_auth_error():
    """chat_json should NOT fall back on authentication errors."""
    from openai import AuthenticationError
    client = _make_mock_client()

    async def mock_create(**kwargs):
        raise AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body=None,
        )

    client._client.chat.completions.create = mock_create

    with pytest.raises(AuthenticationError):
        await client.chat_json([{"role": "user", "content": "test"}])
