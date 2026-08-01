"""
MCP Bridge Configuration — server registry and bridge settings.

MCP 桥接配置 —— 服务器注册表和桥接设置。
连接定义来自 settings.toml 中的内联 JSON 或 JSON 文件路径。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from core.settings import CapabilitySettings, get_settings, validate_server_entry

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server connection.
    单个 MCP 服务器的连接配置。"""

    name: str
    transport: str  # "stdio" | "streamable_http"

    # stdio transport fields
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    # streamable_http transport fields
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0

    # general
    enabled: bool = True


@dataclass
class MCPBridgeConfig:
    """Top-level configuration for the MCP Bridge subsystem.
    MCP Bridge 子系统顶层配置。"""

    servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    schema_mode: str = "loose"       # "loose" | "strict"
    tool_prefix: str = "mcp"
    discovery_ttl_seconds: int = 300
    call_timeout_seconds: int = 30


def _expand_env_vars(value: str) -> str:
    """Expand ${VAR} references in string values.
    展开字符串值中的 ${VAR} 引用。"""
    return os.path.expandvars(value)


def _expand_dict_env_vars(d: dict[str, str]) -> dict[str, str]:
    """Expand ${VAR} in all values of a dict.
    展开字典所有值中的 ${VAR}。"""
    return {k: _expand_env_vars(v) for k, v in d.items()}


def _parse_server_entry(name: str, raw: dict[str, Any]) -> MCPServerConfig:
    """Parse a single server entry from JSON config.
    从 JSON 配置解析单个服务器条目。"""
    if not isinstance(raw, dict):
        raise ValueError(f"MCP server '{name}' must be an object")
    try:
        validate_server_entry(raw, f"MCP server {name!r}")
        transport = raw.get("transport", "streamable_http")
        args = raw.get("args", [])
        raw_env = raw.get("env", {})
        raw_headers = raw.get("headers", {})

        cfg = MCPServerConfig(
            name=name,
            transport=transport,
            command=raw.get("command", ""),
            args=args,
            env=_expand_dict_env_vars(raw_env),
            cwd=raw.get("cwd"),
            url=raw.get("url", ""),
            headers=_expand_dict_env_vars(raw_headers),
            timeout=float(raw.get("timeout", 30)),
            enabled=raw.get("enabled", True),
        )
        return cfg
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid MCP server '{name}': {exc}") from exc


def _load_json_config(json_str: str) -> MCPBridgeConfig:
    """Load bridge config from a JSON string.
    从 JSON 字符串加载桥接配置。"""
    data = json.loads(json_str)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    raw_servers = data.get("servers", {})
    if not isinstance(raw_servers, dict):
        raise ValueError(f"'servers' must be an object, got {type(raw_servers).__name__}")

    servers: dict[str, MCPServerConfig] = {}
    for name, raw in raw_servers.items():
        cfg = _parse_server_entry(name, raw)
        servers[name] = cfg

    return MCPBridgeConfig(
        servers=servers,
        schema_mode=data.get("schema_mode", "loose"),
        tool_prefix=data.get("tool_prefix", "mcp"),
        discovery_ttl_seconds=int(data.get("discovery_ttl_seconds", 300)),
        call_timeout_seconds=int(data.get("call_timeout_seconds", 30)),
    )


def load_mcp_bridge_config(
    settings: CapabilitySettings | None = None,
) -> MCPBridgeConfig:
    """
    Load bridge config from structured settings and an optional JSON file.
    从结构化配置及可选 JSON 文件加载桥接配置。
    """
    settings = settings or get_settings().capabilities

    inline_json = settings.mcp_bridge_servers_json.strip()
    if inline_json:
        bridge_cfg = _load_json_config(inline_json)
        logger.info("[MCPConfig] Loaded %d inline MCP servers", len(bridge_cfg.servers))
        bridge_cfg.schema_mode = settings.mcp_bridge_schema_mode
        bridge_cfg.tool_prefix = settings.mcp_bridge_tool_prefix
        bridge_cfg.discovery_ttl_seconds = settings.mcp_bridge_discovery_ttl
        bridge_cfg.call_timeout_seconds = settings.mcp_bridge_call_timeout
        return bridge_cfg

    # Try JSON file
    config_path = settings.mcp_bridge_config_path.strip()
    if config_path:
        if not os.path.isfile(config_path):
            raise ValueError(f"MCP bridge config file does not exist: {config_path}")
        with open(config_path, encoding="utf-8") as f:
            file_json = f.read().strip()
        if not file_json:
            raise ValueError(f"MCP bridge config file is empty: {config_path}")
        bridge_cfg = _load_json_config(file_json)
        logger.info("[MCPConfig] Loaded %d servers from %s", len(bridge_cfg.servers), config_path)
        bridge_cfg.schema_mode = settings.mcp_bridge_schema_mode
        bridge_cfg.tool_prefix = settings.mcp_bridge_tool_prefix
        bridge_cfg.discovery_ttl_seconds = settings.mcp_bridge_discovery_ttl
        bridge_cfg.call_timeout_seconds = settings.mcp_bridge_call_timeout
        return bridge_cfg

    # No server definitions; keep the remaining behavior settings.
    return MCPBridgeConfig(
        servers={},
        schema_mode=settings.mcp_bridge_schema_mode,
        tool_prefix=settings.mcp_bridge_tool_prefix,
        discovery_ttl_seconds=settings.mcp_bridge_discovery_ttl,
        call_timeout_seconds=settings.mcp_bridge_call_timeout,
    )
