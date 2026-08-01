"""Shared AgentBay SDK helpers.

The SDK is imported lazily so the project does not require AgentBay unless the
feature flag is enabled. Console logging is lowered before import to avoid
printing resource URLs and auth codes by default.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import config


_sem: asyncio.Semaphore | None = None


@dataclass
class AgentBaySessionHandle:
    agent_bay: Any
    session: Any
    session_id: str


def get_agentbay_sdk() -> tuple[Any, Any, Any, Any]:
    """Return SDK classes: AgentBay, CreateSessionParams, LifecyclePolicy, BrowserOption."""
    level_name = str(getattr(config, "AGENTBAY_LOG_LEVEL", "WARNING"))
    level = getattr(logging, level_name, logging.WARNING)
    logging.getLogger("agentbay").setLevel(level)
    logging.getLogger("wuying_agentbay_sdk").setLevel(level)
    from agentbay import AgentBay, BrowserOption, CreateSessionParams, LifecyclePolicy

    return AgentBay, CreateSessionParams, LifecyclePolicy, BrowserOption


def get_api_key() -> str:
    """Read the key loaded by the centralized secret loader."""
    return (getattr(config, "AGENTBAY_API_KEY", "") or "").strip()


def concurrency_sem() -> asyncio.Semaphore:
    global _sem
    limit = max(1, int(getattr(config, "AGENTBAY_MAX_CONCURRENT_SESSIONS", 1)))
    if _sem is None or getattr(_sem, "_value", limit) > limit:
        _sem = asyncio.Semaphore(limit)
    return _sem


def session_labels(tool_name: str) -> dict[str, str]:
    return {
        "project": "manus_demo",
        "owner": "agentbay_tool",
        "tool": tool_name,
    }


def create_params(image_id: str, tool_name: str) -> Any:
    _, CreateSessionParams, LifecyclePolicy, _ = get_agentbay_sdk()
    return CreateSessionParams(
        image_id=image_id,
        labels=session_labels(tool_name),
        lifecycle_policy=LifecyclePolicy(
            idle_release_timeout=max(1, int(config.AGENTBAY_SESSION_IDLE_RELEASE_MINUTES)),
            max_runtime=max(1, int(config.AGENTBAY_SESSION_MAX_RUNTIME_MINUTES)),
        ),
    )


async def create_session(image_id: str, tool_name: str) -> AgentBaySessionHandle:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("AGENTBAY_API_KEY is not configured")

    AgentBay, _, _, _ = get_agentbay_sdk()
    agent_bay = AgentBay(api_key=api_key)
    params = create_params(image_id, tool_name)
    result = await asyncio.to_thread(agent_bay.create, params)
    if not getattr(result, "success", False):
        raise RuntimeError(getattr(result, "error_message", "AgentBay session creation failed"))

    session = result.session
    session_id = getattr(session, "session_id", "") or getattr(session, "sessionId", "") or "(unknown)"
    return AgentBaySessionHandle(agent_bay=agent_bay, session=session, session_id=session_id)


async def delete_session(handle: AgentBaySessionHandle) -> tuple[bool, str]:
    try:
        result = await asyncio.to_thread(handle.agent_bay.delete, handle.session)
        success = bool(getattr(result, "success", True))
        err = getattr(result, "error_message", "") or ""
        return success, err
    except Exception as exc:  # pragma: no cover - defensive cloud cleanup path
        return False, str(exc)
