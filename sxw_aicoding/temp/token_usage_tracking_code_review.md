# Token 消耗追踪（Token Usage Tracking）代码评审报告

> **评审日期**: 2026-05-10  
> **评审范围**: Token Usage Tracking 功能在 Manus Demo 项目中的完整实现  
> **评审依据**: `/Users/shixiangweii/.claude/plans/ultrathink-ultrathink-token-toasty-lemur.md`  
> **涉及文件**: `schema.py`, `config.py`, `llm/client.py`, `agents/orchestrator.py`, `agents/executor.py`, `agents/emergent_planner.py`, `react/engine.py`, `dag/executor.py`, `main.py`

---

## 一、评审概述

本次评审围绕 **Token 消耗追踪** 功能的实现展开，逐条对照实施计划的 8 个步骤，深入检查数据模型、配置、LLMClient 累加器、执行层 per-step 追踪、规划/反思阶段聚合、总量汇总、UI 展示等各个环节的代码实现。

**总体评价**: 该功能在架构设计上选择了 **Option B（内部累加器 + 快照-增量模式）**，成功保持了现有接口不变、调用方零改动。整体实现思路清晰、代码结构良好，覆盖了 Simple、Complex（DAG）、Emergent 三条执行路径。但在 **并发安全性和测试覆盖** 方面存在明显缺陷。

---

## 二、实施计划逐条完整度评估

| 步骤 | 计划内容 | 状态 | 备注 |
|------|----------|------|------|
| Step 1 | Schema 扩展（`TokenUsage`、`TokenUsageSummary`、`StepResult.token_usage`） | ✅ 已实现 | `schema.py` 已完整添加 |
| Step 2 | Config 添加 `TOKEN_TRACKING_ENABLED` | ✅ 已实现 | `config.py` 已添加，默认 `true` |
| Step 3 | LLMClient 内部累加器（`_accumulate_usage`、`get_usage_snapshot`、`reset_usage`、`compute_usage_delta`） | ✅ 已实现 | `llm/client.py` 已完整实现 |
| Step 4 | 执行层 Per-Step 追踪（ExecutorAgent、EmergentPlannerAgent、ReActEngine） | ✅ 已实现 | 三条路径均覆盖 |
| Step 5 | 规划/反思阶段追踪（OrchestratorAgent 各阶段快照、DAGExecutor `_step_results`、Emergent 路径聚合） | ✅ 已实现 | 各阶段聚合逻辑完整 |
| Step 6 | 总量聚合与事件发射（`_finalize_token_usage` + `token_usage_summary` 事件） | ✅ 已实现 | `orchestrator.py` 末尾已完整实现 |
| Step 7 | UI 展示（`_render_token_summary` + `main.py` 事件处理） | ✅ 已实现 | `main.py` 已完整实现 Rich 渲染 |
| Step 8 | 验证（6 个验证场景） | ❌ **未实现** | **缺少专门的测试文件** |

**实现完整度**: 7/8 = **87.5%**（缺少测试验证）

---

## 三、发现的问题

### 🔴 Critical（严重）

#### 问题 1：DAGExecutor 并行执行场景下 per-node token_usage 重复计算

**位置**: `agents/executor.py` `_react_loop()`、`react/engine.py` `execute()`、`dag/executor.py` `_run_node()`

**描述**: 在 DAGExecutor 的 Super-step 并行执行中，同一批次的多个节点通过 `asyncio.gather` 并发调用 `ExecutorAgent.execute_node()`（或 `ReActEngine.execute()`）。每个节点执行开始时获取 `pre_snapshot = self.llm_client.get_usage_snapshot()`。由于 `LLMClient._usage_accumulator` 是全局共享的并发累加器，多个节点并发获取快照和计算增量时，彼此会“污染”对方的 delta 计算。

**示例场景**（两个节点 A、B 并行）：

1. 节点 A 获取 snapshot_A = 全局累加器当前值 = 0
2. 节点 B 获取 snapshot_B = 全局累加器当前值 = 0（与 A 同时刻）
3. 节点 A 调用 LLM，消耗 100 tokens，全局累加器 = 100
4. 节点 B 调用 LLM，消耗 200 tokens，全局累加器 = 300
5. 节点 A 完成，计算 delta_A = 300 - 0 = **300**（实际应为 100）
6. 节点 B 完成，计算 delta_B = 300 - 0 = **300**（实际应为 200）

这导致 per-node 的 `StepResult.token_usage` 严重失真，进而影响 Orchestrator 对 `by_phase` 中 `node_{id}` 条目的准确性。

**影响**: 🔴 **High** — 在 DAG（Complex）路径下，所有并行节点的 token_usage 均不准确，总量可能远超实际值。Simple 和 Emergent 路径（串行执行）不受影响。

