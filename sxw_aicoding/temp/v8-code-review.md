# v8 Goal-Driven Planner 代码评审报告

**评审日期**: 2026-05-12
**评审依据**: `/Users/shixiangweii/.claude/plans/ultrathink-while-coding-toasty-grove.md`
**评审范围**: v8 目标驱动规划引擎全部代码变更

---

## 验证概览

| 检查项 | 结果 |
|-------|--------|
| 语法编译 | 通过 — 所有 6 个文件编译无错误 |
| v8 测试 (36个) | 全部通过 |
| 回归测试 (268个) | 5个既有失败（2个缺少 `@pytest.mark.asyncio`，3个需 API Key），0个 v8 引入的新失败 |

---

## 逐步骤合规分析

### Step 1: Schema 模型 (`schema.py`) — 合规 ✅

`schema.py` L570-641 实现了计划规定的全部 5 个模型：

- `Milestone` — id, description, completion_criteria, estimated_complexity ✅
- `MilestonePlan` — goal_description, milestones, backward_reasoning ✅
- `GoalDocument` — original_task, success_criteria, target_state_description, key_deliverables, constraints, progress_pct, completed_milestones_summary, current_focus ✅
- `GoalReflection` — current_state_summary, gap_analysis, next_milestone, progress_pct, suggested_action, reasoning ✅
- `GoalReanchorResult` — updated_goal_doc, goal_drift_detected, correction_applied ✅

位置正确（TodoList 之后、Memory 之前），注释块 `# Goal-Driven Planning (v8)` 符合规范。

微小偏差：`GoalDocument` 额外增加了 `updated_at` 字段，计划未指定但属于有用补充。

### Step 2: 配置 (`config.py`) — 合规 ✅

`config.py` L90-96 实现了全部 5 个配置变量。

有意偏差：`MAX_GOAL_DRIVEN_ITERATIONS` 默认值为 `str(MAX_TODO_ITEMS * MAX_TODO_RETRIES)` 而非硬编码 `"60"`。结果等价（20×3=60），但与 `MAX_EMERGENT_OUTER_ITERATIONS` 保持一致，**优于计划**。

### Step 3: 核心引擎 (`agents/goal_driven_planner.py`) — 存在问题 ⚠️

详见下方问题清单。

### Step 4: Orchestrator 集成 (`agents/orchestrator.py`) — 存在问题 ⚠️

`__init__` L130-141 完全匹配计划。`_execute_emergent` L370-432 比计划增加了质量门控逻辑（检查 blocked TODOs），属于正向偏差。但存在封装性问题（见下方）。

### Step 5: 追踪支持 (`tracing/spans.py`, `tracing/bridge.py`) — 存在问题 ⚠️

`spans.py` 新增 4 个 SpanName + 7 个 AttrKey（计划要求 4+4，更多无碍）。`bridge.py` 新增 3 个事件处理器。但 phase 映射缺失（见下方）。

### Step 6: 测试 (`tests/test_goal_driven_planner.py`) — 合规 ✅

全部 6 个计划测试类均已实现，另增 `TestGoalGuidedReactLoop`（3个测试），共 36 个测试全部通过。

---

## 问题清单

### P0 — 必须修复（生产环境中会导致 Bug）

#### P0-1: v8 事件数据格式破坏 TracingBridge 兼容性

**位置**: `goal_driven_planner.py` L327, L352-371

**问题**: v8 发射的 `todo_start/complete/failed/blocked` 事件数据格式与 v5 不同：

| 事件 | v5 格式 | v8 格式 |
|------|---------|---------|
| `todo_start` | `{"todo": TodoItem}` | `{"todo_id": int, "description": str}` |
| `todo_complete` | `{"todo": TodoItem, "result": StepResult}` | `{"todo_id": int, "output": str}` |
| `todo_failed` | `{"todo": TodoItem, "result": StepResult}` | `{"todo_id": int, "retry": int, "reason": str}` |
| `todo_blocked` | `{"todo": TodoItem, "result": StepResult}` | `{"todo_id": int, "reason": str}` |

