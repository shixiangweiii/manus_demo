# 评审（第二轮，独立复核）：AgentLoop / Plan-and-Execute 双范式重构

> 评审日期：2026-08-02
> 评审对象：commit `72b23ad 代码重构 to #000000`（104 文件，+4307 / −6264）
> 对照文档：`sxw_aicoding/changelog/2026-08-02-agent-loop-plan-and-execute-refactor.md`
> 前序评审：`sxw_aicoding/review/2026-8-2-评审AgentLoop与Plan-and-Execute双范式重构-01.md`
> 评审方式：独立全量读码 + 6 个离线假模型探针（`/tmp/probe_review02.py`，未落盘进仓库）+ 结构性验证
> 评审边界：**未调用真实 LLM、网络、MCP、AgentBay；未跑正式评测；未做浏览器视觉验收**

本轮是对同一 commit 的**第二次独立读码**，目的有二：一是用可执行探针把上一轮靠读码得出的判断变成证据（或推翻它），二是覆盖上一轮没有展开的层面（评测度量语义、config 门面在新路径中的渗透、DAG 执行器装配）。

---

## 1. 结论摘要

### 1.1 总体判断

**重构的架构判断是对的，落地质量高于其规模所暗示的风险水平。**

三条执行路径的语义边界现在是真实的，不是靠命名区分的：`AgentLoop` 直接继承 `TaskEngine`、不经过 `ActionExecutor`、`actions` 恒为空；`PlanAndExecuteEngine` 承载的是 Sequential/DAG 真正共享的东西（RecordingActionExecutor、typed failure boundary、统计聚合），不是为了共享而共享的空基类。旧架构删得干净，活动源码里搜不到 `ExecutorKind` / `EngineSelector` / `TodoEngine` / `GoalEngine` / `WorkflowEngine` / `ReasoningAware*` / `HandoffTool` 的残留，也没有留下兼容适配器。宿主（CLI / WebUI / Evaluation）没有一处按引擎类型分支——`EventBus` 作为唯一集成缝的约束被严格遵守。

`agent_loop/loop.py` 的消息协议实现值得单独肯定：不伪造 `Continue` user 消息、同轮 `text + tool_calls` 不提前终止、工具错误一律转成模型可见的 tool result、重复 call ID 在产生任何副作用**之前**拒绝、上下文压缩只构造临时 model-view 而不改写 canonical history。这些都是正确且不常见的选择，代码与注释一致。

**但仍有 1 个 P1 直接损害本项目的核心目的（引擎对比），以及 5 个应当修复的 P2。其中 P2-1 是本轮新发现，且它的严重性不低于原 P1——评测层把新引入的 typed stop reason 整个丢掉了。**

### 1.2 问题分级

