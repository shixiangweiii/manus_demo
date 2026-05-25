# v14 Phase 4 代码评审

> 日期：2026-05-24  
> 范围：Phase 4 最新代码改动，重点覆盖 `react/engine_helpers.py`、`reasoning_effort` 流转、ReAct/Reasoning/Emergent/GoalDriven 工具执行 DRY、Task Resume 兑现情况。  
> 方式：静态代码审查 + 定向测试 + 离线回归尝试。  
> 约束：本次只做评审记录，不修改业务代码。

## 总体结论

Phase 4 的 DRY 抽取方向是正确的：`execute_tool_calls()` 把 ReActEngine、ReasoningEngine、EmergentPlanner、GoalDrivenPlanner 四处重复的工具执行逻辑集中到 `react/engine_helpers.py`，并保留了并发 tool calls、ToolRouter 记账、结果截断、错误标记等主行为。新增 `tests/test_engine_helpers.py` 覆盖也比较扎实。

但如果按 roadmap 中 Phase 4 的目标衡量，目前还不能算“全部完成”：

- `reasoning_effort` 只对 simple/ReAct 路径真正生效；DAG/complex 和 emergent/goal-driven 路径没有把 effort 传到底。
- roadmap 明确写的 Task Resume / `OrchestratorAgent.resume(task_id)` 没有看到实现。
- DRY helper 引入了两个行为回退：全局 `TOOL_RESULT_TRUNCATION_LIMIT` 被 hardcoded policy 覆盖；EmergentPlanner 原先支持 markdown fenced JSON 参数，现在退化为裸 `json.loads`。
- 测试隔离问题仍未修复，组合/全量测试会在真实 DDGS worker thread 里 abort。

因此建议先做 Phase 4 fix pass，再进入 Phase 5 或宣称 v14 Phase 4 完成。

## Findings

### P1 - `reasoning_effort` 在 DAG/complex 路径被丢弃

Orchestrator 已经从 Planner 收到 `(complexity, effort)`：

- `agents/orchestrator.py:247-248`

并且 complex 路由把 effort 传给 `_execute_dag_and_reflect()`：

- `agents/orchestrator.py:257-261`

但 `_execute_dag_and_reflect()` 只接收参数，没有继续传给 `DAGExecutor`：

- `agents/orchestrator.py:581-601`

`DAGExecutor.execute()` / `_run_node()` 也没有 effort 参数，最终调用仍是：

- `dag/executor.py:282-307`：`return await executor.execute_node(node, context)`

`ExecutorAgent.execute_node()` 虽然已经支持 `effort`：

- `agents/executor.py:153-169`

但 DAGExecutor 没有传，所以 complex/DAG 任务永远走 `effort=None -> MEDIUM`。这会让 `REASONING_EFFORT=high/low` 对 complex 路径无效，也让 Planner 给出的 effort 无法影响 DAG 节点执行。

建议：

- 给 `DAGExecutor.__init__()` 或 `execute(dag, effort=...)` 增加 effort。
- `_run_node()` / `_run_node_with_timeout()` 继续传 `effort`。
- 增加测试：`PLAN_MODE=complex + REASONING_EFFORT=high` 时，mock `ExecutorAgent.execute_node` 断言收到 `effort=ReasoningEffort.HIGH`。

### P1 - `reasoning_effort` 在 emergent / goal-driven 路径完全未使用

Orchestrator emergent 路由也把 effort 传入了 `_execute_emergent()`：

- `agents/orchestrator.py:262-264`
- `agents/orchestrator.py:512`

但函数体没有使用该参数：

- `agents/orchestrator.py:523-527`：goal-driven 仍调用 `self.goal_driven_planner.execute(task, context)`
- `agents/orchestrator.py:550-552`：emergent 仍调用 `self.emergent_planner.execute(task, context)`

同时 EmergentPlanner / GoalDrivenPlanner 在调用共享 helper 时都使用 default policy：

- `agents/emergent_planner.py:579-595`
- `agents/goal_driven_planner.py:705-721`

这意味着默认会被分类为 HIGH 的 emergent 任务并不会得到更高迭代/截断/预算策略。`reasoning_effort × ToolRouter` 在最需要高 effort 的路径上没有生效。

建议：

- `EmergentPlannerAgent.execute(..., effort=None)` 和 `GoalDrivenPlannerAgent.execute(..., effort=None)` 增加 effort 参数。
- 内部 TODO 执行函数也接收 effort，并传给 `ToolExecutionPolicy.for_effort(effort)`。
- 如果暂时不想改变 emergent 的外部 API，至少在 Orchestrator 层不要把 effort 参数留成“看起来已接入”的死参数。

### P1 - Phase 4 roadmap 的 Task Resume 没有实际实现

roadmap 对 Phase 4 明确写了 Task Resume：

