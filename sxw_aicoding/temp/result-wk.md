# 深度代码评审：manus_demo v9 SubAgent 多智能体机制

> 评审日期：2026-05-13
> 评审人：知恩（资深 AI Agent 架构师视角）
> 评审范围：`agents/subagent.py`、`tools/subagent_tool.py`、`agents/orchestrator.py` 集成点、`schema.py`、`config.py`、`tracing/bridge.py`、`evaluation/runner.py`、`main.py` UI、`tests/test_subagent.py`
> 对照基准：`purrfect-bubbling-fog.md`（实施方案）、调研文档 §1.2 Claude Code Subagent、§1–§2 十大反模式

---

## 一、总体结论

整体落地质量 **B+（良好）**：计划书列出的 10 个实现步骤在源码层面**几乎全部可追溯**（schema/config/subagent.py/subagent_tool.py/orchestrator 注入/main UI/tracing span/evaluation probe/tests 均有对应实现），depth=1、结构化摘要、沙箱子目录、调用次数熔断四项核心反模式防御**物理层面成立**。

但存在 **3 处与方案声明不一致的降级**、**1 个关键反模式（#8 Token 熔断）只实现了事后核算而未真正熔断**、以及**若干误导性字段与可观测性断点**需要修复。**不建议现阶段将 `SUBAGENT_ENABLED=true` 作为默认开关上线**。

---

## 二、按严重度分级的问题清单

### 🔴 P0 — 反模式防御名不副实（必须修复）

#### P0-1：`SUBAGENT_MAX_TOKENS_PER_CALL` 未构成熔断，只是一个"被存储但从不检查"的字段

实施方案 §Step 3 明确写（行 187）：「**Token 预算检查**（反模式 #8）：每次 ReAct 迭代后检查累计 token 是否超过 `max_tokens`，超出则提前终止」。

实际源码：
- `agents/subagent.py:97` 将 `max_tokens` 存入 `self.max_tokens`
- `agents/subagent.py:133-136` 的 `_get_total_tokens()` 只在 `run()` 开始（147 行）和结束（170、232 等）各调一次，**用于事后计算 `tokens_used`**
- 搜索 `max_tokens` 在该文件的所有引用只有 3 处：定义、构造赋值、以及摘要生成时的 `max_tokens=1500`（那是给 LLM 的 completion tokens 上限，与反模式 #8 无关）
- **`ReActEngine.execute()` 的循环体（engine.py:121 起）中根本没有任何回调 / hook 让 SubAgent 中途打断**

**结论**：反模式 #8 的"per-call token 预算熔断"是假防御。一个行为异常的子 Agent 只受 `max_iterations` 和 `timeout` 约束，Token 可以在单次迭代内爆掉（一次 chat_with_tools 就可能消耗 10k+ token）。

**建议修复方向（二选一）**：
1. 给 `ReActEngine.execute` 增加 `on_iteration` 回调（或 `token_budget` 参数），SubAgent 在回调里检查 `self._get_total_tokens() - tokens_before >= self.max_tokens` 并抛 `SubAgentTokenExhausted` 异常，`SubAgent.run` 捕获后进入 FAILED 分支；
2. 在 `subagent.py:160` 的 `wait_for` 外层包装轮询任务同步监控 token 余额，超额时 `task.cancel()`。

反模式汇总表（计划 §370）对 #8 声称"`config, SubAgent.run(), SubAgentTool`"三处防御，实际只有 `config` 和 `SubAgentTool` 的**全局调用次数**生效，单次 per-call token 预算完全未落地。

#### P0-2：`iterations_used` 字段语义错误，评测/观测数据失真

`agents/subagent.py:183`：
```python
iterations_used=step_result.tool_calls_log and len(step_result.tool_calls_log) or 0,
```

这里把 **工具调用次数** 当作 **ReAct 迭代次数**。实际 ReAct 引擎里这两者可能不等：
- 一次迭代可能触发多个并行 tool_call（`engine.py:154` 处 `response_msg.tool_calls` 是 list）
- 一次迭代也可能不调任何工具（LLM 直接给最终答案，`tool_calls_log` 长度不变）

