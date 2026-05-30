"""Tests for MCP Bridge configuration loading."""

import json
import os
import pytest
from unittest.mock import patch

from tools.mcp.config import (
    MCPServerConfig,
    MCPBridgeConfig,
    load_mcp_bridge_config,
    _parse_server_entry,
    _expand_env_vars,
)


class TestMCPServerConfig:
    def test_basic_stdio_config(self):
        cfg = MCPServerConfig(
            name="test",
            transport="stdio",
            command="python",
            args=["server.py"],
        )
        assert cfg.name == "test"
        assert cfg.transport == "stdio"
        assert cfg.command == "python"
        assert cfg.enabled is True

    def test_basic_http_config(self):
        cfg = MCPServerConfig(
            name="remote",
            transport="streamable_http",
            url="http://localhost:8080",
        )
        assert cfg.transport == "streamable_http"
        assert cfg.url == "http://localhost:8080"


class TestParseServerEntry:
    def test_valid_stdio(self):
        raw = {"transport": "stdio", "command": "npx", "args": ["-y", "server"]}
        cfg = _parse_server_entry("fs", raw)
        assert cfg is not None
        assert cfg.name == "fs"
        assert cfg.command == "npx"

    def test_valid_http(self):
        raw = {"transport": "streamable_http", "url": "http://localhost:9000"}
        cfg = _parse_server_entry("api", raw)
        assert cfg is not None
        assert cfg.url == "http://localhost:9000"

    def test_unknown_transport_returns_none(self):
        raw = {"transport": "websocket", "url": "ws://localhost"}
        cfg = _parse_server_entry("ws", raw)
        assert cfg is None

    def test_stdio_missing_command_returns_none(self):
        raw = {"transport": "stdio"}
        cfg = _parse_server_entry("bad", raw)
        assert cfg is None

    def test_http_missing_url_returns_none(self):
        raw = {"transport": "streamable_http"}
        cfg = _parse_server_entry("bad", raw)
        assert cfg is None

    def test_enabled_field(self):
        raw = {"transport": "streamable_http", "url": "http://x", "enabled": False}
        cfg = _parse_server_entry("off", raw)
        assert cfg is not None
        assert cfg.enabled is False

    def test_env_expansion(self):
        with patch.dict(os.environ, {"MY_KEY": "secret123"}):
            raw = {"transport": "streamable_http", "url": "http://x", "headers": {"Auth": "Bearer ${MY_KEY}"}}
            cfg = _parse_server_entry("env", raw)
            assert cfg is not None
            assert cfg.headers["Auth"] == "Bearer secret123"


class TestExpandEnvVars:
    def test_expand_simple(self):
        with patch.dict(os.environ, {"FOO": "bar"}):
            assert _expand_env_vars("${FOO}") == "bar"

    def test_no_expansion(self):
        assert _expand_env_vars("plain_text") == "plain_text"

    def test_missing_var_returns_original(self):
        # os.path.expandvars leaves ${VAR} unchanged when VAR is unset
        result = _expand_env_vars("${NONEXISTENT_VAR_XYZ}")
        assert "${NONEXISTENT_VAR_XYZ}" in result


class TestLoadMcpBridgeConfig:
    def test_load_from_inline_json(self):
        json_str = json.dumps({
            "servers": {
                "test": {"transport": "streamable_http", "url": "http://localhost:9999"}
            },
            "schema_mode": "strict",
        })
        with patch.dict(os.environ, {"MCP_BRIDGE_SERVERS_JSON": json_str}):
            import config as _cfg
            with patch.object(_cfg, "MCP_BRIDGE_SERVERS_JSON", json_str):
                config = load_mcp_bridge_config()
                assert "test" in config.servers
                assert config.schema_mode == "strict"

    def test_load_from_empty_returns_default(self):
        with patch.dict(os.environ, {"MCP_BRIDGE_SERVERS_JSON": "", "MCP_BRIDGE_CONFIG_PATH": ""}):
            import config as _cfg
            with patch.object(_cfg, "MCP_BRIDGE_SERVERS_JSON", ""), \
                 patch.object(_cfg, "MCP_BRIDGE_CONFIG_PATH", ""):
                config = load_mcp_bridge_config()
                assert len(config.servers) == 0
                assert config.schema_mode in ("loose", "strict")

    def test_load_skips_invalid_servers(self):
        json_str = json.dumps({
            "servers": {
                "good": {"transport": "streamable_http", "url": "http://ok"},
                "bad": {"transport": "websocket"},
            }
        })
        with patch.dict(os.environ, {"MCP_BRIDGE_SERVERS_JSON": json_str}):
            import config as _cfg
            with patch.object(_cfg, "MCP_BRIDGE_SERVERS_JSON", json_str):
                config = load_mcp_bridge_config()
                assert "good" in config.servers
                assert "bad" not in config.servers

    def test_load_multiple_servers(self):
        json_str = json.dumps({
            "servers": {
                "fs": {"transport": "stdio", "command": "npx", "args": ["fs-server"]},
                "api": {"transport": "streamable_http", "url": "http://api:8080"},
            }
        })
        with patch.dict(os.environ, {"MCP_BRIDGE_SERVERS_JSON": json_str}):
            import config as _cfg
            with patch.object(_cfg, "MCP_BRIDGE_SERVERS_JSON", json_str):
                config = load_mcp_bridge_config()
                assert len(config.servers) == 2
                assert config.servers["fs"].transport == "stdio"
                assert config.servers["api"].transport == "streamable_http"
