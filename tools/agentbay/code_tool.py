"""AgentBay CodeSpace tool."""

from __future__ import annotations

import logging
import asyncio
from typing import Any

from core.settings import CapabilitySettings
from tools.agentbay.runtime import create_session, delete_session
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class AgentBayCodeTool(BaseTool):
    """Run code in an AgentBay CodeSpace session."""

    def __init__(
        self,
        settings: CapabilitySettings,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self._settings = settings
        self._semaphore = semaphore

    @property
    def name(self) -> str:
        return "agentbay_code"

    @property
    def description(self) -> str:
        return (
            "Execute code in an isolated Alibaba Cloud AgentBay CodeSpace session. "
            "Use this when cloud isolation or a clean remote runtime is preferred."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Code to execute remotely."},
                "language": {
                    "type": "string",
                    "enum": ["python", "javascript"],
                    "description": "Language runtime. Defaults to python.",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Execution timeout in seconds, clamped to 1..60.",
                },
            },
            "required": ["code"],
        }

    async def execute(self, **kwargs: Any) -> str:
        code = str(kwargs.get("code", "") or "")
        if not code.strip():
            return "Error: code parameter is required for agentbay_code."

        language = str(kwargs.get("language") or "python").lower()
        if language not in {"python", "javascript"}:
            return "Error: language must be one of: python, javascript."

        timeout_s = kwargs.get(
            "timeout_s", self._settings.agentbay_code_timeout_seconds
        )
        try:
            timeout_s = int(timeout_s)
        except (TypeError, ValueError):
            timeout_s = self._settings.agentbay_code_timeout_seconds
        timeout_s = max(1, min(60, timeout_s))

        async with self._semaphore:
            handle = None
            result_text = ""
            deleted = True
            delete_error = ""
            try:
                handle = await create_session(
                    self._settings.agentbay_code_image,
                    self.name,
                    self._settings,
                )
                logger.info("[AgentBayCodeTool] Created session %s", handle.session_id)
                result = await self._run_code(handle.session, code, language, timeout_s)
                if not getattr(result, "success", False):
                    error_message = self._format_code_error(result)
                    result_text = f"Error: AgentBay code execution failed in session {handle.session_id}: {error_message}"
                else:
                    result_text = self._format_code_success(handle.session_id, result)
            except Exception as exc:
                sid = getattr(handle, "session_id", "(no session)")
                result_text = f"Error: AgentBay code execution failed in session {sid}: {exc}"
            finally:
                if handle is not None:
                    deleted, delete_error = await delete_session(handle)
                    if not deleted:
                        logger.warning(
                            "[AgentBayCodeTool] Failed to delete session %s: %s",
                            handle.session_id,
                            delete_error,
                        )
            if handle is not None:
                result_text += f"\nsession_deleted: {str(deleted).lower()}"
                if delete_error:
                    result_text += f"\nsession_delete_error: {delete_error}"
            return result_text

    @staticmethod
    async def _run_code(session: Any, code: str, language: str, timeout_s: int) -> Any:
        return await asyncio.to_thread(
            session.code.run_code,
            code,
            language,
            timeout_s=timeout_s,
        )

    @staticmethod
    def _format_code_error(result: Any) -> str:
        err = getattr(result, "error_message", "") or ""
        detailed = getattr(result, "error", None)
        if detailed is not None:
            name = getattr(detailed, "name", "") or ""
            value = getattr(detailed, "value", "") or ""
            trace = getattr(detailed, "traceback", "") or ""
            parts = [p for p in [name, value, trace] if p]
            if parts:
                err = "\n".join(parts)
        return err or "unknown error"

    @staticmethod
    def _format_code_success(session_id: str, result: Any) -> str:
        logs = getattr(result, "logs", None)
        stdout = "".join(getattr(logs, "stdout", []) or []) if logs else ""
        stderr = "".join(getattr(logs, "stderr", []) or []) if logs else ""
        text_result = getattr(result, "result", "") or ""

        parts = [
            "AgentBay CodeSpace execution succeeded.",
            f"session_id: {session_id}",
        ]
        request_id = getattr(result, "request_id", "")
        if request_id:
            parts.append(f"request_id: {request_id}")
        if stdout or text_result:
            parts.append("stdout/result:\n" + (stdout or text_result).strip())
        if stderr:
            parts.append("stderr:\n" + stderr.strip())
        if not stdout and not stderr and not text_result:
            parts.append("No output.")
        return "\n".join(parts)