同一文件 `subagent.py:193` 把该值赋给 `result.iterations_used` 并发射到事件 / tracing 属性（`bridge.py:818` 的 `SUBAGENT_ITERATIONS`）。这会让 §Step 8 的 EvaluationProbe 指标 `subagent_avg_iterations` 失真，也违反反模式 #10 的初衷。

更严重的是失败分支 `subagent.py:215` 写死 `iterations_used=0` —— 失败的 SubAgent 即便已跑了 8 轮迭代，事件里也永远是 0，让排查无从下手。

**建议**：让 `ReActEngine.execute()` 在 `StepResult` 中额外返回 `iterations_completed: int`（最后一次 `iteration` 计数值），SubAgent 直接透传。

#### P0-3：事件键名与 Probe 读取错配，评测指标静默丢失

- `subagent.py:193` 发射 `"tool_calls_count": result.tool_calls_count`（来源于 `len(step_result.tool_calls_log)`，第 182 行）
- `subagent.py:189-196` 发射 complete 事件里**没有** `iterations_used` 键，只有 `"iterations": result.iterations_used`（第 192 行）
- `evaluation/runner.py:303` 却读 `data.get("iterations_used", 0)`

**结果**：任务跑完后 EvaluationProbe 收集到的 `iterations_used` 永远是默认值 0。`duration_ms` 和 `tool_calls_count` 倒是对得上。

同一个问题在 `subagent_failed`/`subagent_timed_out` 事件（`subagent.py:222-226`、`254-258`、`286-291`）里一致存在 —— 发射的是 `"iterations"`，读取的是 `"iterations_used"`。

这是一个编译器不会报错、测试 mock 也难发现的"静默字段错配"，直接让反模式 #10（评测失真防御）失效。

**建议**：统一键名为 `iterations_used`，或在 `runner.py:303, 313` 加容错读取 `data.get("iterations_used", data.get("iterations", 0))` 做兼容，并补单测。


---

### 🟠 P1 — 与实施方案声明不符（应修复）

#### P1-1：`parent_agent_name` 在 SubAgentTool 层被硬编码为字符串 `"parent"`

`tools/subagent_tool.py:162`：
```python
parent_agent_name="parent",
```

计划原意（§79、§Step 7）是让 tracing span 的 `subagent.parent_agent` 属性承载**真实派生者名称**（ExecutorAgent / EmergentPlannerAgent / GoalDrivenPlannerAgent），以便区分不同 Agent 的子派生分布。

当前实现使所有 SubAgent 的 `parent_agent` 在 span 里恒为 `"parent"`，**反模式 #9（可观测性断裂）防御的关键信号被抹平**。

SubAgentTool 并不知道自己挂在哪个 Agent 上，这是**设计缺失**而非笔误 —— 需要让 Orchestrator 在注入时把调用方身份写到 Tool 里，或每次 execute 时从调用栈 / ReAct context 透传。

#### P1-2：`think_json` 未使用，降级为 `llm_client.chat_json` —— 方案偏离

计划 §Step 3（行 191）要求：「使用 `self.think_json()` 要求 LLM 输出符合 SubAgentSummary schema 的 JSON」。

实际 `subagent.py:323` 走 `self.llm_client.chat_json`，并且**没有继承 BaseAgent**（`class SubAgent:` 裸类 — 第 71 行），而计划 §Step 3（行 159）写的是 `class SubAgent(BaseAgent)`。

**后果**：
- 无法复用 BaseAgent 的 JSON 校验 / 重试 / 自愈机制
- `think_json` 通常会在 schema 不符时自动重试一次，当前的 `if isinstance(response, dict) and "accomplished" in response` 判断（327 行）是非常弱的结构校验 —— 只要 LLM 随便回一个含 `accomplished` 键的对象就通过，**反模式 #5（Self-Critique，强制 issues 字段）的"结构化"保证大打折扣**
- 同一文件 119 行 `context_manager=context_manager or ContextManager()` 创建了一个**全新的 ContextManager**，如果 SubAgentTool 传入了父的 context_manager（这里有传，见 subagent_tool.py:157），实际会沿用父的；但调用路径上有两层 `or ContextManager()` 兜底（subagent_tool.py:54 和 subagent.py:118），构造顺序容易引入非预期实例