**根因**: 快照-增量模式在并发场景下的固有限制。该模式假设快照获取和 delta 计算之间不会有其他并发调用修改同一个累加器。

**修复建议**: 
- **短期方案**: 在 DAG 路径下，放弃 per-node 粒度，改为在整个 DAG 执行前后各取一次快照，将总量记录到 `by_phase["dag_execution"]` 中。
- **长期方案**: 在 `LLMClient` 中为每次 API 调用分配唯一 `call_id`，并维护调用历史。per-node 追踪通过记录“本次 node 产生的 call_id 列表”来精确计算。
- **替代方案**: 引入 `asyncio.Lock` 保护快照获取 + 执行 + delta 计算的完整流程。但这会退化为串行执行，违背 DAG 并行设计初衷。

---

### 🟠 High（高）

#### 问题 2：缺少 Token Usage Tracking 的专项测试

**位置**: `tests/` 目录

**描述**: 实施计划 Step 8 列出了 6 个验证场景（DeepSeek simple 任务、Ollama N/A 场景、DAG 路径、Emergent 路径、重试场景、`TOKEN_TRACKING_ENABLED=false`），但项目中没有任何对应的测试文件。

**影响**: 🟠 **High** — 无法通过自动化测试验证 token tracking 的正确性，回归风险高。

**修复建议**: 新增 `tests/test_token_tracking.py`，覆盖以下场景：

```python
class TestTokenTracking:
    async def test_simple_path_tracks_by_phase(self): ...
    async def test_dag_path_tracks_by_node(self): ...
    async def test_emergent_path_tracks_todo_and_planning(self): ...
    async def test_retry_accumulates_multiple_attempts(self): ...
    async test test_disabled_tracking_returns_none(self): ...
    async def test_ollama_provider_shows_na(self): ...
```

---

### 🟡 Medium（中）

#### 问题 3：`compute_usage_delta` 可能遗漏 pre_snapshot 中独有的 engine

**位置**: `llm/client.py` `compute_usage_delta()`

**描述**:

```python
def compute_usage_delta(self, pre_snapshot):
    current = self.get_usage_snapshot()
    for engine, cur_vals in current.items():  # 只遍历 current 中的 engine
        pre_vals = pre_snapshot.get(engine, {...})
        ...
```

如果 `pre_snapshot` 中存在某个 engine，但在 `current` 中不存在（例如 `reset_usage()` 后该 engine 尚未产生新的调用），则该 engine 的 delta 不会被计算。

**影响**: 🟡 **Low** — 在实际场景中不太可能发生（因为 `reset_usage` 后通常会立即有调用）。但如果存在跨 engine 切换（如先调用 model A，reset，再调用 model B），model A 的历史消耗会被静默丢弃。

**修复建议**: 同时遍历 `current` 和 `pre_snapshot` 的 engine 并集：

```python
all_engines = set(current.keys()) | set(pre_snapshot.keys())
for engine in all_engines:
    ...
```

---

#### 问题 4：`_finalize_token_usage` 中 `total.engine` 被最后一个 phase 覆盖

**位置**: `agents/orchestrator.py` `_finalize_token_usage()`

**描述**:

```python
total = TokenUsage(engine=self.llm_client.model)
for phase_usage in self._task_usage.by_phase.values():
    ...
    total.engine = phase_usage.engine or self.llm_client.model
```

`total.engine` 在循环中被不断覆盖，最终值是最后一个 phase 的 engine。当前项目为单 engine 场景，无实际影响。但如果未来支持多 engine 切换，`total.engine` 的语义不明确。

**影响**: 🟡 **Low** — 当前单 engine 场景无影响。

**修复建议**: 将 `total.engine` 设为 `"multiple"` 或收集所有 engine 的集合，以表明这是跨 engine 的汇总。

---

### 🟢 Low（低）

#### 问题 5：`TokenUsage.total_tokens` 的语义可能不一致

**位置**: `llm/client.py` `_accumulate_usage()`

**描述**: `_accumulate_usage` 直接使用 API 返回的 `usage.total_tokens`，而不是自己计算 `prompt_tokens + completion_tokens`。某些 provider（如 DeepSeek reasoning models）的 `total_tokens` 可能包含 `reasoning_tokens`，导致 `prompt + completion < total`。

**影响**: 🟢 **Low** — UI 展示中的明细列（Prompt + Completion）之和可能不等于 Total 列。这不是 bug，但需要文档说明。

**修复建议**: 在 `_render_token_summary` 或文档中明确标注："Total may include reasoning tokens (prompt + completion ≤ total)"。

---

