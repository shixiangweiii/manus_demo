# SubAgent 模式深度评审与实施计划（Wave A+B+C）

## Context

本次任务源于对 v9 SubAgent 多智能体机制的深度复核。SubAgent 是 manus_demo 项目里 depth=1 的隔离式子智能体（仿 Claude Code Subagent 模式），由 LLM 在 ReAct 循环里以 `subagent` 工具的形式调用。距离 v9 引入已经过去若干迭代，期间项目做了 v10/v11/v12 一系列优化（DDGS→Bailian MCP、tool result truncation、并发 tool_calls via asyncio.gather、context injection 注入日期、ContextManager token 估算修正）。

**为什么要做这次优化**：评审发现 SubAgent 模块在四个层次上跟最近的项目演进存在偏离 ——

1. **真 bug 静默吞掉**：UI 渲染 `subagent_complete` 时取 `data['iterations']`，但发射端契约是 `iterations_used`（CLAUDE.md 决策 #15 明文规定）。`_emit` 的防御 try/except 把 KeyError 全部吃掉，用户看不到任何迭代数。
2. **二次方膨胀**：`SubAgent._on_react_iteration` 用 `extend()` 累加 `tool_calls_log`，但回调传入的是**累积**列表而非增量，导致迭代 N 时 `_accumulated_tool_calls` 长度为 N(N+1)/2。所有失败路径（timeout/token-exhausted/exception）都用此累加列表上报 `tool_calls_count`，污染 evaluation 指标。
3. **并发安全完全缺位**：`SUBAGENT_MAX_CONCURRENT=3` 配置存在但未实施 Semaphore；`SubAgentTool._call_count` 在 `check → await → increment` 之间存在竞态，可超出 `SUBAGENT_MAX_CALLS_PER_TASK`。在 v12 的 `asyncio.gather` 并发 tool_calls 之后这是真实风险。
4. **可观测性闭环没合**：`subagent_limit_exceeded` 事件三处观察者（UI / Tracing / Eval）全都不消费；失败路径 emit 缺 `tokens_used`；`EvaluationProbe.subagent_results` 采集后被 `build_result()` 弃用；SubAgent 系统提示词回归——绕过 `build_system_prompt()`，缺少 v12 注入的日期/时间，搜索类任务会重现"年份猜错"。

**预期结果**：13 项最小修复落地后，SubAgent 与 v12 项目演进对齐，bug 收敛、并发受控、observability 闭环。范围限定 Wave A+B+C；Wave D（housekeeping，含 file_ops chroot 强制）作为后续独立 PR 处理。

## Scope

13 项修复，分三波次实施，外加一个测试件：

| Wave | 内容 | 性质 | 预估 |
|------|------|------|------|
| A | #1/#2/#3/#8/#13/#16 | 独立、可并行、零依赖 | 1-2h |
| B | #4 → #5 → #15 | 并发核心，必须串行 | 2-3h |
| C | #6/#7/#9/#12 | 横切观测性，可在 wave 内并行 | 1-2h |

**重要决议**：
- **#7 parent_name 动态化** 采用 `set_caller(name)` 方法注入（用户偏好）。`ReActEngine.__init__` 新增 `agent_name` 参数，在 `_exec_one` 调用 `traced_execute` **之前**同步调用 `tool.set_caller(self.agent_name)`。asyncio 单线程下 set_caller→execute 之间无 await，execute 起始处同步捕获 `_parent_name` 到局部变量后即并发安全。
- **#4 reserve-before-await（不带 refund）**：失败的 SubAgent 仍计入 budget，避免崩溃→重试→崩溃的 thrash 循环。
- **#16 ToolRouter** 新增 `record_rate_limited()` 方法和 `ToolStats.rate_limited` 字段，区分业务限流 vs 工具故障。

## Pre-flight 已验证的依赖签名

- `agents/prompt_utils.py:90-119` `build_system_prompt(base, inject_context=True, inject_subagent_guidance=True, inject_location_guidance=True)` ✓
- `tools/router.py:34-44` `ToolStats(calls, failures, consecutive_failures)` 是 `@dataclass`，可直接加字段 ✓
- `react/engine.py:67-87` `ReActEngine.__init__` 当前无 `agent_name` 参数，需新增（默认空串保兼容）✓
- `react/engine.py:194,285` 两处 `on_iteration(iteration, tool_calls_log)` 都传**累积**列表，#2 修复方向正确 ✓

