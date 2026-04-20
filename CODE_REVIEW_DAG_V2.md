# DAG 模块二次深度代码评审报告

> **评审范围**：`dag/graph.py`、`dag/executor.py`、`dag/state_machine.py`、`config.py`、`schema.py`、`agents/orchestrator.py`
> **评审时间**：2026-04-20
> **评审维度**：架构一致性、并发安全、边界条件、数据完整性、性能、错误处理

---

## 一、评审总览

| 严重程度 | 数量 | 说明 |
|---------|------|------|
| **P0-Critical** | 4 | 可能导致运行时崩溃或状态不一致 |
| **P1-Major** | 7 | 影响系统健壮性和正确性 |
| **P2-Minor** | 6 | 边界场景或代码质量改进 |
| **P3-Suggestion** | 4 | 架构优化建议 |

---

## 二、P0-Critical 问题（4 个）

### P0-1：`_check_exit_criteria()` 异常导致节点状态卡死在 RUNNING

**文件**：`dag/executor.py` 第 163-180 行

**问题描述**：
主循环中，当 `result.success` 为 `True` 时，调用 `_check_exit_criteria()` 进行 LLM 验证。如果此方法抛出异常（如 LLM 调用超时、网络错误），异常会向上传播，**节点状态停留在 RUNNING**，既不会转移到 COMPLETED 也不会转移到 FAILED。后续 `dag.is_complete()` 永远返回 `False`，导致 DAG 执行永远无法结束。

```python
if result.success:
    passed = await self._check_exit_criteria(node, result)  # ← 如果这里抛异常
    if passed:
        self._sm.transition(node, NodeStatus.COMPLETED)     # ← 不会执行
    else:
        self._sm.transition(node, NodeStatus.FAILED)        # ← 也不会执行
```

**修复建议**：
```python
if result.success:
    try:
        passed = await self._check_exit_criteria(node, result)
    except Exception as exc:
        logger.error("[DAGExecutor] Exit criteria check failed for %s: %s", node.id, exc)
        passed = False
    if passed:
        self._sm.transition(node, NodeStatus.COMPLETED)
        self._emit("node_completed", {"node": node, "result": result})
    else:
        self._sm.transition(node, NodeStatus.FAILED)
        self._emit("node_failed", {"node": node, "result": result, "reason": "exit_criteria"})
        await self._handle_failure(node, dag)
```

---

### P0-2：超时后节点状态不一致 — RUNNING 状态残留

**文件**：`dag/executor.py` 第 234-243 行

**问题描述**：
`_run_node_with_timeout()` 中，`_run_node()` 在执行 `executor_agent.execute_node()` 之前已经将节点状态转移到了 `RUNNING`（第 228-229 行）。当 `asyncio.wait_for` 超时后：
1. `_run_node` 协程被取消，但节点状态已经是 `RUNNING`
2. `_run_node_with_timeout` 返回 `StepResult(success=False, ...)`
3. 主循环中 `result.success` 为 `False`，执行 `self._sm.transition(node, NodeStatus.FAILED)`
4. **这条路径本身是正确的**，但存在一个隐患：如果被取消的协程在取消传播过程中触发了其他状态转移（如 `execute_node` 内部的 finally 块），可能导致状态机抛出 `InvalidTransitionError`

此外，超时返回的 `StepResult` 缺少 `step_id` 字段：
```python
return StepResult(success=False, output=f"Node execution timed out after {timeout}s")
# ↑ 缺少 step_id=node.id
```

**修复建议**：
```python
async def _run_node_with_timeout(self, node: TaskNode, dag: TaskDAG) -> StepResult:
    timeout = config.NODE_EXECUTION_TIMEOUT
    try:
        return await asyncio.wait_for(
            self._run_node(node, dag),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.error("[DAGExecutor] Node %s timed out after %ds", node.id, timeout)
        return StepResult(
            step_id=node.id,
            success=False,
            output=f"Node execution timed out after {timeout}s",
        )
```

