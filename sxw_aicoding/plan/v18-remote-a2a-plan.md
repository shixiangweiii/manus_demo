# v18 Multi-Agent 续集方案（v18.3 Remote SubAgent + v18.4 A2A 原型）

目标产物：`sxw_aicoding/plan/v18-remote-a2a-plan.md`（实施时落盘）
生成日期：2026-05-29
适用阶段：v18 - Multi-Agent 远端协作（依赖已完成的 v16 MCP Bridge + v9 SubAgent + v18.1/18.2）

---

## Context（为什么做这件事）

路线图 §10 的 v18.3「通过 MCP 调用远端 agent server（跨进程隔离、长任务稳定性）」和 v18.4「A2A 原型：Agent Card + task request/response，本地可信、不做开放网络发现」。当前代码事实：

- v16 MCP **server 只暴露 tools/memory/prompts**（`tools/mcp/server.py:MCPServerWrapper`，无 agent/task 端点）。
- v16 MCP **client**（`tools/mcp/client.py:MCPClientManager`）可发现+调用远端工具，按调用开 transport，支持 stdio / streamable_http（`tools/mcp/transport.py`、`config.py`）。
- v9 `SubAgent`（`agents/subagent.py`）已是 depth=1、summary-only、独立上下文，天然适合做"远端 agent"的执行体。

**用户已确认的范围与决策**：
1. 本轮做 **v18.3 + v18.4**（两者高度耦合）；18.5 多智能体评测脚手架下一轮。
2. v18.3 **复用现有 MCP server**，传输可配（streamable_http 回环 / stdio 子进程，由配置决定）——最小代码、同进程回环即可演示完整协议往返。
3. v18.3 客户端用**专用 `RemoteSubAgentTool`**（底层 MCPClientManager），带调用限次/超时/事件，tracing 与 eval 可区分于普通 MCP 工具。

设计取向：18.3 提供"远端 agent 调用"机制，18.4 在其上加 **AgentCard 能力广播** 与 **A2ATaskRequest/Response 信封**，让 RemoteSubAgentTool「先读卡、再发任务」。两者作为一个内聚的"远端 agent 子系统"实现。

---

## 设计总览

```text
父 agent 的某个 ReAct loop
   LLM 调用 remote_subagent(task, context)
        ↓                                   [客户端进程]
   [RemoteSubAgentTool.execute]  限次/超时/事件
        └── A2AClient（底层 MCPClientManager，transport 可配）
              ├── (可选) get_agent_card() ──► 能力发现 / 校验
              └── a2a_run_task(A2ATaskRequest JSON) ──┐
                                                       │  MCP transport
   ────────────────────────────────────────────────── ┼ ────────────────
                                                       ▼  [agent server 进程]
                                  [MCPServerWrapper, expose_agent=True]
                                    ├── get_agent_card() → AgentCard JSON
                                    └── a2a_run_task(input, context, task_id)
                                          └── SubAgent(depth=1, summary-only,
                                                server 工具集 − {远端/handoff/subagent/ask_user})
                                          → A2ATaskResponse JSON（status/output/error）
        ↓
   解析 A2ATaskResponse → 返回 output（remote_subagent 工具结果回灌父 loop）
```

要点：远端执行体是 **SubAgent（depth=1）**，server 端工具集剔除 `remote_subagent/handoff/subagent/ask_user` → 杜绝递归与跨进程无限委派。

---

## Part A — v18.3 Remote SubAgent

### 1. 服务端：MCPServerWrapper 暴露 agent 端点（`tools/mcp/server.py`）
- `__init__` 增参：`llm_client: Any | None = None`、`expose_agent: bool = False`。
- 当 `expose_agent and llm_client`：注册两个**带类型签名**的 MCP 工具（FastMCP 据类型生成 schema）：
  - `a2a_run_task(input: str, context: str = "", task_id: str = "") -> str`：构造 `SubAgent`（复用 `agents/subagent.py:SubAgent`），用 server 的 `llm_client` + **过滤后的 server 工具集**（剔除 `_REMOTE_BLOCKED = {remote_subagent, handoff, subagent, ask_user}`）执行，返回 `A2ATaskResponse` JSON（见 Part B）。
  - `get_agent_card() -> str`：返回 `AgentCard` JSON（见 Part B）。
- 复用现有 `_create_tool_handler` 思路，但 agent 端点用显式签名而非 `**kwargs`，确保 schema 完整。
- `_REMOTE_BLOCKED` 过滤在注册 SubAgent 工具时应用，防递归。

