# v18 Multi-Agent 实施方案（第一版：v18.1 Workflow Engine + v18.2 Handoff）

目标产物：`sxw_aicoding/plan/v18-multi-agent-plan.md`（实施时落盘）
生成日期：2026-05-29
适用阶段：v18 - Multi-Agent + 双引擎显式化（依赖 v14.6 + v15 + v16，均已完成）

---

## Context（为什么做这件事）

路线图 §10 把 v18 定位为"把 depth=1 SubAgent 扩展为更清晰的多 agent 架构，同时显式区分确定性 Workflow 与自主 Agentic Loop"。当前代码事实：

- **没有确定性 Workflow 引擎**：`dag/executor.py` 已有确定性 super-step 调度，但**每个节点都是 LLM/ReAct 驱动**（`executor.py:290-315` → `executor.execute_node`）。不存在"工具步骤序列、无每步 LLM 推理"的 workflow，无法体现 Anthropic "workflow vs agent" 的区分。
- **只有隔离式 SubAgent，没有 Handoff**：`agents/subagent.py` + `tools/subagent_tool.py` 是 depth=1、summary-only、**不传父上下文**（`context=""`，subagent_tool.py:246）、结果是压缩摘要、不能调 ask_user。缺少"上下文传递 + 控制权转移"的专家委派原语。
- **MCP server 只暴露 tools**（`tools/mcp/server.py`，无 agent/task 端点）——这是 v18.3/18.4 的前置，本版不做。

**用户已确认的范围与决策**：
1. 本轮只做 **v18.1 Workflow Engine + v18.2 Handoff**；18.3 Remote SubAgent / 18.4 A2A / 18.5 多智能体评测后续。
2. v18.1 = **确定性工具工作流**：声明式工具步骤 DAG，由 `WorkflowEngine` 确定性执行，无每步 LLM。
3. v18.2 Handoff = **上下文传递 + 控制权转移**：专家 agent 拿到调用方上下文后接管，其结果直接成为最终答案（OpenAI Agents SDK 风格），与 SubAgent summary-only 隔离式委派互补。

---

## Part A — v18.1 Workflow Engine（确定性工具工作流）

### 设计总览

```text
显式双引擎：
  OrchestratorAgent.run(task)          → 自主 Agentic Loop（classify → simple/DAG/emergent）  [现状]
  OrchestratorAgent.run_workflow(spec) → 确定性 Workflow（WorkflowEngine，无 LLM 每步）       [新增]

[workflow/engine.py] WorkflowEngine.execute(spec)
   ├── 校验 DAG（缺失依赖 / 环检测）
   ├── 拓扑序遍历 WorkflowStep
   │     ├── 解析参数模板 ${step_id} → 前序步骤输出
   │     ├── tool.traced_execute(**params)   ← 复用 BaseTool，无 LLM
   │     └── 记录输出 + emit 事件
   └── 返回 WorkflowResult（success / step_results / final_output / failed_step）
```

### 实施变更

**1. 新增 `workflow/` 模块**

- **`workflow/models.py`**（pydantic）：
  - `WorkflowStep`：`id: str`、`tool: str`、`params: dict[str, Any]`、`depends_on: list[str] = []`
  - `WorkflowSpec`：`name: str`、`description: str = ""`、`steps: list[WorkflowStep]`
  - `WorkflowResult`：`success: bool`、`step_results: dict[str, str]`、`final_output: str`、`failed_step: str = ""`
- **`workflow/engine.py`**：`WorkflowEngine`
  - `__init__(self, tools: dict[str, BaseTool] | list[BaseTool], on_event=None)`
  - `async def execute(self, spec: WorkflowSpec) -> WorkflowResult`：
    - 拓扑排序（Kahn，复用思路；缺失依赖/环 → 返回失败，emit `workflow_failed`）。
    - 逐步：`_resolve_params(params, step_results)` 把字符串值里的 `${dep_id}` 替换为前序输出 → `tool = self.tools[step.tool]` → `await tool.traced_execute(**resolved)`（复用 `BaseTool.traced_execute`，自动 tracing）。
    - 工具缺失 / 结果 `startswith("Error:")` → fail-fast：标记 `failed_step`，停止，emit `workflow_step_failed`。
    - emit `workflow_start` / `workflow_step_start` / `workflow_step_complete` / `workflow_complete`。
    - `final_output` = 最后一个成功步骤输出（或可在 spec 标注，但 v1 取末步）。
  - `_resolve_params`：仅对 str 类型 value 做 `${id}` 整串/子串替换；非字符串原样传递。
  - `_topo_order` / cycle 检测。
