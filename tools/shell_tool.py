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

    def __init__(
        self,
        workdir: str | None = None,
        timeout: int | None = None,
        python_command: str | None = None,
        max_output_bytes: int | None = None,
        max_concurrent: int | None = None,
        ssl_verify: bool = True,
    ):
        self._workdir = workdir or config.SANDBOX_DIR
        self._timeout = timeout or config.SHELL_EXEC_TIMEOUT
        self._python_command = python_command or config.PYTHON_COMMAND
        self._max_output_bytes = max_output_bytes or config.SUBPROCESS_MAX_OUTPUT_BYTES
        self._concurrency_sem = asyncio.Semaphore(max_concurrent or config.SHELL_MAX_CONCURRENT)
        self._ssl_verify = ssl_verify
        os.makedirs(self._workdir, exist_ok=True)

    @property
    def name(self) -> str:
        return "execute_shell"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return the output. "
            "The command runs in a subprocess with a timeout. "
            "Supports standard bash syntax. "
            "Working directory is the sandbox folder. "
            f"When invoking Python, use `{self._python_command}` "
            "instead of bare `python` in this environment."
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
            timeout = self._timeout

        if not command.strip():
            return "Error: No command provided."

        blocked = self._check_blocked(command)
        if blocked:
            return f"Error: Command blocked for safety: contains '{blocked}'."

        logger.info("Executing shell command: %s", command[:100])

        async with self._concurrency_sem:
            try:
                return await self._run_shell(command, timeout)
            except asyncio.TimeoutError:
                return f"Error: Shell command timed out after {timeout}s."
            except Exception as exc:
                return f"Error: executing shell command failed: {exc}"

    def _check_blocked(self, command: str) -> str | None:
        for pattern in self.BLOCKED_PATTERNS:
            match = pattern.search(command)
            if match:
                return match.group(0)
        return None

    async def _run_shell(self, command: str, timeout: float) -> str:
        result = await run_with_limits(
            cmd=["bash", "-c", command],
            timeout=timeout,
            cwd=self._workdir,
            env=build_safe_env(ssl_verify=self._ssl_verify),
            max_output_bytes=self._max_output_bytes,
        )

        output_parts = []
        if result.stdout:
            output_parts.append(f"Output:\n{result.stdout.strip()}")
        if result.stderr:
            output_parts.append(f"Errors:\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.insert(0, f"Error: process exited with code {result.returncode}")
            combined = f"{result.stdout}\n{result.stderr}".lower()
            if "python: command not found" in combined:
                output_parts.append(
                    f"Hint: use `{self._python_command}` instead of bare `python` in this environment."
                )

        if not output_parts:
            output_parts.append("Command executed successfully (no output).")

        output_parts.append(f"[Working directory: {self._workdir}]")
        return "\n".join(output_parts)
