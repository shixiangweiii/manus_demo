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
        parent_name: str = "OrchestratorAgent",  # Default; ReActEngine overrides via set_caller() per-call
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
        # Wave B #5: limit concurrent SubAgent runs (was declared in config but never enforced)
        # 实施 SUBAGENT_MAX_CONCURRENT 信号量限流（v9 配置存在但未启用）
        self._semaphore = asyncio.Semaphore(config.SUBAGENT_MAX_CONCURRENT)

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
        # Wave-1 N5: capture _parent_name into a local immediately, before any
        # await. Wave-4 M4 introduced exactly such an await later in this
        # function (`await asyncio.to_thread(os.makedirs, ...)`), so this
        # local snapshot is now actively load-bearing rather than purely
        # defensive: while we await on makedirs, another task on a shared
        # SubAgentTool instance could call set_caller(...) and overwrite
        # self._parent_name. local_parent is captured BEFORE the increment +
        # await, so the SubAgent we eventually construct is attributed to
        # the agent that actually invoked us, not to whoever happened to
        # write self._parent_name last.
        # 立即拷贝到局部,Wave-4 引入的 makedirs await 期间不被并发 set_caller 覆盖。
        local_parent = self._parent_name

        # Anti-pattern #3/8: Call count limit
        if self._call_count >= self._max_calls:
            logger.warning("[SubAgentTool] Call limit reached: %d/%d, rejecting task",
                           self._call_count, self._max_calls)
            self._on_event("subagent_limit_exceeded", {
                "call_count": self._call_count,
                "max_calls": self._max_calls,
            })
            return f"Error: SubAgent call limit reached ({self._max_calls} per task). Please continue without spawning more sub-agents."

        task_description = kwargs.get("task_description", "")
        if not task_description:
            return "Error: task_description is required for subagent tool."

        # Wave-4 L2: bound task_description length so an over-eager parent LLM
        # cannot pass tens of thousands of characters and immediately blow up
        # the SubAgent's own context window. Truncate + log a warning rather
        # than rejecting outright — partial work is better than a hard fail.
        # 防止父 LLM 传超长任务描述把子 SubAgent 上下文撑满,直接截断 + 警告。
        max_desc = getattr(config, "SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH", 2000)
        if len(task_description) > max_desc:
            logger.warning(
                "[SubAgentTool] task_description length %d exceeds limit %d; truncating",
                len(task_description), max_desc,
            )
            task_description = task_description[:max_desc] + "\n\n[Description truncated due to SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH]"

        # Wave B #4: reserve budget atomically BEFORE any await.
        # In single-threaded asyncio, the (check + reserve) above is race-free
        # as long as no `await` sits between them. Failures DO NOT refund the
        # slot — repeated SubAgent crashes must not bypass the budget.
        # 检查与预扣在同一同步段内完成；失败不退款，避免崩溃→重试无限循环。
        self._call_count += 1

        logger.info("[SubAgentTool] Spawning SubAgent (call #%d/%d) for task: '%s'",
                    self._call_count, self._max_calls, task_description[:100])

        tool_whitelist = kwargs.get("tool_whitelist", [])

        # Validate and filter tool whitelist — always exclude "subagent" (depth=1) and "ask_user" (HITL isolation)
        validated_whitelist = []
        for name in tool_whitelist:
            if name in ("subagent", "ask_user"):
                continue  # Structural depth=1 enforcement + HITL isolation
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
                    if name and name not in ("subagent", "ask_user") and name in self._available_tools:
                        validated_whitelist.append(name)
            if not validated_whitelist:
                validated_whitelist = [
                    name for name in self._available_tools.keys()
                    if name not in ("subagent", "ask_user")
                ]

        # Build restricted tool list
        restricted_tools = [
            self._available_tools[name]
            for name in validated_whitelist
            if name in self._available_tools
        ]

        logger.debug("[SubAgentTool] Resolved whitelist: requested=%s, final=%s",
                     tool_whitelist if tool_whitelist else "(empty→default)",
                     validated_whitelist)

        # Generate unique SubAgent name
        self._subagent_counter += 1
        subagent_name = f"SubAgent-{self._subagent_counter}"

        # Create isolated sandbox directory (anti-pattern #4)
        # Wave-4 M4: makedirs runs in a thread to avoid blocking the asyncio
        # event loop on slow disks (NFS / network mounts). makedirs itself is
        # microseconds on local SSDs but can be 100ms+ on hostile mounts —
        # blocking would freeze concurrent DAG nodes / SubAgents.
        # makedirs 改异步,避免慢盘上阻塞事件循环冻结其它并行 Task。
        sandbox_subdir = ""
        try:
            sandbox_base = config.SANDBOX_DIR
            sandbox_subdir = os.path.join(sandbox_base, f"subagent_{self._subagent_counter}")
            await asyncio.to_thread(os.makedirs, sandbox_subdir, exist_ok=True)
            logger.debug("[SubAgentTool] Sandbox created: %s", sandbox_subdir)
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
                parent_agent_name=local_parent,
                sandbox_subdir=sandbox_subdir,
            )

            # Wave B #5: only the expensive SubAgent.run() is gated by Semaphore;
            # whitelist validation / sandbox creation above already ran in parallel.
            # 信号量只 wrap 真正昂贵的 ReAct 循环；快路径不挤占并发槽。
            async with self._semaphore:
                result: SubAgentResult = await subagent.run(context="")

            logger.info("[SubAgentTool] SubAgent-%d completed: status=%s, iterations=%d, tokens=%d, duration=%.0fms, artifacts=%s",
                        self._subagent_counter, result.status.value, result.iterations_used,
                        result.tokens_used, result.duration_ms, result.summary.artifacts)
            logger.debug("[SubAgentTool] SubAgent-%d summary: accomplished='%s', issues='%s'",
                        self._subagent_counter,
                        result.summary.accomplished[:200],
                        result.summary.issues[:200])

            # Return structured summary as JSON string (anti-pattern #6)
            return result.summary_text

        # Wave-4 M5: removed dead `except asyncio.TimeoutError` branch — there
        # is no `wait_for` at this layer (SubAgent.run() owns the timeout) so
        # TimeoutError can never reach here. CancelledError IS possible though
        # (parent task being cancelled mid-await), so we honor it explicitly
        # by re-raising without producing a misleading "SubAgent error: ...".
        # 删掉死的 TimeoutError except;CancelledError 保留 re-raise 不吞。
        except asyncio.CancelledError:
            logger.warning("[SubAgentTool] SubAgent-%d cancelled by parent task",
                           self._subagent_counter)
            raise

        except Exception as exc:
            # Wave B #4: budget already reserved at top — no refund on failure.
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
        """Reset per-task state for a new task (called by OrchestratorAgent.run()).

        Wave-4 M3: also clean up `subagent_*` sandbox subdirectories from the
        previous task. Without this, files written by SubAgent-1 in task A
        would still be visible to a freshly-numbered SubAgent-1 in task B
        (since the counter resets to 1 each task). Previous-task leftovers
        would cause "ghost context" — the SubAgent sees files it didn't
        write. Only directories matching the `subagent_<digits>` pattern
        under SANDBOX_DIR are removed; the SANDBOX_DIR root itself and any
        non-SubAgent-owned files are untouched.
        清理上一任务的 subagent_N 子目录,避免新任务的 SubAgent 看到旧任务遗留文件。
        """
        import re
        import shutil

        logger.debug("[SubAgentTool] Resetting task state: call_count=%d→0, subagent_counter=%d→0",
                     self._call_count, self._subagent_counter)
        self._call_count = 0
        self._subagent_counter = 0

        # M3: clean up previous task's SubAgent sandbox subdirs
        try:
            sandbox_base = config.SANDBOX_DIR
            if os.path.isdir(sandbox_base):
                pattern = re.compile(r"^subagent_\d+$")
                for entry in os.listdir(sandbox_base):
                    if pattern.match(entry):
                        target = os.path.join(sandbox_base, entry)
                        if os.path.isdir(target):
                            shutil.rmtree(target, ignore_errors=True)
                            logger.debug("[SubAgentTool] Removed stale sandbox subdir: %s", target)
        except OSError:
            logger.debug("[SubAgentTool] Sandbox cleanup encountered OSError (non-fatal)", exc_info=True)

    def set_caller(self, name: str) -> None:
        """Wave C #7: ReActEngine calls this immediately before traced_execute()
        to inject the actual caller agent's name. asyncio single-threaded model
        guarantees no other task interleaves between set_caller and the
        synchronous prologue of execute() that captures self._parent_name into
        a local variable for the SubAgent constructor.

        ReActEngine 在每次工具调用前同步注入实际 caller 的名称，
        替换硬编码的 'OrchestratorAgent'，让 tracing/eval 准确归因。
        """
        if name:
            self._parent_name = name
