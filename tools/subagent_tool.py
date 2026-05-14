"""
SubAgentTool - Meta-tool that spawns SubAgents for complex subtasks.
子智能体工具 —— 为复杂子任务派生子智能体的元工具。

This tool allows any tool-using agent (ExecutorAgent, EmergentPlannerAgent,
GoalDrivenPlannerAgent) to delegate complex subtasks to an isolated SubAgent
via the standard ReAct tool-calling interface.

Anti-pattern defenses:
- #3 depth=1: SubAgent tool list never includes "subagent" — structural enforcement
- #4 dual-write: SubAgent sandbox directory isolation
- #6 Summary Loss: Returns structured SubAgentSummary JSON, not free text
- #8 Token Explosion: Call count limit + per-call token budget
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable

import config
from context.manager import ContextManager
from llm.client import LLMClient
from schema import SubAgentResult, SubAgentStatus
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class SubAgentTool(BaseTool):
    """
    Meta-tool that spawns a SubAgent for complex subtasks.
    When the LLM calls this tool, it creates an isolated SubAgent
    with a restricted tool set, runs its ReAct loop, and returns
    only a structured summary of the results.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        available_tools: dict[str, BaseTool],
        context_manager: ContextManager | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        max_subagent_iterations: int | None = None,
        subagent_timeout: int | None = None,
        max_calls_per_task: int | None = None,
        max_tokens_per_call: int | None = None,
        parent_name: str = "OrchestratorAgent",  # TODO: should be determined at runtime (actual LLM caller is ExecutorAgent/EmergentPlannerAgent/GoalDrivenPlannerAgent)
    ):
        self._llm_client = llm_client
        self._available_tools = available_tools
        self._context_manager = context_manager or ContextManager()
        self._on_event = on_event or (lambda *_: None)
        self._max_iterations = max_subagent_iterations or config.SUBAGENT_MAX_ITERATIONS
        self._timeout = subagent_timeout or config.SUBAGENT_TIMEOUT
        self._max_calls = max_calls_per_task or config.SUBAGENT_MAX_CALLS_PER_TASK
        self._max_tokens = max_tokens_per_call or config.SUBAGENT_MAX_TOKENS_PER_CALL
        self._parent_name = parent_name
        self._subagent_counter = 0
        self._call_count = 0

    @property
    def name(self) -> str:
        return "subagent"

    @property
    def description(self) -> str:
        return (
            "Spawn a sub-agent to handle a complex subtask independently. "
            "The sub-agent has its own context and can use a specified subset of tools. "
            "Use this for tasks that benefit from focused, isolated execution "
            "(e.g., searching a codebase, performing multi-step analysis). "
            "Returns a structured JSON summary of what the sub-agent accomplished."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        available_names = [n for n in self._available_tools.keys() if n != "subagent"]
        # Support configurable default whitelist
        default_hint = "all available tools"
        default_whitelist = getattr(config, 'SUBAGENT_DEFAULT_TOOL_WHITELIST', '')
        if default_whitelist:
            default_hint = f"defaults to: {default_whitelist}"
        return {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "A clear description of the subtask for the sub-agent to execute",
                },
                "tool_whitelist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of tool names the sub-agent is allowed to use "
                        f"(available: {', '.join(available_names)}). "
                        f"If omitted, {default_hint} are permitted. "
                        "Prefer specifying a minimal subset for safety."
                    ),
                },
            },
            "required": ["task_description"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """
        Spawn a SubAgent, run its ReAct loop, and return a structured summary.
        派生子智能体，运行其 ReAct 循环，返回结构化摘要。
        """
        # Anti-pattern #3/8: Call count limit
        if self._call_count >= self._max_calls:
            self._on_event("subagent_limit_exceeded", {
                "call_count": self._call_count,
                "max_calls": self._max_calls,
            })
            return f"Error: SubAgent call limit reached ({self._max_calls} per task). Please continue without spawning more sub-agents."

        task_description = kwargs.get("task_description", "")
        if not task_description:
            return "Error: task_description is required for subagent tool."

        tool_whitelist = kwargs.get("tool_whitelist", [])

        # Validate and filter tool whitelist — always exclude "subagent" (depth=1)
        validated_whitelist = []
        for name in tool_whitelist:
            if name == "subagent":
                continue  # Structural depth=1 enforcement
            if name in self._available_tools:
                validated_whitelist.append(name)
            else:
                logger.warning("[SubAgentTool] Ignoring invalid tool name in whitelist: %s", name)

        # If whitelist is empty, use config default or fall back to all available tools
        if not validated_whitelist:
            default_whitelist = getattr(config, 'SUBAGENT_DEFAULT_TOOL_WHITELIST', '')
            if default_whitelist:
                for name in default_whitelist.split(","):
                    name = name.strip()
                    if name and name != "subagent" and name in self._available_tools:
                        validated_whitelist.append(name)
            if not validated_whitelist:
                validated_whitelist = [
                    name for name in self._available_tools.keys()
                    if name != "subagent"
                ]

        # Build restricted tool list
        restricted_tools = [
            self._available_tools[name]
            for name in validated_whitelist
            if name in self._available_tools
        ]

        # Generate unique SubAgent name
        self._subagent_counter += 1
        subagent_name = f"SubAgent-{self._subagent_counter}"

        # Create isolated sandbox directory (anti-pattern #4)
        sandbox_subdir = ""
        try:
            sandbox_base = config.SANDBOX_DIR
            sandbox_subdir = os.path.join(sandbox_base, f"subagent_{self._subagent_counter}")
            os.makedirs(sandbox_subdir, exist_ok=True)
        except OSError:
            logger.debug("[SubAgentTool] Failed to create sandbox subdir, continuing without isolation")

        # Create and run SubAgent
        try:
            from agents.subagent import SubAgent

            subagent = SubAgent(
                name=subagent_name,
                task_description=task_description,
                llm_client=self._llm_client,
                tools=restricted_tools,
                context_manager=self._context_manager,
                max_iterations=self._max_iterations,
                timeout=self._timeout,
                max_tokens=self._max_tokens,
                on_event=self._on_event,
                parent_agent_name=self._parent_name,
                sandbox_subdir=sandbox_subdir,
            )

            result: SubAgentResult = await subagent.run(context="")
            self._call_count += 1

            # Return structured summary as JSON string (anti-pattern #6)
            return result.summary_text

        except asyncio.TimeoutError:
            # Outer timeout fallback — normally handled by SubAgent.run() internally
            # 外层超时兜底 — 正常由 SubAgent.run() 内部处理
            self._call_count += 1
            error_summary = {
                "accomplished": "",
                "findings": "",
                "issues": f"SubAgent timed out after {self._timeout}s",
                "artifacts": [],
                "tool_calls_summary": "",
            }
            return json.dumps(error_summary, ensure_ascii=False)

        except Exception as exc:
            self._call_count += 1
            logger.error("[SubAgentTool] SubAgent execution failed: %s", exc, exc_info=True)
            error_summary = {
                "accomplished": "",
                "findings": "",
                "issues": f"SubAgent error: {str(exc)[:300]}",
                "artifacts": [],
                "tool_calls_summary": "",
            }
            return json.dumps(error_summary, ensure_ascii=False)

    def reset_task_state(self) -> None:
        """Reset per-task state for a new task (called by OrchestratorAgent.run())."""
        self._call_count = 0
        self._subagent_counter = 0