`TracingBridge._on_todo_start` 调用 `data.get("todo")`，期望得到 TodoItem 对象。v8 事件中该字段为 None，处理器直接 return — **v8 的 TODO 执行不会创建任何追踪 Span**。

计划明确要求"v5-compatible"事件，但实现使用了不兼容格式。

**修复方案**: 将 `goal_driven_planner.py` 中的事件数据改为与 v5 一致：

```python
# L327: todo_start
self._emit("todo_start", {"todo": current_todo})

# L352-356: todo_complete
self._emit("todo_complete", {"todo": current_todo, "result": result})

# L362-364: todo_blocked
self._emit("todo_blocked", {"todo": current_todo, "result": result})

# L367-371: todo_failed
self._emit("todo_failed", {"todo": current_todo, "result": result})
```

#### P0-2: 内层 ReAct 循环缺少上下文压缩

**位置**: `goal_driven_planner.py` L570-699 (`_execute_todo_goal_guided`)

**问题**: 该方法构建本地 `messages` 列表，仅在迭代次数上有界（`max_iterations`），但无 token 总量控制。v5 使用 `BaseAgent.think_with_tools()` 自动调用 `context_manager.compress_if_needed()`；v8 直接调用 `self.llm_client.chat_with_tools(messages, ...)` — **无压缩**。对于产出大体积工具结果的 TODO，上下文可能超出 LLM token 限制，导致 API 报错。

**修复方案**: 在每次 LLM 调用前添加压缩检查，例如在 L596 之前：

```python
messages = await self.context_manager.compress_if_needed(messages, self.llm_client)
```

或实现简单的滑动窗口裁剪：

```python
if len(messages) > 20:
    messages = [messages[0]] + messages[-19:]  # 保留 system + 最近 19 条
```

---

### P1 — 应修复（设计缺陷，不影响基本功能但影响可观测性/健壮性）

#### P1-1: `_phase_to_span_name` 缺少 v8 阶段映射

**位置**: `tracing/bridge.py` L251-285

**问题**: `spans.py` 定义了 `SpanName.EXECUTION_GOAL_DRIVEN`，但 `_phase_to_span_name` 中无映射到达它。以下 v8 阶段不会生成 Span：

| 阶段文本 | 当前结果 |
|---------|---------|
| "Building goal document..." | 返回 `""` → 无 Span |
| "Planning backward from goal state..." | 返回 `""` → 无 Span |
| "Executing with goal-driven planning (v8)..." | 返回 `""` → 无 Span |
| "Compiling final answer against goal..." | 返回 `""` → 无 Span |

**修复方案**: 在 `_phase_to_span_name` 中添加 v8 映射：

```python
elif "executing" in text_lower and ("goal-driven" in text_lower or "v8" in text_lower):
    return SpanName.EXECUTION_GOAL_DRIVEN
elif "building goal" in text_lower:
    return SpanName.GOAL_ANCHOR
elif "backward" in text_lower and "planning" in text_lower:
    return SpanName.GOAL_ANCHOR
elif "compiling" in text_lower:
    return SpanName.GOAL_ANCHOR
```

#### P1-2: Orchestrator 直接访问 GoalDrivenPlannerAgent 私有属性

**位置**: `agents/orchestrator.py` L388

**问题**: `self.goal_driven_planner._todo_list` 直接访问另一个 Agent 的私有属性，违反封装原则。若属性重命名将导致静默失败。

**修复方案**: 在 `GoalDrivenPlannerAgent` 中添加公共方法：

```python
def get_blocked_todos(self) -> list[TodoItem]:
    if not self._todo_list:
        return []
    return [t for t in self._todo_list.todos.values() if t.status == TodoStatus.BLOCKED]
```

