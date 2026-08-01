"""
MCP Bridge Tool — wraps a discovered MCP tool as a BaseTool instance.

MCP 桥接工具 —— 将发现的 MCP 工具包装为 BaseTool 实例。
每个 MCPBridgeTool 对应一个从 MCP 服务器发现的工具。

Lifecycle:
  1. Created during discovery with MCP tool metadata + schema
  2. Registered in ReActEngine's tool dict alongside native tools
  3. execute() delegates to MCPClientManager.call_tool()
  4. Returns "Error: ..." strings on failure (BaseTool convention)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from react.tool_call_helpers import classify_result
from tools.base import BaseTool
from tools.mcp.schema_adapter import mcp_schema_to_openai

logger = logging.getLogger(__name__)


class MCPBridgeTool(BaseTool):
    """
    A BaseTool that proxies calls to a remote MCP tool.
    One MCPBridgeTool instance is created per discovered MCP tool.

    代理远程 MCP 工具调用的 BaseTool。
    每个发现的 MCP 工具对应一个 MCPBridgeTool 实例。

    Uses the common BaseTool execution and OpenAI schema contracts.
    """

    def __init__(
        self,
        prefixed_name: str,
        description: str,
        mcp_tool_schema: dict[str, Any],
        client_manager: "MCPClientManager",
        original_tool_name: str,
        server_name: str,
        schema_mode: str = "loose",
        on_event: Callable[[str, Any], None] | None = None,
    ):
        self._name = prefixed_name
        self._description = description
        self._mcp_schema = mcp_tool_schema
        self._client_manager = client_manager
        self._original_tool_name = original_tool_name
        self._server_name = server_name
        self._on_event = on_event

        # Eagerly convert MCP schema -> OpenAI parameters_schema at init time.
        # Failures caught and result in minimal schema (loose) or warning (strict).
        self._openai_schema = mcp_schema_to_openai(
            mcp_tool_schema, mode=schema_mode,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self._openai_schema

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def original_tool_name(self) -> str:
        return self._original_tool_name

    async def execute(self, **kwargs: Any) -> str:
        """
        Delegate to MCPClientManager.call_tool().
        The client_manager handles transport, session, and error extraction.

        委托 MCPClientManager.call_tool() 执行。
        client_manager 负责传输、会话和错误提取。
        失败时返回 "Error: ..." 字符串（BaseTool 惯例）。
        """
        try:
            result = await self._client_manager.call_tool(
                self._name, kwargs,
            )
            error, _ = classify_result(result)
            self._emit_executed(error=error)
            return result
        except asyncio.TimeoutError:
            msg = f"timed out after {self._client_manager.config.call_timeout_seconds}s"
            logger.error("[MCPBridgeTool] %s %s", self._name, msg)
            self._emit_executed(error=True)
            return f"Error: MCP tool '{self._name}' {msg}"
        except Exception as exc:
            # Extract sub-exceptions from ExceptionGroup (Python 3.11+)
            inner = getattr(exc, "exceptions", None)
            detail = ""
            if inner:
                detail = "; ".join(f"{type(e).__name__}: {str(e)[:200]}" for e in inner)

            msg = detail or f"{type(exc).__name__}: {exc}"
            # Truncate error messages to prevent oversized tool responses.
            if len(msg) > 500:
                msg = msg[:500] + "..."
            logger.error("[MCPBridgeTool] %s failed: %s", self._name, msg)
            self._emit_executed(error=True)
            return f"Error: MCP tool '{self._name}' failed: {msg}"

    def _emit_executed(self, error: bool) -> None:
        """Emit mcp_tool_executed for the evaluation probe / observability.
        发出 mcp_tool_executed 供评测 probe / 可观测性统计。"""
        if self._on_event is None:
            return
        try:
            self._on_event("mcp_tool_executed", {
                "tool": self._name, "server": self._server_name, "error": error,
            })
        except Exception:
            logger.debug("[MCPBridgeTool] on_event failed", exc_info=True)
