# SubAgent 模块评审问题复核与修复实施计划

> 生成时间: 2026-05-13
> 依据: `result-aone.md`（评审 A）+ `result-wk.md`（评审 B）+ 源码交叉验证
> 原则: 逐条验证评审报告中的问题是否真实存在，评估实际影响，决定是否采纳

---

## 一、复核结论概览

| # | 问题 | 评审来源 | 是否真实存在 | 是否采纳 | 优先级 | 修复量 |
|---|------|---------|------------|---------|--------|--------|
| 1 | Token 预算熔断未实际生效 | A(P1) + B(P0-1) | ✅ 确实存在 | ✅ 采纳 | P0 | ~30行 |
| 2 | `iterations_used` 语义错误 | A(P2) + B(P0-2) | ✅ 确实存在 | ✅ 采纳 | P0 | ~20行 |
| 3 | 事件键名 `iterations` vs `iterations_used` 错配 | B(P0-3) | ✅ 确实存在 | ✅ 采纳 | P0 | ~5行 |
| 4 | `parent_agent_name` 硬编码 `"parent"` | A(P2) + B(P1-1) | ✅ 确实存在 | ✅ 采纳 | P1 | ~10行 |
| 5 | 短输出跳过 LLM 结构化总结 | A(P2) + B(P2-3) | ✅ 确实存在 | ✅ 采纳 | P1 | ~20行 |
| 6 | 失败/超时分支丢失 `tool_calls_log` | B(P1-4) | ✅ 确实存在 | ✅ 采纳 | P1 | ~15行 |
| 7 | SubAgent 未继承 BaseAgent | B(P1-2) | ✅ 存在但 | ⚠️ 部分采纳 | P2 | ~40行 |
| 8 | 摘要未用 `SubAgentSummary.model_validate` | A(P3) + B(P1-2) | ✅ 确实存在 | ✅ 采纳 | P1 | ~5行 |
| 9 | `reset_call_count` 未重置 `_subagent_counter` | B(P1-3) | ✅ 确实存在 | ✅ 采纳 | P2 | ~3行 |
| 10 | 默认白名单"全量授权" | B(P2-1) | ✅ 确实存在 | ⚠️ 部分采纳 | P2 | ~10行 |
| 11 | `tool_schemas` 死代码 | B(P3) | ✅ 确实存在 | ✅ 采纳 | P3 | ~1行 |
| 12 | 限流事件不可观测 | B(P3) | ✅ 确实存在 | ✅ 采纳 | P3 | ~3行 |
| 13 | 双重 timeout 处理 | A(P3) | ✅ 存在但无害 | ⚠️ 加注释 | P3 | ~1行 |
| 14 | `tools/__init__.py` 无条件导入 | A(P5) | ✅ 存在但无害 | ❌ 不采纳 | — | 0 |
| 15 | SubAgent system prompt 缺少抑制语 | B(P2-2) | ✅ 合理建议 | ✅ 采纳 | P2 | ~6行 |
| 16 | 事件层 summary 泄漏隐患 | B(P2-4) | ⚠️ 当前无实际泄漏 | ❌ 不采纳 | — | 0 |
| 17 | Planner 持有 SubAgentTool | B(P3) | ✅ 确实存在 | ⚠️ 低优先级 | P3 | ~5行 |
| 18 | 共享 LLMClient token 差值不安全 | A(P2) | ⚠️ 理论存在 | ⚠️ 部分采纳 | P2 | ~10行 |

---

## 二、逐条详细复核

### 问题 1: Token 预算熔断未实际生效 [P0]

**评审 A 描述**: `self.max_tokens` 赋值后未在 `run()` 方法中使用，`ReActEngine.execute()` 是黑盒调用，SubAgent 无法在迭代间检查 token 消耗。

**评审 B 描述**: 同上，且更详细指出 `max_tokens` 在文件中只有 3 处引用（定义、赋值、摘要生成时 LLM 的 max_tokens=1500），后者与反模式 #8 无关。

**源码验证**:
- `subagent.py:97` — `self.max_tokens = max_tokens or config.SUBAGENT_MAX_TOKENS_PER_CALL` ✅ 赋值存在
- `subagent.py:133-136` — `_get_total_tokens()` 只在 `run()` 开始和结束各调一次 ✅ 确认
- 搜索 `self.max_tokens` 在 `run()` 中的使用 → **仅赋值，无检查逻辑** ✅ 问题确认
- `ReActEngine.execute()` 的 while 循环（`engine.py:121-247`）中**无任何回调/hook** ✅ 问题确认

