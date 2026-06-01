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

    def check_skill_allowed_tools(self, skill_name: str, tool_name: str) -> GuardrailDecision:
        """Verify that a skill's pre-authorized tool does not conflict with ToolGuardrail rules (v20.3).
        验证技能预授权工具不与 ToolGuardrail 规则冲突（v20.3 新增）。

        This is a "shadow check" — it runs the same per-tool rules as `check()`
        but with dummy params, to see if the tool would be blocked. This enforces
        the priority chain: ToolGuardrail > allowed-tools > default tool set.

        Even if a skill pre-authorizes a tool via allowed_tools, if ToolGuardrail
        would block it (e.g., execute_shell with dangerous commands, file_ops with
        sandbox escape), the guardrail wins and the tool is removed from the
        effective tool set.

        这是"影子检查"——使用与 check() 相同的按工具规则但用空参数，
        检查工具是否会被阻止。强制优先级链：
        ToolGuardrail > allowed-tools > default tool set。
        """
        # Tools with no guardrail rules are always safe to pre-authorize
        # 没有护栏规则的工具总是可以安全预授权
        if tool_name not in {"execute_shell", "execute_python", "agentbay_code",
                             "agentbay_browser", "file_ops"}:
            return _allow()

        # Shell and Python tools: always blocked for skill pre-authorization
        # because they can execute arbitrary code which is dangerous
        # Shell 和 Python 工具：技能预授权始终阻止，
        # 因为它们可以执行任意代码，这是危险的
        if tool_name in {"execute_shell", "execute_python", "agentbay_code"}:
            return _block(
                f"skill '{skill_name}' pre-authorizes '{tool_name}' which is blocked by ToolGuardrail "
                f"(dangerous code execution)", risk="ASI02",
            )

        # file_ops: sandbox-gated, write ops require confirmation — too risky for
        # automatic pre-authorization by a skill
        # file_ops: 沙箱门控、写操作需确认——技能自动预授权风险过高
        if tool_name == "file_ops":
            return _block(
                f"skill '{skill_name}' pre-authorizes 'file_ops' which is blocked by ToolGuardrail "
                f"(sandbox/write restrictions)", risk="ASI03",
            )

        # agentbay_browser: URL restrictions apply — block for skills
        # agentbay_browser: URL 限制适用——对技能阻止
        if tool_name == "agentbay_browser":
            return _block(
                f"skill '{skill_name}' pre-authorizes 'agentbay_browser' which is blocked by ToolGuardrail "
                f"(URL restrictions)", risk="ASI03",
            )

        return _allow()