#### 问题 6：`_render_token_summary` 对 "N/A" 的判断条件过于宽松

**位置**: `main.py` `_render_token_summary()`

**描述**:

```python
def _render_token_summary(summary: TokenUsageSummary) -> None:
    if not summary.total.total_tokens:
        console.print("[dim]Token usage: N/A ...")
        return
```

如果 `total_tokens` 恰好为 0（虽然 API 不会返回这种情况，但理论上存在），或者 `TOKEN_TRACKING_ENABLED=false` 时 `total` 为默认值（`TokenUsage()`，所有字段为 0），也会显示 "N/A"。对于后者，显示 "N/A" 是合理的；但如果是 Provider 返回了 `total_tokens=0`（非空 usage 但 total=0），也显示 "N/A"，这可能掩盖 provider 的异常情况。

**影响**: 🟢 **Low** — 边界场景，不影响正常流程。

**修复建议**: 区分 "tracking disabled" 和 "provider returned zero" 两种情况，或使用更精确的判断条件。

---

#### 问题 7：`EmergentPlannerAgent._compile_answer` 中 `_accumulate_usage_delta` 在异常路径下可能重复累加

**位置**: `agents/emergent_planner.py` `_compile_answer()`

**描述**:

```python
try:
    synthesis = await self.think(...)
    self._accumulate_usage_delta(pre_snapshot, "_synthesis_usage")
    return synthesis
except Exception:
    self._accumulate_usage_delta(pre_snapshot, "_synthesis_usage")
    ...
```

如果 try 块中的 `_accumulate_usage_delta` 因某种原因失败（尽管可能性极低），异常会被捕获，然后在 except 块中再次调用 `_accumulate_usage_delta`。由于两次调用使用同一个 `pre_snapshot`，而 `llm_client` 的内部累加器在两次调用之间没有变化（`think()` 已在 try 块中完成），第二次调用会累加和第一次相同的 token 数量，导致重复计算。

**影响**: 🟢 **Low** — 发生概率极低（`_accumulate_usage_delta` 仅涉及字典操作，几乎不可能失败）。

**修复建议**: 使用 `try...except...else...finally` 结构，确保 `_accumulate_usage_delta` 只执行一次：

```python
synthesis = None
err = None
try:
    synthesis = await self.think(...)
except Exception as e:
    err = e
finally:
    self._accumulate_usage_delta(pre_snapshot, "_synthesis_usage")
```

---

## 四、代码质量评价

### 4.1 设计决策（Design Decisions）

| 方面 | 评价 | 说明 |
|------|------|------|
| **Option B 选择** | ✅ 优秀 | 避免了修改 20+ 调用点的返回类型，保持了向后兼容 |
| **快照-增量模式** | ⚠️ 良好但并发受限 | 在串行场景下完美工作，但并发场景有缺陷（见问题 1） |
| **Feature Flag** | ✅ 优秀 | `TOKEN_TRACKING_ENABLED` 允许零开销关闭，默认开启 |
| **数据模型设计** | ✅ 良好 | `TokenUsage` + `TokenUsageSummary` 结构清晰，支持 by_phase 和 by_engine 双维度 |

### 4.2 代码风格与一致性

| 方面 | 评价 | 说明 |
|------|------|------|
| **命名** | ✅ 一致 | `_accumulate_usage`、`get_usage_snapshot`、`compute_usage_delta` 语义清晰 |
| **类型提示** | ✅ 完整 | 所有新增方法均带类型提示（`dict[str, dict[str, int]]`） |
| **文档注释** | ✅ 良好 | 关键方法有中文/英文双语注释 |
| **错误处理** | ⚠️ 基本到位 | 缺少对 `resp.usage is None` 的防御性日志（当前只跳过，无警告） |
| **向后兼容** | ✅ 优秀 | `StepResult.token_usage` 为可选字段，不影响旧代码 |

### 4.3 架构耦合度

| 方面 | 评价 | 说明 |
|------|------|------|
| **LLMClient 侵入性** | ✅ 低 | 仅在三处 API 调用后添加累加逻辑，无侵入式修改 |
| **Agent 侵入性** | ✅ 低 | 各 Agent 只需在方法首尾添加 snapshot/delta 两行代码 |
| **UI 侵入性** | ✅ 低 | 通过事件驱动（`token_usage_summary`）解耦，main.py 只负责渲染 |

---

## 五、改进建议汇总

### 5.1 短期必须修复（P0）

1. **修复 DAG 路径下 per-node token_usage 并发失真问题**
   - 方案：在 DAG 路径下，放弃 per-node 粒度，改为整个 DAG 执行前后取快照
   - 修改点：`orchestrator.py` 的 `_execute_dag_and_reflect` 中，将 per-node 收集改为整体收集

