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

    def __init__(
        self,
        sandbox_dir: str | None = None,
        timeout: int | None = None,
        max_output_bytes: int | None = None,
        ssl_verify: bool | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        self._sandbox_dir = sandbox_dir or config.SANDBOX_DIR
        self._timeout = timeout or config.CODE_EXEC_TIMEOUT
        self._max_output_bytes = max_output_bytes or config.SUBPROCESS_MAX_OUTPUT_BYTES
        self._ssl_verify = config.LOCATION_SSL_VERIFY if ssl_verify is None else ssl_verify
        self._concurrency_sem = asyncio.Semaphore(max_concurrent or config.CODE_MAX_CONCURRENT)

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

        async with self._concurrency_sem:
            try:
                return await self._run_code(code)
            except asyncio.TimeoutError:
                return f"Error: Code execution timed out after {self._timeout}s."
            except Exception as exc:
                return f"Error: executing code failed: {exc}"

    async def _run_code(self, code: str) -> str:
        # When LOCATION_SSL_VERIFY=false, monkeypatch ssl so all HTTPS
        # requests in the subprocess skip certificate verification.
        # 当 LOCATION_SSL_VERIFY=false 时，注入 SSL monkeypatch 让子进程中
        # 所有 HTTPS 请求跳过证书验证。
        if not self._ssl_verify:
            code = (
                "import ssl as _manus_ssl;"
                " _manus_ssl._create_default_https_context"
                " = _manus_ssl._create_unverified_context\n"
            ) + code

        result = await run_with_limits(
            cmd=[sys.executable, "-c", code],
            timeout=self._timeout,
            cwd=self._sandbox_dir,
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

        if not output_parts:
            output_parts.append("Code executed successfully (no output).")

        output_parts.append(f"[Working directory: {self._sandbox_dir}]")
        return "\n".join(output_parts)