- **`workflow/loader.py`**：`load_workflow_spec(path: str) -> WorkflowSpec`（读 JSON → pydantic 校验）。
- **`workflow/__init__.py`**：导出 `WorkflowEngine` / `WorkflowSpec` / `WorkflowStep` / `WorkflowResult` / `load_workflow_spec`。

**2. Orchestrator 接线（`agents/orchestrator.py`）**
- `__init__` 末尾捕获工具集供 workflow 使用：`self._workflow_tools = {t.name: t for t in (tools or [])}`（在所有工具增强之后）。
- 新增 `async def run_workflow(self, spec: WorkflowSpec) -> str`：
  - `if not config.WORKFLOW_ENABLED: raise/return` 提示。
  - `self._emit("task_start", {"task": spec.name, "task_id": ..., "mode": "workflow"})`。
  - `engine = WorkflowEngine(self._workflow_tools, on_event=self._emit)`；`result = await engine.execute(spec)`。
  - 收尾：token usage summary、`task_complete`，返回 `result.final_output`。
  - **不**走 classify / reflection / self-evolution（确定性引擎，显式区分于 agentic）。

**3. CLI（`main.py`）**
- `main()` 新增 `--workflow <path>` 分支（在 `--resume` 之后）：`asyncio.run(run_workflow_file(path))`。
- 新增 `async def run_workflow_file(path)`：`load_workflow_spec(path)` → `_build_tools()` → `OrchestratorAgent(interactive=False)` → `await orchestrator.run_workflow(spec)`。
- 事件渲染：`workflow_start` / `workflow_step_start` / `workflow_step_complete` / `workflow_step_failed` / `workflow_complete` 的 Rich 分支。

**4. 配置（`config.py`）**：`WORKFLOW_ENABLED = os.getenv("WORKFLOW_ENABLED", "true")...`（仅经 `--workflow` / `run_workflow` 显式触发，默认 true 不影响既有 agentic 路径）。

---

## Part B — v18.2 Handoff（上下文传递 + 控制权转移）

### 设计总览

```text
某 ReAct loop（Executor / Emergent / GoalDriven）执行中：
   LLM 调用 handoff(target_specialist, task, context)
        ↓
   [HandoffTool.execute]  is_handoff=True
        └── SpecialistAgent(role prompt + 受限工具 + 可选 ask_user)
              ├── 收到调用方传入的 context 简报 + task（≠ SubAgent 的 context=""）
              ├── 跑自己的 ReActEngine（独立 messages）
              └── 返回【完整输出】（≠ SubAgent 压缩摘要）
        ↓
   [ReActEngine] 识别 handoff 成功 → 终止本 loop，specialist 输出即最终答案（控制权转移）
```

与 SubAgent 的对比（互补）：

| 维度 | SubAgent（v9，隔离） | Handoff（v18.2，传递+转移） |
|---|---|---|
| 上下文 | `context=""` 隔离 | 传入 context 简报 |
| 返回 | 压缩 summary | 完整输出 |
| 控制权 | 父继续 + 综合 | 转移，结果即答案 |
| ask_user | 禁止 | 可显式配置开启 |
| 系统提示 | 通用 subagent | 角色专属（researcher/coder/writer） |
| 递归 | depth=1 | 不可再 handoff（同 depth=1） |

### 实施变更

