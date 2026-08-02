"""
Shell Tool - Execute shell commands in a sandboxed subprocess.
Shell 工具 —— 在沙箱子进程中执行 shell 命令。

Restricted mode executes one allowlisted argv command without a shell.
Trusted mode uses bash and therefore has the current user's local permissions.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from tools.base import BaseTool
from tools.shell_safety import assess_shell_command
from tools.subprocess_utils import build_safe_env, run_with_limits

logger = logging.getLogger(__name__)


class ShellTool(BaseTool):
    """
    Execute shell commands in a subprocess with timeout and safety checks.
    在带超时保护和安全检查的子进程中执行 shell 命令。
    """

    def __init__(
        self,
        workdir: str,
        mode: str,
        timeout: int,
        python_command: str,
        max_output_bytes: int,
        max_concurrent: int,
        ssl_verify: bool = True,
    ):
        self._workdir = workdir
        self._mode = mode
        self._timeout = timeout
        self._python_command = python_command
        self._max_output_bytes = max_output_bytes
        self._concurrency_sem = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._ssl_verify = ssl_verify
        os.makedirs(self._workdir, exist_ok=True)

    def for_sandbox(self, sandbox_dir: str) -> "ShellTool":
        """Return an independent shell tool rooted at a child sandbox."""
        return ShellTool(
            workdir=sandbox_dir,
            mode=self._mode,
            timeout=self._timeout,
            python_command=self._python_command,
            max_output_bytes=self._max_output_bytes,
            max_concurrent=self._max_concurrent,
            ssl_verify=self._ssl_verify,
        )

    @property
    def name(self) -> str:
        return "execute_shell"

    @property
    def description(self) -> str:
        syntax = (
            "one allowlisted command without pipes, redirects, or expansion"
            if self._mode == "restricted"
            else "full bash syntax with the current user's permissions"
        )
        return (
            f"Execute a shell command in {self._mode!r} mode and return the output. "
            "The command runs in a subprocess with a timeout. "
            f"Supports {syntax}. "
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

        assessment = assess_shell_command(command, self._workdir, self._mode)
        if not assessment.allowed:
            return f"Error: Command blocked for safety: {assessment.reason}."

        logger.info("Executing shell command: %s", command[:100])

        async with self._concurrency_sem:
            try:
                return await self._run_shell(assessment.argv, timeout)
            except asyncio.TimeoutError:
                return f"Error: Shell command timed out after {timeout}s."
            except Exception as exc:
                return f"Error: executing shell command failed: {exc}"

    def _check_blocked(self, command: str) -> str | None:
        assessment = assess_shell_command(command, self._workdir, self._mode)
        return None if assessment.allowed else assessment.reason

    async def _run_shell(self, argv: list[str], timeout: float) -> str:
        result = await run_with_limits(
            cmd=argv,
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
