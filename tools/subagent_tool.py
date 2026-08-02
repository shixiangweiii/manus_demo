"""Ordinary tool that delegates one bounded subtask to a child AgentLoop."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from copy import copy
from typing import Any, Callable

from agent_loop.loop import AgentLoop
from agents.prompt_utils import PromptCapabilities, build_system_prompt
from context.manager import ContextManager
from core.models import Effort, EngineStats, EngineStopReason
from llm.client import LLMClient
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_BLOCKED_CHILD_TOOLS = {
    "subagent",
    "ask_user",
    "memory_store",
    "memory_consolidate",
    "memory_revoke",
}

_SUBAGENT_SYSTEM_PROMPT = """\
You are a focused child agent responsible for one bounded subtask.

Use the available tools when needed, stay within the assigned scope, and return
your result directly as a clear final answer. Do not ask the user questions and
do not attempt to delegate to another agent. If the task cannot be completed,
start the final answer with `BLOCKED:` followed by the concrete blocker.
"""


class SubAgentTool(BaseTool):
    """Run an isolated depth-one AgentLoop and return its final text."""

    def __init__(
        self,
        llm_client: LLMClient,
        available_tools: dict[str, BaseTool],
        context_manager: ContextManager | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        max_subagent_iterations: int = 10,
        subagent_timeout: int = 300,
        max_calls_per_task: int = 3,
        max_tokens_per_call: int = 120000,
        max_concurrent: int = 2,
        default_tool_whitelist: str = "",
        max_task_description_length: int = 2000,
        sandbox_dir: str = "",
        parent_name: str = "AgentRuntime",
        guardrail: Any | None = None,
        temperature: float = 0.5,
        result_truncation_limit: int = 2000,
        python_command: str = "python3",
        max_reasoning_tokens: int = 10_000,
        tool_failure_threshold: int = 2,
    ) -> None:
        self._llm_client = llm_client
        self._available_tools = dict(available_tools)
        self._context_max_tokens = (
            context_manager.max_tokens if context_manager is not None else 16000
        )
        self._on_event = on_event
        self._max_turns = max_subagent_iterations
        self._timeout = subagent_timeout
        self._max_calls = max_calls_per_task
        self._max_tokens = max_tokens_per_call
        self._default_whitelist = default_tool_whitelist
        self._max_task_length = max_task_description_length
        self._sandbox_dir = sandbox_dir
        self._parent_name = parent_name
        self._guardrail = guardrail
        self._temperature = temperature
        self._result_truncation_limit = result_truncation_limit
        self._python_command = python_command
        self._max_reasoning_tokens = max_reasoning_tokens
        self._tool_failure_threshold = tool_failure_threshold
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._call_count = 0
        self._subagent_counter = 0
        self._child_sandboxes: set[str] = set()
        self.aggregate_stats = EngineStats()

    @property
    def name(self) -> str:
        return "subagent"

    @property
    def description(self) -> str:
        return (
            "Run a complex, self-contained subtask in an isolated child agent. "
            "The child has its own conversation and a restricted tool set; its final "
            "answer is returned as this tool result."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        names = sorted(
            name
            for name in self._available_tools
            if name not in _BLOCKED_CHILD_TOOLS
        )
        return {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "A complete, bounded description of the subtask.",
                },
                "tool_whitelist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional tool names allowed for the child. Available: "
                        + ", ".join(names)
                    ),
                },
            },
            "required": ["task_description"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        local_parent = self._parent_name
        if self._call_count >= self._max_calls:
            return f"Error: SubAgent call limit reached ({self._max_calls} per task)."

        task = str(kwargs.get("task_description", "")).strip()
        if not task:
            return "Error: task_description is required for subagent tool."
        if len(task) > self._max_task_length:
            task = task[: self._max_task_length] + "\n\n[Task description truncated]"

        self._call_count += 1
        self._subagent_counter += 1
        local_id = self._subagent_counter
        name = f"SubAgent-{local_id}"

        def on_child_event(event: str, payload: Any = None) -> None:
            data = dict(payload) if isinstance(payload, dict) else {"data": payload}
            data.setdefault("subagent_id", name)
            data.setdefault("parent_agent", local_parent)
            # A child owns a private Todo list. Namespace its full snapshots so
            # consumers never replace the root AgentLoop Todo with child state.
            forwarded_event = (
                "subagent_todo_updated" if event == "todo_updated" else event
            )
            self._emit(forwarded_event, data)
            if event == "agent_turn_completed":
                self._emit(
                    "subagent_iteration",
                    {
                        "subagent_id": name,
                        "parent_agent": local_parent,
                        "iteration": data.get("turn", 0),
                        "tool_calls_count": data.get("tool_calls", 0),
                    },
                )

        sandbox = await self._prepare_sandbox(local_id)
        tools, resolved_names = self._resolve_tools(
            kwargs.get("tool_whitelist"),
            sandbox=sandbox,
            on_event=on_child_event,
        )
        activation = next(
            (tool for tool in tools if tool.name == "activate_skill"),
            None,
        )
        prompt_capabilities = PromptCapabilities.from_tools(
            tools,
            python_command=self._python_command,
            skill_descriptions=getattr(activation, "skill_descriptions", ""),
            skills_max_activations=getattr(activation, "max_activations", 1),
        )
        system_prompt = build_system_prompt(
            _SUBAGENT_SYSTEM_PROMPT,
            inject_context=True,
            inject_subagent_guidance=False,
            inject_hitl_guidance=False,
            capabilities=prompt_capabilities,
        )
        if sandbox:
            system_prompt += (
                f"\nYour isolated working directory is {sandbox}. "
                "Keep file operations inside it."
            )

        self._emit(
            "subagent_start",
            {
                "subagent_id": name,
                "parent_agent": local_parent,
                "task_description": task,
                "tool_whitelist": resolved_names,
            },
        )
        loop = AgentLoop(
            llm_client=self._llm_client,
            tools=tools,
            max_turns=self._max_turns,
            timeout_seconds=self._timeout,
            context_manager=ContextManager(
                max_tokens=self._context_max_tokens,
                summarize_with_llm=False,
            ),
            agent_name=name,
            guardrail=self._guardrail,
            temperature=self._temperature,
            result_truncation_limit=self._result_truncation_limit,
            on_event=on_child_event,
            max_total_tokens=self._max_tokens,
            max_reasoning_tokens=self._max_reasoning_tokens,
            tool_failure_threshold=self._tool_failure_threshold,
        )
        if activation is not None:
            activation.set_tool_filter_callback(loop.set_allowed_tools)

        try:
            async with asyncio.timeout(self._timeout):
                async with self._semaphore:
                    result = await loop.run(
                        task,
                        system_prompt=system_prompt,
                        effort=Effort.HIGH,
                    )
        except TimeoutError:
            self._accumulate_stats(loop.stats_snapshot)
            on_child_event(
                "agent_loop_completed",
                {
                    "success": False,
                    "stop_reason": EngineStopReason.TIMEOUT.value,
                    "turns": loop.turns,
                    "stats": loop.stats_snapshot.model_dump(),
                },
            )
            payload = {
                "subagent_id": name,
                "parent_agent": local_parent,
                "timeout": self._timeout,
            }
            self._emit("subagent_timed_out", payload)
            return f"Error: SubAgent timed out after {self._timeout:g} seconds."
        except asyncio.CancelledError:
            self._accumulate_stats(loop.stats_snapshot)
            on_child_event(
                "agent_loop_completed",
                {
                    "success": False,
                    "stop_reason": "cancelled",
                    "turns": loop.turns,
                    "stats": loop.stats_snapshot.model_dump(),
                },
            )
            self._emit(
                "subagent_cancelled",
                {
                    "subagent_id": name,
                    "parent_agent": local_parent,
                    "error": "parent task cancelled",
                },
            )
            raise
        except Exception as exc:
            self._accumulate_stats(loop.stats_snapshot)
            on_child_event(
                "agent_loop_completed",
                {
                    "success": False,
                    "stop_reason": EngineStopReason.ENGINE_ERROR.value,
                    "turns": loop.turns,
                    "stats": loop.stats_snapshot.model_dump(),
                },
            )
            logger.error("[SubAgentTool] child execution failed", exc_info=True)
            self._emit(
                "subagent_failed",
                {
                    "subagent_id": name,
                    "parent_agent": local_parent,
                    "error": str(exc),
                },
            )
            return f"Error: SubAgent failed: {type(exc).__name__}: {exc}"

        total_tokens = loop.tokens_used
        self._accumulate_stats(result.stats)
        payload = {
            "subagent_id": name,
            "parent_agent": local_parent,
            "success": result.success,
            "stop_reason": result.stop_reason.value,
            "turns": result.turns,
            "tool_calls": result.stats.tool_calls,
            "tokens": total_tokens,
        }
        if total_tokens > self._max_tokens:
            payload["success"] = False
            payload["error"] = "token budget exceeded"
            self._emit("subagent_failed", payload)
            return (
                f"Error: SubAgent token budget exceeded "
                f"({total_tokens} > {self._max_tokens})."
            )
        if not result.success:
            payload["error"] = result.output[:300]
            if result.stop_reason == EngineStopReason.TIMEOUT:
                payload["timeout"] = self._timeout
                self._emit("subagent_timed_out", payload)
            else:
                self._emit("subagent_failed", payload)
            return f"Error: SubAgent {result.stop_reason.value}: {result.output}"

        if result.output.lstrip().upper().startswith("BLOCKED:"):
            payload["success"] = False
            payload["error"] = result.output[:300]
            self._emit("subagent_failed", payload)
            return f"Error: SubAgent blocked: {result.output.strip()[8:].strip()}"

        self._emit("subagent_complete", payload)
        return result.output

    def _resolve_tools(
        self,
        requested: Any,
        *,
        sandbox: str,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> tuple[list[BaseTool], list[str]]:
        requested_names = (
            [str(name).strip() for name in requested if str(name).strip()]
            if isinstance(requested, list)
            else []
        )
        if not requested_names and self._default_whitelist:
            requested_names = [
                name.strip()
                for name in self._default_whitelist.split(",")
                if name.strip()
            ]
        if not requested_names:
            requested_names = list(self._available_tools)

        names = list(dict.fromkeys(
            name
            for name in requested_names
            if name not in _BLOCKED_CHILD_TOOLS and name in self._available_tools
        ))
        tools: list[BaseTool] = []
        resolved_names: list[str] = []
        for name in names:
            tool = self._available_tools[name]
            if name == "activate_skill":
                clone = getattr(tool, "clone", None)
                if callable(clone):
                    tool = clone(on_event=on_event)
            elif on_event is not None and hasattr(tool, "_on_event"):
                # Tool instances belong to the parent runtime. A shallow copy
                # preserves their service/client dependencies while making the
                # mutable event callback child-local and concurrency-safe.
                tool = copy(tool)
                setattr(tool, "_on_event", on_event)
            bind_sandbox = getattr(tool, "for_sandbox", None)
            if callable(bind_sandbox):
                # Never fall back to a parent-rooted filesystem/subprocess tool
                # when the child sandbox could not be created.
                if not sandbox:
                    continue
                tool = bind_sandbox(sandbox)
            tools.append(tool)
            resolved_names.append(name)
        return tools, resolved_names

    async def _prepare_sandbox(self, child_id: int) -> str:
        if not self._sandbox_dir:
            return ""
        try:
            root = os.path.realpath(os.path.expanduser(self._sandbox_dir))
            await asyncio.to_thread(os.makedirs, root, exist_ok=True)
            path = await asyncio.to_thread(
                tempfile.mkdtemp,
                prefix=f"subagent_{child_id}_",
                dir=root,
            )
            self._child_sandboxes.add(path)
            return path
        except OSError:
            logger.debug("[SubAgentTool] could not create child sandbox", exc_info=True)
            return ""

    def _accumulate_stats(self, stats: EngineStats) -> None:
        self.aggregate_stats.llm_calls += stats.llm_calls
        self.aggregate_stats.agent_turns += stats.agent_turns
        self.aggregate_stats.context_compaction_calls += (
            stats.context_compaction_calls
        )
        self.aggregate_stats.prompt_tokens += stats.prompt_tokens
        self.aggregate_stats.completion_tokens += stats.completion_tokens
        self.aggregate_stats.total_tokens += stats.total_tokens
        self.aggregate_stats.tool_calls += stats.tool_calls
        self.aggregate_stats.reasoning_tokens += stats.reasoning_tokens
        self.aggregate_stats.subagent_calls += 1

    def reset_task_state(self) -> None:
        self._call_count = 0
        self._subagent_counter = 0
        self.aggregate_stats = EngineStats()
        for target in tuple(self._child_sandboxes):
            shutil.rmtree(target, ignore_errors=True)
        self._child_sandboxes.clear()

    def set_caller(self, name: str) -> None:
        if name:
            self._parent_name = name

    def _emit(self, name: str, payload: Any) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(name, payload)
        except Exception:
            logger.debug("[SubAgentTool] event callback failed", exc_info=True)
