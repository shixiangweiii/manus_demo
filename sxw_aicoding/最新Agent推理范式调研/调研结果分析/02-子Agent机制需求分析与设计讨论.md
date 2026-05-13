# 子 Agent 机制需求分析与设计讨论

> 分析日期：2026-05-13
> 状态：阶段性讨论记录，未达成最终结论
> 背景：用户倾向引入子 Agent 机制以应对复杂任务，但尚未彻底想清楚具体方向

---

## 一、当前系统的能力边界

### 能做到的

四个引擎（simple / DAG / emergent / goal-driven）已覆盖：
- 结构化规划（v1/v2）和涌现式规划（v5/v8）
- 顺序执行和拓扑并行（v2 DAG，当前串行退化）
- 目标锚定和状态反思（v8 ReflAct）
- 动态 DAG 变更（v3 自适应规划）
- 全链路追踪（v7 OpenTelemetry）

### 做不到的（架构层面的根本限制）

**限制 1：无法隔离子任务的上下文**

当 v8 的 GoalDrivenPlanner 执行一个复杂 TODO 时，该 TODO 的全部 ReAct 中间过程（搜索结果、代码输出、文件内容）堆积在 `self._messages` 里。后续 TODO 执行时，前面 TODO 的噪声仍在上下文中，ContextManager 压缩会丢失关键信息。

```
主任务："对比 React 和 Vue 的性能差异"
  → TODO 1: "深入分析 React 的渲染机制"
      → 15 轮 ReAct：读文档、跑 benchmark、分析结果...
      → _messages 已堆积大量 React 细节
  → TODO 2: "深入分析 Vue 的渲染机制"
      → 又 15 轮，继续堆积
      → ContextManager 触发压缩，React 分析的关键细节可能被截断
  → TODO 3: "对比分析"
      → 此时上下文已严重衰减
```

**限制 2：无法按子任务匹配最优策略**

整个任务只能选择一种引擎。但实际复杂任务中，不同子任务可能适合不同策略：
- "搜索最新论文" → 适合 emergent（灵活探索）
- "做技术可行性分析" → 适合 goal-driven（有明确完成标准）
- "写报告" → 适合 simple（按大纲顺序执行）

当前系统在规划阶段（`classify_task()`）就锁定了引擎，无法动态切换。

**限制 3：无法安全并行**

OrchestratorAgent 只创建一个 ExecutorAgent 实例（`orchestrator.py:117-121`），DAG 并行节点共享该实例的 `_messages`，导致竞态。已通过 `DAG_SERIAL_EXECUTION=true` 串行退化修复，但牺牲了并行效率。

### 限制的本质

三个限制归结为一个核心问题：**当子任务本身足够复杂时，其执行过程不应该污染父 agent 的上下文**。

这是一个**上下文工程（Context Engineering）**问题。当前架构没有"隔离执行"的能力——要么不做，要么全塞进同一个 context window。

---

## 二、子 Agent 解决什么

子 Agent 机制的核心价值不是"多 Agent 协作"这个抽象概念，而是三个具体的工程能力：

| 能力 | 当前状态 | 子 Agent 如何提供 |
|------|---------|-----------------|
| **上下文隔离** | 子任务的全部消息堆积在父 agent 的 _messages | 子 agent 有独立 _messages，完成后只回传摘要 |
| **引擎选择** | 整个任务只能选一种引擎 | 子任务可以选不同引擎——DAG 的某个节点用 goal-driven 执行 |
| **实例隔离** | 共享 ExecutorAgent 导致并行竞态 | 每个子 agent 是独立实例，天然隔离 |

第三个点是附加收益：子 Agent 机制也**同时解决了 DAG 并行竞态 bug**——每个并行节点用独立子 agent 执行，`_messages` 天然隔离，无需串行退化。但这不是引入子 Agent 的主要动机，只是架构上的自然结果。

---

## 三、三种可能的设计维度

### 维度 A：作为 Tool（最轻量）