| 编号 | 等级 | 摘要 | 位置 | 来源 |
|---|---|---|---|---|
| P1-1 | **P1** | AgentLoop 上下文压缩每轮重算，摘要调用同时吃掉 `max_turns` 预算与 `llm_calls` 指标 | `agent_loop/loop.py:221`、`context/manager.py:151` | 复核确认（-01 同号） |
| P2-1 | **P2** | **评测层丢弃 `stop_reason`，且把引擎成功与 verifier 结果合并成单一 `success`** | `evaluation/runner.py:118`、`evaluation/models.py:154` | **本轮新增** |
| P2-2 | P2 | `EngineStats` 无 token 维度，`llm_calls` 成为唯一成本代理且已被 P1-1 污染 | `core/models.py:60`、`evaluation/runner.py:128` | 复核确认（-01 P2-2） |
| P2-3 | P2 | AgentLoop 失败路径 `agent_turn_started` 无配对 `agent_turn_completed` | `agent_loop/loop.py:255` | 探针确认（-01 P2-1） |
| P2-4 | P2 | `for_sandbox()` 为每个子 Agent 重建独立并发信号量，绕过全局子进程上限 | `tools/shell_tool.py:49`、`tools/code_executor.py:48` | 探针确认（-01 P2-3） |
| P2-5 | P2 | 错误类工具结果完全不截断，叠加 AgentLoop 永久历史后每轮重复传输 | `tool_calling/tool_execution.py:118` | 探针确认（-01 P2-4） |
| P3-1 | P3 | `_apply_tool_filter` 不同步 `ToolRouter._available_tools` | `agent_loop/loop.py:545` | 探针确认（-01 P3-5） |
| P3-2 | P3 | **skill `license` 由进程级 `config.SKILLS_USER_DIR` 推断，评测重定向后被误判为 `third_party`** | `skills/loader.py:409` | **本轮新增** |
| P3-3 | P3 | **`SkillActivationTool._max_content_tokens` 是死参数，真正截断走全局 config** | `skills/activation.py:62`、`skills/registry.py:111` | **本轮新增** |
| P3-4 | P3 | **`runtime.run()` 无条件 `reset_usage()`，同 runtime 并发运行会互相清空统计基线** | `runtime/app.py:50`、`llm/client.py:569` | **本轮新增** |
| P3-5 | P3 | DAG 路径构造了一个从不用于执行的 ActionExecutor | `runtime/app.py:255`、`engines/dag.py:107` | 复核确认（-01 P3-3） |
| P3-6 | P3 | DAG `gather` 分支不记录 action 轨迹；`CancelledError` 非 `Exception`，会落到 `result.output` | `dag/executor.py:214` | 复核 + 补充（-01 P3-1） |
| P3-7 | P3 | `ActionToolLoop.caller_tag` 用 `id(messages)`，与 AgentLoop 的 uuid4 不一致且可复用 | `tool_calling/loop.py:247` | 复核确认（-01 P3-2） |
| P3-8 | P3 | AgentLoop token 预算两处阈值 `>` 与 `>=` 不一致 | `agent_loop/loop.py:322` vs `:487` | 复核确认（-01 P3-4） |
| P3-9 | P3 | TracingBridge 用子串 `:{action_id}:` 匹配 span key，语义脆弱 | `tracing/bridge.py:327/342` | 复核 + 细化（-01 P3-2 相邻） |
| P3-10 | P3 | `RuntimeCheckpointStore.save` 的 `finally: unlink` 在成功路径是空操作，并发同 task 会误删对方 tmp | `checkpoint/store.py:47` | 复核确认（-01 P3-7） |
| P3-11 | P3 | `capabilities.checkpoint_max_per_task` 与 v2 checkpoint 语义不符 | `settings.toml:77`、`engines/dag.py:104` | 复核确认（-01 P3-6） |
| P3-12 | P3 | 根 AgentLoop 无 token 预算（`max_total_tokens` 仅 SubAgent 传入） | `engines/agent_loop.py:55` | **本轮新增** |
| P3-13 | P3 | `docs/engines.md` 未记录 AgentLoop turn 预算包含摘要调用 | `docs/engines.md:11` | 复核确认（-01 P3-9） |

### 1.3 与前序评审的差异

**独立复核后维持的判断**：P1-1 及 -01 报告的全部 P2/P3 我都独立走了一遍代码，结论一致，其中 5 项本轮补上了可执行证据（见 §2）。

**本轮新增 5 项**（P2-1、P3-2、P3-3、P3-4、P3-12），集中在 -01 没有展开的三个方向：评测记录的语义完整性、`config.py` 门面在「新代码」路径里的残留渗透、以及运行时/预算装配的不对称。

**本轮排除的疑似问题**（读码后确认不是缺陷，记录以免后续重复怀疑）：

- `agents/prompt_utils.py` 中的 `config.SUBAGENT_ENABLED` / `config.HITL_ENABLED` / `config.PYTHON_COMMAND` 读取**不是**违规。这些是 `capabilities is None` 时的显式回退分支，三个引擎（`engines/agent_loop.py`、`execution/tool_calling.py`、`tools/subagent_tool.py`）全部传入了 `PromptCapabilities`，回退路径不会被触发。
- `tool_calling/tool_execution.py` 中 `guardrail.scan_tool_output(...)` 未 await 而 `check_tool_input(...)` 有 await，**不是**遗漏——前者是同步方法。
- `SubAgentTool` 的 `set_caller` 竞态：`local_parent = self._parent_name` 在 `execute()` 首行捕获，而 `set_tool_caller` 在 `traced_execute` 前无 await 间隔地调用，属性归因是安全的。
- `AgentLoop._result` 里 `self._stats.tool_calls = len(self._current_log)` 不存在跨任务累计：`run()` 会重置 `_current_log`。

---

## 2. 探针证据

以下 6 个探针不访问真实 LLM 与网络，全部使用脚本化假 client（`/tmp/probe_review02.py`）。这是本轮相对 -01 报告的主要增量：把「读码判断」变成「可复现观测」。

```
==================================================================
[P1] stop_reason      = completed
[P1] model turns used = 11 (model tool-turns: 7 )
[P1] summary calls    = 4          <- 一次 7 轮任务额外产生 4 次摘要调用
[P1] stats.llm_calls  = 11
[P1] VERDICT: summary calls scale with turn count -> True
==================================================================
[P2] stop_reason        = max_turns
[P2] max_turns          = 10
[P2] real model turns   = 6        <- 10 的预算只换到 6 个真实任务轮次
[P2] summary calls      = 4
==================================================================
[P3] model_error       stop=model_error       started=1 completed=0 *** UNPAIRED ***
[P3] invalid_response  stop=invalid_response  started=1 completed=0 *** UNPAIRED ***
[P3] empty_response    stop=invalid_response  started=1 completed=0 *** UNPAIRED ***
==================================================================
[P4] truncation_limit          = 2000
[P4] error tool message length = 50162   <- 错误结果原样进入永久历史
[P4] VERDICT: error result untruncated -> True
==================================================================
[P5] loop.tools           = ['alpha', 'todo_write']
[P5] router available     = ['alpha', 'beta', 'todo_write']   <- router 未同步
==================================================================
[P6] parent sem id = 4385427136
[P6] child  sem id = 4385875312    <- 子 Agent 拿到独立信号量
==================================================================
```