### 2. 客户端：RemoteSubAgentTool（`tools/remote_subagent_tool.py`，新增）
- `RemoteSubAgentTool(BaseTool)`，name `remote_subagent`。
- `parameters_schema`：`task`（必填）、`context`（选填，传给远端的背景简报）。
- `__init__(server_config, on_event, max_calls_per_task, timeout, parent_name, fetch_card=True)`；内部持有 `A2AClient`（底层 `MCPClientManager`，由单服务器 `MCPServerConfig` 构造）。
- `execute(task, context)`：
  - 限次（`REMOTE_SUBAGENT_MAX_CALLS_PER_TASK`，预扣不退款，仿 `subagent_tool.py:130-161`）。
  - `local_parent = self._parent_name`（await 前捕获）。
  - emit `remote_subagent_start`；（可选）`A2AClient.fetch_agent_card` 做能力发现；`asyncio.wait_for(A2AClient.run_task(...), timeout)`；解析 `A2ATaskResponse`。
  - 成功 emit `remote_subagent_complete` 返回 output；失败/超时 emit `remote_subagent_failed` 返回 `Error: ...`。
  - `set_caller` / `reset_task_state`（仿 SubAgentTool）。
- 复用 `tools/mcp/client.py:MCPClientManager.call_tool` / transport；不重写传输层。

### 3. Orchestrator 注册（`agents/orchestrator.py`）
- 在 SubAgent/Handoff 注册块旁，`if config.REMOTE_SUBAGENT_ENABLED:` 用 `REMOTE_AGENT_SERVER_JSON` 构造 `MCPServerConfig` → `RemoteSubAgentTool(...)`，append 到 `tools`（进入各 ReAct 引擎工具集）。`run()` 起始 `reset_task_state()`。
- 远端工具是"控制权返回式"（结果回灌父 loop，非 handoff 的 `is_handoff`），无需改 ReActEngine。

### 4. main.py 服务端接线
- `_start_mcp_server_background`：当 `config.MCP_SERVER_EXPOSE_AGENT`，给 `MCPServerWrapper` 传 `llm_client=LLMClient()` + `expose_agent=True`。
- 注：agent server 需运行中（`MCP_SERVER_ENABLED=true` + `MCP_SERVER_EXPOSE_AGENT=true`，由 `run_interactive` 后台启动，或单独进程 `python -m tools.mcp.serve`（可后续补一个 serve 入口））。文档说明。

---

## Part B — v18.4 A2A 原型（AgentCard + 任务信封）

### 1. 新增 `a2a/` 模块
- **`a2a/models.py`**（pydantic）：
  - `AgentSkill`：`name`、`description`。
  - `AgentCard`：`name`、`description`、`version`（取 `config.VERSION`）、`skills: list[AgentSkill]`、`endpoint: dict`（transport/url/command）、`auth: str = "local"`、`protocol: str = "a2a-prototype/0.1"`。
  - `A2ATaskRequest`：`task_id`、`input`、`context: str = ""`、`metadata: dict = {}`。
  - `A2ATaskResponse`：`task_id`、`status: str`（completed/failed）、`output: str = ""`、`error: str = ""`。
- **`a2a/client.py`**：`A2AClient`
  - `__init__(server_config, client_manager=None)`：复用/构造 `MCPClientManager`。
  - `async fetch_agent_card() -> AgentCard`：调用远端 `get_agent_card`，解析 JSON。
  - `async run_task(request: A2ATaskRequest) -> A2ATaskResponse`：调用远端 `a2a_run_task(input, context, task_id)`，解析响应。
  - 失败/超时返回 `A2ATaskResponse(status="failed", error=...)`，不抛裸异常。
- **`a2a/__init__.py`**：导出模型 + `A2AClient`。

### 2. 服务端构造 AgentCard / 响应（`tools/mcp/server.py`）
- `get_agent_card`：skills 由 server SubAgent 可用工具集映射（每工具一个 AgentSkill：name=tool.name、description=tool.description）；endpoint 由 server host/port 或 stdio 配置回填；version=`config.VERSION`；auth="local"。
- `a2a_run_task`：构造 `A2ATaskRequest`(本地)→跑 SubAgent→`A2ATaskResponse`(status 由 `SubAgentStatus` 映射，output=summary_text，error=失败 issues)。

### 3. RemoteSubAgentTool 用 A2AClient（Part A.2 已接）
- `fetch_card=True` 时先 `fetch_agent_card`（emit `a2a_card_fetched`，记录可用 skills，用于日志/校验）；再 `run_task`。体现"先读卡、再发任务"的 A2A 交互。

