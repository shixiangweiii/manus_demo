# v14 工作进展记录（5-24 更新）

> **最后更新**：2026-05-24
> **当前状态**：Phase 1-3 + Hardening + Phase 4 + Phase 4 Fix Pass 全部完成，613 条测试通过

---

## 一、本次完成工作

### 1. Phase 3 Hardening — 12 项评审问题修复

基于两轮深度代码评审（`v14-phase3-deep-code-review` + `second-pass`），确认并修复 12 项问题（2 P0 / 4 P1 / 5 P2 / 1 P3），563 条测试通过。

| # | 严重性 | 问题 | 修复 |
|---|---|---|---|
| 1 | P0 | API Message Sanitizer 缺失 | `llm/client.py` 新增 `_INTERNAL_MESSAGE_KEYS` + `_sanitize_messages_for_api()`，`chat/chat_with_tools/chat_json` 发送前剥离 `thinking_content` |
| 2 | P0 | `chat_json` fallback 吞异常 | `except Exception` → `except BadRequestError` + `response_format` 检查 |
| 3 | P1 | ReasoningEngine 无限循环防护 | 新增 `thinking_rounds` 计数器 + `MAX_THINKING_ROUNDS` 硬限（独立于 token tracking） |
| 4 | P1 | Budget guard 仅覆盖纯思考分支 | 统一 budget 检查前置到分支之前，tool-call 分支超限跳过执行，final-answer 分支允许返回 |
| 5 | P1 | ShellTool sandbox 绕过 | `_run_shell` 从 `@staticmethod` 改为实例方法，使用 `self._workdir` |
| 6 | P1 | ContextManager caller_tag 缺失 | `compress_if_needed()` / `_summarize()` 新增 `caller_tag` 参数，传递到 `llm_client.chat()` |
| 7 | P2 | BaseAgent 未传 caller_tag 到 compress | 三处 `think()/think_json()/think_with_tools()` 传入 `caller_tag=self.name` |
| 8 | P2 | Reflector guidance 泄漏工具提示 | `build_system_prompt()` 调用显式关闭所有工具 guidance |
| 9 | P2 | Planner guidance 泄漏 | 两处 planner prompt 构建显式关闭 location/search/HITL guidance |
| 10 | P2 | VERSION 版本号分散 | `config.py` 新增 `VERSION = "v14.0-dev"`，`main.py` + `tracing/config.py` 统一引用 |
| 11 | P3 | conftest.py 缺失 | 新建根 `conftest.py`，注册 `integration` marker |
| 12 | 测试 | 现有测试适配 | `test_token_attribution.py` lambda 签名适配；`test_concurrent_execution.py` 断言值修正 |

**新建测试**：`tests/test_api_sanitizer.py`（13 条）+ `tests/test_v14_reasoning_engine.py`（+6 条）+ `tests/test_shell_tool.py`（+3 条）

### 2. Phase 4 Batch 4.1 — DRY 重构

**新建** `react/engine_helpers.py`：提取 `execute_tool_calls()` 共享函数，封装 `_exec_one` 闭包 + `asyncio.gather` + ToolRouter 三态记账 + `truncate_for_llm` + ToolCallRecord 构建 + error marker 格式化。

从 4 个文件中各删除 ~60 行重复代码：

| 文件 | 删行数 | 替换为 |
|---|---|---|
| `react/engine.py` | ~70 行 | `execute_tool_calls(...)` + `messages.extend()` |
| `react/reasoning_engine.py` | ~70 行 | 同上，同时删掉 `import asyncio/json` 本地重导入 |
| `agents/emergent_planner.py` | ~55 行 | `execute_tool_calls(...)` + `for msg: self.add_tool_result()` |
| `agents/goal_driven_planner.py` | ~65 行 | `execute_tool_calls(...)` + `messages.extend()` |

EmergentPlanner 的 `self._parse_json()` 统一为 `json.loads` + except（tool_call arguments 始终是合法 JSON）。`attribute_caller(t, "EmergentPlanner")` → `attribute_caller(t, self.name)`。

**新建测试**：`tests/test_engine_helpers.py`（13 条）

### 3. Phase 4 Batch 4.2 — reasoning_effort 配置与流程

**新增** `ReasoningEffort` 枚举（LOW/MEDIUM/HIGH, str enum）到 `schema.py`。

**Planner → Orchestrator → Executor → Engine** 完整链路：