结构性验证（全部通过）：

```bash
.venv/bin/python -m compileall -q .                        # exit 0
.venv/bin/python -c "import core, runtime, engines, execution, tool_calling, agent_loop, evaluation, tracing, webui"
git diff --check
```

---

## 3. 做对了什么

以下几点是重构中判断准确、实现也到位的部分，后续改动不应破坏它们：

1. **AgentLoop 与 ActionToolLoop 的共享边界划在了正确的位置。** 共享的是响应归一化（`normalize_model_response`）、assistant 消息序列化、JSON Schema 校验、guardrail、结果截断、事件与 tracing——即「工具调用传输协议」。不共享的是顶层控制流。这正好是两个范式的真实差异所在。

2. **`normalize_model_response` 把 provider 协议错误分类为 `INVALID_RESPONSE` 而不是让异常逃逸成引擎故障。** 重复 tool-call ID 在 `_exec_one` 之前就被拒绝，不会产生"执行了一半副作用再报错"的状态。

3. **上下文压缩不改写 canonical history。** `ContextPreparation.messages` 明确是「给下一次模型调用的视图」，`_history` 保持精确。这个设计本身是对的——问题只在于调用方每轮都重新构造它（P1-1）。

4. **`PlanAndExecuteEngine.model_operation` 把 provider 失败与响应结构失败分开。** `_PlanModelError` → `MODEL_ERROR`，`_PlanInvalidResponse` → `INVALID_RESPONSE`，其余 → `ENGINE_ERROR`。planner/reflector/synthesis 各阶段都套了这个边界，失败分类是真实的而不是统一 `ENGINE_ERROR`。

5. **`CancelledError` 契约在四处一致地保持**：`AgentLoop.run`、`RecordingActionExecutor.execute`、`ActionToolLoop.execute`、`SubAgentTool.execute` 都是「先收口本地状态/事件，再原样抛出」；`AgentRuntime.run` 落 `cancelled` checkpoint 后重抛。上一轮（08-01）评审记录的「CancelledError 致 checkpoint 卡 running」在本次重构中已实际修复。

6. **SubAgent 的隔离是结构性的，不是靠约定。** `_BLOCKED_CHILD_TOOLS` 在 `parameters_schema` 与 `_resolve_tools` 两处都生效（模型看不到，也调不到）；`activate_skill` 每个子 Agent 独立 `clone()`；带 `_on_event` 的工具做浅拷贝换回调；沙箱创建失败时**宁可不给文件/子进程工具也不回退到父级根目录**（`_resolve_tools` 的 `continue`）——这个默认方向是对的。

7. **TracingBridge 的 tool span key 加入了 action 边界**（`owner:action_id:call_id`）。OpenAI 只保证 tool-call ID 在单条 assistant 响应内唯一，并发 DAG Action 同时产出 `call_1` 是现实场景，这个修正是必要的。

---

## 4. 问题清单

### P1-1：AgentLoop 上下文压缩每轮重算，摘要调用同时吃掉 turn 预算与对比指标

**位置**：`agent_loop/loop.py:221-233`、`context/manager.py:151-244`

**机制**：

```python
# agent_loop/loop.py:221
request_messages = self._history
if self.context_manager is not None:
    prepared = await self.context_manager.prepare_messages(
        self._history, self.llm_client, caller_tag=caller_tag,
    )
    request_messages = prepared.messages
    self._stats.llm_calls += prepared.llm_calls
    model_calls += prepared.llm_calls          # <- 计入 max_turns
```

`prepare_messages` 每次都从**完整的、只增不减的** `self._history` 重新估算 token 并重新摘要，返回的压缩视图**不被缓存**。一旦 `_history` 越过 `max_tokens`，之后每一轮都要多付一次摘要模型调用。

**为什么这次一定会触发**：`settings.toml` 里 `execution.max_context_tokens = 16000`，`tools.result_truncation_limit = 2000`，而 AgentLoop 默认 effort 是 HIGH → `ToolExecutionPolicy.for_effort` 把截断放大到 `base * 2 = 4000` 字符 ≈ 1300 token。也就是说约 10 次工具调用之后，AgentLoop 就进入「每轮双调用」状态。这不是边界情况，是默认路径。

