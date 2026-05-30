"""
ToolGuardrail (v19.1) - validate tool inputs before execution.
工具输入护栏（v19.1）—— 工具执行前校验危险参数 / 路径越权 / 写操作。

Defense-in-depth ON TOP of ShellTool.BLOCKED_PATTERNS (not a replacement):
centralized, configurable, event-emitting. Returns a GuardrailDecision; the
engine maps CONFIRM (write ops) per GUARDRAIL_WRITE_CONFIRM.
在 ShellTool 黑名单之上的纵深防御：集中、可配、可埋点。返回决策，写操作 CONFIRM 由 engine 按配置裁决。
"""

from __future__ import annotations

import os
from urllib.parse import urlparse
import ipaddress

import config
from guardrails.models import GuardrailAction, GuardrailDecision, GuardrailLayer
from guardrails.patterns import (
    DANGEROUS_PYTHON_PATTERNS,
    DANGEROUS_SHELL_PATTERNS,
    GENERIC_EXFIL_PATTERNS,
    first_match,
)

# file_ops actions considered "writes" (require confirm/block per config)
_WRITE_ACTIONS = {"write", "append", "delete"}


def _allow() -> GuardrailDecision:
    return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)


def _block(reason: str, risk: str = "ASI02") -> GuardrailDecision:
    return GuardrailDecision(
        action=GuardrailAction.BLOCK, layer=GuardrailLayer.TOOL_INPUT, reason=reason, risk=risk,
    )


def _confirm(reason: str) -> GuardrailDecision:
    return GuardrailDecision(
        action=GuardrailAction.CONFIRM, layer=GuardrailLayer.TOOL_INPUT, reason=reason, risk="ASI02",
    )


def _within_sandbox(filename: str) -> bool:
    """True if filename resolves inside SANDBOX_DIR (blocks ../ traversal)."""
    if not filename:
        return True
    sandbox = os.path.realpath(config.SANDBOX_DIR)
    if os.path.isabs(filename):
        target = os.path.realpath(filename)
    else:
        target = os.path.realpath(os.path.join(sandbox, filename))
    return target == sandbox or target.startswith(sandbox + os.sep)


class ToolGuardrail:
    """Per-tool input validation. / 按工具的输入校验。"""

    def check(self, tool_name: str, params: dict) -> GuardrailDecision:
        params = params or {}

        if tool_name == "execute_shell":
            cmd = str(params.get("command", ""))
            hit = first_match(cmd, DANGEROUS_SHELL_PATTERNS)
            if hit:
                return _block(f"dangerous shell pattern '{hit}'", risk="ASI02")

        elif tool_name in {"execute_python", "agentbay_code"}:
            code = str(params.get("code", ""))
            hit = first_match(code, DANGEROUS_PYTHON_PATTERNS)
            if hit:
                return _block(f"dangerous python pattern '{hit}'", risk="ASI02")

        elif tool_name == "agentbay_browser":
            url = str(params.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return _block("agentbay_browser only permits http(s) URLs", risk="ASI03")
            host = (parsed.hostname or "").lower()
            if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
                return _block(f"agentbay_browser blocks local hostname '{host}'", risk="ASI03")
            try:
                ip = ipaddress.ip_address(host)
            except ValueError:
                ip = None
            if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
                return _block(f"agentbay_browser blocks non-public IP '{host}'", risk="ASI03")

        elif tool_name == "file_ops":
            action = str(params.get("action", "")).lower()
            filename = str(params.get("filename", ""))
            if not _within_sandbox(filename):
                return _block(f"path escapes sandbox: '{filename}'", risk="ASI03")
            if action in _WRITE_ACTIONS:
                return _confirm(f"file_ops '{action}' on '{filename}' is a write operation")

        # Generic exfil markers in any string param value
        for v in params.values():
            if isinstance(v, str):
                hit = first_match(v, GENERIC_EXFIL_PATTERNS)
                if hit:
                    return _block(f"sensitive path/credential reference '{hit}'", risk="ASI05")

        return _allow()
