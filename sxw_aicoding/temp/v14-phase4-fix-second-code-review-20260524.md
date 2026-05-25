# v14 Phase 4 修复后二次代码评审

> 日期：2026-05-24  
> 范围：针对上一轮 `v14-phase4-code-review-20260524.md` findings 的修复复查，重点复核 `reasoning_effort` 端到端流转、`react/engine_helpers.py` 配置行为、Emergent/GoalDriven 工具执行、ReasoningEngine 行为、DDGS 测试隔离，以及 Phase 4 roadmap 中 Task Resume 的兑现情况。  
> 方式：静态审查 + 定向测试 + 离线全量回归。  
> 约束：本次只做评审记录，不修改业务代码。

## Findings

### P1 - Phase 4 roadmap 中的 Task Resume 仍未实现

上一轮 P1 中，Task Resume 是唯一仍未被本轮代码修复覆盖的 Phase 4 范围缺口。

roadmap 对 v14 的任务恢复要求仍然明确：

- `sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md:108-113`：要求任务中断后可恢复，保存 ReAct 状态、tool_calls 历史、Memory snapshot，并提供 `OrchestratorAgent.resume(task_id)`。

当前主执行入口仍然只有 `run(task)`，没有 task id、checkpoint 写入、恢复入口或恢复状态装载流程：

- `agents/orchestrator.py:208-280`

同时本轮静态搜索未发现主线实现：

```bash
rg "def resume|resume\(|TaskCheckpoint|TaskRunState|task_id" agents dag schema.py main.py tests -S
```

搜索结果只命中 evaluation 测试里的 `task_id` 字段，没有命中 Orchestrator 任务恢复实现、schema 或测试。

影响：

- 如果仍按 roadmap 验收，Phase 4 不能宣称完整完成。
- 长任务中断恢复仍停留在设计目标层面；现有 DAG checkpoint 也不能恢复 ReAct transcript、tool call log、短期记忆、HITL/SubAgent 边界。

建议：

- 若 Task Resume 决定延期，应同步更新 roadmap / 当前进度文档，把 Phase 4 验收范围改为 `reasoning_effort + DRY helper + harness fixes`。
- 若仍属于 Phase 4，至少补 `TaskRunState` / `TaskCheckpoint` schema、`OrchestratorAgent.resume(task_id)`、checkpoint 存储策略、三条路由路径的恢复边界和测试。

### P3 - DDGS 离线隔离修复偏全局，会屏蔽真实 integration 覆盖

上一轮中 DDGS 真实网络 worker thread abort 的问题，本轮通过 root `conftest.py` 的 session autouse fixture 解决了：

- `conftest.py:11-27`

这能稳定普通离线测试，但 fixture 文档写的是 “non-integration tests”，实际却没有判断 marker，因此所有测试都会被替换为 fake DDGS，包括未来显式想跑真实 DDGS 的 integration 测试。

影响：

- 普通 CI/本地离线回归稳定性提升，这是好事。
- 但真实搜索集成测试会被静默变成空结果 fake，容易让 integration 覆盖失真。

建议：

- 如果未来要保留真实 DDGS integration，用 `request.node.get_closest_marker("integration")` 或单独 env flag 跳过该 fixture。
- 如果项目决策是不再跑真实 DDGS integration，则把 fixture 注释改准确，说明它是全局网络隔离。

## 上一轮 Findings 复查

### 已修复 - DAG/complex 路径丢失 effort

当前 Orchestrator 从 Planner 收到 `(complexity, effort)` 后，complex 路径已把 effort 传到 DAG 执行：

- `agents/orchestrator.py:247-264`
- `agents/orchestrator.py:581-596`

`DAGExecutor` 也新增 `_effort` 并透传给节点执行：

- `dag/executor.py:94-101`
- `dag/executor.py:306`

对应测试已覆盖：

- `tests/test_reasoning_effort.py` 中 `TestDAGEffortPropagation`

### 已修复 - emergent / goal-driven 路径未使用 effort

EmergentPlanner 已新增 `_current_effort`，`execute(..., effort=...)` 存储当前 effort，并传入 ReActEngine 或 legacy TODO loop：

- `agents/emergent_planner.py:120`
- `agents/emergent_planner.py:143-168`
- `agents/emergent_planner.py:534-540`
- `agents/emergent_planner.py:562-568`
- `agents/emergent_planner.py:588-600`

GoalDrivenPlanner 也完成同样接线：

- `agents/goal_driven_planner.py:262-285`
- `agents/goal_driven_planner.py:668-675`
- `agents/goal_driven_planner.py:713-724`

对应测试已覆盖：

- `tests/test_reasoning_effort.py` 中 `TestEmergentGoalDrivenEffortPropagation`

