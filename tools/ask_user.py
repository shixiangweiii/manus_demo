"""
AskUserTool - Human-in-the-Loop tool for the ReAct engine.
人机交互工具 —— ReAct 引擎中的 HITL 工具（v13 新增）。

When the LLM encounters ambiguous or incomplete information, it can
call this tool to ask the user for clarification. The tool suspends
the ReAct loop until the user provides input.

Design principles:
  - Human-as-Tool pattern: the human is registered as a tool in the
    agent's tool list, and the LLM decides when to call it.
  - Async-safe: uses asyncio.Future to bridge the async ReAct loop
    with synchronous user input, without blocking the event loop.
  - Guarded: max prompts per task prevents infinite prompting loops.
  - Excluded from SubAgent whitelist (depth=1): SubAgents cannot
    ask the user directly (they should report ambiguity via their
    structured summary instead).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable

import config
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class AskUserTool(BaseTool):
    """
    HITL tool that pauses the ReAct loop to ask the user a question.
    人机交互工具，暂停 ReAct 循环向用户提问。

    The tool creates an asyncio.Future, emits an event carrying it,
    and awaits the Future. The UI layer (main.py) resolves the Future
    after collecting user input via asyncio.to_thread(console.input, ...).
    """

    def __init__(
        self,
        on_user_prompt: Callable[[str, str, asyncio.Future[str]], None] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        max_prompts_per_task: int | None = None,
        timeout: int | None = None,
    ):
        self._on_user_prompt = on_user_prompt
        self._on_event = on_event or (lambda *_: None)
        self._max_prompts = max_prompts_per_task or config.HITL_MAX_PROMPTS_PER_TASK
        self._timeout = timeout or config.HITL_USER_INPUT_TIMEOUT
        self._prompt_count = 0
        self._interactive_mode = True
        self._prompt_semaphore = asyncio.Semaphore(1)

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a question and wait for their response. "
            "Use this tool when you have ambiguous or incomplete information "
            "and need the user to clarify or confirm before proceeding. "
            "For example: if you found an approximate location via IP but "
            "are not sure it is correct, ask the user to confirm or provide "
            "a more specific location. "
            "DO NOT use this tool for questions you can answer with other "
            "tools, or for tasks that do not require user input. "
            "Use sparingly — each call pauses execution until the user responds."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The question to ask the user. Be specific and concise. "
                        "For example: 'I found your location as Beijing via IP, "
                        "is this correct? If not, please tell me your city.'"
                    ),
                },
            },
            "required": ["question"],
        }

    async def execute(self, **kwargs: Any) -> str:
        question = kwargs.get("question", "")
        if not question:
            return "Error: question parameter is required for ask_user tool."

        if not self._interactive_mode:
            return (
                "Error: ask_user is not available in non-interactive mode. "
                "Proceed with your best judgment using available tools."
            )

        if self._prompt_count >= self._max_prompts:
            logger.warning(
                "[AskUserTool] Max prompts reached: %d/%d",
                self._prompt_count, self._max_prompts,
            )
            return (
                f"Error: Maximum user prompts reached ({self._max_prompts} per task). "
                "Proceed with your best judgment using available tools."
            )

        async with self._prompt_semaphore:
            self._prompt_count += 1
            prompt_id = str(uuid.uuid4())[:8]

            logger.info(
                "[AskUserTool] Prompt #%d/%d (id=%s): %s",
                self._prompt_count, self._max_prompts, prompt_id, question[:100],
            )

            loop = asyncio.get_running_loop()
            response_future: asyncio.Future[str] = loop.create_future()

            if self._on_user_prompt:
                self._on_user_prompt(question, prompt_id, response_future)
            else:
                return (
                    "Error: No user input handler registered. "
                    "Proceed with your best judgment."
                )

            try:
                user_response = await asyncio.wait_for(
                    response_future,
                    timeout=self._timeout,
                )
                # User cancellation (Ctrl+C / EOF) is signalled by the UI layer
                # via the sentinel "(user cancelled)". Convert to Error: prefix
                # so ReActEngine treats it as a tool failure (ToolRouter accounting,
                # evaluation distinguishability) — matching the timeout/limit paths.
                # 用户取消（Ctrl+C / EOF）由 UI 层通过 "(user cancelled)" sentinel 传达；
                # 转为 Error 前缀以与 timeout/上限路径风格一致，并让 ToolRouter / evaluation 可区分。
                if user_response == "(user cancelled)":
                    logger.info("[AskUserTool] User cancelled prompt %s", prompt_id)
                    self._on_event("ask_user_cancelled", {
                        "prompt_id": prompt_id,
                        "prompt_count": self._prompt_count,
                    })
                    return (
                        "Error: User cancelled the prompt. "
                        "Proceed with your best judgment using available tools."
                    )

                logger.info(
                    "[AskUserTool] Response received for prompt %s: %s",
                    prompt_id, user_response[:100],
                )
                self._on_event("ask_user_response", {
                    "prompt_id": prompt_id,
                    "response": user_response,
                    "prompt_count": self._prompt_count,
                })
                return f"User response: {user_response}"

            except asyncio.TimeoutError:
                logger.warning(
                    "[AskUserTool] Timeout after %ds for prompt %s",
                    self._timeout, prompt_id,
                )
                self._on_event("ask_user_timeout", {
                    "prompt_id": prompt_id,
                    "timeout": self._timeout,
                    "prompt_count": self._prompt_count,
                })
                return (
                    f"Error: User did not respond within {self._timeout} seconds. "
                    "Proceed with your best judgment using available tools."
                )

    def reset_task_state(self) -> None:
        """Reset per-task state for a new task. Called by OrchestratorAgent.run()."""
        logger.debug(
            "[AskUserTool] Resetting task state: prompt_count=%d -> 0",
            self._prompt_count,
        )
        self._prompt_count = 0

    def set_interactive_mode(self, enabled: bool) -> None:
        """Enable or disable interactive mode. Disabled in single-task mode."""
        self._interactive_mode = enabled
