"""
MCP Bridge Configuration — server registry and bridge settings.

MCP 桥接配置 —— 服务器注册表和桥接设置。
支持三种配置源（优先级从高到低）：
  1. MCP_BRIDGE_SERVERS_JSON 环境变量（内联 JSON）
  2. MCP_BRIDGE_CONFIG_PATH 指向的 JSON 文件
  3. 零配置（无服务器）
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

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


def _parse_server_entry(name: str, raw: dict[str, Any]) -> MCPServerConfig | None:
    """Parse a single server entry from JSON config.
    从 JSON 配置解析单个服务器条目。无效条目返回 None。"""
    try:
        transport = raw.get("transport", "streamable_http")
        if transport not in ("stdio", "streamable_http"):
            logger.warning("[MCPConfig] Server '%s': unknown transport '%s', skipping", name, transport)
            return None

        # Fix-16: Validate input types
        args = raw.get("args", [])
        if not isinstance(args, list):
            logger.warning("[MCPConfig] Server '%s': 'args' must be a list, skipping", name)
            return None

        raw_env = raw.get("env", {})
        if not isinstance(raw_env, dict):
            raw_env = {}
        raw_headers = raw.get("headers", {})
        if not isinstance(raw_headers, dict):
            raw_headers = {}

        cfg = MCPServerConfig(
            name=name,
            transport=transport,
            command=raw.get("command", ""),
            args=args,
            env=_expand_dict_env_vars(raw_env),
            cwd=raw.get("cwd"),
            url=raw.get("url", ""),
            headers=_expand_dict_env_vars(raw_headers),
            timeout=max(float(raw.get("timeout", 30)), 1.0),
            enabled=raw.get("enabled", True),
        )

        # Validate required fields per transport
        if transport == "stdio" and not cfg.command:
            logger.warning("[MCPConfig] Server '%s': stdio requires 'command', skipping", name)
            return None
        if transport == "streamable_http" and not cfg.url:
            logger.warning("[MCPConfig] Server '%s': streamable_http requires 'url', skipping", name)
            return None

        return cfg
    except Exception as exc:
        # Fix-24: Include traceback for parse errors
        logger.warning("[MCPConfig] Server '%s': parse error: %s", name, exc, exc_info=True)
        return None


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
        if cfg is not None:
            servers[name] = cfg

    return MCPBridgeConfig(
        servers=servers,
        schema_mode=data.get("schema_mode", "loose"),
        tool_prefix=data.get("tool_prefix", "mcp"),
        discovery_ttl_seconds=int(data.get("discovery_ttl_seconds", 300)),
        call_timeout_seconds=int(data.get("call_timeout_seconds", 30)),
    )


def load_mcp_bridge_config() -> MCPBridgeConfig:
    """
    Load bridge config from env vars and optional JSON source.

    Priority: MCP_BRIDGE_SERVERS_JSON > MCP_BRIDGE_CONFIG_PATH > env-only minimal config

    从环境变量和可选 JSON 源加载桥接配置。
    优先级：MCP_BRIDGE_SERVERS_JSON > MCP_BRIDGE_CONFIG_PATH > 环境变量最小配置。
    """
    import config as _config

    # Fix-17: Read via config module instead of raw os.getenv for consistency
    inline_json = getattr(_config, "MCP_BRIDGE_SERVERS_JSON", "").strip()
    if inline_json:
        try:
            bridge_cfg = _load_json_config(inline_json)
            logger.info("[MCPConfig] Loaded %d servers from MCP_BRIDGE_SERVERS_JSON", len(bridge_cfg.servers))
            return bridge_cfg
        except json.JSONDecodeError as exc:
            logger.error("[MCPConfig] MCP_BRIDGE_SERVERS_JSON parse error: %s", exc)

    # Try JSON file
    config_path = getattr(_config, "MCP_BRIDGE_CONFIG_PATH", "").strip()
    if config_path and os.path.isfile(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                file_json = f.read().strip()
            if file_json:
                bridge_cfg = _load_json_config(file_json)
                logger.info("[MCPConfig] Loaded %d servers from %s", len(bridge_cfg.servers), config_path)
                return bridge_cfg
        except Exception as exc:
            logger.error("[MCPConfig] Failed to read %s: %s", config_path, exc)

    # Fallback: env-only config (tool_prefix, schema_mode, etc. from config.py)
    return MCPBridgeConfig(
        servers={},
        schema_mode=getattr(_config, "MCP_BRIDGE_SCHEMA_MODE", "loose"),
        tool_prefix=getattr(_config, "MCP_BRIDGE_TOOL_PREFIX", "mcp"),
        discovery_ttl_seconds=getattr(_config, "MCP_BRIDGE_DISCOVERY_TTL", 300),
        call_timeout_seconds=getattr(_config, "MCP_BRIDGE_CALL_TIMEOUT", 30),
    )
