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

from core.settings import CapabilitySettings


@dataclass
class AgentBaySessionHandle:
    agent_bay: Any
    session: Any
    session_id: str


def get_agentbay_sdk(settings: CapabilitySettings) -> tuple[Any, Any, Any, Any]:
    """Return SDK classes: AgentBay, CreateSessionParams, LifecyclePolicy, BrowserOption."""
    level_name = settings.agentbay_log_level
    level = getattr(logging, level_name, logging.WARNING)
    logging.getLogger("agentbay").setLevel(level)
    logging.getLogger("wuying_agentbay_sdk").setLevel(level)
    from agentbay import AgentBay, BrowserOption, CreateSessionParams, LifecyclePolicy

    return AgentBay, CreateSessionParams, LifecyclePolicy, BrowserOption


def get_api_key(settings: CapabilitySettings) -> str:
    """Read the key loaded by the centralized secret loader."""
    return settings.agentbay_api_key.strip()


def session_labels(tool_name: str) -> dict[str, str]:
    return {
        "project": "manus_demo",
        "owner": "agentbay_tool",
        "tool": tool_name,
    }


def create_params(
    image_id: str,
    tool_name: str,
    settings: CapabilitySettings,
) -> Any:
    _, CreateSessionParams, LifecyclePolicy, _ = get_agentbay_sdk(settings)
    return CreateSessionParams(
        image_id=image_id,
        labels=session_labels(tool_name),
        lifecycle_policy=LifecyclePolicy(
            idle_release_timeout=max(1, settings.agentbay_idle_release_minutes),
            max_runtime=max(1, settings.agentbay_max_runtime_minutes),
        ),
    )


async def create_session(
    image_id: str,
    tool_name: str,
    settings: CapabilitySettings,
) -> AgentBaySessionHandle:
    api_key = get_api_key(settings)
    if not api_key:
        raise RuntimeError("AGENTBAY_API_KEY is not configured")

    AgentBay, _, _, _ = get_agentbay_sdk(settings)
    agent_bay = AgentBay(api_key=api_key)
    params = create_params(image_id, tool_name, settings)
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
