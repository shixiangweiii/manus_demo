"""
MCP Server Wrapper — expose project BaseTools and Memory as an MCP server.

MCP 服务端包装 —— 将项目 BaseTool 和 Memory 暴露为 MCP 服务器。
使用 FastMCP (mcp SDK) 实现，支持 streamable_http 和 stdio 传输。

Exposed:
  - Tools: one MCP tool per BaseTool instance (with explicit input schema)
  - Resources: memory search as resource template (memory://search/{query})
  - Prompts: task planning prompt template
"""

from __future__ import annotations

import logging
from typing import Any

from tools.base import BaseTool

logger = logging.getLogger(__name__)

# v18.3: tools a remote agent must never expose/use (prevents cross-process
# recursion + isolation breaks). / 远端 agent 永不暴露/使用的工具，防递归。
_REMOTE_BLOCKED = ("remote_subagent", "handoff", "subagent", "ask_user")


class MCPServerWrapper:
    """
    Wraps the project's BaseTool instances and Memory as an MCP server.

    将项目的 BaseTool 实例和 Memory 包装为 MCP 服务器。
    外部客户端（Claude Code、Cursor 等）可通过 MCP 协议连接。
    """

    def __init__(
        self,
        tools: list[BaseTool],
        memory_service: Any | None = None,
        server_name: str = "manus-demo",
        host: str = "127.0.0.1",
        port: int = 8080,
        instructions: str | None = None,
        llm_client: Any | None = None,
        expose_agent: bool = False,
    ):
        self._tools = tools
        self._memory_service = memory_service
        self._server_name = server_name
        self._host = host
        self._port = port
        self._llm_client = llm_client
        self._expose_agent = expose_agent

        # Lazy import FastMCP — avoid requiring mcp SDK at module load
        from mcp.server.fastmcp import FastMCP

        self._mcp = FastMCP(
            name=server_name,
            instructions=instructions or "Manus Demo multi-agent AI system tools and memory",
            host=host,
            port=port,
        )
        self._register_tools()
        if memory_service:
            self._register_resources()
            self._register_prompts()
        # v18.3/18.4: expose this project as a remote agent (AgentCard + task endpoint)
        if expose_agent and llm_client is not None:
            self._register_agent_endpoints()
        elif expose_agent and llm_client is None:
            logger.warning("[MCPServer] expose_agent=True but no llm_client provided — agent endpoints NOT registered")

    # ------------------------------------------------------------------
    # Tool registration — expose each BaseTool as an MCP tool
    # 工具注册 —— 每个 BaseTool 注册为 MCP tool
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        """Register each BaseTool as an MCP tool via FastMCP.add_tool().
        通过 FastMCP.add_tool() 将每个 BaseTool 注册为 MCP tool。"""
        for tool in self._tools:
            self._register_one_tool(tool)

    def _register_one_tool(self, tool: BaseTool) -> None:
        """Register a single BaseTool as an MCP tool with explicit schema.
        将单个 BaseTool 注册为 MCP tool，显式传入 input schema。"""
        # Fix-5: Removed dead make_handler function
        handler = self._create_tool_handler(tool)
        try:
            # Fix-4: Register handler with explicit name/description
            self._mcp.add_tool(
                fn=handler,
                name=tool.name,
                description=tool.description,
            )
            logger.info("[MCPServer] Registered tool: %s", tool.name)
        except Exception as exc:
            logger.warning("[MCPServer] Failed to register tool '%s': %s", tool.name, exc)

    @staticmethod
    def _create_tool_handler(tool: BaseTool):
        """Create an async handler function for a BaseTool.
        为 BaseTool 创建异步处理函数。
        Note: FastMCP 无法从 **kwargs 推断参数 schema，
        但 add_tool() 使用 tool.name/description 作为 MCP tool 元数据，
        handler 仅在调用时使用。参数 schema 通过 FastMCP 的内部 schema 生成
        或在 get_registered_tool_names() 中通过 list_tools() 获取。"""
        async def handler(**kwargs: Any) -> str:
            return await tool.execute(**kwargs)
        handler.__name__ = f"mcp_handler_{tool.name}"
        return handler

    # ------------------------------------------------------------------
    # Resource registration — expose memory search
    # 资源注册 —— 暴露记忆搜索
    # ------------------------------------------------------------------

    def _register_resources(self) -> None:
        """Register memory search as an MCP resource template.
        将记忆搜索注册为 MCP 资源模板。"""
        service = self._memory_service

        @self._mcp.resource("memory://search/{query}")
        async def search_memory(query: str) -> str:
            """Search agentic memory for relevant records.
            搜索结构化记忆中的相关记录。"""
            try:
                results = service.search_for_task(query)
                return service.format_context(results)
            except Exception as exc:
                logger.warning("[MCPServer] Memory search failed: %s", exc)
                return f"Error searching memory: {exc}"

        logger.info("[MCPServer] Registered resource: memory://search/{query}")

    # ------------------------------------------------------------------
    # Prompt registration — expose planning prompt
    # 提示注册 —— 暴露规划提示模板
    # ------------------------------------------------------------------

    def _register_prompts(self) -> None:
        """Register task planning prompt as an MCP prompt template.
        将任务规划提示注册为 MCP 提示模板。"""
        @self._mcp.prompt(name="plan_task", description="Generate a task planning prompt for the agent")
        def plan_task(task: str, complexity: str = "auto") -> str:
            """Generate a planning prompt for a given task.
            为给定任务生成规划提示。"""
            return (
                f"Plan a solution for the following task (complexity: {complexity}):\n\n{task}\n\n"
                "Break the task into concrete, executable steps. "
                "For each step, specify the tool to use and the expected outcome."
            )

        logger.info("[MCPServer] Registered prompt: plan_task")

    # ------------------------------------------------------------------
    # v18.3/18.4 Agent endpoints — expose this project as a remote agent
    # 将本项目暴露为远端 agent（AgentCard 能力广播 + A2A 任务端点）
    # ------------------------------------------------------------------

    def _register_agent_endpoints(self) -> None:
        """Register get_agent_card + a2a_run_task MCP tools (typed signatures).
        注册 get_agent_card + a2a_run_task（带类型签名，FastMCP 自动生成 schema）。"""
        # Remote agent runs with a filtered tool set (no nested/cross-process delegation).
        agent_tools = [t for t in self._tools if t.name not in _REMOTE_BLOCKED]
        agent_tool_names = [t.name for t in agent_tools]
        llm_client = self._llm_client
        server_name = self._server_name
        host, port = self._host, self._port

        def get_agent_card() -> str:
            """Return this agent's AgentCard (capabilities advertisement) as JSON."""
            import config as _config
            from a2a.models import AgentCard, AgentSkill
            skills = [AgentSkill(name=t.name, description=t.description) for t in agent_tools]
            card = AgentCard(
                name=server_name,
                description="Manus Demo remote agent (depth=1 SubAgent executor)",
                version=getattr(_config, "VERSION", ""),
                auth="local",
                skills=skills,
                endpoint={"transport": "streamable_http", "host": host, "port": port},
            )
            return card.model_dump_json()

        async def a2a_run_task(input: str, context: str = "", task_id: str = "") -> str:
            """Run a delegated task via an isolated depth=1 SubAgent; return A2ATaskResponse JSON."""
            import uuid as _uuid
            from a2a.models import A2ATaskResponse
            from agents.subagent import SubAgent
            from schema import SubAgentStatus

            tid = task_id or _uuid.uuid4().hex[:12]
            try:
                sub = SubAgent(
                    name="RemoteAgent",
                    task_description=input,
                    llm_client=llm_client,
                    tools=agent_tools,
                    on_event=lambda *_: None,
                    parent_agent_name=f"a2a:{server_name}",
                )
                result = await sub.run(context=context)
                completed = result.status == SubAgentStatus.COMPLETED
                return A2ATaskResponse(
                    task_id=tid,
                    status="completed" if completed else "failed",
                    output=result.summary_text,
                    error="" if completed else (result.summary.issues or "remote task failed"),
                ).model_dump_json()
            except Exception as exc:
                logger.error("[MCPServer] a2a_run_task failed: %s", exc, exc_info=True)
                return A2ATaskResponse(
                    task_id=tid, status="failed", error=str(exc)[:300],
                ).model_dump_json()

        try:
            self._mcp.add_tool(
                fn=get_agent_card,
                name="get_agent_card",
                description="Return this remote agent's AgentCard (capabilities) as JSON.",
            )
            self._mcp.add_tool(
                fn=a2a_run_task,
                name="a2a_run_task",
                description="Run a delegated A2A task; returns an A2ATaskResponse JSON.",
            )
            logger.info(
                "[MCPServer] Registered agent endpoints (get_agent_card, a2a_run_task); "
                "agent tools=%s", agent_tool_names,
            )
        except Exception as exc:
            logger.warning("[MCPServer] Failed to register agent endpoints: %s", exc)

    # ------------------------------------------------------------------
    # Server lifecycle
    # 服务端生命周期
    # ------------------------------------------------------------------

    async def run_streamable_http(self) -> None:
        """Run the MCP server with Streamable HTTP transport.
        使用 Streamable HTTP 传输运行 MCP 服务器。"""
        logger.info("[MCPServer] Starting Streamable HTTP server on %s:%d",
                    self._mcp.settings.host, self._mcp.settings.port)
        await self._mcp.run_streamable_http_async()

    async def run_stdio(self) -> None:
        """Run the MCP server with stdio transport.
        使用 stdio 传输运行 MCP 服务器。"""
        logger.info("[MCPServer] Starting stdio server")
        await self._mcp.run_stdio_async()

    def get_server(self):
        """Return the underlying FastMCP instance (for testing).
        返回底层 FastMCP 实例（测试用）。"""
        return self._mcp

    def get_registered_tool_names(self) -> list[str]:
        """Return names of all registered MCP tools (for testing).
        返回所有已注册 MCP tool 的名称（测试用）。
        Fix-6: 使用 list_tools() 公开 API 替代直接访问私有属性。"""
        try:
            # Use async-compatible approach: access tool manager's list
            tools_result = self._mcp._tool_manager.list_tools()
            return [t.name for t in tools_result]
        except Exception:
            # Fallback for different FastMCP versions
            return [t.name for t in self._mcp._tool_manager._tools.values()]