**建议**：按计划继承 `BaseAgent`，利用 `think_json` 并把 `response` 通过 `SubAgentSummary.model_validate` 校验 —— 非法字段直接走 fallback。

#### P1-3：`reset_call_count` 未重置 `_subagent_counter`

`tools/subagent_tool.py:195-197` 只把 `_call_count` 清零，但 `_subagent_counter`（第 60 行）持续累加。这会导致：
- 第二个任务里的 sandbox 目录变成 `subagent_4/`、`subagent_5/` …… 而不是从 `_1` 重新开始
- 如果用户跨任务期望 subagent_N 的命名稳定，span name `subagent.execute.SubAgent-4` 出现在第二个任务里会让 trace 浏览困惑

这个不致命，但考虑到反模式 #4 的沙箱隔离初衷是"每次任务独立工作区"，建议在 `reset_call_count` 里一并归零，或在方法名上改为 `reset_task_state`。

#### P1-4：失败 / 超时分支丢失 tool_calls_log，违背反模式 #6 初衷

`subagent.py:234-260` 超时分支未设 `tool_calls_log` 字段，`subagent.py:267-284` 通用异常分支同样未设 —— 它们依赖 `SubAgentResult.tool_calls_log` 的 `default_factory=list`（schema.py:701）。

反模式 #6 强调"完整 tool_calls_log 保留用于调试"，但**超时 / 异常恰恰是最需要回看工具调用轨迹的场景**，这里却丢弃了 `asyncio.wait_for` 被中断前已发生的 tool_calls 记录。

**修复建议**：把 `self._react_engine` 的 tool_calls_log 维护成可中途取回的状态（或让 SubAgent 订阅 `tool_start`/`tool_end` 事件自行累积），超时 / 异常分支从 `self.tool_calls_log_so_far` 拷贝填充。

---

### 🟡 P2 — 反模式 / 最佳实践偏离（建议修复）

#### P2-1：默认白名单"全量授权"，违背 Claude Code"最小权限"实践

`tools/subagent_tool.py:122-126`：
```python
if not validated_whitelist:
    validated_whitelist = [
        name for name in self._available_tools.keys()
        if name != "subagent"
    ]
```

当 LLM 不传 `tool_whitelist` 时，**默认授予所有父级工具**。这与 §1.2 Claude Code 最佳实践的「父 agent 声明允许工具白名单；subagent 自带受限 tool 集（例如 code-review-subagent 只能 read）」（调研文档第 76 行）相违 —— Claude Code 官方设计是"默认最小权限，显式授权"。

**具体风险**：如果一个"代码检索型"子任务意外被赋予了 `shell`/`code_executor` 权限，反模式 #4（双写冲突，已通过 sandbox 缓解）和**安全侧**都有非必要暴露。

**建议**：默认改为"只读工具子集"（read_file / grep / web_search / list_files），需要写能力时必须显式声明白名单。

#### P2-2：SubAgent system prompt 缺少 Claude Code 实证有效的几条抑制语

调研文档 §1.2（第 74 行）强调 Claude Code Subagent 是 **"独立 system prompt + 独立 context 窗口 + 独立工具子集"**，而且 Claude 官方会在 Subagent 系统提示中明确"你只负责被分配的子任务，完成后立即返回摘要，不要扩大 scope"。

`subagent.py:37-52` 的 `SUBAGENT_SYSTEM_PROMPT` 有第 4 条"Focus ONLY on the specific subtask assigned to you — do not expand scope"，方向是对的。但缺了三条实践证明有效的抑制：
- 不要主动读取无关文件以"收集背景"（对抗反模式 #8 token 爆炸）
- 如果任务描述模糊，不要自行补全缺失细节；在 findings 里指出并返回
- 不要重复调用同一工具（Claude Code 官方 prompt 有显式防抖语）

这类细节对"子 Agent 失控"的预防作用，远大于超时和调用次数熔断。

#### P2-3：短 output 跳过 LLM 结构化总结，反模式 #5 实质失效

`subagent.py:305-313`：
```python
if len(output) <= config.SUBAGENT_SUMMARY_MAX_LENGTH:
    return SubAgentSummary(
        accomplished=output,
        findings="",
        issues="",
        artifacts=[],
        tool_calls_summary="",
    )
```