**三重后果，都直接打在项目目标上**：

1. **`llm_calls` 指标被系统性抬高。** 评测的唯一成本代理是 `EngineStats.llm_calls`，而 AgentLoop 因为持有单一持续增长的历史，比 Sequential/DAG（每个 Action 重开消息列表，几乎不触发压缩）更容易进入压缩状态。三引擎对比会读出一个**由架构差异而非任务表现造成**的偏差。
2. **AgentLoop 的有效 turn 预算被静默压缩。** 探针 P2：`max_turns=10` 只换到 6 个真实任务轮次，另外 4 个被摘要吃掉。同样是 `--effort high`，AgentLoop 拿到的「思考次数」比配置值少 40%。
3. **摘要质量随轮次退化。** 每轮都是对全量历史重新摘要，而不是在上一次摘要基础上增量压缩，语义漂移会累积。

**建议**：在 `AgentLoop` 内缓存压缩视图——记录压缩时的 `len(self._history)` 与产出的 summary message，只在新增消息使视图再次越限时重新摘要；并把摘要调用从 `max_turns` 计数中剥离（它不是任务推理轮次），改为单列 `context_compaction_calls` 计入统计但不计入预算。

---

### P2-1：评测层丢弃 `stop_reason`，且把引擎成功与 verifier 结果合并成单一 `success`（新增）

**位置**：`evaluation/runner.py:116-137`、`evaluation/models.py:154-174`、`evaluation/metrics.py:33`

```python
# evaluation/runner.py:116
verifier_passed = verifier.all_passed
success = engine_result.success and verifier_passed is not False
...
metrics=CaseMetrics(
    success=success,                       # <- 合成值
    verifier_passed=verifier_passed,
    llm_calls=..., latency_ms=..., tool_calls=...,
    reasoning_tokens=..., subagent_calls=...,
)
```

`CaseMetrics` 里**没有** `engine_success` 字段，也**没有** `stop_reason` 字段。`CaseResult` 同样没有。于是：

1. **引擎自报成功率这个维度在存储层不存在。** `DimensionSummary.success_rate` 算的是 `engine_success ∧ verifier`。一旦某个 cell 的 `success_rate` 下降，从落盘的报告里**无法区分**是引擎没跑完、还是跑完了但答案不对。`CLAUDE.md` 明确要求「Results report **separate dimensions** (engine success, verifier status ...)」，当前实现与该约定不符。

2. **本次重构新引入的 `EngineStopReason` 在评测里完全不可见。** 重构花了大力气把 planner 失败、action 失败、reflection 失败、max_turns、timeout、model_error、invalid_response 分类成 typed stop reason（changelog §4.3 列了 6 类覆盖），结果这些信息在 `evaluate_case` 里被直接丢弃。三引擎对比时最有价值的问题——「AgentLoop 的失败是撞 max_turns 还是模型协议错误？Sequential 的失败是 planner 还是 action？」——目前只能翻 trace，不能从报告里聚合。

3. 与 P1-1 叠加后更麻烦：`max_turns` 失败会因为摘要吃预算而变多，但报告里既看不到 `stop_reason=max_turns` 的占比，也没有 token 维度可以交叉验证（P2-2）。

**建议**：`CaseMetrics` 增加 `engine_success: bool` 与 `stop_reason: str`，`success` 保留为「综合判定」但在报告中与 `engine_success_rate` 并列展示；`DimensionSummary` 增加 `stop_reason_counts: dict[str, int]`。改动集中在 3 个文件，约 30 行，且不需要改引擎。

---

### P2-2：`EngineStats` 无 token 维度

**位置**：`core/models.py:60-66`、`llm/models.py:14-22`、`evaluation/runner.py:128`

`LLMCallRecord` 已经有 `prompt_tokens` / `completion_tokens` / `total_tokens`，`AgentLoop._tokens_used()` 也已经会读它们（用于 SubAgent 预算），但 `EngineStats` 只暴露 `llm_calls / tool_calls / reasoning_tokens / subagent_calls`。评测因此**没有任何 token 或成本维度**。

后果：`llm_calls` 成为唯一成本代理，而它恰好是被 P1-1 污染的那个量，且没有第二个指标能交叉验证污染幅度。另外「一次 30k prompt 的调用」和「一次 500 token 的调用」在报告里权重相同——对比一个刻意积累长历史的引擎和一个刻意切分上下文的引擎时，这个抹平是致命的。

**建议**：`EngineStats` 增加 `prompt_tokens` / `completion_tokens` / `total_tokens`，在 `TaskEngine.stats_since` 里从 `records` 求和（与现有 `reasoning_tokens` 完全同构，`max(全局记录, 本地观测)` 的策略可直接复用），再透传到 `CaseMetrics` / `DimensionSummary`。

