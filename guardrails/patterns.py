"""
Guardrail detection patterns compiled once at import.
护栏检测模式在模块级编译，供各层复用。
"""

from __future__ import annotations

import re

# ----------------------------------------------------------------------
# 19.2 Indirect prompt injection (in untrusted tool output / retrieved memory)
# 间接提示注入（不可信工具输出 / 检索到的记忆）
# ----------------------------------------------------------------------
INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(the\s+)?(previous|above|prior|earlier)\s+(instructions|prompts?|messages?)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(the\s+)?(previous|above|prior)\b", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all\s+previous|your\s+instructions)", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"\bnew\s+instructions?\s*[:：]", re.IGNORECASE),
    re.compile(r"\bsystem\s*(prompt|message)?\s*[:：]\s*", re.IGNORECASE),
    re.compile(r"do\s+not\s+follow\s+(the\s+)?(user|original)", re.IGNORECASE),
    re.compile(r"<\|.*?\|>"),  # special role tokens
    re.compile(r"忽略(之前|上述|前面|所有).{0,4}(指令|提示|要求|消息)"),
    re.compile(r"(优先)?执行(以下|下面|这些).{0,4}(指令|命令)"),
    re.compile(r"你现在是"),
]

# ----------------------------------------------------------------------
# 19.3 PII / credentials (final output redaction)
# 凭证 / PII（最终输出脱敏）
# ----------------------------------------------------------------------
CREDENTIAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),                     # OpenAI-style key
    re.compile(r"AKIA[0-9A-Z]{16}"),                        # AWS access key id
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),  # private key block
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}"),          # bearer token
    re.compile(r"(?i)\bapi[_-]?key\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\b(secret|password|passwd|token)\b\s*[:=]\s*['\"]?\S{6,}"),
    re.compile(r"root:[^:]*:0:0:"),                          # /etc/passwd line
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),             # email (PII)
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),                   # credit-card-ish digit run
]

# ----------------------------------------------------------------------
# 19.1 Dangerous python code patterns
# 危险 python 代码
# ----------------------------------------------------------------------
DANGEROUS_PYTHON_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bos\.system\s*\("),
    re.compile(r"\bos\.popen\s*\("),
    re.compile(r"\bsubprocess\.(run|call|Popen|check_output)\s*\("),
    re.compile(r"\bimport\s+socket\b|\bsocket\.socket\s*\("),
    re.compile(r"\beval\s*\(|\bexec\s*\("),
    re.compile(r"\b__import__\s*\("),
    re.compile(r"os\.environ.*(API|KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE),
    re.compile(r"\bopen\s*\(\s*['\"]/etc/(passwd|shadow)"),
]

# Generic exfil markers usable across any tool param value
# 通用越权/泄露标记（任意工具参数值）
GENERIC_EXFIL_PATTERNS: list[re.Pattern] = [
    re.compile(r"/etc/(passwd|shadow)"),
    re.compile(r"id_rsa\b|\.ssh/"),
    re.compile(r"\b\.aws/credentials\b"),
]


def first_match(text: str, patterns: list[re.Pattern]) -> str:
    """Return the first matched substring (for the reason message), or empty."""
    if not text:
        return ""
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(0)
    return ""
