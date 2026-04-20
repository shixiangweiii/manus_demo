# Complex DAG 分层规划（v2/v3/v4）代码评审报告

> **评审范围**：`dag/graph.py`、`dag/executor.py`、`dag/state_machine.py`、`agents/planner.py`、`agents/orchestrator.py`、`agents/executor.py`、`agents/reflector.py`、`schema.py`、`config.py`、`tools/router.py`
>
> **评审日期**：2026-04-20
>
> **评审人**：Aone Copilot

---

## 一、架构总览

### 1.1 整体执行流程

```
用户任务
  │
  ▼
OrchestratorAgent.run()
  │
  ├─ _gather_context()          ← 检索记忆 + 知识库
  │
  ├─ PlannerAgent.classify_task()
  │    ├─ Stage 1: _rule_classify()   ← 规则快筛 (<1ms)
  │    └─ Stage 2: _llm_classify()    ← LLM 兜底 (~0.3s)
  │
  ├─ [complex] PlannerAgent.create_dag()
  │    └─ 生成三层 DAG: Goal → SubGoal → Action
  │
  ├─ DAGExecutor.execute(dag)
  │    └─ while not dag.is_complete():
  │         ├─ get_ready_nodes()           ← 动态就绪发现
  │         ├─ asyncio.gather(...)         ← Super-step 并行执行
  │         ├─ _check_exit_criteria()      ← 逐节点质量门控
  │         ├─ _handle_failure()           ← 回滚 + 子树跳过
  │         ├─ _process_conditions()       ← 条件边评估
  │         ├─ _adapt_plan()               ← v3 自适应规划
  │         └─ save_checkpoint()           ← 状态快照
  │
  ├─ ReflectorAgent.reflect_dag()          ← 整体质量评估
  │    └─ 失败 → PlannerAgent.replan_subtree() → 局部重规划
  │
  └─ 存入长期记忆
```

### 1.2 涉及文件与职责

| 文件 | 行数 | 职责 |
|------|------|------|
| `schema.py` | 500 | Pydantic 数据模型：TaskNode、TaskEdge、DAGState、NodeStatus 等 |
| `dag/graph.py` | 466 | TaskDAG 图结构：就绪发现、拓扑排序、子树跳过、动态增删改 |
| `dag/state_machine.py` | 112 | NodeStateMachine：7 种状态的合法转移校验 |
| `dag/executor.py` | 498 | DAGExecutor：Super-step 并行执行引擎 |
| `agents/planner.py` | ~600 | 两阶段分类器 + DAG 生成 + 局部重规划 + 自适应规划 |
| `agents/orchestrator.py` | ~400 | 中央协调者：路由 + 执行 + 反思循环 |
| `agents/executor.py` | ~300 | ReAct 循环执行单个节点 |
| `agents/reflector.py` | ~250 | 质量验证：逐节点 exit criteria + 整体反思 |
| `config.py` | 66 | 配置项：PLAN_MODE、MAX_PARALLEL_NODES 等 |
| `tools/router.py` | 168 | ToolRouter：工具失败追踪 + 切换建议 |

---

## 二、架构优点

### ✅ 2.1 清晰的 Super-step 并行模型

DAGExecutor 的主循环实现了标准的 BSP（Bulk Synchronous Parallel）模型，每轮 Super-step 的流程清晰：发现就绪节点 → 并行执行 → 合并结果 → 验证 → 处理失败 → 条件评估 → 自适应 → Checkpoint。代码注释详尽，中英双语，易于理解。

### ✅ 2.2 状态机设计

`NodeStateMachine` 通过 `VALID_TRANSITIONS` 表定义了严格的状态转移规则，7 种状态、3 个终态，转移图清晰。`InvalidTransitionError` 异常提供了详细的错误信息（当前状态、目标状态、合法目标列表），有助于调试。

### ✅ 2.3 并行安全的集中式状态

