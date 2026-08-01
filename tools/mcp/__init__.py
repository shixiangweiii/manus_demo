"""
MCP Bridge — Generic MCP client/server bridge.

Provides:
  - MCPClientManager: multi-server MCP tool discovery and execution
  - MCPBridgeTool: wraps discovered MCP tools as BaseTool instances
  - MCPServerWrapper: exposes project tools/memory as an MCP server
  - mcp_schema_to_openai: MCP JSON Schema → OpenAI function-calling schema

MCP 桥接模块——通用 MCP 客户端/服务端桥接。
支持 stdio + Streamable HTTP 双传输，多服务器注册，动态工具发现。
"""

from tools.mcp.config import MCPServerConfig, MCPBridgeConfig, load_mcp_bridge_config
from tools.mcp.client import MCPClientManager
from tools.mcp.bridge_tool import MCPBridgeTool
from tools.mcp.schema_adapter import mcp_schema_to_openai, get_schema_metrics, reset_schema_metrics

__all__ = [
    "MCPServerConfig",
    "MCPBridgeConfig",
    "load_mcp_bridge_config",
    "MCPClientManager",
    "MCPBridgeTool",
    "mcp_schema_to_openai",
    "get_schema_metrics",
    "reset_schema_metrics",
]