**实际影响**: 反模式 #8 的 per-call token 预算层形同虚设。虽然 `max_iterations` 和 `timeout` 能间接限制，但单次 LLM 调用（chat_with_tools）可消耗大量 token，在异常情况下 token 消耗不可控。

**采纳方案**: 给 `ReActEngine.execute()` 增加 `on_iteration` 回调参数。SubAgent 在回调中检查 token 累计是否超过预算，超限则抛异常终止。这比方案 B（缩小 max_iterations）更精确，也比轮询监控更简洁。

---

### 问题 2: `iterations_used` 语义错误 [P0]

**评审 A 描述**: `tool_calls_log` 长度是工具调用次数，不等于 ReAct 迭代次数。

**评审 B 描述**: 同上，且指出一次迭代可能触发多个并行 tool_call，也可能不调任何工具。失败分支 `iterations_used=0` 更是错误。

**源码验证**:
- `subagent.py:183` — `iterations_used=step_result.tool_calls_log and len(step_result.tool_calls_log) or 0` ✅ 确认语义错误
- `subagent.py:215` — 失败分支 `iterations_used=0` ✅ 即使已跑 8 轮也报 0
- `engine.py:121-247` — `iteration` 变量在 while 循环中累加，但 `StepResult` 不包含此字段 ✅ 确认缺失
- `schema.py:352-360` — `StepResult` 只有 `step_id/success/output/tool_calls_log`，无 `iterations_completed` ✅ 确认

**实际影响**: EvaluationProbe 的 `subagent_avg_iterations` 指标失真；Tracing span 的 `SUBAGENT_ITERATIONS` 属性不准确。

**采纳方案**: 在 `StepResult` 中新增 `iterations_completed: int = 0` 字段；`ReActEngine.execute()` 在返回时填充此字段；SubAgent 直接透传。

---

### 问题 3: 事件键名 `iterations` vs `iterations_used` 错配 [P0]

**评审 B 描述**: SubAgent 发射事件用 `"iterations"` 键，但 `EvaluationProbe` 读取 `"iterations_used"` 键，导致 `iterations_used` 永远为 0。

**源码验证**:
- `subagent.py:192` — `"iterations": result.iterations_used` ✅ 发射键名是 `iterations`
- `subagent.py:224` — `"iterations": len(step_result.tool_calls_log)` ✅ 同样是 `iterations`
- `subagent.py:256` — `"iterations": 0` ✅ 同样是 `iterations`
- `subagent.py:289` — `"iterations": 0` ✅ 同样是 `iterations`
- `evaluation/runner.py:303` — `data.get("iterations_used", 0)` ✅ 读取键名是 `iterations_used`
- `evaluation/runner.py:313` — `data.get("iterations_used", 0)` ✅ 同样是 `iterations_used`
- `tracing/bridge.py:818` — `data.get("iterations", 0)` ✅ tracing 用的是 `iterations`（匹配发射端）

**实际影响**: EvaluationProbe 的 `subagent_results` 中 `iterations_used` 永远为 0，评测数据失真。这是"静默 bug"——编译不报错，mock 测试也不容易发现。

**采纳方案**: 统一事件键名为 `iterations_used`，与字段名一致。同时修正 `tracing/bridge.py` 中的读取。

---

### 问题 4: `parent_agent_name` 硬编码 `"parent"` [P1]

**评审 A 描述**: 始终为 `"parent"`，丧失调试价值。

**评审 B 描述**: TracingBridge 的 `SUBAGENT_PARENT_AGENT` 属性恒为 `"parent"`，反模式 #9 防御的关键信号被抹平。

**源码验证**:
- `subagent_tool.py:162` — `parent_agent_name="parent"` ✅ 确认硬编码
- `bridge.py:802` — `span.set_attribute(AttrKey.SUBAGENT_PARENT_AGENT, parent_agent)` ✅ 写入 span

**实际影响**: 无法区分不同 Agent（Executor/EmergentPlanner/GoalDrivenPlanner）派生的 SubAgent，tracing 可观测性降低。