---

### P2-3：AgentLoop 失败路径缺少配对的 `agent_turn_completed`

**位置**：`agent_loop/loop.py:253-352`

`_emit("agent_turn_started", ...)` 在 254 行，而 `agent_turn_completed` 只在三条**成功**路径上发出（final / reasoning_only / tool_calls）。探针 P3 确认：模型调用失败、`INVALID_RESPONSE`、空响应三条路径全部 `started=1, completed=0`。

对照 `tool_calling/loop.py:250-463`：`ActionToolLoop` 用 `try/except CancelledError/finally` 包住整轮，`finally` 里无条件 `_emit("action_turn_completed", {**turn_identity, **turn_completion})`，`turn_completion` 预置为失败态。两个循环的事件契约在这一点上不一致。

**实际影响**：

- `SubAgentTool.on_child_event` 只在 `agent_turn_completed` 时派生 `subagent_iteration` 事件。子 Agent 在失败轮上不产生迭代事件，WebUI 的子 Agent 进度会停在上一轮。
- Tracing 侧**不受影响**——`agent_loop_completed` 会调用 `_finish_turn(_turn_key(payload))` 兜底关闭 turn span。这点与 -01 报告的表述略有出入，值得记下：这是纯事件契约问题，不是 span 泄漏。

**建议**：照搬 `ActionToolLoop` 的 `try/finally` 结构，约 20 行。

---

### P2-4：`for_sandbox()` 重建独立并发信号量，绕过全局子进程并发上限

**位置**：`tools/shell_tool.py:44/49-59`、`tools/code_executor.py:45/48-57`

```python
self._concurrency_sem = asyncio.Semaphore(max_concurrent)   # __init__
...
def for_sandbox(self, sandbox_dir: str) -> "ShellTool":
    return ShellTool(..., max_concurrent=self._max_concurrent, ...)  # 新实例 = 新信号量
```

探针 P6 确认父子信号量是不同对象。`settings.toml` 里 `shell_max_concurrent = 3`、`code_max_concurrent = 3`、`subagent_max_concurrent = 2`，因此实际上限是 `3(父) + 2 × 3(子) = 9` 个并发子进程，而不是配置声明的 3。在 `shell_mode = "trusted"` / `python_mode = "trusted"` 的本地环境下，这是真实的资源风险。

**建议**：`for_sandbox` 传递信号量对象本身而非计数，让所有派生实例共享同一个 `asyncio.Semaphore`。约 15 行，3 个文件。

---

### P2-5：错误类工具结果完全不截断

**位置**：`tool_calling/tool_execution.py:112-125`

```python
def truncate_tool_result_for_llm(result, limit, is_error):
    if is_error or not isinstance(result, str) or len(result) <= limit:
        return result, result       # <- is_error 直接绕过截断
```

探针 P4：一个返回 50000 字符错误的工具，其 tool message 长度 50162，而 `truncation_limit=2000`。

单看这个设计有道理（错误信息截断可能丢掉恢复所需的关键行）。但它与 AgentLoop 的**永久历史**语义相乘：这条 50KB 的错误会留在 `_history` 里，之后**每一轮**都完整重传，并且直接把历史推过 `max_context_tokens`，从而触发 P1-1 的每轮摘要。两个问题互相放大。

**建议**：错误结果保留头尾（例如首 1000 + 尾 1000 字符）而不是不截断——错误的关键信息通常在开头（异常类型）和结尾（traceback 末行），中间的堆栈可以省略。约 10 行。

---

### P3 清单

**P3-1 `_apply_tool_filter` 不同步 ToolRouter**（`agent_loop/loop.py:545-558`）
`self.tools` 与 `self.tool_schemas` 被 skill 过滤后收缩，但 `self.tool_router._available_tools` 仍是初始全集（探针 P5 确认 `beta` 仍在）。`ToolRouter.get_alternatives` 会推荐已被过滤掉的工具。

**P3-2 skill `license` 由进程级 config 推断（新增）**（`skills/loader.py:407-420`）
license/信任级别通过与 `config.SKILLS_PROJECT_DIR` / `config.SKILLS_USER_DIR` 比对目录推断，读的是**进程级** `config.py` 门面常量，而不是当前 runtime 的 `AppSettings`。`evaluation/runner.py:101` 把每个矩阵单元的 `skills_user_dir` 重定向到临时目录后，从该目录加载的技能会被判成 `third_party` 而非 `user`——而 `skills/activation.py:162` 正是用 `skill.meta.license` 决定 `scan_skill_content` 的信任级别。方向是偏严格（安全），但**破坏了评测单元与正常运行的等价性**，`skills + skill_auto_distill + guardrails` 组合的 cell 结果不可与常规运行对比。

