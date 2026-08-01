"""
SpecialistAgent and registry - Context-passing specialist for Handoff.
专家智能体与注册表——用于 Handoff 的上下文传递式专家。

Unlike SubAgent (isolated, summary-only, no parent context), a SpecialistAgent:
  - receives the caller's context briefing (NOT context="")
  - returns its FULL output (NOT a compressed summary)
  - has a role-specific system prompt
  - may call ask_user when explicitly allowed (HANDOFF_ALLOW_ASK_USER)
  - is depth=1: it cannot handoff or spawn subagents (whitelist excludes them)

与 SubAgent（隔离、摘要、无父上下文）互补：专家拿到上下文简报、返回完整输出、
角色专属提示、可显式开启 ask_user、同样 depth=1（不可再 handoff/subagent）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import config
from agents.prompt_utils import build_system_prompt
from context.manager import ContextManager
from llm.client import LLMClient
from react.engine import ReActEngine
from execution.models import ReasoningEffort
from tools.base import BaseTool
from tools.router import ToolRouter

logger = logging.getLogger(__name__)

# Tools a specialist may never use (depth=1 + isolation)
# 专家永不可用的工具（depth=1 + 隔离）
_BLOCKED_SPECIALIST_TOOLS = ("handoff", "subagent", "memory_store", "memory_revoke", "remote_subagent")


@dataclass
class SpecialistSpec:
    """Declarative specialist definition. / 声明式专家定义。"""
    name: str
    description: str
    system_prompt: str
    default_tools: list[str] = field(default_factory=list)


# Built-in specialist registry
# 内置专家注册表
SPECIALIST_REGISTRY: dict[str, SpecialistSpec] = {
    "researcher": SpecialistSpec(
        name="researcher",
        description="Information-gathering specialist: web search + page fetching + synthesis.",
        system_prompt=(
            "You are a research specialist. Gather accurate, up-to-date information "
            "using web_search and fetch_url, cross-check sources, and synthesize a "
            "clear, well-organized answer. Cite key facts. Respond in the user's language.\n"
            "你是调研专家：用 web_search/fetch_url 收集准确、最新的信息，交叉验证来源，"
            "给出条理清晰的答案，与用户语言一致。"
        ),
        default_tools=["web_search", "fetch_url", "get_user_location"],
    ),
    "coder": SpecialistSpec(
        name="coder",
        description="Coding specialist: write/run code, manipulate files, run shell commands.",
        system_prompt=(
            "You are a coding specialist. Implement, run, and verify code using "
            "execute_python, file_ops, and execute_shell. Test your work before "
            "reporting. Be precise and report any failures honestly. Respond in the "
            "user's language.\n"
            "你是编码专家：用 execute_python/file_ops/execute_shell 实现、运行、验证代码，"
            "完成前先自测，诚实报告失败，与用户语言一致。"
        ),
        default_tools=["execute_python", "file_ops", "execute_shell"],
    ),
    "writer": SpecialistSpec(
        name="writer",
        description="Writing/synthesis specialist: compose the final answer from context.",
        system_prompt=(
            "You are a writing specialist. Compose a clear, well-structured final "
            "answer from the provided context. Do not fabricate facts; if information "
            "is missing, say so. Respond in the user's language.\n"
            "你是写作/综合专家：基于给定上下文写出条理清晰的最终答案，不臆造事实，"
            "信息缺失时如实说明，与用户语言一致。"
        ),
        default_tools=[],
    ),
}


class SpecialistAgent:
    """
    A specialist agent that takes over (control transfer) with the caller's
    context and returns its full output. Reuses ReActEngine with a private
    messages list (same isolation pattern as SubAgent).
    专家 agent：带调用方上下文接管并返回完整输出；复用 ReActEngine（私有 messages）。
    """

    def __init__(
        self,
        spec: SpecialistSpec,
        llm_client: LLMClient,
        available_tools: dict[str, BaseTool],
        context_manager: ContextManager | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        allow_ask_user: bool = False,
        interactive: bool = False,
        parent_name: str = "",
        max_iterations: int | None = None,
    ):
        self.spec = spec
        self.llm_client = llm_client
        self._on_event = on_event or (lambda *_: None)
        self.parent_name = parent_name
        self._allow_ask_user = allow_ask_user and interactive

        # Resolve tool whitelist: spec defaults ∩ available, minus blocked tools.
        # ask_user only when explicitly allowed AND interactive.
        whitelist: list[str] = []
        for name in spec.default_tools:
            if name in _BLOCKED_SPECIALIST_TOOLS:
                continue
            if name in available_tools:
                whitelist.append(name)
        if self._allow_ask_user and "ask_user" in available_tools:
            whitelist.append("ask_user")
        # de-dup, preserve order
        seen: set[str] = set()
        self._tool_names = [n for n in whitelist if not (n in seen or seen.add(n))]
        tools = [available_tools[n] for n in self._tool_names]

        # Specialist-specific system prompt; inject ask_user guidance only if allowed.
        self.system_prompt = build_system_prompt(
            spec.system_prompt,
            inject_context=True,
            inject_subagent_guidance=False,
            inject_hitl_guidance=self._allow_ask_user,
        )

        tool_router = ToolRouter(available_tools=self._tool_names)
        self._react_engine = ReActEngine(
            llm_client=llm_client,
            tools=tools,
            max_iterations=max_iterations or config.HANDOFF_MAX_ITERATIONS,
            tool_router=tool_router,
            context_manager=context_manager or ContextManager(),
            agent_name=f"Specialist-{spec.name}",
        )
        logger.info(
            "[SpecialistAgent] Created '%s': tools=%s, ask_user=%s, parent=%s",
            spec.name, self._tool_names, self._allow_ask_user, parent_name,
        )

    async def run(
        self,
        task: str,
        context: str = "",
        effort: ReasoningEffort | None = None,
    ) -> str:
        """Run the specialist with the caller's context; return its FULL output.
        带调用方上下文运行专家，返回完整输出（控制权转移由调用方/引擎处理）。"""
        result = await self._react_engine.execute(
            prompt=task,
            context=context,
            node_id=f"specialist-{self.spec.name}",
            system_hint=self.system_prompt,
            effort=effort or ReasoningEffort.MEDIUM,
        )
        return result.output
