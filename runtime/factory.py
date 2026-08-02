"""Application composition without engine-specific branching in hosts."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from context.manager import ContextManager
from core.events import EventBus
from core.redaction import redact_text
from core.settings import AppSettings, get_settings, validate_settings
from llm.client import LLMClient
from openai import OpenAIError
from runtime.context import RuntimeContext
from tools.registry import build_default_tools

logger = logging.getLogger(__name__)


class RuntimeInitializationError(RuntimeError):
    """A configured runtime dependency could not be initialized."""

    def __init__(self, component: str, cause: BaseException) -> None:
        self.component = component
        self.cause = cause
        super().__init__(
            f"Could not initialize runtime component '{component}': "
            f"{type(cause).__name__}: {redact_text(cause)}"
        )


async def _register_capability_tools(context: RuntimeContext) -> None:
    settings = context.settings
    caps = settings.capabilities
    registry = context.tools
    event_callback = context.events.legacy_callback
    if caps.agentbay:
        from tools.agentbay import AgentBayBrowserTool, AgentBayCodeTool

        agentbay_semaphore = asyncio.Semaphore(max(1, caps.agentbay_max_concurrent))
        if caps.agentbay_code_tool:
            registry.register(AgentBayCodeTool(caps, agentbay_semaphore))
        if caps.agentbay_browser_tool:
            registry.register(
                AgentBayBrowserTool(
                    caps,
                    settings.paths.sandbox_dir,
                    agentbay_semaphore,
                )
            )

    if caps.mcp_bridge:
        from tools.mcp.discovery import discover_mcp_bridge_tools

        for tool in await discover_mcp_bridge_tools(
            on_event=event_callback,
            settings=caps,
        ):
            registry.register(tool)

    if caps.agentic_memory:
        from memory.agentic_store import AgenticMemoryStore
        from memory.service import AgenticMemoryService

        service = AgenticMemoryService(
            AgenticMemoryStore(settings.paths.state_dir),
            llm_client=context.llm_client,
        )
        service._store.migrate_from_legacy(settings.paths.state_dir)
        context.agentic_memory_service = service
        if caps.memory_tools:
            from tools.memory_tools import (
                MemoryConsolidateTool,
                MemoryRevokeTool,
                MemorySearchTool,
                MemoryStoreTool,
            )

            for tool in (
                MemorySearchTool(service, on_event=event_callback),
                MemoryStoreTool(service, on_event=event_callback),
                MemoryConsolidateTool(service, on_event=event_callback),
                MemoryRevokeTool(service, on_event=event_callback),
            ):
                registry.register(tool)

    if caps.skills:
        from skills.activation import SkillActivationTool
        from skills.loader import SkillLoader
        from skills.registry import SkillRegistry

        skill_dirs = [str(Path(__file__).resolve().parent.parent / ".agents" / "skills")]
        if caps.skills_user_dir:
            skill_dirs.append(caps.skills_user_dir)
        skill_dirs.extend(path.strip() for path in caps.skills_dirs.split(",") if path.strip())
        skill_registry = SkillRegistry()
        for skill in SkillLoader.discover(skill_dirs):
            skill_registry.register(skill)
        activation = SkillActivationTool(
            registry=skill_registry,
            on_event=event_callback,
            max_activations=caps.skills_max_activations,
            max_content_tokens=caps.skills_max_content_tokens,
            guardrail=context.guardrail,
        )
        registry.register(activation)
        context.resettable_capabilities.append(activation)
        context.skill_activation = activation

    hitl_active = caps.hitl and context.interactive
    if hitl_active:
        from tools.ask_user import AskUserTool

        def on_user_prompt(question: str, prompt_id: str, response_future: Any) -> None:
            context.events.emit(
                "ask_user_prompt",
                {
                    "question": question,
                    "prompt_id": prompt_id,
                    "response_future": response_future,
                    "timeout_seconds": caps.hitl_timeout_seconds,
                },
            )

        ask_user = AskUserTool(
            on_user_prompt=on_user_prompt,
            on_event=event_callback,
            max_prompts_per_task=caps.hitl_max_prompts,
            timeout=caps.hitl_timeout_seconds,
        )
        registry.register(ask_user)
        context.resettable_capabilities.append(ask_user)

    base_tools = registry.as_dict()
    if caps.subagent:
        from tools.subagent_tool import SubAgentTool

        subagent = SubAgentTool(
            llm_client=context.llm_client,
            available_tools=base_tools,
            context_manager=context.context_manager,
            on_event=event_callback,
            max_subagent_iterations=caps.subagent_max_iterations,
            subagent_timeout=caps.subagent_timeout_seconds,
            max_calls_per_task=caps.subagent_max_calls,
            max_tokens_per_call=caps.subagent_max_tokens,
            max_concurrent=caps.subagent_max_concurrent,
            default_tool_whitelist=caps.subagent_tool_whitelist,
            max_task_description_length=caps.subagent_task_max_length,
            sandbox_dir=settings.paths.sandbox_dir,
            parent_name="AgentRuntime",
            guardrail=context.guardrail,
            temperature=settings.execution.tool_calling_temperature,
            result_truncation_limit=settings.tools.result_truncation_limit,
            python_command=settings.tools.python_command,
            max_reasoning_tokens=settings.execution.max_reasoning_tokens,
        )
        registry.register(subagent)
        context.resettable_capabilities.append(subagent)


async def build_runtime(
    settings: AppSettings | None = None,
    events: EventBus | None = None,
    *,
    interactive: bool = False,
) -> "Any":
    from runtime.app import AgentRuntime

    settings = settings or get_settings()
    validate_settings(settings)
    events = events or EventBus()
    llm_client: LLMClient | None = None
    component = "llm"
    try:
        llm_client = LLMClient.from_settings(settings)
        component = "core services"
        context_manager = ContextManager(max_tokens=settings.execution.max_context_tokens)
        context = RuntimeContext(
            settings=settings,
            llm_client=llm_client,
            tools=build_default_tools(settings, events),
            events=events,
            context_manager=context_manager,
            interactive=interactive,
        )
        if settings.tracing.enabled:
            component = "tracing"
            from tracing import TracingBridge, init_tracing

            init_tracing(settings.tracing)
            context.tracing_bridge = TracingBridge(settings.tracing)
            events.subscribe(context.tracing_bridge.on_runtime_event)
        if settings.capabilities.guardrails:
            component = "guardrails"
            from guardrails.engine import GuardrailEngine

            context.guardrail = GuardrailEngine(
                settings.capabilities,
                on_event=events.legacy_callback,
                sandbox_dir=settings.paths.sandbox_dir,
                shell_mode=settings.tools.shell_mode,
            )
        if settings.capabilities.checkpoint:
            component = "checkpoint"
            from checkpoint.store import RuntimeCheckpointStore

            context.checkpoint_store = RuntimeCheckpointStore(
                settings.paths.checkpoint_dir
            )
        component = "capabilities"
        await _register_capability_tools(context)

        if settings.capabilities.self_evolution:
            component = "self evolution"
            if context.agentic_memory_service is None:
                logger.warning("Self-evolution requires capabilities.agentic_memory=true")
            else:
                from evolution.learner import ExperienceLearner

                context.experience_learner = ExperienceLearner(
                    llm_client=context.llm_client,
                    memory_service=context.agentic_memory_service,
                    settings=settings.capabilities,
                    on_event=events.legacy_callback,
                )
                if settings.capabilities.skill_auto_distill:
                    from evolution.skill_distiller import SkillAutoDistiller

                    context.skill_distiller = SkillAutoDistiller(
                        llm_client=context.llm_client,
                        memory_service=context.agentic_memory_service,
                        settings=settings.capabilities,
                        on_event=events.legacy_callback,
                    )
        return AgentRuntime(context)
    except (ImportError, OSError, ConnectionError, TimeoutError, OpenAIError) as exc:
        if llm_client is not None:
            await llm_client.aclose()
        raise RuntimeInitializationError(component, exc) from exc
    except BaseException:
        if llm_client is not None:
            await llm_client.aclose()
        raise
