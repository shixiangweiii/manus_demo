"""
Tests for UserLocationTool — fallback chain unit coverage.
用户位置工具测试 —— fallback 链单元覆盖。

All tests mock external dependencies (env vars, filesystem, urllib);
no real network calls.

覆盖路径：
  1. env var 命中
  2. memory 文件命中（含注释行 / 空行跳过）
  3. memory 文件不存在 → IP 命中（mock urlopen）
  4. IP 服务降级：ipapi.co 失败 → ip.sb 命中
  5. 全部失败（IP 关闭 或 IP 接口异常）→ Error 字符串
  6. 优先级：env > memory > IP
  7. tool contract（name / description / parameters_schema）
"""

from __future__ import annotations

import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.user_location import UserLocationTool


# ======================================================================
# Test helpers
# ======================================================================

def _make_urlopen_response(payload: dict) -> MagicMock:
    """Build a mock urlopen context-manager returning the given JSON payload."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(payload).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


# ======================================================================
# Tool contract
# ======================================================================

class TestToolContract:

    def test_name(self):
        assert UserLocationTool().name == "get_user_location"

    def test_description_mentions_no_default(self):
        desc = UserLocationTool().description
        assert "location" in desc.lower()
        # Must explicitly tell LLM not to invent defaults
        assert "DO NOT" in desc or "do not" in desc

    def test_description_no_longer_mentions_timezone(self):
        """Sanity check: timezone fallback is removed from documentation."""
        desc = UserLocationTool().description
        assert "timezone" not in desc.lower()

    def test_parameters_schema_takes_no_args(self):
        schema = UserLocationTool().parameters_schema
        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert schema["required"] == []


# ======================================================================
# 1. env var path
# ======================================================================

class TestEnvVarPath:

    @pytest.mark.asyncio
    async def test_env_var_hits(self, monkeypatch):
        monkeypatch.setenv("USER_LOCATION", "杭州")
        result = await UserLocationTool().execute()
        assert result == "City: 杭州 (source=env_var)"

    @pytest.mark.asyncio
    async def test_env_var_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("USER_LOCATION", "  Hangzhou  ")
        result = await UserLocationTool().execute()
        assert result == "City: Hangzhou (source=env_var)"

    @pytest.mark.asyncio
    async def test_empty_env_var_falls_through_to_ip_disabled(self, monkeypatch):
        monkeypatch.setenv("USER_LOCATION", "   ")
        # Force memory file path nonexistent and IP disabled
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", False)
        with patch("os.path.isfile", return_value=False):
            result = await UserLocationTool().execute()
        assert result.startswith("Error:")


# ======================================================================
# 2. memory file path
# ======================================================================

class TestMemoryFilePath:

    @pytest.mark.asyncio
    async def test_memory_file_hits(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", return_value=io.StringIO("杭州\n")):
            result = await UserLocationTool().execute()
        assert "City: 杭州 (source=memory_file" in result

    @pytest.mark.asyncio
    async def test_memory_file_skips_comments_and_blanks(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        content = "# This is a comment\n\n   \n# Another\nShanghai\n"
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", return_value=io.StringIO(content)):
            result = await UserLocationTool().execute()
        assert "City: Shanghai (source=memory_file" in result

    @pytest.mark.asyncio
    async def test_memory_file_only_comments_falls_through(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", False)
        content = "# only comments\n# nothing useful\n"
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", return_value=io.StringIO(content)):
            result = await UserLocationTool().execute()
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_memory_file_read_error_falls_through(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", False)
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", side_effect=OSError("permission denied")):
            result = await UserLocationTool().execute()
        assert result.startswith("Error:")


# ======================================================================
# 3. IP geolocation path (default enabled)
# ======================================================================

class TestIPPath:

    @pytest.mark.asyncio
    async def test_ip_disabled_returns_error(self, monkeypatch):
        """When LOCATION_IP_LOOKUP_ENABLED=False, IP path is skipped entirely."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", False)
        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen") as mock_open:
            result = await UserLocationTool().execute()
        # Must be Error and urlopen must NOT have been called
        assert result.startswith("Error:")
        mock_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_ipapi_co_primary_hits(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", return_value=_make_urlopen_response(
                 {"city": "Hangzhou", "country_name": "China"})):
            result = await UserLocationTool().execute()
        assert "City: Hangzhou" in result
        assert "source=ip_geolocation" in result
        assert "APPROXIMATE" in result

    @pytest.mark.asyncio
    async def test_ipapi_fails_ipsb_fallback_hits(self, monkeypatch):
        """ipapi.co raises → ip.sb succeeds → result from ip.sb."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)

        ipsb_response = _make_urlopen_response({"city": "Hangzhou", "country": "China"})
        urlopen_mock = MagicMock(side_effect=[OSError("ipapi down"), ipsb_response])

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert "City: Hangzhou" in result
        assert "source=ip_geolocation" in result
        # Both services should have been attempted
        assert urlopen_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_ipapi_empty_city_falls_through_to_ipsb(self, monkeypatch):
        """ipapi.co returns empty city → tool moves on to ip.sb."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)

        first = _make_urlopen_response({"city": "", "country_name": ""})
        second = _make_urlopen_response({"city": "Shanghai"})
        urlopen_mock = MagicMock(side_effect=[first, second])

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert "City: Shanghai" in result
        assert urlopen_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_all_ip_services_fail(self, monkeypatch):
        """Both ipapi.co and ip.sb fail → final Error string."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", side_effect=OSError("network down")):
            result = await UserLocationTool().execute()
        assert result.startswith("Error:")
        assert "USER_LOCATION" in result
        assert "user_location.md" in result
        # New error message should mention LOCATION_IP_LOOKUP_ENABLED
        assert "LOCATION_IP_LOOKUP_ENABLED" in result

    @pytest.mark.asyncio
    async def test_all_ip_services_return_empty_city(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)

        empty = _make_urlopen_response({"city": ""})
        urlopen_mock = MagicMock(side_effect=[empty, empty])

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert result.startswith("Error:")


# ======================================================================
# 4. priority order — env > memory > IP
# ======================================================================

class TestPriorityOrder:

    @pytest.mark.asyncio
    async def test_env_var_wins_over_memory_file(self, monkeypatch):
        monkeypatch.setenv("USER_LOCATION", "FromEnv")
        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", return_value=io.StringIO("FromMemory\n")):
            result = await UserLocationTool().execute()
        assert "City: FromEnv (source=env_var)" == result

    @pytest.mark.asyncio
    async def test_memory_file_wins_over_ip(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)

        with patch("os.path.isfile", return_value=True), \
             patch("builtins.open", return_value=io.StringIO("FromMemory\n")), \
             patch("urllib.request.urlopen") as mock_open:
            result = await UserLocationTool().execute()
        assert "City: FromMemory" in result
        assert "source=memory_file" in result
        # IP path must not have been touched
        mock_open.assert_not_called()
