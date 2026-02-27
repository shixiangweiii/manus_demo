"""
Base Agent - Foundation class for all agents in the Manus Demo.
BaseAgent —— Manus Demo 中所有智能体的基础类。

Provides common functionality:
  - System prompt management
  - Message history tracking
  - LLM interaction via the shared LLM client
  - Integration with context manager for token-aware conversations

提供通用功能：
  - System prompt 管理
  - 对话历史消息追踪
  - 通过共享 LLMClient 与 LLM 交互
  - 集成 ContextManager 实现 Token 感知的上下文压缩
"""

from __future__ import annotations

import logging
from typing import Any

from context.manager import ContextManager
from llm.client import LLMClient

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Base class that all specialized agents inherit from.
    所有专用智能体继承的基类。

    Each agent maintains its own message history and system prompt,
    and delegates LLM calls to the shared LLMClient instance.
    每个智能体维护自己的消息历史和 system prompt，
    并将 LLM 调用委托给共享的 LLMClient 实例。
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        llm_client: LLMClient,
        context_manager: ContextManager | None = None,
    ):
        self.name = name                            # 智能体名称，用于日志标识
        self.system_prompt = system_prompt          # 系统提示词，定义智能体的角色和行为
        self.llm_client = llm_client                # 共享 LLM 客户端
        self.context_manager = context_manager or ContextManager()  # 上下文管理器（Token 超限时压缩）
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}  # 消息历史初始化，始终以 system prompt 开头
        ]

    # ------------------------------------------------------------------
    # Message management
    # 消息管理
    # ------------------------------------------------------------------

    def add_message(self, role: str, content: str) -> None:
        """
        Append a message to this agent's conversation history.
        将一条消息追加到该智能体的对话历史中。
        """
        self._messages.append({"role": role, "content": content})

    def get_messages(self) -> list[dict[str, Any]]:
        """
        Return a copy of all messages.
        返回当前所有消息的副本（避免外部直接修改内部状态）。
        """
        return list(self._messages)

    def reset(self) -> None:
        """
        Clear conversation history, keeping only the system prompt.
        清空对话历史，只保留 system prompt。
        每次执行新任务前调用，避免历史消息污染当前任务。
        """
        self._messages = [{"role": "system", "content": self.system_prompt}]

    # ------------------------------------------------------------------
    # LLM interaction
    # LLM 交互方法
    # ------------------------------------------------------------------

    async def think(self, user_input: str, **kwargs: Any) -> str:
        """
        Send user_input to the LLM with full conversation context.
        Handles context compression if messages are too long.

        将 user_input 连同完整对话上下文发送给 LLM，返回文本响应。
        若消息总量超过 Token 上限，自动触发上下文压缩。
        """
        self.add_message("user", user_input)

        # 超 Token 时压缩旧消息
        self._messages = await self.context_manager.compress_if_needed(
            self._messages, self.llm_client
        )

        response = await self.llm_client.chat(self._messages, **kwargs)
        self.add_message("assistant", response)
        logger.debug("[%s] Response: %s", self.name, response[:200])
        return response

    async def think_json(self, user_input: str, **kwargs: Any) -> Any:
        """
        Send user_input and expect a JSON response.
        发送 user_input，要求 LLM 返回 JSON 格式的响应。
        用于 Planner 生成结构化计划、Reflector 生成评估结果等场景。
        """
        self.add_message("user", user_input)

        self._messages = await self.context_manager.compress_if_needed(
            self._messages, self.llm_client
        )

        result = await self.llm_client.chat_json(self._messages, **kwargs)
        self.add_message("assistant", str(result))
        return result

    async def think_with_tools(
        self,
        user_input: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        """
        Send user_input with tool definitions, returning the raw response
        message so the caller can handle tool_calls.

        发送 user_input 并附带工具定义，返回原始响应消息对象。
        调用方需自行检查 response.tool_calls 来决定后续执行哪个工具。
        这是 ReAct 循环的核心：LLM 选择并调用工具。
        """
        self.add_message("user", user_input)

        self._messages = await self.context_manager.compress_if_needed(
            self._messages, self.llm_client
        )

        response_msg = await self.llm_client.chat_with_tools(
            self._messages, tools, **kwargs
        )

        # Record the assistant response in history
        # 将 assistant 响应（含工具调用信息）记录到消息历史
        assistant_dict: dict[str, Any] = {
            "role": "assistant",
            "content": response_msg.content or "",
        }
        if response_msg.tool_calls:
            # 将工具调用序列化为 OpenAI 格式并记录
            assistant_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in response_msg.tool_calls
            ]
        self._messages.append(assistant_dict)

        return response_msg

    def add_tool_result(self, tool_call_id: str, result: str) -> None:
        """
        Record a tool execution result in the message history.
        将工具执行结果记录到消息历史中，供下一轮 LLM 推理使用（ReAct 的 Observe 步骤）。
        """
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,  # 与 assistant 消息中的 tool_call id 对应
            "content": result,
        })

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, messages={len(self._messages)})"