- `sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md:108-113`

要求包括：

- 任务中断后可恢复。
- 保存 ReAct 状态、tool_calls 历史、Memory snapshot。
- 接口：`OrchestratorAgent.resume(task_id)`。

当前代码中没有 `OrchestratorAgent.resume()`，也没有 task_id 级运行状态持久化。搜索主线代码只看到 DAG 内存 checkpoint：

- `dag/graph.py:521-542`
- `dag/executor.py:260`

这些 checkpoint 是 DAG 内部调试快照，不是 Orchestrator 任务级 resume。它们没有恢复 ReAct messages、tool_calls log、short/long memory snapshot，也没有 CLI/API 入口。

建议：

- 如果 Phase 4 范围已经缩小，需要更新 roadmap / 进度说明，避免“全部完成”与代码不一致。
- 如果仍按 roadmap 验收，至少需要：
  - `TaskRunState` / `TaskCheckpoint` schema。
  - `OrchestratorAgent.resume(task_id)`。
  - checkpoint 存储位置与清理策略。
  - ReAct/ReasoningEngine run-local transcript 恢复。
  - HITL / SubAgent / DAG 三条路径的恢复边界说明和测试。

### P1 - `ToolExecutionPolicy` 覆盖了全局 `TOOL_RESULT_TRUNCATION_LIMIT`

原系统通过 env/config 控制工具结果截断：

- `config.py:102`：`TOOL_RESULT_TRUNCATION_LIMIT`

新 helper 的 policy 默认值硬编码为 2000：

- `react/engine_helpers.py:33-52`

而 helper 一旦传入 policy，就使用 policy 的值覆盖 `truncation_limit` 参数：

- `react/engine_helpers.py:91-92`

Phase 4 的所有主调用点都传了 policy：

- `react/engine.py:294-306`
- `react/reasoning_engine.py:256-268`
- `agents/emergent_planner.py:583-595`
- `agents/goal_driven_planner.py:709-721`

结果是用户设置 `TOOL_RESULT_TRUNCATION_LIMIT=8000` 时，中等 effort/default policy 仍会截断到 2000；low/high 则固定 1000/4000，也不基于 config。这个行为破坏了已有配置契约。

建议：

- `ToolExecutionPolicy.default()` 应读取 `config.TOOL_RESULT_TRUNCATION_LIMIT`，或 `for_effort()` 接收 `base_limit`。
- low/high 可以按 base 做倍率，例如 low = min(base, 1000)，high = max(base, 4000)，但需要明确配置优先级。
- 增加测试：patch `config.TOOL_RESULT_TRUNCATION_LIMIT=7777`，MEDIUM/default policy 应使用 7777。

### P2 - EmergentPlanner 工具参数解析从宽松 JSON 退化为裸 `json.loads`

EmergentPlanner 原先工具参数解析使用自己的 `_parse_json()`：

- `agents/emergent_planner.py:611-621`

它委托 `LLMClient.parse_json()`，可以处理 markdown fenced JSON。Phase 4 抽到 helper 后，统一使用：

- `react/engine_helpers.py:95-100`

这里是裸 `json.loads(tc.function.arguments)`，解析失败就静默变成 `{}`。对 OpenAI 标准 tool call 来说通常没问题，但该项目支持多种 OpenAI-compatible provider，历史上已经专门为 fenced JSON 做过兼容。这个改动会让 EmergentPlanner 在非标准 provider 或模型输出 fenced arguments 时丢参数，导致工具以空参数执行。

建议：