### 已修复 - ToolExecutionPolicy 覆盖全局截断配置

`ToolExecutionPolicy.default()` 已改为读取运行时 config，`for_effort()` 以 `TOOL_RESULT_TRUNCATION_LIMIT` 为 base：

- `react/engine_helpers.py:51-62`

对应测试已覆盖：

- `tests/test_engine_helpers.py` 中 `test_default_policy_respects_config_override`
- `tests/test_engine_helpers.py` 中 `test_for_effort_low_relative_to_config`
- `tests/test_engine_helpers.py` 中 `test_for_effort_high_relative_to_config`

一个设计点需要后续明确：当前 high effort 会把 base 翻倍。如果 `TOOL_RESULT_TRUNCATION_LIMIT` 被定义为绝对上限，这会改变配置语义；如果它是 effort policy 的基准值，则当前实现合理，但建议在配置文档中写清楚。

### 已修复 - EmergentPlanner fenced JSON 参数解析回退

EmergentPlanner 新增 `_parse_json_for_tool_args()` 并在调用共享 helper 时传入 `parse_args`：

- `agents/emergent_planner.py:589-600`
- `agents/emergent_planner.py:630-640`

共享 helper 也新增 `parse_args` 扩展点：

- `react/engine_helpers.py:65-77`
- `react/engine_helpers.py:98-106`

对应测试已覆盖：

- `tests/test_engine_helpers.py` 中 `TestParseArgs`

### 已修复 - ReasoningEngine MEDIUM 使用错误温度配置

ReasoningEngine 已 override `_apply_effort()`，MEDIUM 分支使用 `config.REASONING_TEMPERATURE`：

- `react/reasoning_engine.py:49-55`

### 已修复 - MAX_THINKING_ROUNDS “连续”语义未 reset

ReasoningEngine 当前在 tool-call 分支和 final-answer 分支都会 reset `thinking_rounds`：

- `react/reasoning_engine.py:190-210`

pure-thinking 分支仍只在连续思考轮中累加：

- `react/reasoning_engine.py:224-266`

### 已修复 - DDGS 测试导致离线回归 abort

使用 `.venv` 并把 `SANDBOX_DIR` 指向仓库内临时目录后，离线全量测试已通过：

```bash
env SANDBOX_DIR=/Users/shixiangweii/PycharmProjects/manus_demo/.test_sandbox \
  .venv/bin/python -m pytest tests/ -q -o asyncio_mode=auto --ignore=tests/test_llm_integration.py
```

结果：

```text
613 passed, 2 warnings in 22.09s
```

## 验证记录

语法检查通过：

```bash
.venv/bin/python -m py_compile \
  react/engine_helpers.py react/engine.py react/reasoning_engine.py \
  agents/emergent_planner.py agents/goal_driven_planner.py agents/planner.py \
  agents/orchestrator.py agents/executor.py dag/executor.py schema.py conftest.py
```

Phase 4 定向测试通过：

```bash
.venv/bin/python -m pytest \
  tests/test_engine_helpers.py \
  tests/test_reasoning_effort.py \
  tests/test_v14_reasoning_engine.py \
  tests/test_dag_capabilities.py \
  tests/test_goal_driven_planner.py \
  tests/test_emergent_planning.py \
  -q -o asyncio_mode=auto
```

结果：

```text
164 passed in 11.16s
```

离线全量回归首次直接运行时，`tests/test_real_tools.py::test_file_ops` 因当前 Codex 沙箱不允许写默认目录 `/Users/shixiangweii/.manus_demo/sandbox/test_ops.txt` 失败。这是验证环境限制，不是本轮业务修复引入的行为回归。

将 `SANDBOX_DIR` 指到仓库内可写目录后，全量离线回归通过：

```text
613 passed, 2 warnings in 22.09s
```

两个 warning 来自 `tests/test_cycle_detection.py` 的测试函数返回 bool，而不是使用 assert；这不是本轮新增回归。

## 结论

这版 fix pass 质量明显比上一版稳：上一轮关于 `reasoning_effort` 断链、helper 配置覆盖、Emergent 参数解析、ReasoningEngine 温度和连续思考轮的核心问题都已经修复，并且有定向测试覆盖。按“Phase 4 fix pass”视角，可以认为代码层面的主要回归风险已被压住。

但按 roadmap 原始范围，Phase 4 仍缺 Task Resume。建议现在做一个范围决策：如果学习目标优先是 reasoning model harness 和 DRY 重构，可以把 Task Resume 正式挪到后续阶段；如果 Phase 4 要严格兑现 roadmap，则下一步应先补 Task Resume，而不是进入 Phase 5。
