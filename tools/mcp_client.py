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

import logging
from typing import Any

import config
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession
from mcp.types import CallToolResult, TextContent

logger = logging.getLogger(__name__)

# Server name → MCP endpoint URL mapping
_SERVER_URLS: dict[str, str] = {
    "WebSearch": config.BAILIAN_WEBSEARCH_MCP_URL,
    "WebParser": config.BAILIAN_WEBPARSER_MCP_URL,
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

        logger.info("[MCPClient] Calling %s/%s(%s)", server_name, tool_name, arguments)

        try:
            async with streamablehttp_client(
                url=url,
                headers=headers,
                timeout=inner_timeout,
                sse_read_timeout=inner_timeout + 60,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    result: CallToolResult = await session.call_tool(
                        name=tool_name,
                        arguments=arguments,
                    )

                    text = self._extract_text(result)

                    if result.isError:
                        logger.warning("[MCPClient] Tool %s returned error: %s", tool_name, text[:200])
                        raise RuntimeError(f"MCP tool error: {text[:500]}")

                    logger.info("[MCPClient] Tool %s succeeded, result length=%d", tool_name, len(text))
                    return text

        except BaseException as exc:
            # Extract sub-exceptions from ExceptionGroup (Python 3.11+ asyncio
            # TaskGroup wraps real causes), so callers see actionable detail
            # instead of "unhandled errors in a TaskGroup (1 sub-exception)".
            inner = getattr(exc, "exceptions", None)
            if inner:
                details = "; ".join(
                    f"{type(e).__name__}: {str(e)[:200]}" for e in inner
                )
                logger.error("[MCPClient] call_tool failed: %s/%s → ExceptionGroup: %s",
                             server_name, tool_name, details)
                raise RuntimeError(
                    f"MCP {server_name}.{tool_name} failed: {details}"
                ) from exc
            logger.error("[MCPClient] call_tool failed: %s/%s → %s",
                         server_name, tool_name, exc)
            raise

    @staticmethod
    def _extract_text(result: CallToolResult) -> str:
        """Extract readable text from MCP CallToolResult content blocks."""
        parts: list[str] = []
        for item in result.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
        return "\n".join(parts)


