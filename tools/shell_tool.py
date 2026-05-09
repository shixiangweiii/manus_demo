"""
Shell Tool - Execute shell commands in a sandboxed subprocess.
Shell 工具 —— 在沙箱子进程中执行 shell 命令。

Executes shell commands via bash with a timeout, capturing stdout and
stderr. Includes a command blacklist for basic safety.
通过 bash 执行 shell 命令，设有超时保护和命令黑名单安全防护。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import config
from tools.base import BaseTool
from tools.subprocess_utils import build_safe_env, run_with_limits

logger = logging.getLogger(__name__)


class ShellTool(BaseTool):
    """
    Execute shell commands in a subprocess with timeout and safety checks.
    在带超时保护和安全检查的子进程中执行 shell 命令。
    """

    BLOCKED_PATTERNS = [
        # Destructive filesystem operations
        re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|.*--no-preserve-root\s+)/"),
        re.compile(r"\bmkfs\b"),
        re.compile(r"\bdd\b\s+.*\bif="),
        re.compile(r">\s*/dev/sd"),
        re.compile(r"\bshred\b"),
        # Privilege escalation
        re.compile(r"\bsudo\b"),
        re.compile(r"\bsu\b"),
        re.compile(r"\bpkexec\b"),
        # Network exfiltration / remote code execution
        re.compile(r"\bcurl\b.*\|\s*sh"),
        re.compile(r"\bwget\b.*\|\s*sh"),
        re.compile(r"\bnc\b.*-e"),
        re.compile(r"\bncat\b.*-e"),
        # System modification
        re.compile(r"\bsystemctl\b"),
        re.compile(r"\bservice\b"),
        re.compile(r"\bcrontab\b"),
        re.compile(r"\blaunchctl\b"),
        # Credential access
        re.compile(r"\bprintenv\b"),
        re.compile(r"\bexport\b.*API_KEY", re.IGNORECASE),
    ]

    _concurrency_sem: asyncio.Semaphore | None = None

    def __init__(self):
        self._workdir = config.SANDBOX_DIR
        os.makedirs(self._workdir, exist_ok=True)

    @classmethod
    def _get_sem(cls) -> asyncio.Semaphore:
        if cls._concurrency_sem is None:
            cls._concurrency_sem = asyncio.Semaphore(config.SHELL_MAX_CONCURRENT)
        return cls._concurrency_sem

    @property
    def name(self) -> str:
        return "execute_shell"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return the output. "
            "The command runs in a subprocess with a timeout. "
            "Supports standard bash syntax. "
            "Working directory is the sandbox folder."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (optional, uses default if not provided)",
                },
            },
            "required": ["command"],
        }

    async def execute(self, **kwargs: Any) -> str:
        command = kwargs.get("command", "")
        timeout = kwargs.get("timeout")
        if timeout is None:
            timeout = config.SHELL_EXEC_TIMEOUT

        if not command.strip():
            return "Error: No command provided."

        blocked = self._check_blocked(command)
        if blocked:
            return f"Error: Command blocked for safety: contains '{blocked}'."

        logger.info("Executing shell command: %s", command[:100])

        async with self._get_sem():
            try:
                return await self._run_shell(command, timeout)
            except asyncio.TimeoutError:
                return f"Error: Shell command timed out after {timeout}s."
            except Exception as exc:
                return f"Error executing shell command: {exc}"

    def _check_blocked(self, command: str) -> str | None:
        for pattern in self.BLOCKED_PATTERNS:
            match = pattern.search(command)
            if match:
                return match.group(0)
        return None

    @staticmethod
    async def _run_shell(command: str, timeout: float) -> str:
        result = await run_with_limits(
            cmd=["bash", "-c", command],
            timeout=timeout,
            cwd=config.SANDBOX_DIR,
            env=build_safe_env(),
            max_output_bytes=config.SUBPROCESS_MAX_OUTPUT_BYTES,
        )

        output_parts = []
        if result.stdout:
            output_parts.append(f"Output:\n{result.stdout.strip()}")
        if result.stderr:
            output_parts.append(f"Errors:\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"Exit code: {result.returncode}")

        if not output_parts:
            return "Command executed successfully (no output)."

        return "\n".join(output_parts)