在 `tools/` 下新增 `spawn_subagent.py`，让任何 ReAct 循环中的 agent 都能通过 tool call 派生子 agent：

```
父 agent ReAct 循环中：
  Thought: 这个子任务比较复杂，交给子 agent 处理
  Action: spawn_subagent(task="分析这份文档并提取关键指标",
                         engine="goal_driven",
                         max_iterations=15)
  Observation: [子 agent 摘要] "文档包含 5 个关键指标: ..."
```

- **优势**：不动 orchestrator 和任何现有 agent，只是在 tools 里加一个。所有引擎都自然获得"委派子任务"的能力。去中心化——v8 的 GoalDrivenPlanner 执行某个复杂 TODO 时，最清楚这个 TODO 是否需要独立上下文。
- **挑战**：子 agent 实例的生命周期管理——同步阻塞还是异步？tool call 是同步的，但子 agent 内部可能跑很多轮。

### 维度 B：在 Orchestrator 层面引入 Task Decomposition

改造 `orchestrator.py`，让它在 `_execute_emergent` 或 `_execute_dag_and_reflect` 时，能将某些步骤/节点委托给独立子 agent：

```
Orchestrator.execute(task)
  → PlannerAgent.classify_task() → "complex"
  → PlannerAgent.create_dag()
  → DAGExecutor.execute(dag)
      → 对每个 ACTION 节点:
          创建独立 SubAgent(task=node.description, engine="emergent")
          result = await sub_agent.run()
          merge result.output → DAGState
```

- **优势**：集中控制，改动面可控。直接解决 DAG 并行问题。
- **挑战**：Orchestrator 必须在规划阶段就预判哪些步骤需要子 Agent，又回到"预先规划一切"的老路。

### 维度 C：递归分形（最深度的改造）

参考 Claude Code 的 Task tool——子 agent 也可以调用 `spawn_subagent`，形成递归：

```
Orchestrator (task: "做一份市场调研报告")
  → spawn_subagent(task="调研 AI Agent 市场", engine="goal_driven")
      → 内部 spawn_subagent(task="搜索最新论文", engine="simple")
      → 内部 spawn_subagent(task="分析竞品功能", engine="emergent")
      → 综合返回摘要
  → spawn_subagent(task="撰写报告", engine="goal_driven")
  → 汇总输出
```

- **优势**：最灵活，真正实现"分形递归"——复杂任务可以无限拆分。
- **挑战**：需要深度限制（MAX_SUBAGENT_DEPTH）、成本控制、防止无限递归。

### 三个维度的对比

| 维度 | 代码量 | 覆盖范围 | 解决 DAG 并行 | 灵活性 |
|------|--------|---------|--------------|--------|
| A (Tool) | ~300 行 | 所有引擎都获得委派能力 | 间接（需改 DAGExecutor 用 tool） | 高（任何 agent 都能派生） |
| B (Orchestrator 改造) | ~500 行 | DAG 路径直接受益 | 直接解决 | 中（集中控制） |
| C (递归) | 在 A/B 基础上加 ~200 行 | 最灵活 | 取决于基础层 | 最高（但有失控风险） |

---

## 四、关键设计问题（待决策）

### 问题 1：子 Agent 与现有引擎的关系

两种理解：

- **理解 A**：子 Agent 是一个"横切"升级——不替代现有任何引擎，而是让所有引擎获得"委派"能力。子 Agent 是一个 **Runtime**（独立 context + 生命周期管理），内部可以选择任意引擎执行。
- **理解 B**：子 Agent 是一个独立的新引擎，和 simple/DAG/emergent/goal-driven 并列，作为 PLAN_MODE 的第五个选项。

理解 A 更合理——子 Agent 不是一种"规划策略"，而是一种"执行基础设施"。所有现有引擎都运行在子 Agent Runtime 之上。

### 问题 2：谁能派生子 Agent？

- **选项 A**：只有 Orchestrator 能派生（集中控制，简单）
- **选项 B**：任何 agent 在 ReAct 循环中都能通过 tool call 派生（去中心化，灵活）

