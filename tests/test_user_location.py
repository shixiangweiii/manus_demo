"""
Tests for UserLocationTool — fallback chain unit coverage.
用户位置工具测试 —— fallback 链单元覆盖。

All tests mock external dependencies (env vars, filesystem, urllib);
no real network calls.

覆盖路径：
  1. env var 命中
  2. memory 文件命中（含注释行 / 空行跳过）
  3. memory 文件不存在 → IP 命中（mock urlopen）
  4. IP 服务降级：ip-api.com 失败 → ipapi.co 命中
  5. 全部失败（IP 关闭 或 IP 接口异常）→ Error 字符串
  6. 优先级：env > memory > IP
  7. tool contract（name / description / parameters_schema）
  8. SSL 降级：verify=True SSL 失败 → verify=False 重试成功
  9. 非 SSL 错误不触发降级重试
  10. LOCATION_SSL_VERIFY=false 直接跳过验证
"""

from __future__ import annotations

import io
import json
import os
import ssl
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
    async def test_ip_api_com_primary_hits(self, monkeypatch):
        """ip-api.com is the first service and should hit immediately."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   return_value=_make_urlopen_response(
                       {"city": "Hangzhou", "status": "success"})):
            result = await UserLocationTool().execute()
        assert "City: Hangzhou" in result
        assert "source=ip_geolocation" in result
        assert "APPROXIMATE" in result

    @pytest.mark.asyncio
    async def test_ip_api_co_fails_ipapi_co_hits(self, monkeypatch):
        """ip-api.com raises → ipapi.co succeeds → result from ipapi.co."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        ipapi_response = _make_urlopen_response(
            {"city": "Shanghai", "country_name": "China"})
        urlopen_mock = MagicMock(
            side_effect=[OSError("ip-api down"), ipapi_response])

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert "City: Shanghai" in result
        assert "source=ip_geolocation" in result
        # ip-api.com failed once, ipapi.co succeeded once
        assert urlopen_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_ipapi_empty_city_falls_through(self, monkeypatch):
        """ip-api.com returns empty city → ipapi.co returns empty → ip.sb hits."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        empty = _make_urlopen_response({"city": ""})
        hit = _make_urlopen_response({"city": "Beijing"})
        urlopen_mock = MagicMock(side_effect=[empty, empty, hit])

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert "City: Beijing" in result
        assert urlopen_mock.call_count == 3

    @pytest.mark.asyncio
    async def test_all_ip_services_fail(self, monkeypatch):
        """All three services fail → final Error string."""
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   side_effect=OSError("network down")):
            result = await UserLocationTool().execute()
        assert result.startswith("Error:")
        assert "USER_LOCATION" in result
        assert "user_location.md" in result
        assert "LOCATION_IP_LOOKUP_ENABLED" in result

    @pytest.mark.asyncio
    async def test_all_ip_services_return_empty_city(self, monkeypatch):
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        empty = _make_urlopen_response({"city": ""})
        urlopen_mock = MagicMock(side_effect=[empty, empty, empty])

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


# ======================================================================
# 5. SSL degradation — verify=True fails → verify=False retry
# ======================================================================

class TestSSLDegradation:

    @pytest.mark.asyncio
    async def test_ssl_verify_true_degrades_on_cert_error(self, monkeypatch):
        """
        verify=True hits CERTIFICATE_VERIFY_FAILED → _last_was_ssl_error=True
        → retry with verify=False succeeds.
        """
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        ssl_err = OSError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        ok_response = _make_urlopen_response({"city": "Hangzhou"})
        urlopen_mock = MagicMock(side_effect=[ssl_err, ok_response])

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert "City: Hangzhou" in result
        # verify=True failed, verify=False retry succeeded — 2 calls total
        assert urlopen_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_ssl_verify_true_non_ssl_error_skips_degrade(self, monkeypatch):
        """
        Non-SSL error (timeout) does NOT trigger verify=False retry;
        directly moves to next service.
        """
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        timeout_err = OSError("timed out")
        ok_response = _make_urlopen_response({"city": "Shanghai"})
        # ip-api.com timeout → no SSL degrade → ipapi.co succeeds
        urlopen_mock = MagicMock(side_effect=[timeout_err, ok_response])

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert "City: Shanghai" in result
        # ip-api.com: 1 call (timeout, no retry), ipapi.co: 1 call (success)
        assert urlopen_mock.call_count == 2

    @pytest.mark.asyncio
    async def test_ssl_verify_false_direct_skip(self, monkeypatch):
        """
        LOCATION_SSL_VERIFY=false → directly use unverified context,
        no verify=True attempt first.
        """
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", False)

        ok_response = _make_urlopen_response({"city": "Beijing"})

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen",
                   return_value=ok_response) as mock_open, \
             patch("ssl._create_unverified_context") as mock_ctx:
            result = await UserLocationTool().execute()
        assert "City: Beijing" in result
        # Only 1 urlopen call (no verify=True attempt first)
        assert mock_open.call_count == 1
        # unverified context was used
        mock_ctx.assert_called_once()

    @pytest.mark.asyncio
    async def test_ssl_degrade_still_fails_across_all_services(self, monkeypatch):
        """
        All services fail SSL + degrade retry also fails → Error.
        """
        monkeypatch.delenv("USER_LOCATION", raising=False)
        monkeypatch.setattr("config.LOCATION_IP_LOOKUP_ENABLED", True)
        monkeypatch.setattr("config.LOCATION_SSL_VERIFY", True)

        ssl_err = OSError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        # 3 services × 2 attempts each = 6 calls, all fail
        urlopen_mock = MagicMock(side_effect=[ssl_err] * 6)

        with patch("os.path.isfile", return_value=False), \
             patch("urllib.request.urlopen", urlopen_mock):
            result = await UserLocationTool().execute()
        assert result.startswith("Error:")
        assert urlopen_mock.call_count == 6