---

## Wave A — 独立修复（可并行实施）

### #1 main.py:412 KeyError

**File**: `main.py:412`

**Fix**:
```python
# 原: f"({data['iterations']} iters, ...)"
# 改: f"({data.get('iterations_used', 0)} iters, ...)"
```
顺手把同行的 `data['duration_ms']` 改 `data.get('duration_ms', 0)` 防御未来漂移。

### #2 `_accumulated_tool_calls` 二次方膨胀

**File**: `agents/subagent.py:181`

**Fix**:
```python
# 原: self._accumulated_tool_calls.extend(tool_calls)
# 改: self._accumulated_tool_calls = list(tool_calls)
```
回调传入的 `tool_calls` 已经是累积快照（`react/engine.py:194,285` 确认），用浅拷贝保留快照语义即可。修复后 `len(self._accumulated_tool_calls)` 在 timeout/token-exhausted/exception 三个路径上恢复正确语义。

### #3 SubAgent 系统提示词缺 context injection

**File**: `agents/subagent.py:139`（`__init__` 内构建 system_prompt 的位置）

**Fix**:
```python
from agents.prompt_utils import build_system_prompt

# 原: system_prompt = SUBAGENT_SYSTEM_PROMPT
system_prompt = build_system_prompt(
    SUBAGENT_SYSTEM_PROMPT,
    inject_context=True,
    inject_subagent_guidance=False,  # depth=1 — SubAgent 不能再 spawn SubAgent
)
# 后续 sandbox_subdir 追加保持不变
```
`inject_location_guidance` 留默认 True，与 Executor 一致；whitelist 不含 `get_user_location` 时 guidance 是无害噪声。

### #8 失败路径 emit 缺 `tokens_used`

**File**: `agents/subagent.py`，4 个 emit 点：
- L297-303（step failed）
- L337-343（token exhausted）
- L377-383（timed out）
- L418-424（unexpected exception）

**Fix**：每处 `_emit("subagent_failed"/...)` 的 dict 增加 `"tokens_used": tokens_used` 一行（`tokens_used` 局部变量四处都已计算）。

### #13 .env.example 缺 SUBAGENT_*

**File**: `.env.example`

**Fix**：在合适位置追加 8 行（与 `config.py:121-130` 注释保持一致）：
```bash
# --- v9.0 SubAgent (Claude Code Subagent pattern, default OFF) ---
# SUBAGENT_ENABLED=false
# SUBAGENT_MAX_ITERATIONS=10
# SUBAGENT_TIMEOUT=300
# SUBAGENT_MAX_CONCURRENT=3
# SUBAGENT_SUMMARY_MAX_LENGTH=2000
# SUBAGENT_MAX_CALLS_PER_TASK=3
# SUBAGENT_MAX_TOKENS_PER_CALL=50000
# SUBAGENT_DEFAULT_TOOL_WHITELIST=
```

### #16 ToolRouter 区分业务限流 vs 工具故障

**Files**:
- `tools/router.py`: `ToolStats` 加 `rate_limited: int = 0`；`ToolRouter` 加 `record_rate_limited(node_id, tool_name)` 方法（递增 `rate_limited`，不动 `calls/failures/consecutive_failures`）。
- `react/engine.py:_exec_one`：返回元组增加 `is_rate_limited` 维度；检测条件 `isinstance(res, str) and "SubAgent call limit reached" in res`。
- `react/engine.py` 主循环：`if is_rate_limited: tool_router.record_rate_limited(...) elif is_error: record_failure(...) else: record_success(...)`。
- `tools/subagent_tool.py:121` 的 return 字符串保持 `Error: SubAgent call limit reached ...` 不变（LLM 仍能感知失败、不会盲目重试）。

---

## Wave B — 并发核心（必须 #4 → #5 → #15 顺序）

### #4 reserve-before-await（移走 _call_count 自增）

