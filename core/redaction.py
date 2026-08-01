"""Shared secret detection and redaction helpers."""

from __future__ import annotations

import re
from typing import Any


_SENSITIVE_NAMES = (
    "apikey",
    "apisecret",
    "accesskey",
    "privatekey",
    "token",
    "password",
    "passwd",
    "credential",
    "secret",
    "authorization",
    "cookie",
)

_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(
        r"(?i)\b(api[-_ ]?key|token|password|passwd|secret|authorization)\b"
        r"\s*[:=]\s*(['\"]?)[^\s,'\"}]+"
    ),
)


def normalize_key(key: object) -> str:
    """Normalize key spelling so api-key, api_key, and apiKey compare equally."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: object) -> bool:
    normalized = normalize_key(key)
    return any(name in normalized for name in _SENSITIVE_NAMES)


def redact_text(value: object) -> str:
    text = str(value)
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def redact_value(value: Any) -> Any:
    """Recursively redact secret-bearing mapping keys and recognizable text values."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key] = "[REDACTED]" if is_sensitive_key(key) else redact_value(item)
        return result
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
