"""Runtime-owned services shared by engine instances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from context.manager import ContextManager
from core.events import EventBus
from core.settings import AppSettings
from llm.client import LLMClient
from tools.registry import ToolRegistry


@dataclass
class RuntimeContext:
    settings: AppSettings
    llm_client: LLMClient
    tools: ToolRegistry
    events: EventBus
    context_manager: ContextManager
    interactive: bool = False
    resettable_capabilities: list[Any] = field(default_factory=list)
    agentic_memory_service: Any | None = None
    checkpoint_store: Any | None = None
    experience_learner: Any | None = None
    skill_distiller: Any | None = None
    skill_activation: Any | None = None
    guardrail: Any | None = None
    tracing_bridge: Any | None = None