`DAGState.node_results` 采用 `dict[str, str]`（node_id → output），每个节点写入独立 key，并行执行天然无冲突。这是对 LangGraph "LastValue" channel 的简洁等价实现。

### ✅ 2.4 丰富的动态能力

v2/v3/v4 累计实现了 9 种动态性：运行时就绪发现、自动并行、条件分支、失败隔离+回滚、局部重规划、状态机强制转移、超步间自适应、工具智能路由、DAG 运行时增删改。

### ✅ 2.5 两阶段混合分类器

规则快筛（<1ms）处理 60-70% 的明确请求，LLM 兜底处理模糊区间，兼顾效率和准确性。

---

## 三、问题发现

### 🔴 P0 — 严重问题（应立即修复）

#### 3.1 状态机被多处绕过，状态一致性无法保证

**问题描述**：系统设计了 `NodeStateMachine` 来强制合法状态转移，但多处代码直接赋值 `node.status = ...`，完全绕过了状态机校验，使得状态机形同虚设。

**影响位置**：

| 位置 | 代码 | 风险 |
|------|------|------|
| `dag/graph.py:178` `mark_subtree_skipped()` | `node.status = NodeStatus.SKIPPED` | 绕过状态机，可能从 RUNNING 直接跳到 SKIPPED（非法转移） |
| `dag/graph.py:191` `refresh_ready_states()` | `node.status = NodeStatus.READY` | 绕过状态机，无转移日志和回调 |
| `dag/executor.py:395` `_complete_structural_nodes()` | `node.status = NodeStatus.SKIPPED` | 绕过状态机，可能从非法源状态转移 |
| `dag/executor.py:348` `_process_conditions()` | `target.status = NodeStatus.SKIPPED` | 绕过状态机，无合法性校验 |

**建议**：所有状态修改必须通过 `NodeStateMachine.transition()` 方法。`TaskDAG` 应持有或接收一个 `NodeStateMachine` 实例，`mark_subtree_skipped()` 和 `refresh_ready_states()` 内部调用 `sm.transition()` 而非直接赋值。

---

#### 3.2 `_validate_dag()` 校验失败仅记录日志，不阻断

**问题描述**：

```python
# dag/graph.py:443-449
def _validate_dag(self) -> None:
    node_ids = set(self.nodes.keys())
    for e in self.edges:
        if e.source not in node_ids:
            logger.warning("[DAG] Edge source '%s' not found in nodes", e.source)
        if e.target not in node_ids:
            logger.warning("[DAG] Edge target '%s' not found in nodes", e.target)
```

**影响**：悬空边（引用不存在的节点）不会被拦截，后续 `get_ready_nodes()` 中 `self.nodes[d]` 会抛出 `KeyError`，错误信息不直观，难以定位根因。

**建议**：校验失败时抛出 `ValueError`，在 DAG 构造阶段就暴露问题：

```python
if e.source not in node_ids:
    raise ValueError(f"Edge source '{e.source}' not found in nodes")
```

---

#### 3.3 `_handle_failure()` 回滚节点失败后处理不当

**问题描述**：

```python
# dag/executor.py:289-296
for rb_id in rollback_targets:
    rb_node = dag.nodes.get(rb_id)
    if rb_node and rb_node.status == NodeStatus.PENDING:
        rb_result = await self._run_node(rb_node, dag)
        dag.state.merge_result(rb_id, rb_result.output)
        if rb_result.success:
            self._sm.transition(rb_node, NodeStatus.COMPLETED)
        else:
            self._sm.transition(rb_node, NodeStatus.FAILED)  # ← 回滚失败，然后呢？

# 回滚执行后将原节点标记为 ROLLED_BACK
self._sm.transition(node, NodeStatus.ROLLED_BACK)  # ← 即使回滚失败也标记为 ROLLED_BACK
```