**File**: `tools/subagent_tool.py:108-238`

**Fix**：
```python
async def execute(self, **kwargs):
    if self._call_count >= self._max_calls:
        self._on_event("subagent_limit_exceeded", {"call_count": self._call_count, "max_calls": self._max_calls})
        return f"Error: SubAgent call limit reached ..."
    self._call_count += 1   # === RESERVE in atomic sync block — before any await ===

    # 删掉原 line 200, 216, 229 的三处 self._call_count += 1
    # 之后所有路径（success / timeout / exception）都不再变更 _call_count
    ...
```
关键：`check → reserve` 之间无 await，asyncio 单线程下原子；失败路径**不退款**——避免 LLM 反复调用导致死循环。

### #5 SUBAGENT_MAX_CONCURRENT Semaphore

**File**: `tools/subagent_tool.py`

**Fix**：
```python
# __init__:
self._semaphore = asyncio.Semaphore(config.SUBAGENT_MAX_CONCURRENT)

# execute() — 仅 wrap 真正昂贵的部分（subagent.run），不 wrap whitelist 校验/sandbox 创建
try:
    subagent = SubAgent(...)
    async with self._semaphore:
        result: SubAgentResult = await subagent.run(context="")
    return result.summary_text
except asyncio.TimeoutError:
    ...
except Exception as exc:
    ...
```
slot 必须在 reservation 之后（先满额拒绝再排队）；只 wrap `subagent.run()`，让快路径（如 sandbox 创建失败）不挤占并发槽。

### #15 并发回归测试

**File**: `tests/test_subagent.py`（新增测试用例）

**Fix**：
```python
async def test_concurrent_subagent_respects_semaphore_and_call_limit(monkeypatch):
    # 设置 SUBAGENT_MAX_CONCURRENT=2, SUBAGENT_MAX_CALLS_PER_TASK=10
    in_flight = 0
    max_in_flight = 0
    
    async def fake_run(self, context=""):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)  # 制造可观测窗口
        in_flight -= 1
        return _make_completed_result()
    
    monkeypatch.setattr(SubAgent, "run", fake_run)
    
    tool = SubAgentTool(...)
    results = await asyncio.gather(*(tool.execute(task_description=f"task-{i}") for i in range(5)))
    
    assert max_in_flight <= 2          # Semaphore 生效
    assert tool._call_count == 5       # 没漏 reserve
    
    # 第二轮跑 5 次，前 5 次已耗完 budget=10？不，10。先用 10 次:
    # 改造: max_calls_per_task=2, 5 并发，期望 2 成功 3 限流
    ...
    # 验证：调用 1+2 后 _call_count=2，调用 3/4/5 立即返回 "Error: ... limit reached"
    # 而不是先排队再被拒
```

---

## Wave C — 可观测性闭环（wave 内可并行）

### #6 `subagent_limit_exceeded` 三处消费

**Files**:
- `main.py:404-435`：新增 `elif event == "subagent_limit_exceeded": console.print(f"    [yellow][SubAgent][/yellow] limit exceeded ({data['call_count']}/{data['max_calls']})")`
- `tracing/bridge.py`：在 `dispatch` 内对 `subagent_limit_exceeded` 增加 `_on_subagent_limit_exceeded` handler。不开新 span，对当前 `_phase_span`（若存在）调用 `span.add_event("subagent_limit_exceeded", {"call_count": ..., "max_calls": ...})`；否则只 logger.info 记一笔。
- `evaluation/runner.py`：`EvaluationProbe.__init__` 加 `self.subagent_limits_hit = 0`；`_handle_event` 内 `elif event == "subagent_limit_exceeded": self.subagent_limits_hit += 1`。

### #7 parent_name 动态归因（set_caller）

**Files**:
- `tools/subagent_tool.py`：加 `def set_caller(self, name: str): self._parent_name = name`。
- `react/engine.py`：
  - `__init__` 加参数 `agent_name: str = ""`，存为 `self.agent_name`。
  - `_exec_one`（line 207-222）：在拿到 `t = self.tools.get(fn_name)` 之后、`await t.traced_execute(...)` 之前，加：
    ```python
    if self.agent_name and hasattr(t, "set_caller"):
        t.set_caller(self.agent_name)
    ```
