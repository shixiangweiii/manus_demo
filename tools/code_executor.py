"""
Code Executor Tool - Runs Python code in a sandboxed subprocess.
代码执行工具 —— 在沙箱子进程中运行 Python 代码。

Executes user-provided Python code with a timeout, capturing stdout and
stderr. Uses subprocess isolation for basic safety.
执行 LLM 生成的 Python 代码，设有超时保护，捕获 stdout 和 stderr。
通过 subprocess 隔离实现基础安全防护。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import config
from tools.base import BaseTool
from tools.subprocess_utils import build_safe_env, run_with_limits

logger = logging.getLogger(__name__)


class CodeExecutorTool(BaseTool):
    """
    Execute Python code in a subprocess with timeout protection.
    在带超时保护的子进程中执行 Python 代码。
    """

    _concurrency_sem: asyncio.Semaphore | None = None

    @classmethod
    def _get_sem(cls) -> asyncio.Semaphore:
        if cls._concurrency_sem is None:
            cls._concurrency_sem = asyncio.Semaphore(config.CODE_MAX_CONCURRENT)
        return cls._concurrency_sem

    @property
    def name(self) -> str:
        return "execute_python"

    @property
    def description(self) -> str:
        return (
            "Execute Python code and return the output. "
            "The code runs in a subprocess with a timeout. "
            "Use print() to produce output that will be captured."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
            },
            "required": ["code"],
        }

    async def execute(self, **kwargs: Any) -> str:
        code = kwargs.get("code", "")
        if not code.strip():
            return "Error: No code provided."

        logger.info("Executing Python code (%d chars)", len(code))

        async with self._get_sem():
            try:
                return await self._run_code(code)
            except asyncio.TimeoutError:
                return f"Error: Code execution timed out after {config.CODE_EXEC_TIMEOUT}s."
            except Exception as exc:
                return f"Error executing code: {exc}"

    @staticmethod
    async def _run_code(code: str) -> str:
        result = await run_with_limits(
            cmd=[sys.executable, "-c", code],
            timeout=config.CODE_EXEC_TIMEOUT,
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
            return "Code executed successfully (no output)."

        return "\n".join(output_parts)