**采纳方案**: SubAgentTool 构造时接受 `parent_name` 参数，Orchestrator 注入时传入标识。但由于 SubAgentTool 是在 Orchestrator `__init__` 中创建的，此时不知道具体调用方是哪个 Agent。实际方案：在 `SubAgentTool.execute()` 中不硬编码，而是传入一个动态的 caller 标识（如 `"Orchestrator"` 或根据 tools 列表推断）。考虑到当前架构中 SubAgentTool 始终由 Orchestrator 注入，最简单的方案是让 Orchestrator 传入 `"OrchestratorAgent"` 作为默认 parent_name。

---

### 问题 5: 短输出跳过 LLM 结构化总结 [P1]

**评审 A 描述**: 输出 ≤ 2000 字符时直接塞入 `accomplished`，其余字段全空，结构化摘要退化为单一文本。

**评审 B 描述**: 同上，且指出 LLM 的最终 output 可能本身是美化过的，直接塞进 `accomplished` 无任何结构化拆分。

**源码验证**:
- `subagent.py:306-313` — `if len(output) <= config.SUBAGENT_SUMMARY_MAX_LENGTH: return SubAgentSummary(accomplished=output, findings="", issues="", artifacts=[], tool_calls_summary="")` ✅ 确认

**实际影响**: 反模式 #5 和 #6 在短输出路径上完全失效。`findings`/`issues`/`artifacts`/`tool_calls_summary` 恒为空，父 Agent 无法区分"没有问题"和"没有结构化分析"。

**采纳方案**: 无论输出长短，都从 `tool_calls_log` 自动提取 `artifacts` 和 `tool_calls_summary`（无需 LLM 调用）。对 `findings` 和 `issues`，如果输出较短，可以用轻量级提取逻辑（从 output 中识别关键句），而非完全留空。但为了不增加复杂度，最简方案是：短输出时也至少提取 `tool_calls_summary` 和 `artifacts`（这些可从 log 中机械提取），`findings` 和 `issues` 设为"详见 accomplished"。

---

### 问题 6: 失败/超时分支丢失 `tool_calls_log` [P1]

**评审 B 描述**: 超时和异常分支的 `SubAgentResult` 未设 `tool_calls_log` 字段，依赖 `default_factory=list` 产生空列表，但超时/异常恰恰是最需要回看工具调用轨迹的场景。

**源码验证**:
- `subagent.py:243-251` — 超时分支的 `SubAgentResult` 未传 `tool_calls_log` ✅ 确认
- `subagent.py:276-284` — 异常分支同样未传 ✅ 确认
- `schema.py:701` — `tool_calls_log: list[ToolCallRecord] = Field(default_factory=list)` ✅ 默认空列表

**实际影响**: 超时/异常时无法回溯已执行的工具调用，降低调试能力。

**采纳方案**: 让 SubAgent 在 `run()` 中维护一个 `self._tool_calls_so_far: list[ToolCallRecord]`，通过 ReActEngine 的 `on_iteration` 回调或在每次迭代后从 `step_result`（如果有的话）更新。更简单的方案：在 `ReActEngine` 上增加一个 `get_tool_calls_log()` 方法，返回当前已积累的 log。超时/异常分支从该方法取回已执行的记录。

但考虑到 ReActEngine 是共享组件且改动影响面大，更安全的方案是：在 SubAgent 中维护 `_accumulated_tool_calls`，通过 `on_iteration` 回调（与问题 1 一起实现）来收集。

---

### 问题 7: SubAgent 未继承 BaseAgent [P2]

**评审 B 描述**: 计划要求 `SubAgent(BaseAgent)` 但实际是裸类，无法复用 `think_json` 的 JSON 校验/重试/自愈机制。

**源码验证**:
- `subagent.py:71` — `class SubAgent:` ✅ 确认非 BaseAgent
- `base.py:107-121` — `think_json` 内部调 `chat_json`，会自动管理 messages 和 context compression

**实际影响**: SubAgent 的 `_summarize_result` 用 `llm_client.chat_json` 直接调用，缺少 BaseAgent 的消息管理、context 压缩、JSON 校验重试等功能。

