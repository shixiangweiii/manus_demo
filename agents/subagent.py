"""
SubAgent - Isolated sub-agent following Claude Code Subagent pattern.
子智能体 —— 遵循 Claude Code Subagent 模式的隔离式子智能体。

Key design principles (from research doc §1.2):
- depth=1 (cannot spawn further SubAgents — enforced structurally via tool whitelist)
- Independent system prompt + independent context window
- Summary-only return (parent never sees full conversation history)
- Restricted tool subset (parent declares tool whitelist)

反模式防御:
- #2 上下文泄漏: 独立 messages，只回传结构化 summary
- #4 双写冲突: 可选工作目录隔离
- #5 Self-Critique: 结构化摘要模板强制 issues 字段
- #6 Summary Loss: SubAgentSummary 结构化 artifact + 完整 tool_calls_log 保留
- #8 Token Explosion: per-call token 预算熔断 (on_iteration callback)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import config
from agents.prompt_utils import build_system_prompt
from context.manager import ContextManager
from llm.client import LLMClient
from pydantic import ValidationError
from react.engine import ReActEngine
from schema import SubAgentResult, SubAgentStatus, SubAgentSummary, ToolCallRecord
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)


class SubAgentTokenExhausted(Exception):
    """Raised when a SubAgent exceeds its per-call token budget (anti-pattern #8)."""
    pass


SUBAGENT_SYSTEM_PROMPT = """\
You are a specialized sub-agent executing a specific subtask.
你的任务是高效完成指定的子任务。

Rules:
1. Complete the described subtask efficiently using the available tools.
   高效使用可用工具完成子任务。
2. When done, provide a clear, concise summary of what you accomplished.
   完成后，提供清晰简洁的工作成果摘要。
3. Do NOT call any more tools once the subtask is complete.
   子任务完成后不要再调用工具。
4. Focus ONLY on the specific subtask assigned to you — do not expand scope.
   只关注分配给你的子任务，不要扩大范围。
5. If you encounter an error, describe it clearly in your final answer.
   遇到错误时，在最终回答中清楚描述。
6. Do NOT read files unrelated to your task just to "gather context" — this wastes tokens.
   不要为了"收集背景"而读取无关文件。
7. If the task description is unclear, note it in your findings and return — do NOT assume missing details.
   如果任务描述不清晰，在 findings 中指出后返回，不要自行补全。
8. Do NOT call the same tool with the same arguments repeatedly — if a tool call fails, try a different approach.
   不要用相同参数重复调用同一工具，失败时换一种方式。
"""

SUMMARIZE_PROMPT = """\
Summarize your work according to this exact JSON structure:
请按以下 JSON 结构总结你的工作成果：

{
  "accomplished": "What you completed (specific, factual)",
  "findings": "Key findings and results",
  "issues": "Problems encountered or items left incomplete (be honest)",
  "artifacts": ["list of file paths you created/modified"],
  "tool_calls_summary": "Which tools you used and what each did"
}

Be honest about issues — do not omit problems. Return ONLY valid JSON.
对问题要诚实，不要遗漏。只返回合法 JSON。
"""


def _extract_artifacts_from_log(tool_calls_log: list[ToolCallRecord]) -> list[str]:
    """Extract file paths from tool calls log (no LLM needed).
    Note: shell-created files cannot be statically detected; this is best-effort.
    """
    artifacts = []
    for tc in tool_calls_log:
        if tc.tool_name == "file_ops" and tc.parameters.get("action") == "write":
            path = tc.parameters.get("filename", "")
            if path and path not in artifacts:
                artifacts.append(path)
    return artifacts


def _extract_tool_calls_summary_from_log(tool_calls_log: list[ToolCallRecord]) -> str:
    """Build a concise tool calls summary from log (no LLM needed)."""
    if not tool_calls_log:
        return ""
    parts = []
    for tc in tool_calls_log:
        args_preview = ", ".join(f"{k}={v}" for k, v in list(tc.parameters.items())[:2])
        parts.append(f"{tc.tool_name}({args_preview})")
    return "; ".join(parts)


class SubAgent:
    """
    Isolated sub-agent that executes a specific subtask with its own
    context and restricted tool set. Follows Claude Code Subagent pattern:
    depth=1, summary-only return, independent system prompt.
    隔离式子智能体，拥有独立上下文和受限工具集，遵循 Claude Code Subagent 模式。
    """

    def __init__(
        self,
        name: str,
        task_description: str,
        llm_client: LLMClient,
        tools: list[BaseTool],
        context_manager: ContextManager | None = None,
        max_iterations: int | None = None,
        timeout: int | None = None,
        max_tokens: int | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        parent_agent_name: str = "",
        sandbox_subdir: str = "",
    ):
        self.name = name
        self.task_description = task_description
        self.llm_client = llm_client
        self.timeout = timeout or config.SUBAGENT_TIMEOUT
        self.max_tokens = max_tokens or config.SUBAGENT_MAX_TOKENS_PER_CALL
        self._on_event = on_event or (lambda *_: None)
        self.parent_agent_name = parent_agent_name
        self.sandbox_subdir = sandbox_subdir

        # Build system prompt with v12 context injection (date/time) + optional sandbox isolation
        # inject_subagent_guidance=False because depth=1 — SubAgent cannot spawn further SubAgents
        # 用 build_system_prompt 注入 v12 日期/时间上下文；depth=1 故不再附加 SubAgent 引导
        system_prompt = build_system_prompt(
            SUBAGENT_SYSTEM_PROMPT,
            inject_context=True,
            inject_subagent_guidance=False,
        )
        if sandbox_subdir:
            system_prompt += f"\n9. Your working directory is {sandbox_subdir}. All file operations must be within this directory.\n   你的工作目录是 {sandbox_subdir}，所有文件操作应在此目录下进行。"

        # Build tool infrastructure (same pattern as ExecutorAgent)
        self.tools = {t.name: t for t in tools}
        tool_router = ToolRouter(available_tools=list(self.tools.keys()))

        # Internal ReAct engine with its own independent messages list
        self._react_engine = ReActEngine(
            llm_client=llm_client,
            tools=tools,
            max_iterations=max_iterations or config.SUBAGENT_MAX_ITERATIONS,
            tool_router=tool_router,
            context_manager=context_manager or ContextManager(),
            agent_name=name,  # Wave C #7: SubAgent's own name (e.g. "SubAgent-1") for tool attribution
        )

        # For summary generation
        self._system_prompt = system_prompt
        self._summary_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

        # State tracked via on_iteration callback
        self._accumulated_tool_calls: list[ToolCallRecord] = []
        self._iterations_so_far: int = 0
        self._token_exceeded: bool = False
        self._records_before: int = 0

        logger.info("[SubAgent] Created %s: tools=%s, max_iterations=%d, timeout=%ds, max_tokens=%d, sandbox='%s'",
                    self.name, list(self.tools.keys()), max_iterations or config.SUBAGENT_MAX_ITERATIONS,
                    self.timeout, self.max_tokens, sandbox_subdir or "(none)")

    def _emit(self, event: str, data: Any = None) -> None:
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[SubAgent] Event callback error for '%s'", event, exc_info=True)

    def _on_react_iteration(self, iteration: int, tool_calls: list[ToolCallRecord]) -> None:
        """ReAct iteration callback — snapshot tool calls and check token budget.

        ReAct engine passes the **cumulative** tool_calls_log (engine.py:194,285),
        not a delta. Use shallow copy to snapshot it; using extend() here would
        cause quadratic growth (iteration N produces N(N+1)/2 entries).
        """
        self._iterations_so_far = iteration
        self._accumulated_tool_calls = list(tool_calls)

        # Wave C #12: emit progress event so UI/Tracing/Eval can observe
        # SubAgent's internal ReAct iterations rather than seeing only
        # start/complete bookends.
        # 发出迭代进度事件，让观察者看到 SubAgent 内部 ReAct 进展。
        self._emit("subagent_iteration", {
            "subagent_id": self.name,
            "iteration": iteration,
            "tool_calls_count": len(tool_calls),
        })

        # Anti-pattern #8: per-call token budget check (index range method)
        records = self.llm_client.get_call_records()
        current_tokens = sum(r.total_tokens for r in records[self._records_before:])
        logger.debug("[SubAgent] Iteration %d: tokens=%d/%d, tool_calls=%d",
                     iteration, current_tokens, self.max_tokens, len(self._accumulated_tool_calls))
        if current_tokens >= self.max_tokens:
            logger.warning(
                "[SubAgent] Token budget exceeded: %d >= %d",
                current_tokens, self.max_tokens,
            )
            self._token_exceeded = True
            raise SubAgentTokenExhausted(
                f"Token budget exceeded: {current_tokens} >= {self.max_tokens}"
            )

    async def run(self, context: str = "") -> SubAgentResult:
        """
        Execute the subtask and return a structured result.
        执行子任务并返回结构化结果。

        Only the summary is returned to the parent agent;
        the full tool_calls_log is preserved for debugging.
        """
        start_time = time.perf_counter()
        self._records_before = len(self.llm_client.get_call_records())

        logger.info("[SubAgent] %s starting run: task='%s', context='%s', records_before=%d",
                    self.name, self.task_description[:100], context[:80] if context else "(empty)", self._records_before)

        # Reset per-run state
        self._accumulated_tool_calls = []
        self._iterations_so_far = 0
        self._token_exceeded = False

        tool_whitelist = list(self.tools.keys())

        self._emit("subagent_start", {
            "subagent_id": self.name,
            "task_description": self.task_description,
            "parent_agent": self.parent_agent_name,
            "tool_whitelist": tool_whitelist,
        })

        try:
            # Run ReAct loop with timeout protection + on_iteration callback
            step_result = await asyncio.wait_for(
                self._react_engine.execute(
                    prompt=self.task_description,
                    context=context,
                    node_id=self.name,
                    system_hint=self._system_prompt,
                    on_iteration=self._on_react_iteration,
                ),
                timeout=self.timeout,
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            # Token calculation via record index range (safe under shared LLMClient)
            records = self.llm_client.get_call_records()
            tokens_used = sum(r.total_tokens for r in records[self._records_before:])
            iterations_used = step_result.iterations_completed

            logger.info("[SubAgent] %s ReAct loop done: success=%s, iterations=%d, tokens=%d, duration=%.0fms",
                        self.name, step_result.success, iterations_used, tokens_used, elapsed_ms)

            if step_result.success:
                summary = await self._summarize_result(step_result)
                summary_text = summary.model_dump_json(ensure_ascii=False)

                result = SubAgentResult(
                    subagent_id=self.name,
                    task_description=self.task_description,
                    status=SubAgentStatus.COMPLETED,
                    summary=summary,
                    summary_text=summary_text,
                    tool_calls_count=len(step_result.tool_calls_log),
                    iterations_used=iterations_used,
                    duration_ms=round(elapsed_ms, 2),
                    tokens_used=tokens_used,
                    tool_calls_log=step_result.tool_calls_log,
                )

                self._emit("subagent_complete", {
                    "subagent_id": self.name,
                    "summary": summary_text,
                    "iterations_used": result.iterations_used,
                    "tool_calls_count": result.tool_calls_count,
                    "duration_ms": result.duration_ms,
                    "tokens_used": tokens_used,
                })
            else:
                # SubAgent failed internally
                fallback_summary = SubAgentSummary(
                    accomplished="",
                    findings="",
                    issues=step_result.output[:500],
                    artifacts=_extract_artifacts_from_log(step_result.tool_calls_log),
                    tool_calls_summary=_extract_tool_calls_summary_from_log(step_result.tool_calls_log) or "Sub-task execution failed",
                )
                summary_text = fallback_summary.model_dump_json(ensure_ascii=False)

                result = SubAgentResult(
                    subagent_id=self.name,
                    task_description=self.task_description,
                    status=SubAgentStatus.FAILED,
                    summary=fallback_summary,
                    summary_text=summary_text,
                    tool_calls_count=len(step_result.tool_calls_log),
                    iterations_used=iterations_used,
                    duration_ms=round(elapsed_ms, 2),
                    tokens_used=tokens_used,
                    tool_calls_log=step_result.tool_calls_log,
                )

                self._emit("subagent_failed", {
                    "subagent_id": self.name,
                    "error": step_result.output[:300],
                    "iterations_used": iterations_used,
                    "tool_calls_count": result.tool_calls_count,
                    "duration_ms": result.duration_ms,
                    "tokens_used": tokens_used,
                })

            return result

        except SubAgentTokenExhausted:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            records = self.llm_client.get_call_records()
            tokens_used = sum(r.total_tokens for r in records[self._records_before:])

            logger.warning("[SubAgent] %s token budget exceeded: %d >= %d (iterations=%d, duration=%.0fms)",
                           self.name, tokens_used, self.max_tokens, self._iterations_so_far, elapsed_ms)

            budget_summary = SubAgentSummary(
                accomplished="",
                findings="",
                issues=f"SubAgent token budget exceeded ({tokens_used} >= {self.max_tokens})",
                artifacts=_extract_artifacts_from_log(self._accumulated_tool_calls),
                tool_calls_summary=_extract_tool_calls_summary_from_log(self._accumulated_tool_calls) or "Token budget exceeded before completion",
            )
            summary_text = budget_summary.model_dump_json(ensure_ascii=False)

            result = SubAgentResult(
                subagent_id=self.name,
                task_description=self.task_description,
                status=SubAgentStatus.FAILED,
                summary=budget_summary,
                summary_text=summary_text,
                tool_calls_count=len(self._accumulated_tool_calls),
                iterations_used=self._iterations_so_far,
                duration_ms=round(elapsed_ms, 2),
                tokens_used=tokens_used,
                tool_calls_log=list(self._accumulated_tool_calls),
            )

            self._emit("subagent_failed", {
                "subagent_id": self.name,
                "error": f"Token budget exceeded: {tokens_used} >= {self.max_tokens}",
                "iterations_used": self._iterations_so_far,
                "tool_calls_count": len(self._accumulated_tool_calls),
                "duration_ms": result.duration_ms,
                "tokens_used": tokens_used,
            })

            return result

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            records = self.llm_client.get_call_records()
            tokens_used = sum(r.total_tokens for r in records[self._records_before:])

            logger.warning("[SubAgent] %s timed out after %ds (iterations=%d, tokens=%d)",
                           self.name, self.timeout, self._iterations_so_far, tokens_used)

            timeout_summary = SubAgentSummary(
                accomplished="",
                findings="",
                issues=f"SubAgent timed out after {self.timeout}s",
                artifacts=_extract_artifacts_from_log(self._accumulated_tool_calls),
                tool_calls_summary=_extract_tool_calls_summary_from_log(self._accumulated_tool_calls) or "Execution exceeded timeout limit",
            )
            summary_text = timeout_summary.model_dump_json(ensure_ascii=False)

            result = SubAgentResult(
                subagent_id=self.name,
                task_description=self.task_description,
                status=SubAgentStatus.TIMED_OUT,
                summary=timeout_summary,
                summary_text=summary_text,
                tool_calls_count=len(self._accumulated_tool_calls),
                iterations_used=self._iterations_so_far,
                duration_ms=round(elapsed_ms, 2),
                tokens_used=tokens_used,
                tool_calls_log=list(self._accumulated_tool_calls),
            )

            self._emit("subagent_timed_out", {
                "subagent_id": self.name,
                "timeout": self.timeout,
                "iterations_used": self._iterations_so_far,
                "tool_calls_count": len(self._accumulated_tool_calls),
                "duration_ms": result.duration_ms,
                "tokens_used": tokens_used,
            })

            return result

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            records = self.llm_client.get_call_records()
            tokens_used = sum(r.total_tokens for r in records[self._records_before:])
            error_msg = str(exc)[:500]

            logger.error("[SubAgent] %s unexpected error: %s (iterations=%d, duration=%.0fms)",
                         self.name, error_msg[:200], self._iterations_so_far, elapsed_ms, exc_info=True)

            error_summary = SubAgentSummary(
                accomplished="",
                findings="",
                issues=error_msg,
                artifacts=_extract_artifacts_from_log(self._accumulated_tool_calls),
                tool_calls_summary=_extract_tool_calls_summary_from_log(self._accumulated_tool_calls) or "Unexpected error during execution",
            )
            summary_text = error_summary.model_dump_json(ensure_ascii=False)

            result = SubAgentResult(
                subagent_id=self.name,
                task_description=self.task_description,
                status=SubAgentStatus.FAILED,
                summary=error_summary,
                summary_text=summary_text,
                tool_calls_count=len(self._accumulated_tool_calls),
                iterations_used=self._iterations_so_far,
                duration_ms=round(elapsed_ms, 2),
                tokens_used=tokens_used,
                tool_calls_log=list(self._accumulated_tool_calls),
            )

            self._emit("subagent_failed", {
                "subagent_id": self.name,
                "error": error_msg,
                "iterations_used": self._iterations_so_far,
                "tool_calls_count": len(self._accumulated_tool_calls),
                "duration_ms": result.duration_ms,
                "tokens_used": tokens_used,
            })

            return result

    async def _summarize_result(self, step_result: Any) -> SubAgentSummary:
        """
        Generate a structured summary of the SubAgent's work.
        生成子智能体工作成果的结构化摘要。

        Anti-pattern #5 defense: structured template forces honest issues reporting.
        Anti-pattern #6 defense: structured artifact instead of free-text.
        """
        output = step_result.output or ""
        tool_calls_log = step_result.tool_calls_log or []

        # Always extract mechanical fields from tool_calls_log (no LLM needed)
        artifacts = _extract_artifacts_from_log(tool_calls_log)
        tool_calls_summary = _extract_tool_calls_summary_from_log(tool_calls_log)

        logger.debug("[SubAgent] _summarize_result: output_len=%d, tool_calls=%d, extracted_artifacts=%s",
                     len(output), len(tool_calls_log), artifacts)

        # Use LLM to generate structured summary
        # (no short-path bypass — anti-pattern #5 defense requires all paths to go through LLM reflection)
        try:
            messages = list(self._summary_messages)
            messages.append({
                "role": "user",
                "content": f"{SUMMARIZE_PROMPT}\n\nYour work output:\n{output[:8000]}",
            })

            logger.debug("[SubAgent] Generating LLM summary: output_truncated=%d chars sent",
                         min(len(output), 8000))

            response = await self.llm_client.chat_json(
                messages, temperature=0.2, max_tokens=1500,
            )

            if isinstance(response, dict):
                try:
                    summary = SubAgentSummary.model_validate(response)
                    logger.debug("[SubAgent] LLM summary validated: accomplished='%s', issues='%s'",
                                 summary.accomplished[:100], summary.issues[:100])
                    # Override mechanical fields with extracted data for accuracy
                    if not summary.artifacts and artifacts:
                        summary = summary.model_copy(update={"artifacts": artifacts})
                    if not summary.tool_calls_summary and tool_calls_summary:
                        summary = summary.model_copy(update={"tool_calls_summary": tool_calls_summary})
                    return summary
                except ValidationError:
                    logger.debug("[SubAgent] LLM summary validation failed, using fallback")

            # JSON parsed but wrong structure — fallback
            logger.debug("[SubAgent] LLM returned unexpected structure, using truncated fallback")
            return SubAgentSummary(
                accomplished=str(response)[:500],
                findings="",
                issues="Summary structure unexpected",
                artifacts=artifacts,
                tool_calls_summary=tool_calls_summary,
            )

        except Exception:
            logger.debug("[SubAgent] Summary generation failed, truncating (output_len=%d)", len(output), exc_info=True)
            return SubAgentSummary(
                accomplished=output[:config.SUBAGENT_SUMMARY_MAX_LENGTH],
                findings="",
                issues="Summary generation failed; output truncated",
                artifacts=artifacts,
                tool_calls_summary=tool_calls_summary,
            )
