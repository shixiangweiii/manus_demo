"""
MCP Transport Factory — unified transport opening for stdio and Streamable HTTP.

MCP 传输工厂 —— 统一 stdio 和 Streamable HTTP 传输的开启。
使用 lazy import 避免在模块加载时触发 mcp SDK 依赖。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from tools.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_transport(
    config: MCPServerConfig,
) -> AsyncIterator[tuple[Any, Any]]:
    """
    Open a transport to an MCP server.
    Yields (read_stream, write_stream) for use with ClientSession.

    根据配置打开到 MCP 服务器的传输连接，返回 (read_stream, write_stream)。
    使用 lazy import 避免运行时之外触发 mcp SDK 依赖。
    """
    if config.transport == "stdio":
        from mcp.client.stdio import stdio_client, StdioServerParameters

        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env or None,
            cwd=config.cwd,
        )
        logger.info("[MCPTransport] Opening stdio transport: %s %s", config.command, " ".join(config.args))
        async with stdio_client(params) as (read_stream, write_stream):
            yield read_stream, write_stream

    elif config.transport == "streamable_http":
        from mcp.client.streamable_http import streamablehttp_client

        # Cap the inner timeout; the outer asyncio.wait_for is authoritative.
        inner_timeout = min(config.timeout * 4 + 30, 300)
        logger.info("[MCPTransport] Opening HTTP transport: %s", config.url)
        async with streamablehttp_client(
            url=config.url,
            headers=config.headers,
            timeout=inner_timeout,
            sse_read_timeout=inner_timeout + 60,
        ) as (read_stream, write_stream, _):
            yield read_stream, write_stream

    else:
        raise ValueError(f"Unknown MCP transport: {config.transport}")