然后将 `orchestrator.py` 中的 `self.goal_driven_planner._todo_list` 替换为 `self.goal_driven_planner.get_blocked_todos()`。

---

### P2 — 建议修复（提升代码健壮性，非阻塞）

#### P2-1: `_milestones_to_todos` 绕过 `TodoList.add_todo()`

**位置**: `goal_driven_planner.py` L446-459

**问题**: 直接插入 `todo_list.todos[ms.id]`，跳过了：
- 环检测 (`_has_cycle()`)
- `next_id` 自增（`next_id` 停留在 1）

当前里程碑是线性链，不可能有环，功能正常。但 `next_id` 过时，若后续 `_refresh_todo_list` 使用 `todo_list.next_id`（实际未使用，改用 `max(keys)+1`）则可能冲突。

**修复方案**: 插入后更新 `next_id`：

```python
todo_list.next_id = max(todo_list.todos.keys(), default=0) + 1
```

或改用 `todo_list.add_todo()`（需适配 Milestone.id 与自动 ID 的映射）。

#### P2-2: `GoalReflection.progress_pct` 缺少校验约束

**位置**: `schema.py` L628

**问题**: 模型描述为 `0-100` 但无 Pydantic 约束。LLM 返回超出范围的值不会被拦截。

**修复方案**:

```python
progress_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="0-100 progress estimate / 进度百分比")
```

同理 `GoalDocument.progress_pct`（L611）也应添加约束。

#### P2-3: `_select_todo_by_reflection` 关键词匹配过于简单

**位置**: `goal_driven_planner.py` L523-529

**问题**: 使用 `set(word.lower().split())` 做交集匹配。"Implement async patterns" 与 "Set up asynchronous code" 无法匹配，尽管语义相同。

**当前为可接受的 MVP 方案**，后续可考虑嵌入语义匹配或让 LLM 直接返回目标 TODO ID。

---

## 测试覆盖评估

### 已覆盖

- ✅ 数据模型创建、默认值、序列化（10个测试）
- ✅ 构建目标文档（Mock LLM）
- ✅ 逆向规划（Mock LLM）
- ✅ 目标反思（Mock LLM）
- ✅ 里程碑转 TODO
- ✅ 反思引导的 TODO 选择
- ✅ 停滞检测
- ✅ 目标重锚定
- ✅ 条件判断（should_reanchor, should_refresh_todos）
- ✅ 答案汇编（成功/全失败）
- ✅ Orchestrator 路由（v8 启用/禁用）
- ✅ 事件序列验证
- ✅ TracingBridge 处理器存在性
- ✅ 内层 ReAct 循环（无工具调用、最大迭代、目标注入）

### 缺失覆盖

- ❌ **事件格式兼容性测试**：未验证 v8 事件数据能通过 TracingBridge 处理器正确创建 Span
- ❌ **上下文压缩测试**：未验证长消息列表不会导致 LLM API 错误
- ❌ **progress_pct 边界测试**：计划要求但模型无约束，测试也未验证
- ❌ **`_refresh_todo_list` 端到端测试**：新 TODO 添加、修改、阻塞的完整流程

---

## 正向评价

1. **架构设计精准**：三个核心创新（GoalDocument 持久化、ReflAct 反思、逆向规划）完整落地，与计划高度一致
2. **特性开关设计**：`ENABLE_GOAL_DRIVEN_PLANNER` 默认关闭，零侵入现有 v5 路径
3. **代码风格统一**：双语注释、事件驱动、Pydantic 模型等与项目现有规范完全一致
4. **测试质量**：36个测试覆盖模型、Agent、路由、事件、ReAct 循环，远超计划最低要求
5. **Orchestrator 质量门控**：超出计划的 blocked TODO 检查，提供有价值的执行质量反馈
6. **配置设计**：`MAX_GOAL_DRIVEN_ITERATIONS` 动态计算优于硬编码
