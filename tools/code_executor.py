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
import subprocess
import sys
from typing import Any

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class CodeExecutorTool(BaseTool):
    """
    Execute Python code in a subprocess with timeout protection.
    在带超时保护的子进程中执行 Python 代码。
    使用 subprocess 隔离执行，避免恶意代码影响主进程。
    """

    @property
    def name(self) -> str:
        return "execute_python"

    @property
    def description(self) -> str:
        return (
            "Execute Python code and return the output. "
            "The code runs in a subprocess with a timeout. "
            "Use print() to produce output that will be captured."
            # 执行 Python 代码并返回输出。
            # 代码在子进程中运行，设有超时限制。
            # 使用 print() 产生会被捕获的输出。
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",  # 要执行的 Python 代码字符串
                },
            },
            "required": ["code"],
        }

    async def execute(self, **kwargs: Any) -> str:
        code = kwargs.get("code", "")
        if not code.strip():
            return "Error: No code provided."

        logger.info("Executing Python code (%d chars)", len(code))

        try:
            # 使用 asyncio.wait_for 实现异步超时控制
            result = await asyncio.wait_for(
                self._run_code(code),
                timeout=config.CODE_EXEC_TIMEOUT,
            )
            return result
        except asyncio.TimeoutError:
            return f"Error: Code execution timed out after {config.CODE_EXEC_TIMEOUT}s."
        except Exception as exc:
            return f"Error executing code: {exc}"

    @staticmethod
    async def _run_code(code: str) -> str:
        """
        Run Python code in a subprocess and capture output.
        在子进程中运行 Python 代码并捕获输出。
        使用 run_in_executor 将同步的 subprocess.run 包装为异步，避免阻塞事件循环。
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, "-c", code],  # 使用当前 Python 解释器执行代码字符串
                capture_output=True,            # 同时捕获 stdout 和 stderr
                text=True,
                timeout=config.CODE_EXEC_TIMEOUT,
            ),
        )

        output_parts = []
        if result.stdout:
            output_parts.append(f"Output:\n{result.stdout.strip()}")
        if result.stderr:
            output_parts.append(f"Errors:\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"Exit code: {result.returncode}")  # 非零退出码提示执行异常

        if not output_parts:
            return "Code executed successfully (no output)."

        return "\n".join(output_parts)