- 调用方（4 处构造 ReActEngine 的地方）显式传 `agent_name`：
  - `agents/executor.py`: `agent_name="ExecutorAgent"`
  - `agents/emergent_planner.py`: `agent_name="EmergentPlannerAgent"`
  - `agents/goal_driven_planner.py`: `agent_name="GoalDrivenPlannerAgent"`
  - `agents/subagent.py:148`: `agent_name=name`（"SubAgent-N"，虽然 SubAgent 不会调 SubAgentTool，但保持一致）
- `tools/subagent_tool.py:51` 删掉 TODO 注释。

**并发安全**：execute() 起始处把 `self._parent_name` 同步赋值给局部变量再传给 SubAgent 构造（事实上现在 line 195 就是这样），asyncio 单线程下 `set_caller → execute 起始 → 局部赋值` 之间无 await 间隙，安全。

### #9 EvaluationProbe.subagent_results 进入评分

**Files**:
- `schema.py`：在 evaluation 结果模型上加 `subagent_metrics: dict[str, Any] = Field(default_factory=dict)`（确认现有 result schema 路径后添加）。
- `evaluation/runner.py:build_result`：
  ```python
  sa = self.subagent_results
  total = len(sa)
  succ = sum(1 for r in sa if r.get("status") == "completed")
  subagent_metrics = {
      "count": total,
      "success_rate": succ / total if total else 1.0,
      "tokens_total": sum(r.get("tokens_used", 0) for r in sa),
      "avg_iterations": (sum(r.get("iterations_used", 0) for r in sa) / total) if total else 0,
      "limits_hit": self.subagent_limits_hit,  # Wave C #6
  }
  ```
- 暂不引入 composite_score 惩罚（避免改动现有评分语义，留作后续 PR）。

### #12 subagent_iteration 中间事件

**File**: `agents/subagent.py:_on_react_iteration` (line 178-196)

**Fix**：在 token 检查之前 emit：
```python
def _on_react_iteration(self, iteration, tool_calls):
    self._iterations_so_far = iteration
    self._accumulated_tool_calls = list(tool_calls)  # 含 #2 修复

    self._emit("subagent_iteration", {
        "subagent_id": self.name,
        "iteration": iteration,
        "tool_calls_count": len(tool_calls),
    })

    # token budget check 保持不变
    ...
```
渲染：
- `main.py`：dim 风格 `console.print(f"      [dim][SubAgent][/dim] {data['subagent_id']} iter {data['iteration']} ({data['tool_calls_count']} tool calls)")`，避免噪声。
- `tracing/bridge.py`：在 `_subagent_spans[id]` span 上 `span.add_event("iteration", {...})`，OTel 标准做法记录中间里程碑，不增 span 数量。

---

## Critical Files

