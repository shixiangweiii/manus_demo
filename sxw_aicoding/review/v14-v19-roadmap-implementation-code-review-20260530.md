# v14-v19 Roadmap 全阶段实现代码评审

日期：2026-05-30  
范围：基于 `sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md` 与当前源码，对已完成的 v15-v19 主线能力做快速全面代码评审。  
方式：只读评审；未修改源码。重点检查 roadmap 能力是否真实接入运行链路、失败语义是否闭环、评测是否能证明实现效果。

## 总体结论

当前实现已经覆盖了 roadmap 要求的大部分模块形态：Agentic Memory、MCP Bridge/Server、Self-Evolution、Workflow/Handoff/Remote SubAgent/A2A、Guardrails、Red-Team/Multi-Agent/MCP/Memory 评测脚手架均已有对应源码。

但从运行链路看，还存在几处会影响阶段验收的关键问题：

- v15 Memory Tools 注册顺序错误，导致常规 ReAct agent 实际拿不到 memory tools。
- v19 Guardrails 没有覆盖 v18.1 WorkflowEngine，确定性 workflow 可以绕过护栏。
- v16 MCP Server 暴露工具时没有正确暴露 BaseTool schema，外部 MCP client 看到的参数 schema 失真。
- v16 MCP 评测链路没有真正完成 discovery/execution 埋点，suite 结果会失真。
- 基础 FileOpsTool 仍有沙箱路径 sibling-prefix 逃逸问题。

## Findings

### P0: v15 Memory as Tool 没有真正接入常规 ReAct 路径

证据：

- `agents/orchestrator.py:221-234` 先构造 `PlannerAgent`、`ExecutorAgent`、`EmergentPlannerAgent`、`GoalDrivenPlannerAgent`。
- `agents/orchestrator.py:271-282` 之后才把 `MemorySearchTool`、`MemoryStoreTool`、`MemoryConsolidateTool`、`MemoryRevokeTool` 追加到局部 `tools` 列表。
- `agents/executor.py:97-100` 和 `agents/emergent_planner.py:113-117` 在初始化时已经把工具列表复制为 `self.tools` / `self.tool_schemas` / `ToolRouter`。

影响：

`MEMORY_TOOLS_ENABLED=true` 时，`memory_search` / `memory_store` / `memory_revoke` 只进入 `self._workflow_tools`，不会进入常规 Executor/EmergentPlanner/GoalDrivenPlanner 的 ReAct 工具集。roadmap v15 的 “Memory as Tool” 和 `memory_agentic` suite 中 `expected_tools=["memory_store"]`、`expected_tools=["memory_search"]` 的验收目标会失效。

建议：

把 memory tools 注册提前到所有执行型 agent 构造之前，或者在追加后重建 executor/emergent/goal-driven agent 的工具表。更推荐统一成 `_augment_tools()` 装配阶段，先完成 SubAgent/HITL/Handoff/Remote/Memory 的完整工具集，再创建各执行 agent。

### P0: v19 Guardrails 没有保护 v18.1 WorkflowEngine

证据：

- `agents/orchestrator.py:473` 在 `run_workflow()` 中调用 `_wire_guardrail_runtime()`。
- `workflow/engine.py:84-99` 直接执行 `tool.traced_execute(**resolved)`，只按 `Error:` 失败。
- `react/engine_helpers.py:98-150` 中的 tool-input / tool-output guardrail 逻辑只在 `execute_tool_calls()` 里执行。

影响：

Workflow JSON 可以绕过：

- `GUARDRAIL_TOOL_ENABLED` 的危险 shell/python 参数拦截。
- `GUARDRAIL_WRITE_CONFIRM` 的写操作确认。
- `GUARDRAIL_INPUT_ENABLED` 的工具输出注入中和。

这与 roadmap v19 “tool/context/output guardrails” 的全局防护目标冲突，尤其因为 workflow 是显式的确定性工具执行入口。

建议：

抽出一个不依赖 OpenAI tool_call 对象的共享 `guarded_execute_tool(tool_name, params, ...)`，让 `WorkflowEngine` 和 `execute_tool_calls()` 共用。至少在 `WorkflowEngine` 执行前调用 `current_guardrail().check_tool_input()`，执行后调用 `scan_tool_output()`。