2. **补充测试覆盖**
   - 新增 `tests/test_token_tracking.py`
   - 覆盖 Simple、DAG、Emergent 三条路径，以及重试和禁用场景

### 5.2 中期优化（P1）

3. **修复 `compute_usage_delta` 对 pre_snapshot 独有 engine 的遗漏**
   - 遍历 `current` 和 `pre_snapshot` 的 engine 并集

4. **消除 `_compile_answer` 中 `_accumulate_usage_delta` 的重复调用风险**
   - 使用 `try...except...finally` 确保只执行一次

5. **添加 `resp.usage is None` 的防御性日志**
   - 在 `llm/client.py` 中，当 `resp.usage is None` 时打印 warning，帮助排查 Provider 行为

### 5.3 长期改进（P2）

6. **引入 call-id 级别的精确追踪**
   - 为每次 LLM API 调用分配唯一 `call_id`
   - 记录 call_id -> usage 的映射
   - per-node / per-step 追踪通过记录“属于本次执行的 call_id 列表”来实现
   - 彻底解决并发场景下的精确追踪问题

7. **多 engine 支持**
   - `TokenUsageSummary.total` 的 `engine` 字段改为列表或 `"multiple"`
   - `_finalize_token_usage` 正确聚合多 engine 数据

---

## 六、结论

Token 消耗追踪功能在 **架构设计、接口兼容性、UI 展示** 方面表现优秀，成功实现了实施计划的 7/8 个步骤。但在 **并发安全（DAG 路径）** 和 **测试覆盖** 方面存在不容忽视的缺陷。

| 维度 | 评分（满分 10） |
|------|----------------|
| 设计合理性 | 8/10 |
| 实现完整性 | 8.5/10 |
| 代码质量 | 8/10 |
| 并发安全性 | 4/10（DAG 路径存在失真） |
| 测试覆盖 | 2/10（缺少专项测试） |
| 可维护性 | 8.5/10 |

**建议优先级**: 问题 1（并发失真）> 问题 2（缺少测试）> 其余低优先级问题。

---

## 附录：关键代码片段引用

### A.1 并发失真关键路径

```python
# dag/executor.py (简化)
results = await asyncio.gather(*[
    self._run_node_with_timeout(node, dag) for node in batch
], return_exceptions=True)

# agents/executor.py _react_loop (简化)
pre_snapshot = self.llm_client.get_usage_snapshot()  # ← 并发获取快照
# ... 执行 LLM 调用 ...
return StepResult(
    step_id=step_id,
    token_usage=self._build_token_usage(pre_snapshot),  # ← delta 包含其他并行节点的消耗
)
```

### A.2 快照-增量模式核心逻辑

```python
# llm/client.py
class LLMClient:
    def _accumulate_usage(self, usage: Any) -> None:
        engine = self.model
        acc = self._usage_accumulator.setdefault(
            engine, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        acc["prompt_tokens"] += usage.prompt_tokens or 0
        acc["completion_tokens"] += usage.completion_tokens or 0
        acc["total_tokens"] += usage.total_tokens or 0

    def compute_usage_delta(self, pre_snapshot: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        current = self.get_usage_snapshot()
        delta: dict[str, dict[str, int]] = {}
        for engine, cur_vals in current.items():  # ← 只遍历 current 中的 engine
            pre_vals = pre_snapshot.get(engine, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
            d = {
                "prompt_tokens": cur_vals["prompt_tokens"] - pre_vals["prompt_tokens"],
                "completion_tokens": cur_vals["completion_tokens"] - pre_vals["completion_tokens"],
                "total_tokens": cur_vals["total_tokens"] - pre_vals["total_tokens"],
            }
            if d["total_tokens"] > 0:
                delta[engine] = d
        return delta
```

### A.3 Orchestrator 汇总逻辑

```python
# agents/orchestrator.py _finalize_token_usage
def _finalize_token_usage(self) -> None:
    final_snapshot = self.llm_client.get_usage_snapshot()
    for engine, vals in final_snapshot.items():
        self._task_usage.by_engine[engine] = TokenUsage(
            prompt_tokens=vals["prompt_tokens"],
            completion_tokens=vals["completion_tokens"],
            total_tokens=vals["total_tokens"],
            engine=engine,
        )
    total = TokenUsage(engine=self.llm_client.model)
    for phase_usage in self._task_usage.by_phase.values():
        total.prompt_tokens += phase_usage.prompt_tokens
        total.completion_tokens += phase_usage.completion_tokens
        total.total_tokens += phase_usage.total_tokens
        total.engine = phase_usage.engine or self.llm_client.model
    self._task_usage.total = total
```