| 文件 | 修改性质 | 涉及 Wave |
|------|----------|-----------|
| `agents/subagent.py` | 关键修改：context injection、_accumulated 修复、4 处 emit 加 tokens_used、新 emit subagent_iteration | A(#3,#2,#8) C(#12) |
| `tools/subagent_tool.py` | reserve-before-await 重排、Semaphore 注入、set_caller 方法 | B(#4,#5) C(#7) |
| `main.py` | KeyError 修复 + 2 个新 elif 分支 | A(#1) C(#6,#12) |
| `tracing/bridge.py` | 新 handler + span event | C(#6,#12) |
| `evaluation/runner.py` | 新计数器、build_result 聚合 | C(#6,#9) |
| `react/engine.py` | agent_name 参数、_exec_one 增 set_caller 调用、is_rate_limited 三态 | C(#7) A(#16) |
| `tools/router.py` | ToolStats.rate_limited、record_rate_limited 方法 | A(#16) |
| `agents/executor.py` / `emergent_planner.py` / `goal_driven_planner.py` | 构造 ReActEngine 时传 agent_name | C(#7) |
| `schema.py` | 评估结果模型加 subagent_metrics | C(#9) |
| `.env.example` | 8 行 SUBAGENT_* 注释 | A(#13) |
| `tests/test_subagent.py` | 并发测试 | B(#15) |

## Reused Existing Utilities

- `agents/prompt_utils.build_system_prompt` (`agents/prompt_utils.py:90`) — #3 复用
- `agents/prompt_utils.build_context_injection` (`agents/prompt_utils.py:64`) — #3 间接复用
- `tools/router.ToolStats` 数据类 (`tools/router.py:34`) — #16 扩展字段
- `react/engine._exec_one` 内部函数 (`react/engine.py:207`) — #7 注入点、#16 三态判定点
- `tracing/bridge.py` 现有 `_subagent_spans` dict (`tracing/bridge.py:806`) — #12 复用作为 add_event 目标
- `evaluation/runner.EvaluationProbe.subagent_results` (`evaluation/runner.py:125`) — #9 复用已采集数据

## Verification

### Wave A 完成后
1. 启用 `SUBAGENT_ENABLED=true` 跑一次任务，UI 应显示完整 `({N} iters, {M} tokens, {T}ms)` 而非崩溃静默。
2. 运行 `python -m pytest tests/test_subagent.py -v -o asyncio_mode=auto` 全部绿。
3. 故意触发一次 token 超预算，验证 `tool_calls_count` 不再异常膨胀（对比修复前后日志的累计长度）。
4. SubAgent 任务里发起 web_search 类操作，检查 LLM 不再用错年份。
5. 故意把 `SUBAGENT_MAX_CALLS_PER_TASK=1` 触发限流，ToolRouter 不再把这次"调用"算进失败计数（用 `tool_router.get_node_summary()` 验证）。

### Wave B 完成后（关键）
1. **并发回归测试 #15 必须先于 merge**：`pytest tests/test_subagent.py::test_concurrent_subagent_respects_semaphore_and_call_limit -v`。
2. 设 `SUBAGENT_MAX_CONCURRENT=1` 跑一个会触发并行 subagent tool_calls 的任务，观察 logger 串行执行（不交错）。
3. 设 `SUBAGENT_MAX_CALLS_PER_TASK=2` 高并发触发，断言总调用数不超过 2。

### Wave C 完成后
1. 启用 tracing：`TRACING_ENABLED=true python -m tracing &` 跑 SubAgent 任务，trace 详情页应展示：
   - `subagent.execute.{id}` span 上有多个 `iteration` add_event
   - 失败/超时 span 含 `subagent.tokens_used` 属性
   - `subagent_limit_exceeded` 作为 phase span 的 add_event
2. 跑一次 evaluation：`python -m evaluation.eval_cli --difficulty easy --modes emergent`（启用 SUBAGENT_ENABLED），输出 JSON 应含非空 `subagent_metrics` 字段。
3. 查看 trace span 的 `subagent.parent_agent` 属性：从 emergent 路径触发应显示 `EmergentPlannerAgent`，从 simple 路径触发应显示 `ExecutorAgent`。
4. UI 输出包含黄色 `limit exceeded` 行（手工触发 max=1，连发 2 个 task）。

### 回归保护
- 现有 `tests/test_subagent.py` 1795 行测试必须全绿（不允许 regression）。
- 整体测试套件：`python -m pytest tests/ -o asyncio_mode=auto --ignore=tests/test_llm_integration.py` 通过。
- 在没有 `SUBAGENT_ENABLED=true` 的默认场景下，所有改动不应影响 baseline 行为（验证：跑 `PLAN_MODE=simple python main.py "测试任务"` 与改动前 trace/output 一致）。

## 显式不在本次范围

- **#10 Sandbox 文件系统强制**：FileOpsTool / ShellTool 加 `chroot_prefix` 参数 + SubAgent 构造时 clone 工具实例。本次只做 prompt 加固（system prompt 加"NEVER use absolute paths outside <sandbox>"），完整方案留作独立 PR（影响其他模块的工具）。
- **#11 `_subagent_counter` monotonic**：低优先级，sandbox 用 `exist_ok=True` 已掩盖冲突。
- **#14 反模式 #5 显式测试**：可作为独立测试 PR。
- **composite_score 惩罚**：Wave C #9 只暴露指标，不改评分公式（避免现有 benchmark 不可比）。