**影响**：
1. 回滚节点失败后，其状态为 FAILED，但没有进一步处理（不会触发二次回滚或跳过）
2. 即使回滚失败，原节点仍被标记为 `ROLLED_BACK`，语义不准确
3. 回滚节点失败可能导致 DAG 卡死（FAILED 状态的回滚节点阻断 `is_complete()`）

**建议**：
- 回滚节点失败时，将其标记为 `SKIPPED` 而非 `FAILED`
- 原节点在回滚失败时应标记为 `SKIPPED` 而非 `ROLLED_BACK`
- 记录回滚失败的详细日志

---

### 🟡 P1 — 中等问题（建议尽快修复）

#### 3.4 `get_downstream()` 时间复杂度 O(V×E)

**问题描述**：

```python
# dag/graph.py:148-158
while queue:
    nid = queue.popleft()
    if nid in visited:
        continue
    visited.add(nid)
    for e in self.edges:  # ← 每个节点都遍历全部边
        if e.source == nid and e.edge_type == EdgeType.DEPENDENCY:
            queue.append(e.target)
```

**影响**：对于 N 个节点、E 条边的 DAG，BFS 中每个节点都遍历所有边，总复杂度为 O(V×E)。当 DAG 规模增大时性能下降明显。同样的问题存在于 `topological_sort()` 中。

**建议**：预构建邻接表 `_adjacency: dict[str, list[str]]`，在 `__init__` 和动态增删边时维护，将 BFS 复杂度降至 O(V+E)。

---

#### 3.5 Super-step 并行执行缺少超时控制

**问题描述**：

```python
# dag/executor.py:161-164
results = await asyncio.gather(*[
    self._run_node(node, dag) for node in batch
])
```

**影响**：`asyncio.gather` 没有超时参数。如果某个节点的 ReAct 循环卡死（如 LLM 无响应），整个批次的所有节点都会被阻塞，无法继续执行。

**建议**：使用 `asyncio.wait_for` 为每个节点添加超时：

```python
results = await asyncio.gather(*[
    asyncio.wait_for(self._run_node(node, dag), timeout=config.NODE_TIMEOUT)
    for node in batch
])
```

---

#### 3.6 主循环死锁检测不完整

**问题描述**：

```python
# dag/executor.py:137-140
if not ready:
    logger.warning("[DAGExecutor] No ready nodes at super-step %d. %s", step, dag.summary())
    break
```

**影响**：当没有就绪节点但 DAG 未完成时，仅记录 warning 并 break。调用者无法区分"正常完成"和"死锁退出"。可能的死锁原因包括：
- 存在 FAILED 节点阻断了下游但未被跳过
- 循环依赖（虽然 `topological_sort` 会检测，但动态添加的边可能引入环）

**建议**：break 前检查是否有 FAILED 节点，并在返回值或异常中明确标识死锁状态：

```python
if not ready:
    if dag.has_failed_nodes():
        logger.error("[DAGExecutor] DAG stuck: failed nodes blocking progress")
    break
```

---

#### 3.7 `replan_subtree()` 合并后未检测环

**问题描述**：局部重规划生成新子树后合并到原 DAG，但合并后没有调用 `topological_sort()` 检测是否引入了循环依赖。

**影响**：LLM 生成的新子树可能包含指向已有节点的边，形成环，导致后续执行死锁。

**建议**：合并后调用 `topological_sort()` 并检查结果长度是否等于节点数。

---

#### 3.8 Checkpoint 仅存内存，无恢复机制

**问题描述**：

```python
# dag/graph.py:400
def save_checkpoint(self) -> None:
    self._checkpoints.append(self.to_dict())
```

**影响**：
1. Checkpoint 仅存在内存中，进程退出即丢失
2. `_checkpoints` 列表无限增长，长时间运行存在内存泄漏风险
3. 虽然有 `from_dict()` 方法，但没有从 checkpoint 恢复的完整流程