这意味着**多数中等长度任务**（output < 2000 字符）根本不会生成结构化字段 —— `findings` / `issues` / `artifacts` / `tool_calls_summary` 恒为空。反模式 #5（Self-Critique，强制 issues 字段减少美化偏差）在这条路径上**完全失效**。

更隐蔽的问题是：LLM 的最终 output 可能本身是美化过的（"已完成所有子任务 ✓"），这里直接塞进 `accomplished` 字段，没有任何结构化拆分。

**建议**：无论 output 长度，都走一次轻量结构化生成（或至少让 LLM 回 issues / artifacts 两字段的 JSON）；只有在 LLM 降级时才保留当前 fallback 路径。

#### P2-4：反模式 #2（上下文泄漏）防御在事件层有隐患

`subagent.py:151-156` 的 `subagent_start` 事件发射了 `task_description`，`bridge.py:800` 写入 span 属性 `SUBAGENT_TASK` —— 这里通过 `_safe_set_attr` 有截断保护（好）。

但 `subagent_complete` 事件（`subagent.py:189-196`）发射了 `summary`（即整个 `summary_text` JSON 字符串）。该字段后续：
- 流向 main.py:418 的 `Panel` 渲染（用户可见，OK）
- 流向 evaluation/runner.py：probe 没消费 `summary` 字段，OK
- 流向 tracing bridge：`_on_subagent_complete`（bridge.py:808-824）没有写入 summary 到 span，OK

实际没泄漏。但计划 §363 声称"事件不泄漏内部细节" —— 严格讲 summary 本身就是"内部细节"，如果未来某个订阅者记录了 summary，会构成反模式 #2 回归。

**建议**：在事件上加注释或把 summary 从 subagent_complete 里移除（让 UI 单独订阅 `subagent_summary` 事件）。

---

### 🟢 P3 — 细节 / 可读性

- **`agents/subagent.py:107-108`**：`tool_schemas` 赋值后未再被使用（实际 schemas 由 `ReActEngine` 内部的 `BaseTool.to_openai_tool()` 重新生成，engine.py:146）。属于死代码，删除即可。
- **`agents/subagent.py:123-125`**：`_summary_messages` 初始化时 push 了 `system_prompt`，但 `_summarize_result` 实际用的是 `list(self._summary_messages)` 副本（317 行），并**没有**添加任何总结引导的 system 消息 —— 当前实现里第一条 system 就是子 agent 的执行指令，紧接一条 SUMMARIZE_PROMPT 的 user 消息。语义上"用执行用的 system + 总结用的 user"是可行的，但容易让人误读，建议把总结消息列表独立拆出来。
- **`tools/subagent_tool.py:101-103`**：限流错误信息以字符串形式返回给 LLM，下游 ReAct 会把它塞回 messages。这是正确做法（LLM 能感知并停止派生），但建议同时 `_emit("subagent_limit_exceeded", ...)` 以便 tracing / 评测看到"限流发生了"。现在限流事件是不可观测的。
- **`tools/subagent_tool.py:79`**：`parameters_schema` 在每次 LLM 调用时动态生成，会把**所有工具名**暴露给 LLM（包括敏感工具名如 `shell`）。这在安全敏感场景里需要过滤，但当前项目是 demo，接受现状。
- **`agents/orchestrator.py:115-128`**：SubAgentTool 注入在 PlannerAgent / ExecutorAgent / EmergentPlannerAgent / GoalDrivenPlannerAgent 构造**之前**完成，四个下游 Agent 都能通过 `tools` 参数拿到 SubAgentTool。**Planner 不应该持有 SubAgentTool**（它只做规划不执行），如果消费了，会让 Planner 也能派生 subagent —— 语义污染。建议 Orchestrator 显式传 `tools_for_planner = [t for t in tools if t.name != "subagent"]`。
- **调研文档 §1.2 第 84 行**：「Named, isolated Claude instance with its own system prompt」—— 当前 `SubAgent.name = f"SubAgent-{counter}"` 只是序号命名，没有体现"类型化命名"（Claude Code 有 `code-reviewer` / `test-runner` / `debugger` 等命名的专家型 subagent）。这不是 bug，是 v10 可以演进的方向：让 LLM 在 `task_description` 之外再声明 `subagent_type`，以便做 prompt 特化和工具白名单预设。