**是否完全采纳**: ⚠️ 部分采纳。完全继承 BaseAgent 会引入一些问题：
1. BaseAgent 的 `think_json` 会向 `self._messages` 追加消息，但 SubAgent 的摘要生成应该用独立的消息列表
2. BaseAgent 的 `_messages` 是为 ReAct 循环设计的，SubAgent 的摘要生成是单次调用
3. 继承 BaseAgent 意味着要处理 `add_message`、context compression 等不必要的状态管理

**采纳方案**: 不继承 BaseAgent（保持当前设计），但**采纳评审 B 的核心关切**：用 `SubAgentSummary.model_validate()` 做 Pydantic 校验（问题 8），并在校验失败时重试一次（模拟 `think_json` 的自愈行为）。

---

### 问题 8: 摘要未用 `SubAgentSummary.model_validate` [P1]

**评审 A 描述**: `chat_json` 返回后只检查 `"accomplished"` 字段存在，未校验类型安全。

**评审 B 描述**: 同上，只要 LLM 回一个含 `accomplished` 键的对象就通过。

**源码验证**:
- `subagent.py:327` — `if isinstance(response, dict) and "accomplished" in response:` ✅ 确认弱校验
- 然后用 `.get()` 逐字段提取，无 Pydantic 校验

**实际影响**: LLM 返回的 `artifacts` 可能是字符串而非 `list[str]`，`issues` 可能缺失，类型错误不会被捕获。

**采纳方案**: 用 `SubAgentSummary.model_validate(response)` 替代手动 `.get()`，Pydantic 会做类型强制和校验。校验失败走 fallback。

---

### 问题 9: `reset_call_count` 未重置 `_subagent_counter` [P2]

**评审 B 描述**: 只重置了 `_call_count`，但 `_subagent_counter` 持续累加，导致第二个任务的 sandbox 目录从 `subagent_4/` 开始。

**源码验证**:
- `subagent_tool.py:195-197` — `self._call_count = 0` ✅ 确认只重置了 `_call_count`
- `subagent_tool.py:60` — `self._subagent_counter = 0` ✅ 初始化

**实际影响**: 低。跨任务的命名不连续不会影响功能，但对 trace 可读性有轻微影响。

**采纳方案**: 在 `reset_call_count` 中一并重置 `_subagent_counter`，方法名改为 `reset_task_state`。

---

### 问题 10: 默认白名单"全量授权" [P2]

**评审 B 描述**: 当 LLM 不传 `tool_whitelist` 时，默认授予所有父级工具，违背"最小权限"实践。

**源码验证**:
- `subagent_tool.py:122-126` — `if not validated_whitelist: validated_whitelist = [name for name in self._available_tools.keys() if name != "subagent"]` ✅ 确认

**是否完全采纳**: ⚠️ 部分采纳。评审 B 建议改为"默认只读工具子集"，但这会导致：
1. LLM 大多数情况下不会显式传 `tool_whitelist`（参数是 optional）
2. 只读默认集会让大多数子任务失败（无法执行代码、无法写文件）
3. Claude Code 实际上也是"如果 LLM 不指定，则使用合理的默认集"——但 Claude Code 的默认集较大（包含 code execution）

**采纳方案**: 保持全量授权作为默认（否则功能不可用），但增加 `SUBAGENT_DEFAULT_TOOL_WHITELIST` 配置项，让用户可以自定义默认白名单。同时，在 `parameters_schema` 的 `tool_whitelist` 描述中加强提示，鼓励 LLM 显式指定最小权限集。

---

### 问题 11: `tool_schemas` 死代码 [P3]

**评审 B 描述**: `subagent.py:109` 的 `self.tool_schemas` 赋值后未再使用。

**源码验证**:
- `subagent.py:109` — `self.tool_schemas = [t.to_openai_tool() for t in tools]` ✅ 确认
- `engine.py:82` — ReActEngine 内部会重新生成 `self.tool_schemas` ✅ 确认重复

**采纳方案**: 删除该行。

---

### 问题 12: 限流事件不可观测 [P3]

**评审 B 描述**: `SubAgentTool.execute()` 中调用次数超限返回错误字符串，但没有发射事件，tracing/评测看不到"限流发生了"。

**源码验证**:
- `subagent_tool.py:102-103` — `return f"Error: SubAgent call limit reached..."` ✅ 确认无事件发射

**采纳方案**: 在返回限流错误前，发射 `subagent_limit_exceeded` 事件。

---

