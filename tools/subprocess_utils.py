"""
Subprocess Utilities - Shared subprocess management for shell and code tools.
子进程工具 —— 为 Shell 和代码执行工具提供共享的子进程管理。

Provides safe subprocess execution with:
- Environment variable sanitization (strip API keys, secrets)
- Output size limits to prevent memory exhaustion
- Proper timeout handling with guaranteed process cleanup
- asyncio-native implementation (no thread pool)

提供安全的子进程执行能力：
- 环境变量清理（移除 API Key、密钥等敏感信息）
- 输出大小限制，防止内存耗尽
- 正确的超时处理，保证子进程被清理
- asyncio 原生实现（不使用线程池）
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)

# Keys matching these patterns (case-insensitive) are stripped from subprocess env.
_SENSITIVE_PATTERNS = [
    re.compile(r"api.?key", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
]


def build_safe_env() -> dict[str, str]:
    """
    Return a sanitized copy of os.environ with sensitive keys removed.
    返回清理后的环境变量副本，移除 API Key、密钥等敏感条目。
    """
    env = dict(os.environ)
    keys_to_remove = []
    for key in env:
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(key):
                keys_to_remove.append(key)
                break
    for key in keys_to_remove:
        env.pop(key, None)

    # When LOCATION_SSL_VERIFY=false, inject env vars so subprocess tools
    # (execute_shell, etc.) skip SSL certificate verification.
    # 当 LOCATION_SSL_VERIFY=false 时，注入环境变量让子进程工具跳过 SSL 证书验证。
    if not config.LOCATION_SSL_VERIFY:
        env["CURL_CA_BUNDLE"] = ""                    # curl: skip cert verification
        env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"     # Node.js: skip TLS verification

    return env


@dataclass
class SubprocessResult:
    stdout: str
    stderr: str
    returncode: int


async def run_with_limits(
    cmd: list[str],
    timeout: float,
    cwd: str,
    env: dict[str, str] | None = None,
    max_output_bytes: int = 512 * 1024,
) -> SubprocessResult:
    """
    Execute a subprocess with timeout, output limits, and guaranteed cleanup.
    执行子进程，带超时保护、输出限制和保证的进程清理。

    Uses asyncio.create_subprocess_exec for direct process lifecycle control.
    On timeout or error, the process is killed and waited on to prevent orphans.
    使用 asyncio.create_subprocess_exec 实现直接的进程生命周期控制。
    超时或异常时会 kill 进程并 wait，防止产生孤儿进程。
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            _read_with_limit(proc, max_output_bytes),
            timeout=timeout,
        )
    except BaseException:
        proc.kill()
        await proc.wait()
        raise

    await proc.wait()

    return SubprocessResult(
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        returncode=proc.returncode,
    )


async def _read_with_limit(
    proc: asyncio.subprocess.Process,
    max_bytes: int,
) -> tuple[bytes, bytes]:
    """
    Read stdout and stderr concurrently with a byte budget.
    并发读取 stdout 和 stderr，带字节预算限制。

    If total output exceeds max_bytes, kills the process and returns
    truncated output with a marker appended.
    如果总输出超过 max_bytes，会杀掉进程并返回截断的输出（附带截断标记）。
    """
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    total = 0
    truncated = False

    async def _read_stream(stream: asyncio.StreamReader | None, chunks: list[bytes]) -> None:
        nonlocal total, truncated
        if stream is None:
            return
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            if not truncated:
                if total + len(chunk) <= max_bytes:
                    chunks.append(chunk)
                    total += len(chunk)
                else:
                    remaining = max_bytes - total
                    if remaining > 0:
                        chunks.append(chunk[:remaining])
                        total += remaining
                    truncated = True
                    proc.kill()
                    await proc.wait()
                    break

    await asyncio.gather(
        _read_stream(proc.stdout, stdout_chunks),
        _read_stream(proc.stderr, stderr_chunks),
    )

    stdout = b"".join(stdout_chunks)
    stderr = b"".join(stderr_chunks)

    if truncated:
        marker = f"\n[output truncated at {max_bytes} bytes]".encode()
        stdout += marker

    return stdout, stderr