---

## 三、对照实施方案的交付完整性矩阵

| 步骤 | 方案承诺 | 实际落地 | 状态 |
|---|---|---|---|
| Step 1 schema | 3 个模型 +30 行 | schema.py:657-701 三模型齐全 | ✅ |
| Step 2 config | 7 个变量 | config.py:101-107 七项齐全，默认值一致 | ✅ |
| Step 3 SubAgent 类 | 继承 BaseAgent，`think_json` 摘要，**token 熔断** | 裸类（非 BaseAgent），用 `chat_json`；**token 熔断未实现** | ⚠️ 见 P1-2 / **P0-1** |
| Step 4 SubAgentTool | meta-tool，深度=1，调用次数上限 | 实现完整，depth=1 结构性过滤生效 | ✅ |
| Step 5 Orchestrator 集成 | 注入 + reset_call_count | orchestrator.py:115-128、193-195 均到位 | ✅ |
| Step 6 main.py UI | 4 个事件渲染 | main.py:400-432 四事件齐全 | ✅ |
| Step 7 tracing | spans + bridge 父 span context | spans.py / bridge.py 齐全，**parent_agent 被硬编码** | ⚠️ 见 P1-1 |
| Step 8 evaluation | subagent_results + 指标 | 收集到位，**字段名错配** | ❌ 见 P0-3 |
| Step 9 导出 | `__init__.py` 加 `__all__` | import 路径已工作 | ✅ |
| Step 10 测试 | 6 类测试 | tests/test_subagent.py 889 行齐全 | ✅ |

---

## 四、反模式防御的有效性复核

> 按计划 §358 的防御总结表逐项审计，标注实际状态

| # | 反模式 | 计划声称 | 实际状态 | 证据 |
|---|---|---|---|---|
| 1 | 角色爆炸 | 5 角色上限 | ✅ 真实 | Orchestrator 只加了 1 个工具 |
| 2 | 上下文泄漏 | 独立 messages + summary only | ✅ 基本成立 | engine.py:115 每次 execute 都 `messages=[]` 新建 |
| 3 | 通信死循环 | depth=1 + 调用次数上限 | ✅ 真实 | subagent_tool.py:114-115 过滤，`_call_count` 限流 |
| 4 | 双写冲突 | 工作目录隔离 | ⚠️ 部分 | sandbox 目录建立了，但 system prompt 的"必须在此目录下操作"是软约束，shell / code_executor 依然能写任意路径 |
| 5 | Self-Critique | 强制 issues 字段 | ⚠️ 部分失效 | output < 2000 字符时 issues 恒为空（见 P2-3） |
| 6 | Summary Loss | 结构化 + tool_calls_log 保留 | ⚠️ 部分 | 超时 / 异常分支 tool_calls_log 丢失（见 P1-4） |
| 7 | 工具分裂 | 单一 SubAgentTool | ✅ 真实 | 只有一个 meta-tool |
| 8 | Token 爆炸 | per-call 预算 + 调用次数 + 默认关闭 | ❌ **per-call 预算未实现** | 见 P0-1 |
| 9 | 可观测性断裂 | 父 span context + parent_agent 属性 | ⚠️ 部分 | span 父子关联正确，但 parent_agent 硬编码为 "parent"（见 P1-1） |
| 10 | 评测失真 | EvaluationProbe 收集指标 | ❌ **iterations_used 永远为 0** | 见 P0-3 |

**有效防御率：7/10 完整成立，3/10 有实质缺陷**。对照计划的"十大反模式全覆盖"宣称，存在 30% 的"纸面防御"。

---

## 五、与 Claude Code Subagent 最佳实践（§1.2）的差距

