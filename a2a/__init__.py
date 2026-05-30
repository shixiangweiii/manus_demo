"""
A2A prototype (v18.4) - local-trusted Agent-to-Agent over MCP.
A2A 原型（v18.4）—— 走 MCP 的本地可信 agent 间协作。
"""

from a2a.client import A2AClient
from a2a.models import (
    A2A_PROTOCOL,
    A2ATaskRequest,
    A2ATaskResponse,
    AgentCard,
    AgentSkill,
)

__all__ = [
    "A2AClient",
    "AgentCard",
    "AgentSkill",
    "A2ATaskRequest",
    "A2ATaskResponse",
    "A2A_PROTOCOL",
]