**P3-3 `_max_content_tokens` 是死参数（新增）**（`skills/activation.py:62/241`、`skills/registry.py:111`）
`SkillActivationTool` 接收并 `clone()` 传递 `max_content_tokens`，但从不使用它；真正的内容截断在 `SkillRegistry.load_full_content` 里读全局 `config.SKILLS_MAX_CONTENT_TOKENS`。per-runtime 的 `capabilities.skills_max_content_tokens` 覆盖实际无效。

**P3-4 `reset_usage()` 清空共享统计基线（新增）**（`runtime/app.py:50`、`llm/client.py:569-571`）
`AgentRuntime.run()` 每次无条件调用 `self.context.llm_client.reset_usage()`，该方法 `self._call_records.clear()`。而 `TaskEngine.usage_marker()` 返回的 `records_before` 是一个**列表下标**。同一个 runtime 上并发跑两个任务时，后启动的 run 会清空前一个 run 的记录，导致 `stats_since` 的 `records[records_before:]` 退化为空、`llm_calls` 只剩本地观测值。当前 CLI / WebUI / Evaluation 都是「一个 runtime 一次任务」，所以尚不触发；但 `AgentRuntime.run` 本身并没有拒绝并发，这是个等待被踩的约束。建议要么在 `run()` 上加显式串行保护，要么把 marker 从下标换成单调游标。

**P3-5 DAG 构造了一个从不用于执行的 ActionExecutor**（`runtime/app.py:255-264`、`engines/dag.py:107`）
`_build_engine` 既传 `executor=` 又传 `executor_factory=`；`_DagActionAdapter.__init__` 无条件调用 `engine.new_action_executor()`，而后者在 factory 存在时永远走 factory。于是构造函数里那个 executor（连同其 `activate_skill` 回调注册）纯属浪费，只有 `RecordingActionExecutor.results` 这个列表被当作轨迹容器使用。可读性成本高于运行成本。

**P3-6 DAG `gather` 异常分支不记录轨迹，且漏掉 `BaseException`**（`dag/executor.py:214-228`）
`isinstance(result, Exception)` 分支只做状态转移，不调用 `_record_external_result`，该节点不会出现在 `EngineResult.actions` 里——与 timeout 分支的处理不一致。另外 `asyncio.gather(return_exceptions=True)` 在子任务被单独取消时会把 `CancelledError` 作为结果返回，而它在 3.8+ 继承自 `BaseException` 而非 `Exception`，会漏过该分支落到 `dag.state.merge_result(node.id, result.output)` 触发 `AttributeError`。当前基本不可达（父任务取消时 gather 会直接重抛），属潜在不一致。

**P3-7 `ActionToolLoop.caller_tag` 用 `id(messages)`**（`tool_calling/loop.py:247`）
`f"{agent_name}:{step_id}:{id(messages):x}"`。CPython 的 `id()` 在对象释放后会被复用，顺序执行的两个 Action 完全可能拿到相同 tag。当前因为 `records_before` 每轮重取而没有实际串话，但这是靠外层不变量兜住的脆弱设计。`AgentLoop` 同一目的用的是 `uuid4().hex`（`agent_loop/loop.py:172`）——两处应统一到 uuid4。

**P3-8 token 预算阈值不一致**（`agent_loop/loop.py:320-323` vs `485-489`）
轮内检查用 `self.tokens_used > self.max_total_tokens`（超出才失败），轮首检查用 `_remaining_tokens() > 0` 即 `tokens_used >= max_total_tokens` 就失败。恰好用满预算时两处判定相反。

**P3-9 TracingBridge 用子串匹配 span key**（`tracing/bridge.py:302/327/342`）
`_finish_action_turn` 用 `key.rsplit(":", 1)[0].split(":", 1)[-1]` 反解 action_id，`_finish_*_for_action` 用 `f":{action_id}:" in key` 匹配。当前 planner 生成的 ID 形如 `act_1_1`，不含冒号，因此可用；但这是对 ID 字符集的隐式依赖，且拼接键没有转义。建议改用 `tuple` key 或 dataclass key。

**P3-10 `checkpoint/store.py:47` 的 `finally: unlink`**
`os.replace` 成功后临时文件已不存在，`unlink(missing_ok=True)` 是空操作；`os.replace` 失败时才有清理意义。但同一 `task_id` 的两个并发 `save` 共用同一个 `.tmp` 路径，先完成的那个会 unlink 掉另一个正在写的临时文件。建议 tmp 文件名加随机后缀，并把 unlink 收进 `except` 而非 `finally`。