**建议**：
- 添加 `MAX_CHECKPOINTS` 配置，限制快照数量（如保留最近 10 个）
- 提供 `restore_from_checkpoint(index)` 方法
- 长期考虑：将 checkpoint 持久化到磁盘

---

### 🟢 P2 — 轻微问题（可择机修复）

#### 3.9 条件边评估过于简单

**问题描述**：

```python
# dag/executor.py:361-366
@staticmethod
def _evaluate_condition(edge, dag: TaskDAG) -> bool:
    if not edge.condition:
        return True
    source_result = dag.state.node_results.get(edge.source, "")
    return edge.condition.lower() in source_result.lower()
```

**影响**：仅做大小写不敏感的关键词匹配，无法处理语义级条件（如"如果价格低于 100 元"）。代码注释中也标注了"Production systems would use LLM-based evaluation here"。

**建议**：作为教学 Demo 可接受，但应在文档中明确标注此限制。

---

#### 3.10 `_compile_output()` 输出质量差

**问题描述**：

```python
# dag/executor.py:405-413
@staticmethod
def _compile_output(dag: TaskDAG) -> str:
    parts = []
    for node in dag.nodes.values():
        if node.node_type == NodeType.ACTION and node.status == NodeStatus.COMPLETED:
            if node.result:
                parts.append(node.result)
    if not parts:
        return "No action nodes completed successfully."
    return "\n\n".join(parts)
```

**影响**：
- 输出顺序取决于 `dict.values()` 的迭代顺序，不保证按拓扑序排列
- 没有节点标识，无法区分哪段结果来自哪个节点
- 简单拼接，无格式化

**建议**：按拓扑序排列，添加节点标识前缀。

---

#### 3.11 `topological_sort()` 入度计算冗余

**问题描述**：

```python
# dag/graph.py:211-214
in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}  # 已初始化为 0
for e in self.edges:
    if e.edge_type == EdgeType.DEPENDENCY:
        in_degree[e.target] = in_degree.get(e.target, 0) + 1  # .get() 冗余
```

**影响**：功能正确，但 `in_degree.get(e.target, 0)` 是冗余的，因为已经用字典推导式初始化了所有 key。

**建议**：直接使用 `in_degree[e.target] += 1`。

---

#### 3.12 状态机回调异常被静默吞掉

**问题描述**：

```python
# dag/state_machine.py:107-110
if self._on_transition:
    try:
        self._on_transition(node.id, old_status, new_status)
    except Exception:
        pass  # UI errors should never crash the pipeline
```

**影响**：所有异常（包括编程错误）都被静默忽略，调试困难。

**建议**：至少记录日志：`logger.debug("UI callback error", exc_info=True)`。

---

#### 3.13 `config.py` 中 API Key 硬编码

**问题描述**：`LLM_API_KEY` 的默认值直接暴露了密钥字符串。

**影响**：安全风险，密钥可能被提交到版本控制。

**建议**：默认值设为空字符串，启动时校验必填。

---

#### 3.14 `validate_exit_criteria` 失败时默认通过

**问题描述**：`ReflectorAgent.validate_exit_criteria()` 在 LLM 调用失败时默认返回 `passed=True`。

**影响**：LLM 服务不可用时，所有节点都会被标记为通过，质量门控失效。

**建议**：提供可配置的失败策略（默认通过 / 默认失败 / 重试）。

---

#### 3.15 工具结果截断可能丢失关键信息

**问题描述**：`ExecutorAgent` 中工具结果被截断为 1000 字符，`ReflectorAgent` 中节点结果被截断为 300 字符。

**影响**：长输出的关键信息可能被截断，导致后续推理或验证失败。

**建议**：将截断长度移到 `config.py` 中，提供可配置选项。

---

#### 3.16 `add_dynamic_edge()` 未检测环

**问题描述**：动态添加边时只检查了端点存在性和重复边，没有检测是否引入了环。