### 问题 13: 双重 timeout 处理 [P3]

**评审 A 描述**: SubAgent.run() 内部已 `asyncio.wait_for` 处理 timeout，外层 `except asyncio.TimeoutError` 永远不会触发。

**源码验证**:
- `subagent.py:160-167` — `asyncio.wait_for(..., timeout=self.timeout)` ✅ 内部 timeout
- `subagent_tool.py:172-181` — `except asyncio.TimeoutError:` ✅ 外层 timeout

**实际影响**: 外层 timeout 是兜底，正常由内层处理。无害但可能误导读者。

**采纳方案**: 加注释说明"最外层兜底，正常由 SubAgent.run() 内部处理"。

---

### 问题 14: `tools/__init__.py` 无条件导入 SubAgentTool [不采纳]

**评审 A 描述**: 即使 `SUBAGENT_ENABLED=false`，模块加载时仍导入 SubAgentTool。

**复核结论**: 这与 `agents/__init__.py` 无条件导入 `SubAgent` 是同一模式。模块级导入是 Python 标准做法，`SubAgentTool` 本身是轻量对象（不触发 LLM 调用或网络请求），无条件导入无害。改为条件导入反而增加维护复杂度。

---

### 问题 15: SubAgent system prompt 缺少抑制语 [P2]

**评审 B 描述**: 缺少"不要主动读取无关文件"、"不要自行补全模糊细节"、"不要重复调用同一工具"等抑制语。

**源码验证**:
- `subagent.py:37-52` — 当前 prompt 有 5 条规则 ✅ 确认缺少抑制语

**实际影响**: 中等。抑制语对防止子 Agent 失控的作用，远大于超时和调用次数熔断。

**采纳方案**: 在 `SUBAGENT_SYSTEM_PROMPT` 中添加 3 条抑制语。

---

### 问题 16: 事件层 summary 泄漏隐患 [不采纳]

**评审 B 描述**: `subagent_complete` 事件发射了 `summary`（整个 `summary_text` JSON），如果未来某个订阅者记录了 summary，会构成反模式 #2 回归。

**复核结论**: 当前 `main.py`（UI 渲染）、`evaluation/runner.py`（不消费 summary）、`tracing/bridge.py`（不写入 summary 到 span）三个订阅者都没有泄漏 summary。评审 B 也承认"实际没泄漏"。将 summary 从事件中移除会破坏 UI 渲染（main.py:419 需要显示摘要 Panel），风险大于收益。未来新增订阅者时，由开发者自行判断是否需要记录 summary。

---

### 问题 17: Planner 持有 SubAgentTool [P3]

**评审 B 描述**: Orchestrator 将包含 SubAgentTool 的 tools 列表传给了 PlannerAgent，Planner 不应该持有 SubAgentTool。

**源码验证**:
- `orchestrator.py:131` — `self.planner = PlannerAgent(self.llm_client, self.context_manager)` — 注意：PlannerAgent **不接收 tools 参数** ✅

**复核结论**: 评审 B 的分析有误。查看 `orchestrator.py:131`，`PlannerAgent` 构造函数只接受 `llm_client` 和 `context_manager`，不接受 `tools`。PlannerAgent 不会持有 SubAgentTool。问题不存在。

---

### 问题 18: 共享 LLMClient token 差值不安全 [P2]

**评审 A 描述**: LLMClient 是共享实例，若父 Agent 或其他 SubAgent 并发使用，差值会包含其他调用者的 token。

**源码验证**:
- `subagent.py:147` — `tokens_before = self._get_total_tokens()`
- `subagent.py:170` — `tokens_used = self._get_total_tokens() - tokens_before`
- 当前 SubAgentTool.execute() 是 `await subagent.run()`，串行执行

**实际影响**: 评审 A 也承认"实际风险低（当前 SubAgentTool 内部串行 await）"。未来如果引入并发 SubAgent（`SUBAGENT_MAX_CONCURRENT > 1`），差值计算会不准确。

**采纳方案**: 用 `call_records` 索引区间替代差值计算。在 `run()` 开始时记录 `records_before = len(self.llm_client.get_call_records())`，结束时只计算新增记录的 token。

---

## 三、修复实施计划

### Phase 1: P0 修复（必须修复，阻塞 v9 GA）

