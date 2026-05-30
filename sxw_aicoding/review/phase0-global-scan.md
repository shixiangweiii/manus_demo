# Phase 0 — 全局横切扫描 findings

日期：2026-05-30 · 范围：105 个源码 .py（excl backups/tests/pycache）

## 扫描项与结果

### 1. config 漂移（复现 main.py `import config` 缺陷类）—— ✅ 干净
AST/正则扫描"使用 `config.X` 但未 `import config`"+"`config.X` 未在 config.py 定义"。
所有候选经人工核验**均为误报**：
- `tools/mcp/transport.py`、`bridge_tool.py`：`config` 是函数参数 / `client_manager.config` 属性，非模块。
- `evaluation/benchmark.py`：`"config.json"` 等任务描述字符串字面量。
- `conftest.py`：pytest 的 `config` fixture。
- `output_guardrail.py` 的 `config.SENSITIVE_KEYS`：仅出现在 docstring（指 `tracing.config.SENSITIVE_KEYS`），代码实际用 `config.GUARDRAIL_OUTPUT_MODE` 且已 `import config`。
- `TRACING_ENABLED`/`TRACING_MAX_ATTRIBUTE_LENGTH`：config.py 中**带类型注解**定义（`: bool =`），扫描正则漏匹配 → 实际已定义。
**结论**：除本会话已修的 `main.py` 外，无同类缺陷。

### 2. 特性开关默认值审计 —— ✅ 通过
所有新能力主开关默认 `false`：GUARDRAILS / HANDOFF / REMOTE_SUBAGENT / SELF_EVOLUTION / AGENTIC_MEMORY / SUBAGENT / HITL / MCP_BRIDGE / MCP_SERVER / MCP_SERVER_EXPOSE_AGENT / ENABLE_GOAL_DRIVEN_PLANNER / ENABLE_REASONING_ENGINE / TRACING。
默认 `true` 者均为核心/显式触发：`EMERGENT_PLANNING_ENABLED`、`TASK_RESUME_ENABLED`、`WORKFLOW_ENABLED`（仅经 `--workflow`/`run_workflow` 显式触发）。符合"新特性默认关"约定。

### 3. 编译 —— ✅ 通过
`compileall` 覆盖全部源码包 + root，0 错误。

### 4. 调试债务标记 —— 基本干净
TODO/FIXME/XXX/HACK：FIXME=XXX=HACK=0；"TODO" 的 274 处全部是 **TODO-list 功能词汇**（emergent planner 的 TODO 列表 / `MAX_TODO_RETRIES` 等），非债务标记。
唯一真实债务 TODO：见 F0.1。

### 5. CLAUDE.md 模块清单 vs 实际 —— ✅ 当前
evolution/、workflow/、a2a/、guardrails/ 均已在本会话同步进 CLAUDE.md 模块角色；无遗漏。

## Findings
- **F0.1 (P3, backlog)** `memory/service.py:125` — 陈旧 `# TODO: v16 启用 LLM 辅助巩固`。`config.MEMORY_LLM_CONSOLIDATION_ENABLED` 已定义但未被任何代码读取；`consolidate_task` 仍为确定性实现。建议：要么实现 LLM 辅助巩固，要么删注释 + 文档标注该 flag 为预留。**不影响正确性，入 backlog。**

## 结论
Phase 0 **无 P0/P1**。全局健康度良好：配置无漂移、开关默认安全、编译干净、债务极少。可进入 Phase 1（最高风险：chokepoint & 共享 ReAct 引擎）。