**1. 专家注册表 + 专家 agent（`agents/specialist.py`，新增）**
- `SpecialistSpec`（dataclass/pydantic）：`name`、`description`、`system_prompt`、`default_tools: list[str]`。
- `SPECIALIST_REGISTRY: dict[str, SpecialistSpec]`：内置 `researcher`（web_search/fetch_url）、`coder`（execute_python/file_ops/execute_shell）、`writer`（综合，少工具）。代码级注册表，后续可配置化。
- `SpecialistAgent`：
  - `__init__(name, spec, llm_client, available_tools, context_manager, on_event, allow_ask_user, interactive, parent_name)`。
  - 工具白名单 = `spec.default_tools ∩ available_tools`，强制剔除 `handoff` / `subagent`；`ask_user` 仅当 `allow_ask_user and interactive`。
  - 系统提示 = `build_system_prompt(spec.system_prompt, inject_context=True, inject_subagent_guidance=False, inject_hitl_guidance=allow_ask_user)`（复用 `agents/prompt_utils.py`）。
  - `async def run(self, task, context="") -> str`：构造私有 `ReActEngine`（同 SubAgent 模式，独立 messages），`execute(prompt=task, context=context, system_hint=self.system_prompt, effort=...)`，返回 `StepResult.output`（完整）。

**2. Handoff 工具（`tools/handoff_tool.py`，新增）**
- `HandoffTool(BaseTool)`：类属性 `is_handoff = True`（供 ReActEngine 识别控制权转移）。
- `parameters_schema`：`target_specialist`（enum = 注册表 keys）、`task`（string，必填）、`context`（string，选填，调用方传递的背景简报）。
- `execute(**kwargs)`：
  - 校验 `target_specialist ∈ 注册表`，否则返回 `Error:`。
  - per-task 调用上限 `HANDOFF_MAX_CALLS_PER_TASK`（计数 + 复位，仿 `subagent_tool.py:130-161`）。
  - `local_parent = self._parent_name`（await 前本地捕获，仿 subagent 反并发覆盖）。
  - 构造 `SpecialistAgent` → `asyncio.wait_for(agent.run(task, context), timeout=HANDOFF_TIMEOUT)` → 返回完整输出。
  - emit `handoff_start` / `handoff_complete` / `handoff_failed`。
  - `set_caller(name)` + `reset_task_state()`（仿 SubAgentTool）。

**3. ReActEngine 控制权转移钩子（`react/engine.py`，最小改动）**
- `__init__`：`self._handoff_tool_names = {n for n, t in self.tools.items() if getattr(t, "is_handoff", False)}`（默认空集 → 零行为变更）。
- `execute()` 在 `execute_tool_calls`（engine.py:296-307）之后、`on_iteration` 之前：若 `self._handoff_tool_names` 且本轮 `response_msg.tool_calls` 含 handoff，从 `tool_messages` 按 `tool_call_id` 取其结果；结果非 `Error:` → `return StepResult(success=True, output=<handoff_result>, ...)`（终止 loop = 控制权转移）；失败则不转移、继续循环。
  - 实现注意：需确认 `execute_tool_calls` 返回的 message dict 含 `tool_call_id`（`react/engine_helpers.py`，实施时核对；标准 OpenAI tool message 含此字段）。
  - 该改动被 `_handoff_tool_names` 非空门控，SubAgent/Specialist 的私有 engine 不含 handoff 工具 → 不受影响。

**4. BaseTool 标记位（`tools/base.py`）**：新增类属性 `is_handoff: bool = False`（默认；HandoffTool 覆盖为 True）。

**5. SubAgent 隔离加固（`tools/subagent_tool.py`）**：把 `"handoff"` 加入被屏蔽工具集（现有 `{subagent, ask_user, memory_store, memory_revoke}`），确保 SubAgent 不能 handoff。

**6. Orchestrator 注册（`agents/orchestrator.py`）**：在 SubAgent 注册块（orchestrator.py:137-151）旁，`if config.HANDOFF_ENABLED:` 构造 `HandoffTool(llm_client, available_tools=dict, context_manager, on_event=self._emit, allow_ask_user=config.HANDOFF_ALLOW_ASK_USER, interactive=interactive, parent_name="OrchestratorAgent")` 并 append 到 `tools`（在创建 sub-agents 之前，使其进入各 ReAct 引擎工具集）。`run()` 起始处 `reset_task_state()`（仿 subagent）。