#### 1.1 ReActEngine 增加 `on_iteration` 回调 + `StepResult.iterations_completed`

**文件**: `react/engine.py`

```python
# execute() 方法签名增加:
async def execute(
    self,
    prompt: str,
    context: str = "",
    node_id: str | None = None,
    system_hint: str = "",
    on_iteration: Callable[[int, list[ToolCallRecord]], None] | None = None,  # 新增
) -> StepResult:

# while 循环末尾（每次迭代完成后）:
if on_iteration:
    on_iteration(iteration, tool_calls_log)

# 所有 return StepResult 的地方，增加 iterations_completed 字段:
return StepResult(
    step_id=step_id,
    success=True,
    output=final_output,
    tool_calls_log=tool_calls_log,
    iterations_completed=iteration,  # 新增
)
```

**文件**: `schema.py` — StepResult 新增字段:
```python
class StepResult(BaseModel):
    step_id: int | str = Field(...)
    success: bool
    output: str = ""
    tool_calls_log: list[ToolCallRecord] = Field(default_factory=list)
    iterations_completed: int = Field(default=0, description="Actual ReAct iterations completed / 实际 ReAct 迭代次数")  # 新增
```

#### 1.2 SubAgent 实现 Token 预算熔断

**文件**: `agents/subagent.py`

```python
class SubAgent:
    def __init__(self, ...):
        ...
        self._accumulated_tool_calls: list[ToolCallRecord] = []  # 新增：用于超时/异常分支保留
        self._token_exceeded = False  # 新增：token 预算超限标记

    def _on_react_iteration(self, iteration: int, tool_calls: list[ToolCallRecord]) -> None:
        """ReAct iteration callback — check token budget and accumulate tool calls."""
        self._accumulated_tool_calls.extend(tool_calls)
        # Token budget check (anti-pattern #8)
        current_tokens = self._get_total_tokens() - self._tokens_before
        if current_tokens >= self.max_tokens:
            logger.warning("[SubAgent] Token budget exceeded: %d >= %d", current_tokens, self.max_tokens)
            self._token_exceeded = True
            raise SubAgentTokenExhausted(f"Token budget exceeded: {current_tokens} >= {self.max_tokens}")

    async def run(self, context: str = "") -> SubAgentResult:
        ...
        self._tokens_before = self._get_total_tokens()  # 改为实例变量
        self._accumulated_tool_calls = []
        self._token_exceeded = False

        try:
            step_result = await asyncio.wait_for(
                self._react_engine.execute(
                    prompt=self.task_description,
                    context=context,
                    node_id=self.name,
                    on_iteration=self._on_react_iteration,  # 新增
                ),
                timeout=self.timeout,
            )
            # 使用 step_result.iterations_completed 替代 len(tool_calls_log)
            iterations_used = step_result.iterations_completed
            ...

        except SubAgentTokenExhausted:
            # Token 预算超限 — 返回已完成的摘要
            ...
```

新增异常类:
```python
class SubAgentTokenExhausted(Exception):
    """Raised when a SubAgent exceeds its per-call token budget."""
    pass
```

#### 1.3 修正 `iterations_used` 语义

**文件**: `agents/subagent.py`

- 成功分支: `iterations_used=step_result.iterations_completed`（替代 `len(tool_calls_log)`）
- 失败分支: `iterations_used=step_result.iterations_completed`（替代硬编码 `0`）
- 超时/异常分支: 从 `_accumulated_tool_calls` 推算（粗略等于 `len(_accumulated_tool_calls)`，因为无法获取精确迭代数。更精确方案：在 `on_iteration` 回调中记录 `self._iterations_so_far`）

#### 1.4 统一事件键名为 `iterations_used`

**文件**: `agents/subagent.py` — 所有事件发射:
- `"iterations"` → `"iterations_used"`

**文件**: `tracing/bridge.py` — 所有读取:
- `data.get("iterations", 0)` → `data.get("iterations_used", 0)`

**文件**: `evaluation/runner.py` — 已使用 `"iterations_used"`，无需修改

---

### Phase 2: P1 修复（建议 v9.1 一起修复）

#### 2.1 `parent_agent_name` 透传

**文件**: `tools/subagent_tool.py`

