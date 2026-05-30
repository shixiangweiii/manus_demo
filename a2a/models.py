"""
A2A prototype models (v18.4) - Agent Card + task request/response envelope.
A2A 原型模型（v18.4）—— Agent Card 能力广播 + 任务请求/响应信封。

A local-trusted, MCP-transported subset inspired by the Google A2A spec.
No open-network discovery, no auth beyond "local". Just enough to demonstrate
capability advertisement (AgentCard) and a standardized task envelope.
本地可信、走 MCP 传输的精简子集（参考 Google A2A 规范）；不做开放网络发现/鉴权。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

A2A_PROTOCOL = "a2a-prototype/0.1"


class AgentSkill(BaseModel):
    """A single advertised capability. / 单条能力广播。"""
    name: str
    description: str = ""


class AgentCard(BaseModel):
    """Capability advertisement for a (local, trusted) remote agent.
    远端（本地可信）agent 的能力广播卡片。"""
    name: str
    description: str = ""
    version: str = ""
    protocol: str = A2A_PROTOCOL
    auth: str = "local"
    skills: list[AgentSkill] = Field(default_factory=list)
    endpoint: dict[str, Any] = Field(default_factory=dict)  # transport/url/command


class A2ATaskRequest(BaseModel):
    """A task delegated to a remote agent. / 委派给远端 agent 的任务。"""
    task_id: str = ""
    input: str = ""
    context: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ATaskResponse(BaseModel):
    """The remote agent's response envelope. / 远端 agent 的响应信封。"""
    task_id: str = ""
    status: str = "failed"   # "completed" | "failed"
    output: str = ""
    error: str = ""
