"""
User Location Tool - Resolve current user's city via a fallback chain.
用户位置工具 —— 通过 fallback 链解析当前用户所在城市。

Resolution order (each step is independent; first hit wins):
  1. USER_LOCATION env var                       (explicit, highest priority)
  2. {MEMORY_DIR}/user_location.md               (persistent user fact file)
  3. IP geolocation                              (ipapi.co primary + ip.sb fallback)
  4. Error string                                (no source resolved)

解析顺序（按优先级降级）：
  1. USER_LOCATION 环境变量                     （显式配置，最高优先级）
  2. {MEMORY_DIR}/user_location.md              （用户事实文件，可手工维护）
  3. IP 地理定位                                 （ipapi.co 主 + ip.sb 备份）
  4. Error: ... 字符串                          （所有源均失败）

设计要点：
- **不再使用系统时区推断**：IANA 时区命名（如 Asia/Shanghai）是时间偏移
  的代号而非地理位置；中国大陆全境、美国西海岸三州都映射到同一个 zone，
  把 zone tail 当 city 是 hack，对地理大国注定失败。
- **IP 默认启用**：IP 段的运营商地理注册是真实地理信号，精度通常到城市；
  即便有偏差（如 CGNAT 汇聚到省会），仍比时区推断高一个数量级。
- **错误透传**：所有异常 catch 后转 "Error:" 前缀字符串，配合
  react/engine.py 的 Error: 检测进入 ToolRouter 失败计数。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)


# IP geolocation services tried in order. Each entry: (display_name, url, city_extractor)
# IP 定位服务列表，按顺序尝试。每项：(显示名, URL, city 字段提取函数)
_IP_SERVICES: list[tuple[str, str, Any]] = [
    ("ipapi.co", "https://ipapi.co/json/", lambda d: d.get("city")),
    ("ip.sb", "https://api.ip.sb/geoip", lambda d: d.get("city")),
]


class UserLocationTool(BaseTool):
    """
    Resolve the user's current city via fallback chain.
    通过 fallback 链解析用户当前城市。
    """

    @property
    def name(self) -> str:
        return "get_user_location"

    @property
    def description(self) -> str:
        return (
            "Resolve the user's current city for tasks that depend on "
            "location (weather, local time, nearby restaurants, news, "
            "etc.). Takes no parameters. Returns a string like "
            "'City: <name> (source=<env_var|memory_file|ip_geolocation>)' "
            "on success, or 'Error: ...' if no source resolves. "
            "Sources marked APPROXIMATE (ip_geolocation) may be off by "
            "tens of kilometres due to CGNAT or ISP backbone aggregation "
            "— confirm with the user if precision matters. DO NOT call "
            "this tool for tasks that do not depend on location (math, "
            "coding, general Q&A)."
            # 解析用户当前城市；按 env > memory > IP 顺序降级；
            # APPROXIMATE 标记的来源精度有限，必要时与用户确认；
            # 不要为与位置无关的任务（数学、编码）调用此工具。
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        # 1. env var (explicit, highest priority)
        # 1. 环境变量（显式配置，最高优先级）
        env_value = (os.getenv("USER_LOCATION") or "").strip()
        if env_value:
            return f"City: {env_value} (source=env_var)"

        # 2. memory file: {MEMORY_DIR}/user_location.md
        # 2. 用户事实文件（可手工维护）
        memory_city = await asyncio.to_thread(self._read_memory_file)
        if memory_city:
            path = os.path.join(config.MEMORY_DIR, "user_location.md")
            return f"City: {memory_city} (source=memory_file path={path})"

        # 3. IP geolocation (default enabled; gated by config flag for privacy)
        # 3. IP 地理定位（默认启用，可通过开关关闭以保护隐私）
        if config.LOCATION_IP_LOOKUP_ENABLED:
            ip_city = await asyncio.to_thread(self._lookup_via_ip)
            if ip_city:
                return (
                    f"City: {ip_city} (source=ip_geolocation, APPROXIMATE — "
                    "IP-based geolocation may be off by tens of kilometres "
                    "due to CGNAT or ISP backbone aggregation)"
                )

        # 4. all sources failed
        # 4. 所有来源均失败
        memory_path = os.path.join(config.MEMORY_DIR, "user_location.md")
        return (
            "Error: get_user_location could not resolve a location. "
            "Set the USER_LOCATION env var, write a city to "
            f"{memory_path}, ensure LOCATION_IP_LOOKUP_ENABLED is true "
            "and network access works, or ask the user directly for "
            "their city."
        )

    # ------------------------------------------------------------------
    # Helpers (sync, run in asyncio.to_thread)
    # 辅助方法（同步实现，调用方用 asyncio.to_thread 包裹）
    # ------------------------------------------------------------------

    def _read_memory_file(self) -> str | None:
        """
        Read first non-empty, non-comment line from user_location.md.
        读取 user_location.md 第一条非空非注释行。
        """
        path = os.path.join(config.MEMORY_DIR, "user_location.md")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        return stripped
        except OSError as exc:
            logger.warning(
                "[UserLocationTool] Failed to read %s: %s", path, exc
            )
        return None

    def _lookup_via_ip(self) -> str | None:
        """
        Try IP geolocation services in order; first non-empty city wins.
        按顺序尝试 IP 定位服务；首个返回非空 city 字段的胜出。

        Service list (in order):
          1. ipapi.co — well-formed JSON, may be slow/rate-limited from CN
          2. ip.sb    — CloudFlare-backed, generally reachable from CN

        服务列表（按顺序）：
          1. ipapi.co — JSON 接口规范，但国内访问可能慢/限频
          2. ip.sb    — CloudFlare CDN 加速，国内一般可达
        """
        import json as _json
        import urllib.request

        for name, url, extract in _IP_SERVICES:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "manus-demo/1.0"},
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                city = (extract(data) or "").strip()
                if city:
                    return city
                logger.warning(
                    "[UserLocationTool] %s returned empty city, trying next",
                    name,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning(
                    "[UserLocationTool] IP lookup via %s failed: %s",
                    name, exc,
                )
        return None