```python
class SubAgentTool(BaseTool):
    def __init__(self, ..., parent_name: str = "OrchestratorAgent"):
        ...
        self._parent_name = parent_name

    async def execute(self, **kwargs):
        ...
        subagent = SubAgent(
            ...
            parent_agent_name=self._parent_name,  # 替代硬编码 "parent"
            ...
        )
```

**文件**: `agents/orchestrator.py`

```python
self._subagent_tool = SubAgentTool(
    ...
    parent_name="OrchestratorAgent",  # 新增
)
```

#### 2.2 短输出也提取 `tool_calls_summary` 和 `artifacts`

**文件**: `agents/subagent.py` — `_summarize_result` 方法:

```python
async def _summarize_result(self, step_result: Any) -> SubAgentSummary:
    output = step_result.output or ""
    tool_calls_log = step_result.tool_calls_log or []

    # 从 tool_calls_log 中自动提取（无需 LLM）
    artifacts = [
        tc.parameters.get("path", tc.parameters.get("file_path", ""))
        for tc in tool_calls_log
        if tc.tool_name in ("file_ops", "write_file", "read_file")
        and tc.parameters.get("path") or tc.parameters.get("file_path")
    ]
    tool_calls_summary = "; ".join(
        f"{tc.tool_name}({', '.join(f'{k}={v}' for k, v in list(tc.parameters.items())[:2])})"
        for tc in tool_calls_log
    ) if tool_calls_log else ""

    if len(output) <= config.SUBAGENT_SUMMARY_MAX_LENGTH:
        return SubAgentSummary(
            accomplished=output,
            findings="See accomplished field" if output else "",
            issues="",
            artifacts=artifacts,
            tool_calls_summary=tool_calls_summary,
        )

    # Use LLM to generate structured summary (同之前，但增加 model_validate)
    ...
```

#### 2.3 失败/超时分支保留 `tool_calls_log`

**文件**: `agents/subagent.py`

所有失败/超时/异常分支的 `SubAgentResult` 增加:
```python
tool_calls_log=list(self._accumulated_tool_calls),
```

#### 2.4 摘要用 `SubAgentSummary.model_validate`

**文件**: `agents/subagent.py`

```python
if isinstance(response, dict):
    try:
        return SubAgentSummary.model_validate(response)
    except ValidationError:
        # Pydantic 校验失败，走 fallback
        pass
```

---

### Phase 3: P2/P3 修复（代码质量改进）

#### 3.1 `reset_call_count` → `reset_task_state`

**文件**: `tools/subagent_tool.py`

```python
def reset_task_state(self) -> None:
    """Reset per-task state (called by OrchestratorAgent.run())."""
    self._call_count = 0
    self._subagent_counter = 0
```

**文件**: `agents/orchestrator.py` — 调用处同步更新

#### 3.2 增加 `SUBAGENT_DEFAULT_TOOL_WHITELIST` 配置

**文件**: `config.py`

```python
SUBAGENT_DEFAULT_TOOL_WHITELIST: str = os.getenv("SUBAGENT_DEFAULT_TOOL_WHITELIST", "")  # 逗号分隔的工具名，空=全量
```

**文件**: `tools/subagent_tool.py` — 使用配置

#### 3.3 删除 `tool_schemas` 死代码

**文件**: `agents/subagent.py` — 删除第 109 行

#### 3.4 限流事件可观测

**文件**: `tools/subagent_tool.py`

```python
if self._call_count >= self._max_calls:
    self._on_event("subagent_limit_exceeded", {
        "call_count": self._call_count,
        "max_calls": self._max_calls,
    })
    return f"Error: SubAgent call limit reached ..."
```

#### 3.5 SubAgent system prompt 增加抑制语

**文件**: `agents/subagent.py` — `SUBAGENT_SYSTEM_PROMPT`:

```
6. Do NOT read files unrelated to your task just to "gather context" — this wastes tokens.
   不要为了"收集背景"而读取无关文件。
7. If the task description is unclear, note it in your findings and return — do NOT assume missing details.
   如果任务描述不清晰，在 findings 中指出后返回，不要自行补全。
8. Do NOT call the same tool with the same arguments repeatedly — if a tool call fails, try a different approach.
   不要用相同参数重复调用同一工具。
```

#### 3.6 双重 timeout 注释

**文件**: `tools/subagent_tool.py`