### P1: MCP Server 暴露 BaseTool 时 schema 失真

证据：

- `tools/mcp/server.py:89-95` 用 `FastMCP.add_tool(fn=handler, name=..., description=...)` 注册工具。
- `tools/mcp/server.py:101-110` 生成的 handler 签名是 `async def handler(**kwargs)`。
- 本地验证 FastMCP 注册出的参数 schema 只有 `kwargs` 字段，而不是 BaseTool 的 `parameters_schema`。

影响：

外部 MCP client 通过 `list_tools()` 看到的参数 schema 不是真实工具 schema。对于 `file_ops(action, filename, content)`、`execute_shell(command)` 等工具，MCP client 很可能生成错误参数，或把参数包进 `kwargs`，导致调用失败。

建议：

不要用裸 `**kwargs` 作为 FastMCP 公开 schema。可选方案：

- 使用 MCP SDK 支持的显式 schema 注册方式。
- 为 BaseTool 动态生成带签名的 wrapper。
- 或在 MCP server 层直接返回 `Tool` 元数据时注入 `tool.parameters_schema`，并在 call 层拆包兼容。

同时补一个真实断言：`MCPServerWrapper(...).list_tools()` 的 schema 必须包含 BaseTool 的字段名，而不是只包含 `kwargs`。

### P1: v16 MCP 评测链路没有真正测到 MCP Bridge

证据：

- `evaluation/runner.py:97-108` 默认只构造基础工具，没有复用 `main._build_tools()` 或 MCP discovery。
- `evaluation/probe.py:494-502` 期待 `mcp_tools_discovered`、`mcp_tool_executed`、`mcp_schema_error` 事件。
- 全仓搜索没有发现生产代码 emit 这些 MCP 事件。
- `main.py:877-884` 只有正常 CLI 路径会调用 `_discover_mcp_bridge_tools()`。

影响：

`mcp_bridge` suite 的任务和指标会出现“配置打开但工具未发现/未执行”的假阴性或空指标，无法证明 v16 的收益，也无法捕获 MCP schema/call 错误。

建议：

- 将 tool building 抽成 evaluation 可复用的 async builder，或让 EvaluationRunner 在 `MCP_BRIDGE_ENABLED` 时执行 MCP discovery。
- 在 `_discover_mcp_bridge_tools()` / `MCPBridgeTool.execute()` 中 emit `mcp_tools_discovered` 和 `mcp_tool_executed`。
- 将 schema adapter metrics 接入 probe，或在 discovery 阶段显式 emit schema conversion errors。

### P1: FileOpsTool 沙箱路径校验存在 sibling-prefix 逃逸

证据：

- `tools/file_ops.py:87-96` 使用 `path.startswith(os.path.realpath(self._sandbox))` 判断是否在沙箱内。
- `config.py:74` 默认沙箱为 `~/.manus_demo/sandbox`。
- 路径 `~/.manus_demo/sandbox_escape/probe.txt` 会通过这个前缀判断。

影响：

当 `file_ops` 接收到绝对路径或特殊相对路径时，可能读写沙箱 sibling 目录。虽然 v19 `ToolGuardrail._within_sandbox()` 使用了更严格的 `target == sandbox or target.startswith(sandbox + os.sep)`，但 guardrails 默认关闭，基础工具自身仍应具备正确隔离。

建议：

把 `FileOpsTool._safe_path()` 改成与 `guardrails/tool_guardrail.py` 一致的边界判断：

```python
sandbox = os.path.realpath(self._sandbox)
target = os.path.realpath(os.path.join(sandbox, filename))
return target if target == sandbox or target.startswith(sandbox + os.sep) else None
```

并补绝对路径、`../`、sibling-prefix 三类单测。

### P2: Memory tool 失败语义与评测计数不一致

证据：

- `tools/memory_tools.py:83-86` `MemorySearchTool` 只 emit `memory_search_result`，没有 emit `memory_search_start`。
- `evaluation/probe.py:485-488` `memory_search_count` 只在 `memory_search_start` 上递增，`memory_hit_count` 在 `memory_search_result` 上递增。
- `tools/memory_tools.py:284-288` `MemoryRevokeTool` 找不到记录时返回 JSON `{"status": "error"}`，不是 `Error:`。
- `react/tool_call_helpers.py:82-84` 只把 `Error:` 前缀结果识别为失败。