**P3-11 `checkpoint_max_per_task` 语义不符**（`settings.toml:77`、`engines/dag.py:104`）
该配置位于 `[capabilities]` 且名字暗示「每个任务保留几个 checkpoint」，但 v2 store 每个 task_id 只存一份最新记录；它实际唯一的用途是 `dag.max_checkpoints`，即 DAG 内部 super-step 快照上限。名字与所在 section 都有误导性。

**P3-12 根 AgentLoop 无 token 预算（新增）**（`engines/agent_loop.py:55-67`）
`AgentLoopEngine` 构造 `AgentLoop` 时不传 `max_total_tokens`（默认 `None`），只有 `SubAgentTool` 传（`max_tokens_per_call=120000`）。于是 `_remaining_tokens()` 恒为 `None`，`call_kwargs["max_tokens"]` 不设，`_token_budget_failure` 永远不触发。子 Agent 有硬性 token 上限而根循环没有——考虑到 AgentLoop 是默认引擎且历史只增不减，这个不对称值得至少在文档里写清楚，或补一个 `engines.max_agent_total_tokens` 配置。

**P3-13 `docs/engines.md` 未记录 AgentLoop 的 turn 预算语义**
文档只写「effort ... adjusts model temperature, loop or Action turn limits」，没有说明：(a) `effort=low` 会把 `max_agent_turns` 折半（`agent_loop/loop.py:390`）；(b) 上下文摘要调用计入 turn 预算。这两条会直接影响读者对实验结果的解释。

---

## 5. 观察项（非缺陷）

**OBS-1 工具调用由并行改为串行。** `execute_tool_calls` 明确按 provider 返回顺序串行执行，注释给了理由（同一 assistant 消息里的工具调用不保证独立，写操作顺序敏感）。这是正确的默认，但会拉长有多个独立只读工具调用时的延迟，且**直接影响三引擎的 latency 对比**——AgentLoop 更倾向于单轮多工具，受影响最大。建议在评测报告里注明这个前提；若后续要优化，正确做法是给 `BaseTool` 加 `side_effect_free` 声明后再并行，而不是无条件并行。

**OBS-2 `classify_tool_result` 的启发式偏宽。** `result.lstrip().lower().startswith("error")` 会把任何以 "error" 开头的正常文本判为失败（例如一次成功的 grep 返回 `"error handling in ..."`）。会污染 `ToolRouter` 的失败计数并给模型加上误导性的重试提示。属既有行为，非本次引入。

**OBS-3 `validate_tool_arguments` 每次调用都重跑 `check_schema`。** 工具 schema 在运行期不变，每次工具调用都重新编译校验器有固定开销。可按 `tool.name` 缓存 validator。

**OBS-4 `AgentLoop` 里 `todo_write` 的双重创建。** `__init__` 创建一次，`run()` 里为「复用同一 AgentLoop 开新任务」再创建一次。语义是对的（任务级重置），但第一个实例在 `run()` 前从不被使用，`_tools_full` 也要跟着改写——可以简化为只在 `run()` 里创建。

---

## 6. 与 changelog 声明的对照核查

| changelog 声明 | 核查结果 |
|---|---|
| §2.1 `EngineKind` 只含三值，删除 `ExecutorKind` / `EngineSelector` / `--executor` | 属实，活动源码 0 命中 |
| §2.2-4 不注入 `Continue` 合成 user 消息 | 属实，`_history` 仅追加 assistant 与 tool 消息 |
| §2.2-5 同轮文本 + tool calls 不视为终态 | 属实（`agent_loop/loop.py:354`） |
| §2.2-8 重复 tool-call ID 记为 `invalid_response` | 属实，且在任何工具执行前拒绝 |
| §2.2-9 `max_turns` 以模型调用数计算，**包括上下文压缩模型调用** | **属实，且这正是 P1-1**。changelog 把它写成了设计意图，但没有说明它会使 AgentLoop 的有效预算低于配置值、并污染 `llm_calls` 对比 |
| §4.2 上下文压缩只构造临时 model-view，不改写 canonical history | 属实——但未缓存，导致每轮重算 |
| §4.5 全局 `ToolRegistry` 不含 `todo_write`，P&E 工具集不含 | 属实（`TodoWriteTool` 仅在 `AgentLoop.__init__` / `run` 内创建） |
| §4.6 结构性排除 `subagent` / `ask_user` / 父级 memory 写工具，深度固定为 1 | 属实，schema 与解析两处均生效 |
| §4.6 成功子循环最终文本直接返回，无二次摘要调用 | 属实 |
| §4.7 统计避免子 Agent 双计 | 属实。`tool_calls` 用加法（记录里没有工具维度），`llm_calls` / `reasoning_tokens` 用 `max(全局记录, 本地+子级)`，逻辑正确 |
| §4.8 Evaluation 指标读取 `EngineStats` | 属实，但**丢弃了 `stop_reason`，并把 engine success 与 verifier 合并**（P2-1）；changelog §4.8 未提及这一点 |
| §4.8 每个矩阵单元隔离 state / sandbox / checkpoint / user-skill 目录 | 目录确实被重定向，但 `skills/loader.py` 的 license 推断仍读进程级 config（P3-2），隔离**不完全** |
| §4.8 Checkpoint v2，v1 显式拒绝、列表跳过并警告 | 属实（`store.py:58-65`、`list_tasks` 的 `except CheckpointError` 分支） |
| §6.1 全部静态验收命令通过 | 复核通过 |

