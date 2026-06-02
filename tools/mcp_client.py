"""
Bailian MCP Client — lightweight wrapper around the mcp SDK's Streamable HTTP
transport for connecting to Aliyun Bailian WebSearch and WebParser MCP servers.

百炼 MCP 客户端 —— 基于 mcp SDK Streamable HTTP 传输的轻量封装，
用于连接阿里云百炼 WebSearch 和 WebParser MCP 服务。

Uses per-call connection pattern: each call_tool() establishes a fresh MCP
session, calls the tool, then closes the session. This avoids session caching
complexity and is acceptable for a demo project where search latency dominates
the connection overhead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession
from mcp.types import CallToolResult, TextContent

logger = logging.getLogger(__name__)

# Markers that indicate a transient, retry-worthy failure. "429"/"too many
# requests" = rate limiting; "brokenresourceerror" = the SSE stream breaking,
# which on Bailian is the downstream effect of a 429 killing the post writer.
# 可重试的瞬时失败标记：429 限流 + SSE 连接断裂（百炼上后者通常是 429 的下游效应）。
_RETRYABLE_MARKERS = ("429", "too many requests", "brokenresourceerror")


def _flatten_exc_messages(exc: BaseException, _depth: int = 0) -> list[str]:
    """Recursively collect messages from an exception and any nested
    ExceptionGroups. The real 429 HTTPStatusError is often buried 2+ levels
    deep inside asyncio TaskGroup ExceptionGroups, so a shallow str(exc) misses
    it (shows only "unhandled errors in a TaskGroup").
    递归展开（嵌套）ExceptionGroup 的所有异常消息——真实 429 常埋在 2 层以上。"""
    msgs = [f"{type(exc).__name__}: {exc}"]
    inner = getattr(exc, "exceptions", None)
    if inner and _depth < 6:
        for e in inner:
            msgs.extend(_flatten_exc_messages(e, _depth + 1))
    return msgs


def _is_retryable(text: str) -> bool:
    """True if the (possibly aggregated) message indicates a retryable failure."""
    low = text.lower()
    return any(m in low for m in _RETRYABLE_MARKERS)


class _RateLimited(Exception):
    """Internal sentinel: a transient/rate-limited failure that should be retried."""

# Server name → MCP endpoint URL mapping
_SERVER_URLS: dict[str, str] = {
    "WebSearch": config.BAILIAN_WEBSEARCH_MCP_URL,
    "WebParser": config.BAILIAN_WEBPARSER_MCP_URL,
}

# Server name → transport. The two Bailian MCP servers differ:
#   - WebSearch supports Streamable HTTP (POST /mcp → 200)
#   - WebParser ONLY supports SSE (POST /mcp → 405 "current mcp not support
#     streamableHttp"; GET /sse → text/event-stream). Using the wrong transport
#     surfaces as a hard 405 on every fetch_url call.
# 两个百炼 MCP 传输协议不同：WebSearch=streamable HTTP，WebParser=SSE。用错传输 → 405。
_SERVER_TRANSPORT: dict[str, str] = {
    "WebSearch": "streamable_http",
    "WebParser": "sse",
}


class BailianMCPClient:
    """
    Async client for Aliyun Bailian MCP servers.

    Each call_tool() invocation:
      1. Opens a Streamable HTTP connection to the target server
      2. Creates a ClientSession and initializes it
      3. Calls the requested tool
      4. Extracts text content from the result
      5. Closes the session
    """

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> str:
        """
        Call an MCP tool on a Bailian server and return the text content.

        Args:
            server_name: "WebSearch" or "WebParser" (maps to endpoint URL)
            tool_name: MCP tool name (e.g. "bailian_web_search")
            arguments: Tool call arguments dict
            timeout: Optional timeout override (seconds)

        Returns:
            Concatenated text from all TextContent items in the result.

        Raises:
            ValueError: If server_name is unknown or DASHSCOPE_API_KEY is missing.
            Exception: On MCP connection or tool call failure.
        """
        if not config.DASHSCOPE_API_KEY:
            raise ValueError("DASHSCOPE_API_KEY is required for Bailian MCP calls")

        url = _SERVER_URLS.get(server_name)
        if url is None:
            raise ValueError(f"Unknown MCP server: {server_name}. Available: {list(_SERVER_URLS.keys())}")

        headers = {"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"}
        # Inner streamablehttp timeout is generous so the outer asyncio.wait_for
        # at the caller side is the single source of truth. Avoids inner-timeout
        # raising ExceptionGroup before the outer wait_for can fire cleanly.
        effective_timeout = timeout or float(config.WEB_SEARCH_TIMEOUT)
        inner_timeout = effective_timeout * 4 + 30

        transport = _SERVER_TRANSPORT.get(server_name, "streamable_http")
        logger.info("[MCPClient] Calling %s/%s via %s(%s)", server_name, tool_name, transport, arguments)

        # Retry loop for transient/rate-limited (429) failures with exponential
        # backoff. NOTE: total time is bounded by the caller's asyncio.wait_for
        # (web_search=WEB_SEARCH_TIMEOUT, fetch_url=2×); a backoff that would
        # exceed it is simply cancelled (WebSearch then falls back to DDGS).
        # 429/瞬时错误指数退避重试；总时长受调用方 wait_for 限制，超出会被取消。
        max_retries = config.BAILIAN_MCP_MAX_RETRIES
        base_delay = config.BAILIAN_MCP_RETRY_BASE_DELAY

        for attempt in range(max_retries + 1):
            try:
                return await self._call_once(
                    transport=transport, url=url, headers=headers,
                    inner_timeout=inner_timeout, server_name=server_name,
                    tool_name=tool_name, arguments=arguments,
                )
            except _RateLimited as rl:
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "[MCPClient] %s/%s rate-limited/transient (%s); retry %d/%d after %.1fs",
                        server_name, tool_name, str(rl)[:120], attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "[MCPClient] %s/%s still failing after %d retries (rate-limited/transient): %s",
                    server_name, tool_name, max_retries, str(rl)[:200],
                )
                raise RuntimeError(
                    f"MCP {server_name}.{tool_name} failed after {max_retries} retries: {str(rl)[:300]}"
                ) from rl

    async def _call_once(
        self,
        *,
        transport: str,
        url: str,
        headers: dict[str, str],
        inner_timeout: float,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Single connect→initialize→call_tool attempt. Raises ``_RateLimited``
        for retry-worthy failures (429 / transient SSE break) and re-raises other
        errors unchanged (preserving the original ExceptionGroup unwrapping).
        单次连接+调用；429/瞬时失败抛 _RateLimited 交给重试循环，其余按原逻辑抛出。"""
        # Pick the transport client. SSE yields a 2-tuple (read, write);
        # Streamable HTTP yields a 3-tuple (read, write, get_session_id). We
        # unpack generically below so the rest of the flow is identical.
        # 按传输选择 client：SSE 返回 2 元组，streamable HTTP 返回 3 元组，下面统一解包。
        if transport == "sse":
            from mcp.client.sse import sse_client  # lazy import (见 #15 约定)
            client_cm = sse_client(
                url=url,
                headers=headers,
                timeout=inner_timeout,
                sse_read_timeout=inner_timeout + 60,
            )
        else:
            client_cm = streamablehttp_client(
                url=url,
                headers=headers,
                timeout=inner_timeout,
                sse_read_timeout=inner_timeout + 60,
            )

        try:
            async with client_cm as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    result: CallToolResult = await session.call_tool(
                        name=tool_name,
                        arguments=arguments,
                    )

                    text = self._extract_text(result)

                    if result.isError:
                        logger.warning("[MCPClient] Tool %s returned error: %s", tool_name, text[:200])
                        # Tool-level rate limit (e.g. WebSearch returns {"status":429}).
                        if _is_retryable(text):
                            raise _RateLimited(f"tool error: {text[:200]}")
                        raise RuntimeError(f"MCP tool error: {text[:500]}")

                    logger.info("[MCPClient] Tool %s succeeded, result length=%d", tool_name, len(text))
                    return text

        except _RateLimited:
            raise  # already classified — propagate to the retry loop untouched
        except Exception as exc:
            # NOTE: catch Exception (not BaseException) so asyncio.CancelledError
            # (raised when the caller's wait_for times out) / KeyboardInterrupt /
            # SystemExit propagate untouched. The asyncio TaskGroup 429 arrives as
            # an ExceptionGroup, which subclasses Exception, so it is still caught.
            # 用 Exception 而非 BaseException：让取消/中断正常透传；429 的 ExceptionGroup 仍可捕获。
            # Flatten nested ExceptionGroups (asyncio TaskGroup wraps the real
            # cause, often 2+ levels deep) so we (a) detect a buried 429 and
            # (b) surface actionable detail instead of "unhandled errors...".
            messages = _flatten_exc_messages(exc)
            details = "; ".join(m[:200] for m in messages)
            if _is_retryable(details):
                raise _RateLimited(details[:300]) from exc
            logger.error("[MCPClient] call_tool failed: %s/%s → %s",
                         server_name, tool_name, details)
            raise RuntimeError(
                f"MCP {server_name}.{tool_name} failed: {details}"
            ) from exc

    @staticmethod
    def _extract_text(result: CallToolResult) -> str:
        """Extract readable text from MCP CallToolResult content blocks."""
        parts: list[str] = []
        for item in result.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
        return "\n".join(parts)


