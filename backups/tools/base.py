"""
Base Tool - Abstract interface for all tools in the Manus Demo.
BaseTool —— Manus Demo 中所有工具的抽象接口。

Each tool exposes:
  - name / description for the LLM to understand its purpose
  - parameters_schema (JSON Schema) for OpenAI function-calling
  - execute() to actually run the tool

每个工具暴露：
  - name / description：供 LLM 理解工具用途（写入 system prompt 或 function calling schema）
  - parameters_schema（JSON Schema）：OpenAI function calling 的参数描述
  - execute()：实际执行工具逻辑
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    """
    Abstract base class for all agent tools.
    所有 Agent 工具的抽象基类。
    所有具体工具（web_search、execute_python、file_ops 等）都继承自此类。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique tool name used in function calls.
        工具唯一名称，用于 function calling 中的函数名标识。
        """

    @property
    @abstractmethod
    def description(self) -> str:
        """
        Human-readable description of what the tool does.
        工具功能的人类可读描述，LLM 根据此描述判断何时调用该工具。
        """

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """
        JSON Schema describing the tool's parameters.
        描述工具参数的 JSON Schema，供 LLM 生成正确的函数调用参数。
        """

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        Run the tool and return a string result.
        执行工具并返回字符串结果（所有工具均返回字符串，便于 LLM 处理）。
        """

    # ------------------------------------------------------------------
    # OpenAI function-calling schema
    # OpenAI function calling 格式转换
    # ------------------------------------------------------------------

    def to_openai_tool(self) -> dict[str, Any]:
        """
        Convert to the OpenAI tools format:
        转换为 OpenAI tools 格式，直接传入 chat completions API 的 tools 参数：

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": { ... JSON Schema ... }
            }
        }
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