---

### P0-3：DAG 和 Executor 使用不同的 NodeStateMachine 实例

**文件**：
- `dag/graph.py` 第 62 行：`self._sm = state_machine or NodeStateMachine()`
- `dag/executor.py` 第 100 行：`self._sm = NodeStateMachine(on_transition=self._on_node_transition)`

**问题描述**：
`TaskDAG` 和 `DAGExecutor` 各自创建独立的 `NodeStateMachine` 实例。这导致：

1. **DAG 的 `_sm`**（无回调）用于：`refresh_ready_states()`、`mark_subtree_skipped()`
2. **Executor 的 `_sm`**（有 UI 回调）用于：`_run_node()`、`_handle_failure()`、`_process_conditions()`、`_complete_structural_nodes()`

两个状态机实例对**同一个节点**做状态转移，但回调行为不同。DAG 内部的状态转移（如 `PENDING→READY`）不会触发 UI 事件，而 Executor 的转移会。更严重的是，如果未来添加审计日志功能，两个实例的日志会不一致。

**修复建议**：
让 `DAGExecutor` 使用 `dag._sm`，或在构造时将 Executor 的状态机注入 DAG：
```python
# 方案 A：Executor 使用 DAG 的状态机
async def execute(self, dag: TaskDAG) -> str:
    # 将 Executor 的回调注入 DAG 的状态机
    dag._sm = NodeStateMachine(on_transition=self._on_node_transition)
    ...

# 方案 B：DAGExecutor 接受外部状态机参数
def __init__(self, ..., state_machine: NodeStateMachine | None = None):
    self._sm = state_machine or NodeStateMachine(on_transition=self._on_node_transition)
```

---

### P0-4：`get_ready_nodes()` 中依赖节点不存在时抛出 KeyError

**文件**：`dag/graph.py` 第 113-120 行

**问题描述**：
```python
def get_ready_nodes(self) -> list[TaskNode]:
    ...
    deps = self.get_dependency_ids(node.id)
    if all(self.nodes[d].status == NodeStatus.COMPLETED for d in deps):
        #              ↑ 如果 d 不在 self.nodes 中，抛出 KeyError
```

虽然 `_validate_dag()` 在构造时校验了边引用的节点存在性，但如果后续通过 `remove_pending_node()` 移除了节点，而该节点仍被其他边引用（`remove_pending_node` 只移除了以该节点为 source 或 target 的边，但如果边的 source 被移除后，其他节点的 `get_dependency_ids` 仍可能返回已删除的节点 ID），就会导致 `KeyError`。

**修复建议**：
```python
deps = self.get_dependency_ids(node.id)
if all(
    self.nodes.get(d, None) is not None
    and self.nodes[d].status == NodeStatus.COMPLETED
    for d in deps
):
    ready.append(node)
```

---

## 三、P1-Major 问题（7 个）

### P1-1：`get_dependency_ids()` 仍为 O(E) 全量遍历，与邻接表优化矛盾

**文件**：`dag/graph.py` 第 122-127 行

**问题描述**：
已经为正向遍历（source→targets）构建了 `_dep_adjacency` 邻接表，但反向查找（target→sources）的 `get_dependency_ids()` 仍然是 O(E) 遍历所有边。这个方法在 `get_ready_nodes()` 和 `refresh_ready_states()` 中被高频调用，导致这两个方法的总复杂度为 **O(V×E)**，与邻接表优化的初衷矛盾。

同样的问题也存在于 `get_conditional_edges()` 和 `get_rollback_targets()`。

**修复建议**：
构建反向邻接表 `_reverse_dep_adjacency: dict[str, list[str]]`（target → [sources]），在 `_rebuild_adjacency()` 中同步构建。

---

### P1-2：重规划循环中新 DAG 的状态机未更新

**文件**：`agents/orchestrator.py` 第 325-396 行