---

## 配置（`config.py`）

| 变量 | 默认 | 用途 |
|---|---|---|
| `MCP_SERVER_EXPOSE_AGENT` | `false` | 服务端暴露 `get_agent_card` + `a2a_run_task`（需 `MCP_SERVER_ENABLED`） |
| `REMOTE_SUBAGENT_ENABLED` | `false` | 客户端 `remote_subagent` 工具开关 |
| `REMOTE_AGENT_SERVER_JSON` | `""` | 远端 agent server 的 MCPServerConfig 内联 JSON（transport/url 或 command） |
| `REMOTE_SUBAGENT_MAX_CALLS_PER_TASK` | `2` | 单任务远端调用上限 |
| `REMOTE_SUBAGENT_TIMEOUT` | `=NODE_EXECUTION_TIMEOUT` | 远端任务超时（长任务可调大） |
| `REMOTE_AGENT_FETCH_CARD` | `true` | 调用前是否先拉取 AgentCard |

事件：`remote_subagent_start/complete/failed`、`a2a_card_fetched`（main.py Rich 渲染）。

---

## 复用清单

| 复用对象 | 位置 | 用途 |
|---|---|---|
| `MCPClientManager`（discover/call/transport） | `tools/mcp/client.py` | A2AClient 底层调用，不重写传输 |
| `MCPServerConfig` / `load_mcp_bridge_config` 解析 | `tools/mcp/config.py` | 远端 server 配置解析 |
| `open_transport`（stdio/streamable_http 工厂） | `tools/mcp/transport.py` | 传输可配 |
| `MCPServerWrapper` FastMCP 注册 | `tools/mcp/server.py` | 加 agent 端点，复用 add_tool |
| `SubAgent`（depth=1, summary-only） | `agents/subagent.py` | 远端执行体 |
| SubAgentTool 限次/set_caller/reset 模式 | `tools/subagent_tool.py` | RemoteSubAgentTool 照搬 |
| `_start_mcp_server_background` / `_build_tools` | `main.py` | 服务端 + 客户端工具接线 |

---

## 验证方法（本轮不写单测、不跑评测，确保编译 + 冒烟）

1. **静态编译**：`python3 -m py_compile config.py a2a/models.py a2a/client.py tools/remote_subagent_tool.py tools/mcp/server.py agents/orchestrator.py main.py`
2. **模型/构造冒烟（无网络、无 LLM）**：构造 `AgentCard`/`A2ATaskRequest`/`A2ATaskResponse` 序列化往返；`A2AClient` / `RemoteSubAgentTool` 用一个假的 MCPServerConfig 构造成功。
3. **服务端注册冒烟**：`MCPServerWrapper(tools=[...], llm_client=dummy, expose_agent=True)`，`get_registered_tool_names()` 含 `get_agent_card` + `a2a_run_task`；`expose_agent=False` 时不含。
4. **默认零副作用**：`MCP_SERVER_EXPOSE_AGENT=false` + `REMOTE_SUBAGENT_ENABLED=false`（默认）时，server 与 orchestrator 行为与现状一致。
5. **回环往返（需 API key + 运行 server，手动/集成）**：`MCP_SERVER_ENABLED=true MCP_SERVER_EXPOSE_AGENT=true` 起交互；配 `REMOTE_SUBAGENT_ENABLED=true REMOTE_AGENT_SERVER_JSON='{"transport":"streamable_http","url":"http://127.0.0.1:8080/mcp"}'`；任务中触发 `remote_subagent` → 观察 `a2a_card_fetched` + `remote_subagent_complete`。
6. **文档同步**：`CLAUDE.md` 架构图（远端 agent + A2A）、模块角色（`a2a/`、remote_subagent_tool、server agent 端点）、配置表（6 变量）、关键实现注记（远端 SubAgent 防递归 #22、A2A 信封 #23）。

---

## 不在本版范围

- v18.5 多智能体评测脚手架（handoff/remote 指标镜像 subagent、`multi_agent` suite/variant、与 single-agent baseline 对比）——下一轮（已勘明集成点：`evaluation/{probe,metrics,suites,variants,benchmark}.py`）。
- 完整 Google A2A 规范（JSON-RPC、streaming、push、开放网络发现、鉴权）——仅本地可信原型。
- 远端 agent 的 checkpoint/resume、多跳 A2A、远端并发池——后续。
- 单元测试与评测运行——用户后续整体进行。