**影响**：运行时添加的边可能形成循环依赖，导致 `get_ready_nodes()` 永远无法返回环中的节点，造成死锁。

**建议**：添加边后调用 `topological_sort()` 检测环，若检测到环则回滚添加操作。

---

## 四、代码重复问题

| 重复代码 | 位置 | 建议 |
|----------|------|------|
| `get_ready_nodes()` 与 `refresh_ready_states()` 的依赖检查逻辑 | `dag/graph.py:96-104` vs `dag/graph.py:186-193` | 提取为 `_are_deps_completed(node_id)` 私有方法 |
| `reflect()` 与 `reflect_dag()` 的 prompt 构建和 LLM 调用逻辑 | `agents/reflector.py` | 提取为 `_evaluate(task, summary, results)` 通用方法 |
| `execute_step()` 与 `execute_node()` 的 ReAct 循环逻辑 | `agents/executor.py` | 合并为统一入口，通过参数区分 |

---

## 五、安全与健壮性

| 风险项 | 描述 | 严重程度 |
|--------|------|----------|
| **API Key 泄露** | `config.py` 中硬编码了 LLM API Key 默认值 | 🔴 高 |
| **无并发控制** | 多个 Super-step 理论上不会并发，但 `asyncio.gather` 内的节点可能修改共享状态 | 🟡 中 |
| **无资源限制** | Checkpoint 列表无限增长、ReAct 循环无超时 | 🟡 中 |
| **异常处理不一致** | 有的地方抛异常、有的记日志、有的静默忽略 | 🟡 中 |

---

## 六、总结评分

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构设计** | ⭐⭐⭐⭐ | Super-step 模型清晰，分层架构合理，职责分明 |
| **代码可读性** | ⭐⭐⭐⭐⭐ | 中英双语注释详尽，命名规范，代码结构清晰 |
| **正确性** | ⭐⭐⭐ | 状态机绕过问题严重，回滚失败处理不当，存在潜在死锁 |
| **健壮性** | ⭐⭐⭐ | 缺少超时控制、环检测、资源限制，异常处理不一致 |
| **可维护性** | ⭐⭐⭐⭐ | 模块化良好，但存在代码重复，部分硬编码值应提取为配置 |
| **安全性** | ⭐⭐ | API Key 硬编码，缺少输入验证 |

### 综合评价

> 作为教学 Demo，代码架构设计优秀，注释质量极高，清晰地展示了 DAG 分层规划的核心思想。但在**状态一致性**方面存在严重的设计-实现脱节：精心设计的状态机被多处绕过，使得"状态机强制合法转移"这一核心保障名存实亡。建议优先修复 P0 级别的状态机绕过问题和校验缺失问题，这是整个 DAG 执行引擎可靠性的基石。

---

## 七、修复优先级建议

### P0 — 立即修复
1. **统一状态修改入口**：所有 `node.status = ...` 改为 `sm.transition(node, ...)`
2. **`_validate_dag()` 抛出异常**：校验失败时 `raise ValueError`
3. **修复回滚失败处理**：回滚节点失败时正确处理原节点状态

### P1 — 尽快修复
4. **预构建邻接表**：优化 `get_downstream()` 和 `topological_sort()` 性能
5. **添加超时控制**：`asyncio.gather` 中为每个节点添加超时
6. **死锁检测增强**：主循环 break 前明确标识死锁原因
7. **动态边添加后检测环**：防止运行时引入循环依赖
8. **Checkpoint 数量限制**：防止内存泄漏

### P2 — 择机修复
9. **提取重复代码**：统一依赖检查、反思评估、ReAct 循环的公共逻辑
10. **配置化硬编码值**：截断长度、提示文本等移到 `config.py`
11. **改进输出汇总**：按拓扑序排列，添加节点标识
12. **状态机回调记录日志**：异常不应被完全静默
13. **移除硬编码 API Key**：默认值设为空字符串
