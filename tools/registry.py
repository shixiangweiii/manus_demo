"""Explicit registry and construction of the local tool set."""

from __future__ import annotations

from collections.abc import Iterable

from core.events import EventBus
from core.settings import AppSettings
from tools.base import BaseTool


class ToolRegistry:
    def __init__(self, tools: Iterable[BaseTool] = ()) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: BaseTool, *, replace: bool = False) -> None:
        if tool.name in self._tools and not replace:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def require(self, name: str) -> BaseTool:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool

    def schemas(self) -> list[dict]:
        return [tool.to_openai_tool() for tool in self._tools.values()]

    def values(self) -> list[BaseTool]:
        return list(self._tools.values())

    def as_dict(self) -> dict[str, BaseTool]:
        return dict(self._tools)


def build_default_tools(settings: AppSettings, events: EventBus) -> ToolRegistry:
    """Build only dependency-free local tools; capabilities are added by runtime."""
    from tools.code_executor import CodeExecutorTool
    from tools.fetch_url import FetchUrlTool
    from tools.file_ops import FileOpsTool
    from tools.shell_tool import ShellTool
    from tools.user_location import UserLocationTool
    from tools.web_search import WebSearchTool

    tools: list[BaseTool] = [
        WebSearchTool(settings.tools),
        FetchUrlTool(settings.tools),
        UserLocationTool(
            configured_location=settings.tools.user_location,
            state_dir=settings.paths.state_dir,
            ip_lookup=settings.tools.location_ip_lookup,
            ssl_verify=settings.tools.location_ssl_verify,
        ),
        FileOpsTool(settings.paths.sandbox_dir),
    ]
    if settings.tools.python_mode == "trusted":
        tools.append(
            CodeExecutorTool(
                trusted=True,
                sandbox_dir=settings.paths.sandbox_dir,
                timeout=settings.tools.code_timeout_seconds,
                max_output_bytes=settings.tools.subprocess_max_output_bytes,
                ssl_verify=settings.tools.location_ssl_verify,
                max_concurrent=settings.tools.code_max_concurrent,
            )
        )
    if settings.tools.shell_mode != "disabled":
        tools.append(
            ShellTool(
                workdir=settings.paths.sandbox_dir,
                mode=settings.tools.shell_mode,
                timeout=settings.tools.shell_timeout_seconds,
                python_command=settings.tools.python_command,
                max_output_bytes=settings.tools.subprocess_max_output_bytes,
                max_concurrent=settings.tools.shell_max_concurrent,
                ssl_verify=settings.tools.location_ssl_verify,
            )
        )
    return ToolRegistry(tools)