**问题描述**：
```python
async def _execute_dag_and_reflect(self, dag: TaskDAG) -> str:
    dag_executor = DAGExecutor(...)  # 循环外创建，持有自己的 _sm

    for attempt in range(self.max_replan + 1):
        final_output = await dag_executor.execute(dag)
        ...
        if attempt < self.max_replan and failed_nodes:
            dag = await self.planner.replan_subtree(dag, ...)
            # ↑ 新 DAG 使用默认的无回调 NodeStateMachine
            # dag_executor._sm 仍然是旧的带回调实例
```

`replan_subtree()` 返回新的 `TaskDAG`，其内部 `_sm` 是默认的无回调状态机。而 `dag_executor` 的 `_sm` 是带 UI 回调的。新 DAG 的 `refresh_ready_states()` 等方法使用的是无回调状态机，UI 事件会丢失。

**修复建议**：
在重规划后，将 Executor 的状态机注入新 DAG：
```python
dag = await self.planner.replan_subtree(dag, ...)
dag._sm = dag_executor._sm  # 或统一使用共享状态机
```

---

### P1-3：`from_dict()` 反序列化丢失状态机回调和 checkpoint 历史

**文件**：`dag/graph.py` 第 461-478 行

**问题描述**：
1. `from_dict()` 没有传入 `state_machine` 参数，使用默认的无回调状态机
2. `to_dict()` 没有序列化 `_checkpoints`，从 checkpoint 恢复时丢失历史快照

**修复建议**：
- `from_dict()` 增加 `state_machine` 可选参数
- `to_dict()` 增加 `_checkpoints` 序列化（可选）

---

### P1-4：`_complete_structural_nodes()` 未处理 FAILED 状态的结构节点

**文件**：`dag/executor.py` 第 401-434 行

**问题描述**：
```python
if node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK):
    continue  # 已处于终态，跳过
```

`FAILED` 不在跳过列表中。虽然结构节点通常不会直接进入 `FAILED` 状态（它们不被直接执行），但如果未来代码变更导致结构节点进入 `FAILED`，这里会尝试对其做状态转移，可能抛出 `InvalidTransitionError`。

**修复建议**：
将 `FAILED` 加入终态跳过列表：
```python
if node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK, NodeStatus.FAILED):
    continue
```

---

### P1-5：FAILED 状态不支持重试路径

**文件**：`dag/state_machine.py` 第 49 行

**问题描述**：
```python
NodeStatus.FAILED: {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED},
```

`FAILED` 只能转移到 `ROLLED_BACK` 或 `SKIPPED`，不支持 `FAILED→PENDING`（重试）。在实际业务场景中，很多失败是可重试的（如网络超时、临时资源不足）。当前的 `replan_subtree` 通过创建新 DAG 来"重试"，但这种方式开销较大。

**修复建议**：
如果需要支持节点级重试，添加 `PENDING` 到合法目标：
```python
NodeStatus.FAILED: {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED, NodeStatus.PENDING},
```

---

### P1-6：`_node_to_result()` 包含回滚节点结果，可能干扰反思

**文件**：`agents/orchestrator.py` 第 399-407 行

**问题描述**：
```python
results = [
    r for r in [
        self._node_to_result(nid, dag)
        for nid in dag.state.node_results
    ]
    if r is not None
]
```

`dag.state.node_results` 包含所有写入过结果的节点，包括回滚节点。回滚节点的结果（如"清理临时文件成功"）会被传给 Reflector 进行反思评估，可能干扰对任务完成度的判断。

**修复建议**：
过滤只保留 ACTION 节点的结果：
```python
results = [
    r for r in [
        self._node_to_result(nid, dag)
        for nid in dag.state.node_results
        if dag.nodes.get(nid) and dag.nodes[nid].node_type == NodeType.ACTION
    ]
    if r is not None
]
```

---

### P1-7：`DAGState.merge_result()` 静默覆盖旧结果，无历史追溯

**文件**：`schema.py` 第 207-220 行

**问题描述**：
```python
def merge_result(self, node_id: str, output: str) -> None:
    self.node_results[node_id] = output  # 直接覆盖
```