| 层 | 改动 |
|---|---|
| `schema.py` | 新增 `ReasoningEffort` 枚举 |
| `config.py` | 新增 `REASONING_EFFORT = "auto"` 配置（auto/low/medium/high） |
| `agents/planner.py` | `classify_task()` 返回 `tuple[str, ReasoningEffort]`；新增 `_classify_complexity()` / `_resolve_effort_override()` / `_effort_for_complexity()` |
| `agents/orchestrator.py` | 接收 `(complexity, effort)` 元组，传递到三条执行路径 |
| `agents/executor.py` | `execute_step()` / `execute_node()` 接受 `effort` 参数 |
| `react/engine.py` | `execute()` 接受 `effort` 参数，`_apply_effort()` 返回 (temperature, max_iterations) |
| `react/reasoning_engine.py` | 同上，LOW effort 额外限制 `effective_thinking_budget = min(2000)` |

**Effort 行为表**：

| 参数 | LOW | MEDIUM | HIGH |
|---|---|---|---|
| temperature | 0.3 | REACT_TEMPERATURE | 0.7 |
| max_iterations | max(3, N//2) | N | N |
| thinking_budget | min(2000) | MAX_THINKING_TOKENS | MAX_THINKING_TOKENS |
| truncation | 1000 | 2000 | 4000 |

**新建测试**：`tests/test_reasoning_effort.py`（14 条）

### 4. Phase 4 Batch 4.3 — ToolExecutionPolicy 策略可配置

**新增** `ToolExecutionPolicy` dataclass 到 `react/engine_helpers.py`：

```python
@dataclass
class ToolExecutionPolicy:
    truncation_limit: int = 2000
    error_prefix: str = "[TOOL ERROR]"
    include_alternatives_hint: bool = True
    error_retry_guidance: str = "IMPORTANT: ..."
    @staticmethod
    def default() -> ToolExecutionPolicy
    @staticmethod
    def for_effort(effort: ReasoningEffort) -> ToolExecutionPolicy
```

`execute_tool_calls()` 新增 `policy` 参数（可选，向后兼容 Batch 4.1 调用点）：
- `policy=None` → 使用传入的 `truncation_limit` 构建默认 policy
- `policy=for_effort(LOW)` → truncation=1000
- `policy=for_effort(HIGH)` → truncation=4000

4 个调用点均已接入：ReActEngine 和 ReasoningEngine 使用 `for_effort(effort)` 构建 policy；EmergentPlanner 和 GoalDrivenPlanner 使用 `default()` policy。

**新建测试**：`tests/test_engine_helpers.py`（+7 条 policy 测试）

### 5. Phase 4 Fix Pass — 7 项评审问题修复

基于 `v14-phase4-code-review-20260524.md` 评审报告，验证确认 6 个代码 bug + 1 个测试基础设施问题，全部修复。613 条测试通过。

| # | 严重性 | 问题 | 修复 |
|---|---|---|---|
| 1 | P1 | reasoning_effort 在 DAG 路径丢失 | `DAGExecutor.__init__()` 增加 `effort` 参数，`_run_node()` 传给 `execute_node(effort=self._effort)` |
| 2 | P1 | reasoning_effort 在 emergent/goal-driven 路径未使用 | EmergentPlanner/GoalDrivenPlanner 的 `execute()` 增加 `effort` 参数，内部使用 `for_effort(effort)` 替代 `default()`，temperature 基于 effort 调整 |
| 3 | P1 | ToolExecutionPolicy 覆盖全局 TOOL_RESULT_TRUNCATION_LIMIT | `default()` 和 `for_effort()` 改为读取 `config.TOOL_RESULT_TRUNCATION_LIMIT` 作为 base，LOW = `max(500, base//2)`，HIGH = `base*2` |
| 4 | P2 | EmergentPlanner JSON 参数解析退化 | `execute_tool_calls()` 增加 `parse_args` 参数，EmergentPlanner 传入 `self._parse_json_for_tool_args`（兼容 markdown fenced JSON） |
| 5 | P2 | ReasoningEngine MEDIUM 用错温度 | ReasoningEngine 覆写 `_apply_effort()`，MEDIUM 返回 `config.REASONING_TEMPERATURE` |
| 6 | P2 | MAX_THINKING_ROUNDS 累计 vs 连续 | `thinking_rounds` 在 `has_tool_calls` 和 `has_final_answer` 分支重置为 0 |
| 7 | P2 | DDGS 测试隔离 | `conftest.py` 增加 session-scoped autouse fixture，全局 patch `ddgs.DDGS` 返回空结果 |

**新增/更新测试**：`test_engine_helpers.py`（+8 条），`test_reasoning_effort.py`（+6 条），`test_v14_reasoning_engine.py`（+3 条），`test_dag_capabilities.py`（5 处签名适配）

---

## 二、当前测试状态

| 测试集 | 数量 |
|---|---|
| 全量测试（排除 integration/real_tools） | **613 passed** |
| 新增 v14 测试 | +34 条 |
| 新增 Phase 4 Fix Pass 测试 | +17 条 |
| 新增 Phase 3 hardening 测试 | +22 条 |

---

## 三、v14 整体完成度

| 子项 | 描述 | 完成度 |
|---|---|---|
| 1 | 双协议支持（DeepSeek R1 / OpenAI o 系列） | **90%** |
| 2 | ReAct 迭代计数修正 | **90%**（thinking_rounds 连续语义已修正） |
| 3 | Interleaved Thinking | **20%**（thinking_content 贯穿，但未实现多轮思考） |
| 4 | reasoning_effort × ToolRouter 联动 | **100%** ✅（Fix Pass: 全路径 effort 生效 + config 契约修复） |
| 5 | 任务持久化与恢复（Task Resume） | **0%** |
| DRY | engine_helpers.py 共享工具执行 | **100%** ✅（Fix Pass: parse_args 可选参数） |
| Harness a | prompt_utils 配置层 | **60%**（6/6 配置接入，未抽独立模块） |
| Harness b | ToolExecutionPolicy 策略可配置 | **100%** ✅（Fix Pass: 读取 config base 值） |
| Harness c | ContextManager thinking-aware split | **100%** ✅ |
| Hardening | API sanitizer + 循环防护 + sandbox 修复 | **100%** ✅ |
| Fix Pass | Phase 4 评审 7 项修复 + DDGS 隔离 | **100%** ✅ |
| **整体** | | **~75%** |

---

## 四、关键文件索引（本轮新增/修改）

| 文件 | Phase | 说明 |
|---|---|---|
| `react/engine_helpers.py` | 4.1+4.3 | **新建** — execute_tool_calls() + ToolExecutionPolicy |
| `tests/test_engine_helpers.py` | 4.1+4.3 | **新建** — 20 条测试 |
| `tests/test_reasoning_effort.py` | 4.2 | **新建** — 14 条测试 |
| `tests/test_api_sanitizer.py` | 3 hardening | **新建** — 13 条测试 |
| `schema.py` | 4.2 | 新增 ReasoningEffort 枚举 |
| `config.py` | 4.2 | 新增 REASONING_EFFORT + VERSION |
| `agents/planner.py` | 4.2 | classify_task 返回 (str, ReasoningEffort) |
| `agents/orchestrator.py` | 4.2 | effort 传递到三条执行路径 |
| `agents/executor.py` | 4.2 | execute_step/execute_node 接受 effort |
| `react/engine.py` | 4.1+4.2+4.3 | DRY 替换 + effort 参数 + _apply_effort + policy |
| `react/reasoning_engine.py` | 4.1+4.2+4.3 | 同上 + thinking_budget effort 调整 |
| `llm/client.py` | 3 hardening | API sanitizer + BadRequestError fallback |
| `context/manager.py` | 3 hardening | caller_tag 参数 |
| `agents/base.py` | 3 hardening | caller_tag 传递到 compress_if_needed |
| `agents/reflector.py` | 3 hardening | guidance 边界关闭 |
| `agents/planner.py` | 3 hardening | guidance 边界关闭 |
| `tools/shell_tool.py` | 3 hardening | 实例 sandbox |
| `conftest.py` | 3 hardening | **新建** — pytest marker 注册 |
| `tests/test_dag_capabilities.py` | 4.1+4.3 | 适配 test_tool_error_detection |

---

## 五、下游阻塞关系

```
v14 Phase 1-3 + Hardening + Phase 4 (已完成)
    ├── Phase 5a: Task Resume（任务持久化与恢复）
    ├── Phase 5b: Interleaved Thinking（范式重写）
    └── per-tool policy override（在 ToolExecutionPolicy 基础上扩展）

v14 全部完成 ──→ v15 Agentic Memory
            ──→ v17 Self-Evolution（依赖 reasoning_effort + cost-aware）
            ──→ v18 Handoff（依赖 thinking 归因语义）
```

---

## 六、恢复会话时

下次启动时说"继续 v14 Phase 5"即可。关键上下文：
- Phase 1-3 + Hardening + Phase 4 全部完成，597 条测试通过
- Phase 5a: Task Resume — 独立阶段，依赖 engine_helpers.py
- Phase 5b: Interleaved Thinking — 范式重写，独立阶段
- 实施计划在 `.claude/plans/ultrathink-sxw-aicoding-temp-v14-phase3-replicated-finch.md`