| 维度 | Claude Code | 本项目 v9 | 差距评级 |
|---|---|---|---|
| 通信拓扑 | `Task(description, subagent_type)` 单次派生 | `subagent(task_description, tool_whitelist)` | 对齐 ✅ |
| 深度限制 | depth=1 | depth=1（物理过滤） | 对齐 ✅ |
| Subagent 策略 | 独立 system prompt + 独立 context + 独立工具子集 + **命名类型化** | 前三者对齐，**命名仅序号** | 中度差距，建议 v10 引入 `subagent_type` |
| 上下文隔离 | 只回传摘要 | 只回传 summary_text JSON | 对齐 ✅ |
| 工具接入 | 父声明白名单，**默认最小权限** | 父声明白名单，**默认全量授权** | P2-1 |
| 失败处理 | 主 agent 可复派 | SubAgentTool 限流后 LLM 可收到错误字符串 | 对齐 ✅ |
| 可观测 | Summary isolation + 共享 filesystem scratchpad | Summary 回传 + 沙箱子目录（非共享 scratchpad） | 设计选择不同（更保守） |
| 持久化身份 | Subagent 可长驻、可被反复调用 | 每次 execute 新建 SubAgent 实例 | 中度差距，性能影响可忽略，身份连续性缺失 |

**总体匹配度：约 80%**。核心机制对齐，差距集中在"类型化 subagent"和"默认最小权限"两个工程侧细节。对标 `04-多Agent架构全景.md` 第 351 行给出的"v9/v10 起点建议（depth=1 Subagent + orchestrator-worker + summary-only return）"，本项目已经达到 v9 的目标形态。

---

## 六、修复优先级建议

**必须在合入主干前修复（P0）**：
1. 修正 `iterations_used` / `iterations` 字段错配（P0-3）—— 5 分钟，改 3 处字符串
2. 修正 `iterations_used` 语义错误（P0-2）—— 需要 `ReActEngine` 增加 `iterations_completed` 返回
3. 真正实现 Token 预算熔断（P0-1）—— 需要 ReAct 引擎加回调或轮询监控，约半天工作

**建议在 v9.1 解决（P1）**：
4. `parent_agent_name` 透传真实调用方（P1-1）
5. 继承 BaseAgent 并改用 `think_json` + `SubAgentSummary.model_validate`（P1-2）
6. 失败 / 超时分支保留已发生的 tool_calls_log（P1-4）

**v10 演进方向（P2/P3）**：
7. 默认白名单改为"最小只读集"，需要写权限必须显式声明（P2-1）
8. 短 output 也走一次结构化总结以保住反模式 #5（P2-3）
9. 引入 `subagent_type` 类型化命名，对齐 Claude Code 官方 `code-reviewer` / `test-runner` 范式
10. Planner 不应持有 SubAgentTool（P3 第 5 条）

---

## 七、亮点（值得肯定）

1. **depth=1 的物理过滤实现干净**（subagent_tool.py:114-115、79）—— 不是运行时检查而是工具列表构造时过滤，从根上杜绝递归，这比计划的"runtime 检查"更安全，方案里第 94 行的设计动机落实到位。
2. **SubAgentSummary schema 设计合理**（schema.py:671-681）—— 五个字段覆盖 what / findings / issues / artifacts / tools 五个维度，结构化程度高于一般"summary: str"。
3. **Tracing 父子 span 关联正确**（bridge.py:789-796）—— `trace.set_span_in_context(self._phase_span)` 这种父 context 继承是 OpenTelemetry 正确用法，反模式 #9 的核心诉求成立。
4. **feature flag 默认关闭 + 全局熔断开关（SUBAGENT_MAX_CALLS_PER_TASK=3）**—— 符合调研文档给出的"风险最低起点"的克制精神。
5. **测试覆盖完整**—— tests/test_subagent.py 889 行，按计划 §Step 10 的 6 类测试全部落实（schema / SubAgent / SubAgentTool / Integration / Tracing / AntiPatterns 专项）。

---

## 八、一句话总结

> **设计优秀、落地到位、但反模式 #8（Token 熔断）与 #10（评测数据）存在"声明级 vs 实现级"落差。** 在修复 P0-1 / P0-2 / P0-3 之前，`SUBAGENT_ENABLED=true` 属于"看起来很稳、跑起来失控时无法感知"的状态，不建议默认开启。这三处修复的总工作量约一天，修完后可直接作为 v9 GA 的基线。