如果同一个节点被多次执行（如重试、回滚后重新执行），旧结果会被直接覆盖，无法追溯历史。在调试和审计场景下，这会导致关键信息丢失。

**修复建议**：
添加历史记录（可选）：
```python
def merge_result(self, node_id: str, output: str) -> None:
    if node_id in self.node_results:
        logger.debug("[DAGState] Overwriting result for node %s", node_id)
    self.node_results[node_id] = output
```

---

## 四、P2-Minor 问题（6 个）

### P2-1：`remove_pending_node()` 中邻接表清理效率低

**文件**：`dag/graph.py` 第 345-348 行

```python
for source_targets in self._dep_adjacency.values():
    while node_id in source_targets:
        source_targets.remove(node_id)
```

`while + list.remove()` 的最坏时间复杂度为 O(V×E)。应改用列表推导式一次性过滤：
```python
for source, targets in self._dep_adjacency.items():
    self._dep_adjacency[source] = [t for t in targets if t != node_id]
```

---

### P2-2：`topological_sort()` 检测到环时仅 warning，调用方处理不一致

**文件**：`dag/graph.py` 第 207 行

`topological_sort()` 检测到环时只记录 `logger.warning` 并返回不完整结果。调用方的处理方式不一致：
- `add_dynamic_edge()`：正确检查 `len(topo_result) != len(self.nodes)` 并回滚
- `_compile_output()`：直接遍历不完整结果，可能遗漏已完成的节点
- `summary()` 中未调用，无影响

**修复建议**：
`_compile_output()` 中添加降级处理：
```python
topo_order = dag.topological_sort()
if len(topo_order) != len(dag.nodes):
    logger.warning("[DAGExecutor] Topological sort incomplete, falling back to dict order")
    topo_order = list(dag.nodes.keys())
```

---

### P2-3：`orchestrator._emit()` 静默吞掉所有异常

**文件**：`agents/orchestrator.py` 第 419-429 行

```python
except Exception:
    pass  # UI errors should never crash the pipeline
```

与 `state_machine.py` 中已修复的回调异常处理（改为 `logger.debug`）不一致。应保持一致的日志策略。

**修复建议**：
```python
except Exception:
    logger.debug("[Orchestrator] UI callback error for event '%s'", event, exc_info=True)
```

---

### P2-4：`TaskNode.status` 可被直接赋值绕过状态机

**文件**：`schema.py` 第 146 行

`status` 是普通的 Pydantic 字段，任何代码都可以 `node.status = NodeStatus.COMPLETED` 直接赋值。测试代码中大量使用了这种方式（如 `test_dag_capabilities.py` 第 177 行）。虽然这在测试中是合理的简化，但生产代码也可能意外绕过状态机。

**修复建议**：
短期：在代码规范中明确禁止直接赋值，仅通过 `NodeStateMachine.transition()` 修改。
长期：考虑使用 Pydantic 的 `model_validator` 或自定义 `__setattr__` 在非测试环境下拦截直接赋值。

---

### P2-5：`ExitCriteria.required=True` 但 `validation_prompt` 为空时的行为不明确

**文件**：`schema.py` 第 113-121 行 + `dag/executor.py` 第 263-278 行

当 `required=True`（默认值）但 `validation_prompt` 为空时，`_check_exit_criteria()` 直接返回 `result.success`，等价于不做 LLM 验证。这个行为没有在文档中明确说明，可能导致使用者误以为设置了 `required=True` 就一定会进行 LLM 验证。

**修复建议**：
在 `ExitCriteria` 的 docstring 中明确说明此行为。

---

### P2-6：`save_checkpoint()` 中函数内 `import config` 的代码风格

**文件**：`dag/graph.py` 第 418 行

```python
def save_checkpoint(self) -> None:
    import config as _config
```