**7. 配置（`config.py`）**
| 变量 | 默认 | 用途 |
|---|---|---|
| `HANDOFF_ENABLED` | `false` | v18.2 主开关 |
| `HANDOFF_ALLOW_ASK_USER` | `false` | 专家 agent 是否可调 ask_user（路线图要求显式配置） |
| `HANDOFF_MAX_CALLS_PER_TASK` | `2` | 单任务 handoff 调用上限 |
| `HANDOFF_TIMEOUT` | `=NODE_EXECUTION_TIMEOUT` | 专家执行超时 |
| `HANDOFF_MAX_ITERATIONS` | `=MAX_REACT_ITERATIONS` | 专家 ReAct 迭代上限 |

**8. `main.py` 事件渲染**：`handoff_start` / `handoff_complete` / `handoff_failed` 的 Rich 分支。

---

## 复用清单

| 复用对象 | 位置 | 用途 |
|---|---|---|
| `BaseTool.traced_execute` | `tools/base.py:60` | Workflow 步骤执行 + tracing（无 LLM） |
| `ReActEngine`（私有实例模式） | `react/engine.py:52` | SpecialistAgent 复用，独立 messages |
| `build_system_prompt` | `agents/prompt_utils.py:188` | 专家角色 prompt + ask_user 引导门控 |
| SubAgentTool 防并发/限流/set_caller 模式 | `tools/subagent_tool.py:127,130-161,317` | HandoffTool 直接照搬模式 |
| `execute_tool_calls` 返回的 tool_messages | `react/engine_helpers.py` | 取 handoff 结果做控制权转移 |
| `_emit` 多播 | `agents/orchestrator.py` | workflow/handoff 事件 → UI/Tracing/Eval |
| `_build_tools` / `run_single` 模式 | `main.py:751,771` | `run_workflow_file` 照搬装配 |
| DAG 拓扑/环检测思路 | `dag/graph.py:219` | WorkflowEngine 拓扑排序参考 |

---

## 验证方法（本版不写单测、不跑评测，只确保编译 + 冒烟）

1. **静态编译**：`python3 -m py_compile config.py tools/base.py tools/handoff_tool.py tools/subagent_tool.py react/engine.py agents/specialist.py agents/orchestrator.py workflow/models.py workflow/engine.py workflow/loader.py main.py`
2. **导入冒烟**：`python3 -c "from workflow.engine import WorkflowEngine; from agents.specialist import SpecialistAgent, SPECIALIST_REGISTRY; from tools.handoff_tool import HandoffTool; from agents.orchestrator import OrchestratorAgent; print('ok')"`
3. **Workflow 冒烟**（无 LLM）：用一个仅含 `execute_python`/`file_ops` 的两步 spec（step2 用 `${step1}` 引用 step1 输出），构造 `WorkflowEngine` 直接 `execute(spec)`，确认确定性产出 + 参数模板替换 + 事件触发。
4. **Handoff 控制权转移单元冒烟**：mock 一个 `is_handoff=True` 的假工具返回固定文本，喂给 `ReActEngine`（mock LLM 让其调用该工具），确认 loop 终止且 `StepResult.output` == 假工具返回（成功转移）；返回 `Error:` 时不转移、继续。
5. **默认零副作用**：`HANDOFF_ENABLED=false`（默认）时 `_handoff_tool_names` 为空、ReActEngine 行为与现状一致；现有用例不受影响。
6. **CLI**：`python main.py --workflow <spec.json>`（需 API key 才跑真实工具，但加载/装配路径可在无 key 下走到工具执行边界）。
7. **文档同步**：`CLAUDE.md` 架构图（双引擎 + handoff）、模块角色（workflow/、specialist、handoff_tool）、配置表（6 个新变量）、Common Commands（`--workflow`）、关键实现注记（handoff 控制权转移钩子 #20、workflow 确定性引擎 #21）。

---

## 不在本版范围

- v18.3 Remote SubAgent（MCP server 加 agent/task 端点）、v18.4 A2A（Agent Card）、v18.5 多智能体评测 suite/baseline——后续。
- Workflow 的 checkpoint/resume、self-evolution、reflection 集成——确定性短任务，v1 保持精简。
- Handoff 的完整对话历史（messages）转移——v1 用调用方传入的 `context` 简报；richer message-history handoff 列为后续。
- 专家注册表配置化（JSON/env）——v1 代码级内置 researcher/coder/writer。
- 单元测试与评测验收——用户后续整体进行。