---

## 7. 建议的处理顺序

| 顺序 | 项 | 理由 | 预估改动 |
|---|---|---|---|
| 1 | **P1-1** 压缩视图缓存 + 摘要调用从 turn 预算剥离 | 决定引擎对比结论是否可信；跑真实评测前必须先修 | `context/manager.py` + `agent_loop/loop.py`，约 60 行 |
| 2 | **P2-1** `CaseMetrics` 补 `engine_success` + `stop_reason` | 让重构辛苦建立的 typed stop reason 在对比中真正可见；改动小、不碰引擎 | 3 文件约 30 行 |
| 3 | **P2-2** `EngineStats` 补 token 字段并回填评测 | 与 1、2 同属「让对比可信」，且与现有 reasoning_tokens 完全同构 | 3 文件约 30 行 |
| 4 | **P2-4** 子工具共享并发信号量 | 本机 trusted 模式下的实际资源风险 | 3 文件约 15 行 |
| 5 | **P2-3** AgentLoop turn 事件 try/finally | 统一两个循环的事件契约，修复 subagent 迭代可见性 | 约 20 行 |
| 6 | **P2-5** 错误结果头尾截断 | 与 P1-1 相互加剧，修完 1 后收益更明显 | 约 10 行 |
| 7 | P3-2 / P3-3 skills 的 config 门面依赖 | 影响评测可复现性；也是「新代码用 AppSettings」这条约定的最后几个缺口 | 2 文件约 20 行 |
| 8 | P3-1、P3-6~P3-13 | 一致性、脆弱键与文档，可合并成一次清理提交 | 分散小改 |
| 9 | OBS-1 | 先在评测报告中注明串行前提；`side_effect_free` 声明进 backlog | 文档 |

**建议在 1、2、3 完成后再执行 changelog §8 的真实运行验证**，否则采集到的三引擎对比样本需要重跑。

---

## 8. 验证边界

本轮**没有**执行：真实 LLM API、真实网络/浏览器/MCP、AgentBay、正式 Evaluation 评测、WebUI 浏览器视觉验收、新增 unittest/pytest 套件。

因此本报告可以证明：

- 代码可编译、核心包可导入、`git diff --check` 干净。
- 三引擎的公开契约、包导出、CLI/WebUI/Evaluation 表面一致，旧架构已从活动源码删除。
- 假模型下的消息协议、终止条件、事件配对、统计聚合、隔离边界、错误分类符合（或不符合）设计——具体见 §2 的探针输出。

它**不能**证明：

- 某个真实模型会遵守 tool-use 协议，或不同 OpenAI-compatible provider 的 reasoning 字段与 usage 完全兼容。
- AgentLoop 在真实长任务中优于或劣于 Sequential / DAG。
- 真实工具、网络、SubAgent 并发与正式评测下的质量、延迟、成本表现。
- P1-1 修复后 `llm_calls` 偏差的实际幅度（需要真实运行才能量化）。

---

## 9. 结论

第二轮独立复核维持第一轮的整体判断：**这次重构在架构上是准确的，实现质量配得上它的规模**。识别出旧 `TodoEngine` 只是 Plan-and-Execute 的变体、拒绝在旧层上加适配器、以删除为主收缩公开范式——这些决定让「模型自主控制 vs 外置计划控制」第一次成为项目里一条真实可比的轴。契约层、隔离边界、取消语义、事件驱动的宿主解耦，都经得起读码检验。

主要遗留风险仍然不在架构，而在**度量**，而且本轮把这个判断加强了一层：

- **采集端**：AgentLoop 的每轮重压缩既抬高 `llm_calls` 又压缩有效 turn 预算（P1-1，已用探针量化）。
- **记录端**：评测把 `stop_reason` 整个丢掉，并把引擎成功与 verifier 结果合并成一个数（P2-1）——这意味着**即使修好了采集端，落盘的报告仍然回答不了「为什么失败」**。
- **交叉验证**：没有 token 维度（P2-2），偏差既无法被独立验证也无法被量化。

这三项构成一条完整的链路缺口。把它们补上之后，这套框架才真正具备它想要的那个能力——用同一任务、同一模型、同一 capability 集，得出可信且可归因的三引擎对比。