影响：

Memory tool 的调用次数与失败次数会被低估或误判成功，影响 `memory_agentic` suite 的效果判断，也会让 ToolRouter 无法对错误 revoke 触发失败提示。

建议：

- `MemorySearchTool.execute()` 入口 emit `memory_search_start`。
- `MemoryRevokeTool` 找不到记录时返回 `Error: Memory record ... not found`。
- 或统一扩展 `classify_result()` 支持结构化 JSON error，但当前项目约定明显是 `Error:`。

### P2: Agentic Memory 检索与迁移还有质量边角

证据：

- `memory/agentic_store.py:272-275` legacy migration 只要发现任一 `source=="legacy"` 就整体跳过。
- `memory/service.py:223-226` 中文关键词也使用 `\b...\b` 做边界匹配。

影响：

- 如果 legacy migration 曾部分失败，或旧 `memory.json` 后续新增记录，后续迁移会直接跳过。
- 中文任务标签如 `数据分析`、`机器学习`、`前端`、`后端` 在连续中文文本中可能无法稳定提取，削弱后续检索和经验分类。

建议：

- legacy migration 使用可复算 legacy id 或 metadata 记录源条目 timestamp/task，按条目去重。
- 中文关键词走 substring 匹配，英文关键词继续用 word boundary。

## 验证记录

已运行静态编译：

```bash
python3 -m py_compile memory/models.py memory/agentic_store.py memory/service.py tools/memory_tools.py tools/mcp/config.py tools/mcp/client.py tools/mcp/server.py tools/mcp/bridge_tool.py evolution/models.py evolution/learner.py workflow/models.py workflow/engine.py agents/specialist.py tools/handoff_tool.py a2a/models.py a2a/client.py tools/remote_subagent_tool.py guardrails/models.py guardrails/engine.py guardrails/tool_guardrail.py guardrails/input_guardrail.py guardrails/output_guardrail.py agents/orchestrator.py react/engine.py react/engine_helpers.py evaluation/runner.py evaluation/probe.py evaluation/metrics.py evaluation/variants.py
```

结果：通过。

已运行 v15/v16 相关单测：

```bash
python3 -m pytest tests/test_agentic_memory.py tests/test_memory_tools.py tests/test_orchestrator_memory.py tests/test_evaluation_memory.py tests/test_mcp_config.py tests/test_mcp_schema_adapter.py tests/test_mcp_bridge.py tests/test_mcp_client_manager.py tests/test_mcp_server.py -q -o asyncio_mode=auto
```

结果：`130 passed`。存在 1 个 warning：

- `tests/test_mcp_client_manager.py::TestMCPClientManagerDiscover::test_discover_continues_on_failure`
- warning 内容为 mock coroutine 未 await，说明该测试 mock 形态本身不够严谨，但不影响本次发现的主问题。

## 建议修复顺序

1. 先修 v15 memory tools 注册顺序。这是 roadmap v15 验收和后续 self-evolution / red-team memory poisoning 的基础。
2. 修 WorkflowEngine guardrail 覆盖，避免新增 workflow 成为安全绕过路径。
3. 修 MCP Server schema 暴露，再补真实 schema 测试。
4. 修 EvaluationRunner 的 MCP discovery 与 MCP 事件埋点，否则 v16 suite 不可信。
5. 修 FileOpsTool 沙箱边界。
6. 清理 memory tool 失败语义、中文 tag、legacy migration 这些质量边角。

## 残余风险

本次评审是快速全面 review，不是完整端到端跑评测。尤其以下路径仍建议后续单独验证：

- `memory_agentic` suite 在 `agentic_memory_on` 下是否真的触发 memory tools。
- `mcp_bridge` suite 是否能在 mock MCP server 启动时发现并调用工具。
- `red_team` suite 的 `guardrails_on` vs baseline 是否能同时降低 attack success 且不显著提高 benign block。
- `multi_agent` suite 中 handoff/subagent 的 ROI 是否真实优于 baseline。