```python
except asyncio.TimeoutError:
    # Outer timeout fallback — normally handled by SubAgent.run() internally
    # 外层超时兜底 — 正常由 SubAgent.run() 内部处理
```

#### 3.7 Token 差值改为索引区间

**文件**: `agents/subagent.py`

```python
async def run(self, ...):
    records_before = len(self.llm_client.get_call_records())
    ...
    # Token 计算
    records = self.llm_client.get_call_records()
    tokens_used = sum(r.total_tokens for r in records[records_before:])
```

---

## 四、修改文件汇总

| 文件 | 操作 | 改动量 | Phase |
|------|------|--------|-------|
| `schema.py` | 修改: StepResult 新增 `iterations_completed` | +2行 | P1 |
| `react/engine.py` | 修改: execute() 增加 `on_iteration` 回调 + 填充 `iterations_completed` | +12行 | P1 |
| `agents/subagent.py` | 修改: token 熔断 + iterations 修正 + 事件键名统一 + 短输出改进 + model_validate + 抑制语 + 死代码删除 + token 差值改进 + 超时分支保留 tool_calls_log | +60行, 改~30行 | P1+P2+P3 |
| `tools/subagent_tool.py` | 修改: parent_name 透传 + reset_task_state + 限流事件 + timeout 注释 + 默认白名单配置 | +15行, 改~10行 | P1+P2+P3 |
| `agents/orchestrator.py` | 修改: 传入 parent_name + reset_task_state | +2行 | P1+P3 |
| `tracing/bridge.py` | 修改: `iterations` → `iterations_used` | ~5行 | P1 |
| `config.py` | 修改: 新增 `SUBAGENT_DEFAULT_TOOL_WHITELIST` | +1行 | P3 |
| `tests/test_subagent.py` | 修改: 新增/更新测试用例 | +80行 | 全部 |

---

## 五、测试更新

### 新增测试用例

| 测试类 | 用例 | 对应问题 |
|--------|------|---------|
| `TestReActEngine` | `on_iteration` 回调被调用 | 1.1 |
| `TestReActEngine` | `StepResult.iterations_completed` 正确返回 | 1.1 |
| `TestSubAgent` | token 预算超限时抛 `SubAgentTokenExhausted` | 1.2 |
| `TestSubAgent` | token 超限后返回 TIMED_OUT 状态 + 已有摘要 | 1.2 |
| `TestSubAgent` | `iterations_used` 来自 `iterations_completed` | 1.3 |
| `TestSubAgent` | 失败分支 `iterations_used` 非 0 | 1.3 |
| `TestSubAgent` | 事件键名 `iterations_used` 正确 | 1.4 |
| `TestSubAgent` | 短输出时 `artifacts`/`tool_calls_summary` 非空 | 2.2 |
| `TestSubAgent` | 超时分支 `tool_calls_log` 非空 | 2.3 |
| `TestSubAgent` | `model_validate` 校验失败时走 fallback | 2.4 |
| `TestSubAgentTool` | `parent_name` 非硬编码 "parent" | 2.1 |
| `TestSubAgentTool` | `reset_task_state` 重置 counter | 3.1 |
| `TestSubAgentTool` | 限流事件 `subagent_limit_exceeded` 发射 | 3.4 |
| `TestEvaluationProbe` | `iterations_used` 键名匹配 | 1.4 |
| `TestTracingBridge` | `iterations_used` 键名匹配 | 1.4 |

### 回归测试

```bash
python3 -m pytest tests/ -v --ignore=tests/test_llm_integration.py --ignore=tests/test_real_tools.py
```

确保现有 295+ 测试不受影响。

---

## 六、实施顺序

1. **Phase 1 (P0)**: 先改 `schema.py`（StepResult）→ `react/engine.py`（on_iteration + iterations_completed）→ `agents/subagent.py`（token 熔断 + iterations 修正 + 事件键名）→ `tracing/bridge.py`（键名统一）→ 运行测试
2. **Phase 2 (P1)**: `agents/subagent.py`（短输出改进 + model_validate + 超时分支 tool_calls_log）→ `tools/subagent_tool.py`（parent_name）→ `agents/orchestrator.py`（parent_name + reset_task_state）→ 运行测试
3. **Phase 3 (P2/P3)**: 剩余改动 → 运行测试
4. **全量回归**: `python3 -m pytest tests/ -v`