倾向选 B 的理由：v8 的 GoalDrivenPlanner 在执行某个复杂 TODO 时，最清楚这个 TODO 是否需要独立上下文。如果只有 Orchestrator 能派生，那 Orchestrator 必须在规划阶段就预判——这又回到了 v1/v2 的"预先规划一切"老路，违背 v5/v8 的涌现精神。

### 问题 3：上下文回传粒度

子 agent 完成后回传什么？

| 策略 | 内容 | 优势 | 劣势 |
|------|------|------|------|
| 只传摘要 | 1-2 段自然语言总结 | 压缩比最高，父 agent 最干净 | 可能丢失关键细节 |
| 结构化结果 | summary + key_findings + artifacts | 保留关键数据点 | 需要定义 schema |
| 混合 | 摘要 + 可选的详细附件 | 灵活 | 复杂度高 |

Claude Code 的 Task tool 采用"只传摘要"策略，这是最成熟的设计。子 agent 的完整历史不回传，只回传 LLM 生成的结构化摘要。

### 问题 4：引擎选择策略

子 Agent 内部跑什么引擎，谁决定？

- 由父 agent 的 LLM 在 tool call 参数中指定（`spawn_subagent(engine="goal_driven")`）
- 由系统根据子任务复杂度自动分类
- 固定一种（比如子 agent 一律用 simple 模式，保持轻量）

### 问题 5：递归深度

子 agent 是否也能派生子 agent？

- 允许递归：更灵活，但需要 MAX_SUBAGENT_DEPTH 限制（Claude Code 默认 3 层）
- 只允许一层：简单可控，但无法处理"子任务的子任务"

### 问题 6：与 DAG 并行的关系

子 Agent 和 DAG 串行化是同一个问题的两面：

- 当前方案：`DAG_SERIAL_EXECUTION=true` → 串行退化，保证正确
- 子 Agent 方案：为每个 DAG 节点创建独立子 agent → 恢复并行，实例隔离保证正确

如果引入子 Agent，`DAG_SERIAL_EXECUTION` 可以设回 `false`。但这是**附加收益**，不是引入子 Agent 的主要动机。

---

## 五、与调研报告路线图的关系

调研报告的路线图：

```
v8 (当前)    ReflAct 目标驱动
v8.1        独立 Critic 双核校验
v9          CodeAct + LATS 树搜索
v10         Subagent + CoA + MUSE     ← 子 Agent 在 v10
v11         Model-native 对照
```

子 Agent 在路线图中被放在 v10（协作革命），但从架构角度看，它的定位需要重新思考：

**子 Agent 是"横切"升级（所有引擎受益），还是"纵深"升级（L4 新能力）？**

- 如果是横切：可以更早引入（甚至在 v9 之前或同时），因为它增强了所有现有引擎
- 如果是纵深：按路线图 v10 引入是合理的

这个问题的答案取决于引入子 Agent 的主要动机：
- 如果主要是为了**上下文隔离**（解决当前引擎的上下文溢出问题）→ 横切，应提前
- 如果主要是为了**多 Agent 协作**（CoA Worker-Manager 模式）→ 纵深，v10 合理

---

## 六、尚未想清楚的问题

1. **定位问题**：子 Agent 是横切基础设施还是 L4 新范式？这决定了它的引入时机和设计深度。

2. **与 CodeAct 的优先级**：CodeAct（v9）是执行层升级，ROI 最高、实现最简单。子 Agent 是架构层升级，影响面更大但实现更复杂。两者正交，先做哪个？

3. **最小可行设计**：子 Agent 的最小 MVP 是什么？是否可以先用一个极简的 `spawn_subagent` tool 跑通，验证价值后再扩展？

4. **递归必要性**：单层委派（父 → 子，不递归）是否已经足够覆盖大部分复杂任务场景？

5. **成本控制**：子 Agent 意味着更多 LLM 调用。如何在不显著增加成本的前提下引入子 Agent？