- 给 `execute_tool_calls()` 增加 `parse_args` 参数，默认 `json.loads`，EmergentPlanner 传入 `self._parse_json`。
- 或统一改为 `LLMClient.parse_json()` 并要求结果必须是 dict。
- 增加测试：tool_call.arguments 为 ```json fenced block 时，EmergentPlanner/helper 能正确传参。

### P2 - ReasoningEngine MEDIUM effort 使用了 ReAct 温度配置

ReasoningEngine 复用了 ReActEngine 的 `_apply_effort()`：

- `react/reasoning_engine.py:66-68`

而 `_apply_effort()` 的 MEDIUM 分支返回：

- `react/engine.py:112-121`：`config.REACT_TEMPERATURE`

这改变了 ReasoningEngine 原先使用 `config.REASONING_TEMPERATURE` 的行为。当前两个默认值都是 0.5，所以默认测试不会暴露；但用户一旦设置 `REASONING_TEMPERATURE` 与 `REACT_TEMPERATURE` 不同，ReasoningEngine MEDIUM 会读错配置。

建议：

- ReasoningEngine override `_apply_effort()`，MEDIUM 使用 `config.REASONING_TEMPERATURE`。
- 或 `_apply_effort(base_temperature=...)`，由子类传入 base。
- 增加测试：patch `REACT_TEMPERATURE=0.1`、`REASONING_TEMPERATURE=0.6`，ReasoningEngine MEDIUM 应使用 0.6。

### P2 - `MAX_THINKING_ROUNDS` 的“连续”语义仍未修复

前一轮评审已经指出该问题，本轮 Phase 4 后仍存在：

- `config.py:155` 注释写“连续纯思考轮次硬上限”。
- `react/reasoning_engine.py:70-72` 只初始化一次 `thinking_rounds`。
- `react/reasoning_engine.py:214-225` pure-thinking 时递增并判断。
- tool-call 分支 `react/reasoning_engine.py:179-199` 没有 reset。

这意味着它仍是“累计 pure-thinking rounds”，不是“连续 pure-thinking rounds”。如果这是有意设计，应改名为 `MAX_TOTAL_THINKING_ROUNDS`；如果不是，应在 tool-call / final-answer 分支重置。

### P2 - 测试隔离仍未解决，组合/全量测试会被真实 DDGS abort

定向组合测试中，只要包含部分 emergent/goal-driven/tracing 路径，仍会触发真实 DDGS：

```text
Fatal Python error: Aborted
tools/web_search.py", line 238 in _sync
.venv/lib/python3.12/site-packages/ddgs/http_client.py
```

这说明前一轮报告中的 DDGS 测试隔离问题仍未关闭。Phase 4 增加了新的执行路径和共享 helper 后，更需要稳定回归；否则后续很难判断失败来自新重构还是外部网络库。

建议：

- 非 integration 测试全局 monkeypatch `WebSearchTool._ddgs_search`。
- 或将所有真实 WebSearchTool 执行测试标记为 `integration`，默认回归命令加 `-m "not integration"`。
- 修正文档中的默认测试命令，避免继续推荐会 abort 的离线回归方式。

## 正向观察

- `react/engine_helpers.py` 抽取边界基本合理，保留了原先工具消息顺序、ToolRouter 三态记账、错误 marker、成功结果截断。
- `tests/test_engine_helpers.py` 覆盖了成功、并发、未知工具、异常、rate_limited、截断、caller attribution、policy 等关键路径。
- `tests/test_reasoning_effort.py` 覆盖了 enum、Planner mapping、ReActEngine effort 基本行为。
- ReActEngine / ReasoningEngine 的工具执行重复代码明显减少，后续做 tool policy / MCP outputSchema / guardrail 会更集中。

## 验证结果

语法检查通过：

```bash
.venv/bin/python -m py_compile \
  react/engine_helpers.py react/engine.py react/reasoning_engine.py \
  agents/emergent_planner.py agents/goal_driven_planner.py \
  agents/planner.py agents/orchestrator.py agents/executor.py schema.py
```

核心 Phase 4 定向测试通过：

```bash
.venv/bin/python -m pytest \
  tests/test_engine_helpers.py \
  tests/test_reasoning_effort.py \
  tests/test_v14_reasoning_engine.py \
  tests/test_dag_capabilities.py \
  -q -o asyncio_mode=auto
```

结果：

```text
88 passed in 2.18s
```

更宽的组合测试失败，原因仍是真实 DDGS abort：

```bash
.venv/bin/python -m pytest \
  tests/test_engine_helpers.py tests/test_reasoning_effort.py \
  tests/test_v14_reasoning_engine.py tests/test_dag_capabilities.py \
  tests/test_goal_driven_planner.py tests/test_emergent_planning.py \
  -q -o asyncio_mode=auto
```

结果：

```text
Fatal Python error: Aborted
tools/web_search.py", line 238 in _sync
```

## 建议修复顺序

1. 补通 `effort` 到 DAGExecutor / EmergentPlanner / GoalDrivenPlanner 的执行入口。
2. 修 `ToolExecutionPolicy` 与 `TOOL_RESULT_TRUNCATION_LIMIT` 的配置契约。
3. 恢复 EmergentPlanner 的宽松参数解析能力。
4. 修 ReasoningEngine MEDIUM temperature 使用 `REASONING_TEMPERATURE`。
5. 明确并修正 `MAX_THINKING_ROUNDS` 连续/累计语义。
6. 决定 Task Resume 是否仍属于 Phase 4：如果是，补实现；如果不是，更新 roadmap 和进度文档。
7. 处理 DDGS 测试隔离，让非 integration 回归稳定跑完。

## 最终判断

Phase 4 当前更像“Batch 4.1 DRY 抽取 + Batch 4.2 effort 初步接线”，还不是完整 Phase 4。DRY 抽取可以保留，但 `reasoning_effort × ToolRouter` 只覆盖了部分路径，Task Resume 未兑现，且测试隔离仍阻塞全量验证。建议先做一轮 Phase 4 fix-audit，再继续 Phase 5。