函数内 import 虽然可以避免循环导入，但不符合 Python 的常规代码风格（PEP 8 建议在文件顶部导入）。当前 `dag/graph.py` 顶部并未导入 `config`，而 `dag/executor.py` 顶部已经正常导入了 `import config`。

**修复建议**：
在 `dag/graph.py` 顶部添加 `import config`，移除函数内的延迟导入。需要先确认不存在循环导入。

---

## 五、P3-Suggestion 建议（4 个）

### P3-1：状态机缺少转移历史审计日志

`NodeStateMachine` 当前只有 `logger.debug` 级别的日志和可选回调，没有结构化的转移历史记录。建议添加 `_audit_log: list[dict]` 用于调试和故障排查。

### P3-2：`transition()` 方法的 TOCTOU 问题

`can_transition()` 检查和 `node.status = new_status` 赋值之间不是原子操作。在当前 asyncio 单线程事件循环中，由于 Python GIL 和 asyncio 的协作式调度，同步代码块不会被中断，因此**当前不存在实际的竞态风险**。但如果未来引入多线程执行，需要添加锁保护。

### P3-3：为 CONDITIONAL 和 ROLLBACK 边也构建邻接表

当前只为 DEPENDENCY 边构建了邻接表。如果 DAG 规模增大，`get_conditional_edges()` 和 `get_rollback_targets()` 的 O(E) 遍历也会成为瓶颈。建议按 EdgeType 分别构建邻接表。

### P3-4：`_compile_output()` 使用 `dag.nodes[node_id]` 而非 `dag.nodes.get(node_id)`

如果 `topological_sort()` 返回了不在 `nodes` 中的 ID（理论上不可能，但防御性编程），会抛出 `KeyError`。建议改用 `.get()` 并跳过 `None`。

---

## 六、与一次评审的对比

| 维度 | 一次评审 | 二次评审新发现 |
|------|---------|-------------|
| 状态机绕过 | ✅ 已修复 | 发现 DAG 和 Executor 使用不同状态机实例（P0-3） |
| 性能优化 | ✅ 正向邻接表已构建 | 发现反向查找仍为 O(E)（P1-1） |
| 超时控制 | ✅ 已添加 | 发现超时后 StepResult 缺少 step_id（P0-2） |
| 错误处理 | ✅ 回滚失败已处理 | 发现 exit_criteria 异常未捕获（P0-1） |
| 死锁检测 | ✅ 已增强 | 发现 get_ready_nodes 可能 KeyError（P0-4） |
| 数据一致性 | 未涉及 | 发现 from_dict 丢失状态机和 checkpoint（P1-3） |
| 跨模块集成 | 未涉及 | 发现重规划循环状态机不同步（P1-2） |
| 并发安全 | 未涉及 | 评估为当前 asyncio 模型下风险较低（P3-2） |

---

## 七、修复优先级建议

### 第一优先级（P0，建议立即修复）
1. **P0-1**：`_check_exit_criteria()` 异常捕获 — 防止节点状态卡死
2. **P0-2**：超时 StepResult 补充 `step_id` — 防止结果追踪丢失
3. **P0-3**：统一 DAG 和 Executor 的状态机实例 — 消除双状态机不一致
4. **P0-4**：`get_ready_nodes()` 防御性检查 — 防止 KeyError 崩溃

### 第二优先级（P1，建议近期修复）
5. **P1-1**：构建反向邻接表 — 消除 O(V×E) 性能瓶颈
6. **P1-2**：重规划后同步状态机 — 确保 UI 事件不丢失
7. **P1-3**：`from_dict()` 支持状态机注入 — 确保反序列化完整性
8. **P1-4**：结构节点终态列表补充 FAILED — 防御性编程
9. **P1-5**：评估是否需要 FAILED→PENDING 重试路径
10. **P1-6**：过滤回滚节点结果 — 提高反思准确性
11. **P1-7**：`merge_result()` 覆盖时记录日志

### 第三优先级（P2/P3，可纳入后续迭代）
12-21. 见上文 P2 和 P3 详细描述
