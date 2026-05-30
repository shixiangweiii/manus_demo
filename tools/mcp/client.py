"""
MCP Client Manager — multi-server tool discovery and execution.

MCP 客户端管理器 —— 多服务器工具发现与执行。
管理多个 MCP 服务器的连接、工具发现和工具调用。
采用 per-call 连接模式（与 BailianMCPClient 一致），避免 session pooling 复杂度。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from tools.mcp.config import MCPBridgeConfig, MCPServerConfig
from tools.mcp.transport import open_transport

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredTool:
    """A tool discovered from an MCP server.
    从 MCP 服务器发现的工具。"""
    original_name: str
    prefixed_name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str
    server_config: MCPServerConfig


class MCPClientManager:
    """
    Manages connections to multiple MCP servers.
    Provides tool discovery (list_tools) and tool execution (call_tool).

    管理多个 MCP 服务器的连接。
    提供工具发现（list_tools）和工具调用（call_tool）。
    """

    def __init__(self, config: MCPBridgeConfig):
        self._config = config
        self._discovered: dict[str, DiscoveredTool] = {}  # prefixed_name -> DiscoveredTool
        self._last_discovery_time: float = 0.0
        self._discovery_lock = asyncio.Lock()  # 防止并发 re-discovery 竞态

    @property
    def discovered_tools(self) -> dict[str, DiscoveredTool]:
        """Return all discovered tools keyed by prefixed name.
        返回所有已发现工具（以 prefixed name 为键）。"""
        return dict(self._discovered)

    @property
    def config(self) -> MCPBridgeConfig:
        return self._config

    def make_prefixed_name(self, server_name: str, tool_name: str) -> str:
        """Build the prefixed tool name: {prefix}_{server}_{tool}.
        构建前缀工具名。"""
        return f"{self._config.tool_prefix}_{server_name}_{tool_name}"

    async def discover_all_tools(self) -> list[DiscoveredTool]:
        """
        Connect to each enabled server, call list_tools(), record results.
        Returns flat list of all discovered tools.

        遍历所有已启用的服务器，调用 list_tools()，返回所有发现的工具。
        失败的服务器被跳过（WARNING），不阻断其他服务器的发现。
        每次调用清空旧工具，避免累积过时条目。
        """
        from mcp.client.session import ClientSession

        # Fix-2: 清空旧工具，避免重复 discovery 累积过时条目
        self._discovered.clear()
        discovered: list[DiscoveredTool] = []

        for server_name, server_cfg in self._config.servers.items():
            if not server_cfg.enabled:
                logger.info("[MCPClient] Server '%s' disabled, skipping", server_name)
                continue

            try:
                async with open_transport(server_cfg) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await asyncio.wait_for(
                            session.initialize(),
                            timeout=self._config.call_timeout_seconds,
                        )
                        result = await asyncio.wait_for(
                            session.list_tools(),
                            timeout=self._config.call_timeout_seconds,
                        )

                        for mcp_tool in result.tools:
                            prefixed = self.make_prefixed_name(server_name, mcp_tool.name)
                            dt = DiscoveredTool(
                                original_name=mcp_tool.name,
                                prefixed_name=prefixed,
                                description=mcp_tool.description or "",
                                input_schema=mcp_tool.inputSchema,
                                server_name=server_name,
                                server_config=server_cfg,
                            )
                            self._discovered[prefixed] = dt
                            discovered.append(dt)
                            logger.info(
                                "[MCPClient] Discovered tool: %s (%s/%s)",
                                prefixed, server_name, mcp_tool.name,
                            )

                logger.info(
                    "[MCPClient] Server '%s': discovered %d tools",
                    server_name, len([t for t in discovered if t.server_name == server_name]),
                )

            except Exception as exc:
                # Extract sub-exceptions from ExceptionGroup
                inner = getattr(exc, "exceptions", None)
                detail = ""
                if inner:
                    detail = "; ".join(f"{type(e).__name__}: {str(e)[:200]}" for e in inner)
                logger.warning(
                    "[MCPClient] Server '%s' discovery failed: %s",
                    server_name, detail or exc,
                )

        self._last_discovery_time = time.time()
        logger.info("[MCPClient] Total discovered: %d tools from %d servers", len(discovered), len(self._config.servers))
        return discovered

    async def call_tool(self, prefixed_name: str, arguments: dict[str, Any]) -> str:
        """
        Execute a tool by its prefixed name.
        Looks up the server config, establishes transport, calls tool, returns text.

        通过前缀名执行工具。查找对应的服务器配置，建立传输，调用工具，返回文本。
        失败时抛出异常（由 MCPBridgeTool.execute() 捕获并转为 Error: 字符串）。
        """
        # Fix-3: TTL-based re-discovery with lock to prevent concurrent races
        if not self._discovered or (
            self._config.discovery_ttl_seconds > 0
            and time.time() - self._last_discovery_time > self._config.discovery_ttl_seconds
        ):
            async with self._discovery_lock:
                # Double-check after acquiring lock
                if not self._discovered or (
                    self._config.discovery_ttl_seconds > 0
                    and time.time() - self._last_discovery_time > self._config.discovery_ttl_seconds
                ):
                    logger.info("[MCPClient] TTL expired, re-discovering tools")
                    await self.discover_all_tools()

        if prefixed_name not in self._discovered:
            raise ValueError(f"Unknown MCP tool: {prefixed_name}")

        dt = self._discovered[prefixed_name]
        # Fix-1: 用 asyncio.wait_for 包裹，强制超时
        return await asyncio.wait_for(
            self._connect_and_call(dt.server_config, dt.original_name, arguments),
            timeout=self._config.call_timeout_seconds,
        )

    async def _connect_and_call(
        self,
        server_config: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Open transport, create session, call tool, extract text, close session.
        开启传输、创建会话、调用工具、提取文本、关闭会话。"""
        from mcp.client.session import ClientSession
        from mcp.types import CallToolResult, TextContent

        logger.info("[MCPClient] Calling %s.%s(%s)", server_config.name, tool_name, arguments)

        async with open_transport(server_config) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                result: CallToolResult = await session.call_tool(
                    name=tool_name,
                    arguments=arguments,
                )

                text = self._extract_text(result)

                if result.isError:
                    raise RuntimeError(f"MCP tool error: {text[:500]}")

                logger.info("[MCPClient] Tool %s succeeded, result length=%d", tool_name, len(text))
                return text

    @staticmethod
    def _extract_text(result: "CallToolResult") -> str:
        """Extract readable text from MCP CallToolResult content blocks.
        从 MCP CallToolResult 提取可读文本。非文本内容记录 debug 日志。"""
        from mcp.types import TextContent

        parts: list[str] = []
        skipped_types: list[str] = []
        for item in result.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
            else:
                skipped_types.append(type(item).__name__)
                logger.debug("[MCPClient] Skipping non-text content: %s", type(item).__name__)
        if not parts and skipped_types:
            return f"(no text content; skipped types: {', '.join(skipped_types)})"
        return "\n".join(parts)
