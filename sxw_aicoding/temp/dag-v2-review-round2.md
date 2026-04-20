# 二次深度代码评审：DAG 模块修复后补充审查

> 评审时间：2026-04-20
> 评审范围：首次评审（`dag-v2-review-deep-review.md`）中发现的问题 + 新增并发安全、序列化一致性、状态泄漏等维度
> 涉及文件：`dag/executor.py`, `dag/graph.py`, `dag/state_machine.py`, `agents/orchestrator.py`, `agents/planner.py`, `schema.py`

---

## 一、首次评审问题复验与补充分析

### 1.1 问题 A 补充（Critical）：`_complete_structural_nodes` terminal 集合不含 FAILED

首次评审已发现此问题。二次评审补充一个**更深层的影响路径**：

- **执行时序分析**：`_handle_failure` 在处理 FAILED 节点时，会先完成 FAILED→ROLLED_BACK/SKIPPED 转移，再调用 `dag.mark_subtree_skipped(node.id)`。但 `mark_subtree_skipped` 只处理 PENDING/READY 的下游节点，不会处理已经 COMPLETED 的下游节点。
- **场景**：如果 FAILED 节点的某个下游 ACTION 节点刚好在同一个 super-step 中已经执行成功并 COMPLETED，但 exit criteria 验证失败导致它也变为 FAILED，此时 **两个 FAILED 节点在同一批次中出现**。`_handle_failure` 会串行处理它们，先处理第一个 FAILED→SKIPPED，再处理第二个 FAILED→SKIPPED。但如果第二个 FAILED 节点也是第一个 FAILED 节点的下游，那么第一个节点的 `mark_subtree_skipped` 会尝试将第二个已经 FAILED 的节点 SKIPPED——但 `mark_subtree_skipped` 只处理 PENDING/READY，所以 FAILED 的下游节点不会被 SKIPPED。
- **结论**：此问题在特定并发失败场景下可能产生**孤儿 FAILED 节点**。应将 FAILED 加入 `terminal` 集合并在 `mark_subtree_skipped` 中也处理 FAILED 状态的下游节点。

### 1.2 问题 E 补充（Medium→Critical 升级）：RUNNING→SKIPPED 状态机转移路径缺失

首次评审将其定为 Low。二次评审发现 **此问题有实际触发路径**：

- **触发路径**：`_process_conditions` (`executor.py:372`) 中，当一个 COMPLETED 节点的条件不满足时，对目标节点执行 `self._sm.transition(target, NodeStatus.SKIPPED)`。但 `_process_conditions` 只检查 `target.status == NodeStatus.PENDING`（line 362），所以 RUNNING 状态的目标不会被条件边跳过——这是正确的。
- **但 `_complete_structural_nodes` 有真实触发路径**：当一个 GOAL/SUBGOAL 处于 RUNNING 状态（被 `execute()` 的 line 147-148 快速路径 PENDING→READY→RUNNING→COMPLETED 连续转移触发），如果其子节点全部被 SKIPPED/ROLLED_BACK（无 COMPLETED），line 436-439 会尝试 `self._sm.transition(node, NodeStatus.SKIPPED)`——此时如果 node.status 是 RUNNING（因为 line 147 已经把它转为 RUNNING），**RUNNING→SKIPPED 是非法转移，会抛出 InvalidTransitionError**。
- **触发条件**：所有子节点都被跳过/回滚（包括条件边导致的跳过），且结构节点在 `execute()` 的快速路径中已被转为 RUNNING。
- **升级理由**：这是有明确触发路径的运行时异常，不应视为 Low。

---

## 二、新发现的问题

### 问题 F（Critical）：`_run_node_with_timeout` 超时后节点状态残留 RUNNING

- **文件**：`executor.py:250-263`
- **现状**：`_run_node_with_timeout` 在超时时直接返回 `StepResult(success=False)`，但 **此时节点状态已被 `_run_node` 转为 RUNNING**（line 244-245）。返回后，主循环的 line 196 会执行 `self._sm.transition(node, NodeStatus.FAILED)`，然后 `_handle_failure` 会处理 FAILED→SKIPPED/ROLLED_BACK。
- **问题**：这是**正确的**——超时节点会被正常处理为 FAILED。但有一个微妙问题：`asyncio.wait_for` 取消协程时，`_run_node` 中的 `_executor_agent.execute_node` 可能已经部分修改了 DAG 状态（如已写入部分结果到 `dag.state.merge_result`）。**超时取消不会回滚已写入的 `node.result` 和 `dag.state.node_results`**。
- **影响**：超时后 line 176 `dag.state.merge_result(node.id, result.output)` 会被空字符串覆盖（因为超时 StepResult 的 output 是超时消息），所以实际上 `node.result` 和 `dag.state.node_results[node.id]` 都被正确重写为超时消息。**这是安全的**，但 `node.result` 是在超时 StepResult 创建之前由 `_run_node` 内部设置的（如果 `execute_node` 已经写入了部分结果），而超时后 line 177 `node.result = result.output` 会覆盖为超时消息。
- **结论**：当前逻辑是安全的，因为超时 StepResult 的 output 会覆盖任何部分结果。但建议在超时日志中注明结果被覆盖。

### 问题 G（High）：并发执行期间 `_run_node` 修改共享 DAG 状态

- **文件**：`executor.py:166-168` + `executor.py:243-248`
- **现状**：`asyncio.gather` 并行执行多个 `_run_node_with_timeout`，每个 `_run_node` 在开始时修改节点状态（PENDING→READY→RUNNING，line 243-245）并通过 `_emit` 发送 UI 事件。这些修改发生在并行协程中。
- **Python asyncio 单线程模型**：由于 Python asyncio 是协作式并发（单线程），`_run_node` 的前半部分（状态转移 + emit）在 `await self._executor_agent.execute_node` 之前是同步执行的。`asyncio.gather` 会先启动所有协程，每个协程在第一个 `await` 点暂停。这意味着：
  - 所有 batch 节点的 PENDING→READY→RUNNING 转移**会在各自的第一个 await 前依次执行**（因为 asyncio 单线程，协程切换只在 await 时发生）。
  - 状态转移的顺序取决于 `asyncio.gather` 的内部调度，但不会产生真正的并发写入冲突。
- **结论**：Python asyncio 的单线程特性保证了状态修改的安全性。但 `_emit` 事件的发送顺序可能不符合用户预期（节点 A 的 RUNNING 事件可能在节点 B 的 READY 事件之后）。这是可接受的，但值得在文档中说明。

### 问题 H（High）：`_handle_failure` 中 `dag.nodes[rb_id]` 直接索引而非 `.get()`

- **文件**：`executor.py:322-325`
- **现状**：
```python
all_rollbacks_succeeded = all(
    dag.nodes[rb_id].status == NodeStatus.COMPLETED
    for rb_id in rollback_targets
    if rb_id in dag.nodes
)
```
- **问题**：`if rb_id in dag.nodes` 的守卫条件与 `dag.nodes[rb_id]` 的访问在同一行中，逻辑上不会触发 KeyError。但 line 309-310 中 `rb_node = dag.nodes.get(rb_id)` 只处理 `rb_node and rb_node.status == NodeStatus.PENDING` 的回滚节点——如果回滚节点不是 PENDING 状态（如已经 RUNNING/COMPLETED），它不会被执行，也不会被转移。但 `all_rollbacks_succeeded` 会检查所有 `rollback_targets` 中仍在 `dag.nodes` 中的节点是否 COMPLETED——**一个跳过执行的回滚节点（非 PENDING）可能不是 COMPLETED，导致 `all_rollbacks_succeeded` 为 False**。
- **影响**：如果一个回滚节点已经被其他逻辑处理为 SKIPPED/COMPLETED，`all_rollbacks_succeeded` 的判断可能不准确。建议仅检查被执行的回滚节点（而非所有 rollback_targets）。
- **建议修复**：
```python
executed_rollbacks = [rb_id for rb_id in rollback_targets
                      if dag.nodes.get(rb_id) and dag.nodes[rb_id].status != NodeStatus.PENDING]
# 或更精确：只检查确实被执行过的回滚节点
```

### 问题 I（Medium）：`replan_subtree` 返回的新 DAG 不继承 `_checkpoints`

- **文件**：`planner.py:865-870`
- **现状**：`_merge_dags` 通过 `TaskDAG(task=..., nodes=..., edges=..., context=...)` 创建新 DAG。构造函数中 `self._checkpoints: list[dict[str, Any]] = []` 会初始化为空列表。
- **问题**：原 DAG 的 checkpoints（包含历史状态快照）在重新规划后丢失。如果用户需要时间旅行调试（回溯到重新规划前的状态），这是不可能的。
- **建议**：在 `_merge_dags` 中将 `old_dag._checkpoints` 复制到 `result_dag._checkpoints`，或至少保留最近一个 checkpoint：
```python
result_dag._checkpoints = list(old_dag._checkpoints)
```

### 问题 J（Medium）：序列化往返丢失 `_sm` 和 `_checkpoints`

- **文件**：`graph.py:456-467` (to_dict) + `graph.py:470-484` (from_dict)
- **现状**：`to_dict()` 不序列化 `_sm`（状态机）和 `_checkpoints`（检查点列表）。`from_dict()` 可通过 `state_machine` 参数注入状态机，但调用方（如 checkpoint 恢复）需要显式传入。
- **问题**：
  1. `save_checkpoint()` 调用 `to_dict()` 生成快照，然后 `from_dict()` 恢复时，**如果不传入原状态机，恢复的 DAG 会使用默认的无回调状态机**——所有 UI 事件回调丢失。
  2. `_checkpoints` 本身也不在 `to_dict` 的输出中，意味着 checkpoint 恢复后的 DAG 的 checkpoint 列表为空——**嵌套 checkpoint 信息丢失**。
- **影响**：如果系统依赖 checkpoint 恢复来实现故障恢复，恢复后的 DAG 缺少 UI 回调和历史快照。
- **建议**：在 `from_dict` 的调用方（如 `_merge_dags` 或 checkpoint 恢复逻辑）中显式传入原状态机；或在 `to_dict` 中记录状态机的回调信息（但回调函数不可序列化，这是根本限制）。

### 问题 K（Medium）：`_validate_dag` 从 warning 升级为 ValueError 的向后兼容性风险

- **文件**：`graph.py:486-493`
- **现状**：原版 `_validate_dag` 对非法边端点使用 `logger.warning`；修复后改为 `raise ValueError`。
- **影响**：这是一个**行为变更**——如果外部代码（如测试、LLM 生成的 DAG）创建了引用不存在节点的边，原版会容忍（仅打日志），新版会直接抛异常。
- **场景**：LLM 生成 DAG 时可能产生不完美的边（如引用了被移除的节点 ID）。原版会在运行时通过 `get_ready_nodes` 的防御性检查隐式跳过这些边；新版会在 DAG 构造阶段直接拒绝。
- **建议**：这个变变更严格，是正确的方向。但需确认所有 DAG 创建入口（如 `_parse_dag`、`_merge_dags`）不会产生引用不存在节点的边。如果 LLM 输出的 JSON 可能包含无效边，应在 `_parse_dag` 中先清理再构造。

### 问题 L（Low）：`_complete_structural_nodes` 中 `elif node.status == NodeStatus.RUNNING` 的 else 分支缺失

- **文件**：`executor.py:436-439`
- **现状**：
```python
if node.status == NodeStatus.PENDING:
    self._sm.transition(node, NodeStatus.SKIPPED)
elif node.status == NodeStatus.READY:
    self._sm.transition(node, NodeStatus.SKIPPED)
elif node.status == NodeStatus.RUNNING:
    # 此处无处理逻辑 — RUNNING→SKIPPED 是非法转移
```
- **问题**：如果结构节点意外处于 RUNNING 状态（如上文问题 E 的触发路径），此 elif 分支**没有任何处理**，节点会被跳过（因为 `continue` 在 line 412 的终态检查中不会触发——RUNNING 不是终态），但也不会被自动完成。**RUNNING 状态的结构节点会永远卡在 RUNNING**。
- **这与问题 E 是同一问题的不同视角**：RUNNING→SKIPPED 转移缺失导致结构节点无法被跳过，也无法被完成。

### 问题 M（Low）：`_merge_dags` 不继承原 DAG 的 `_dep_adjacency` 和 `_reverse_dep_adjacency`

- **文件**：`planner.py:865-870`
- **现状**：`_merge_dags` 创建新 `TaskDAG` 对象，构造函数中会调用 `_rebuild_adjacency()` 重建邻接表。
- **分析**：这是正确的——因为 `TaskDAG.__init__` 会调用 `_rebuild_adjacency()` 从 edges 列表重建邻接表。新 DAG 的边列表已正确合并（含去重），邻接表会被正确重建。
- **结论**：无需修复。

---

## 三、状态机完整性交叉分析

### 3.1 状态转移图 vs 代码实际路径对比

| 转移路径 | 状态机声明 | 代码实际触发点 | 是否被覆盖 |
|----------|-----------|--------------|-----------|
| PENDING→READY | ✅ | `refresh_ready_states`, `_run_node`, `_complete_structural_nodes` | ✅ 完整 |
| PENDING→SKIPPED | ✅ | `mark_subtree_skipped`, `_complete_structural_nodes`, `_process_conditions` | ✅ 完整 |
| READY→RUNNING | ✅ | `_run_node`, `_complete_structural_nodes` | ✅ 完整 |
| READY→SKIPPED | ✅ | `mark_subtree_skipped`, `_complete_structural_nodes` | ✅ 完整 |
| RUNNING→COMPLETED | ✅ | `execute()` line 148, `_complete_structural_nodes` | ✅ 完整 |
| RUNNING→FAILED | ✅ | `execute()` 主循环 | ✅ 完整 |
| RUNNING→SKIPPED | ❌ 非法 | `_complete_structural_nodes` line 439 尝试触发 | ❌ 缺失 |
| FAILED→ROLLED_BACK | ✅ | `_handle_failure` | ✅ 完整 |
| FAILED→SKIPPED | ✅ | `_handle_failure` | ✅ 完整 |
| FAILED→PENDING | ✅ | 无代码触发 | ⚠️ 未激活 |
| FAILED→FAILED（回滚节点） | ✅ (FAILED→SKIPPED) | `_handle_failure` line 317-318 | ✅ 间接覆盖 |

**关键缺口**：`RUNNING→SKIPPED` 是唯一被代码尝试触发但状态机拒绝的路径。

### 3.2 终态闭合性验证

| 状态 | `is_complete()` 认为终态 | `_complete_structural_nodes` 认为终态 | 实际可达性 |
|------|--------------------------|---------------------------------------|-----------|
| COMPLETED | ✅ | ✅ (跳过) | ✅ 正常路径可达 |
| SKIPPED | ✅ | ✅ (跳过) | ✅ 多路径可达 |
| ROLLED_BACK | ✅ | ✅ (跳过) | ✅ 回滚成功可达 |
| FAILED | ❌ | ✅ (跳过自身) 但 ❌ (不在子节点 terminal 集合) | ⚠️ 理论上不应停留，但防御性不足 |

---

## 四、修复优先级矩阵

| 问题 | 严重度 | 触发概率 | 影响范围 | 建议优先级 |
|------|--------|---------|---------|-----------|
| A: terminal 不含 FAILED | Critical | 低（需异常路径） | 结构节点卡住 | P1 — 必须修复 |
| E: RUNNING→SKIPPED 缺失 | Critical | 中（条件边跳过触发） | InvalidTransitionError 崩溃 | P0 — 必须立即修复 |
| F: 超时结果覆盖 | Info | 中 | 结果被超时消息覆盖 | P3 — 仅需日志说明 |
| G: 并发状态修改顺序 | Info | 始终 | UI 事件顺序可能非预期 | P3 — 文档说明即可 |
| H: 回滚成功判断不精确 | High | 低（需非 PENDING 回滚节点） | 原节点被错误标记为 SKIPPED | P2 — 应修复 |
| I: replan 不继承 checkpoints | Medium | 中 | 时间旅行调试丢失 | P2 — 应修复 |
| J: 序列化丢失 _sm/_checkpoints | Medium | 低（仅 checkpoint 恢复场景） | UI 回调丢失 | P2 — 应修复 |
| K: _validate_dag 行为变更 | Medium | 低（LLM 输出可能无效） | DAG 创建失败 | P2 — 应确认入口 |
| L: RUNNING 结构节点无 else | Low | 同问题 E | 同问题 E | 已被问题 E 覆盖 |
| M: 邻接表重建 | 无问题 | — | — | 无需修复 |

---

## 五、最高优先级修复建议（二次评审新增）

### 5.1 P0：添加 RUNNING→SKIPPED 状态机转移路径

**文件**：`state_machine.py:44`

```diff
-    NodeStatus.RUNNING:     {NodeStatus.COMPLETED, NodeStatus.FAILED},
+    NodeStatus.RUNNING:     {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED},
```

**同步更新注释**：
```diff
     PENDING ──> READY ──> RUNNING ──> COMPLETED   (happy path / 正常路径)
-                                  ──> FAILED ──> ROLLED_BACK
+                                  ──> FAILED ──> ROLLED_BACK
+                                  ──> SKIPPED  (structural node: all children skipped / 结构节点：子节点全被跳过)
```

**理由**：结构节点（GOAL/SUBGOAL）在所有子节点被跳过时需要 RUNNING→SKIPPED 路径。这是合法的业务语义——结构节点本身不执行动作，其"运行"只是一个状态占位符。

### 5.2 P1：`_complete_structural_nodes` 的 terminal 集合加入 FAILED

**文件**：`executor.py:423`

```diff
-    terminal = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}
+    terminal = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK, NodeStatus.FAILED}
```

**理由**：防御性编程。即使 `_handle_failure` 通常会将 FAILED→ROLLED_BACK/SKIPPED，异常场景下 FAILED 可能停留。

### 5.3 P2：`_handle_failure` 中回滚成功判断应排除未执行的回滚节点

**文件**：`executor.py:322-325`

```diff
-    all_rollbacks_succeeded = all(
-        dag.nodes[rb_id].status == NodeStatus.COMPLETED
-        for rb_id in rollback_targets
-        if rb_id in dag.nodes
-    )
+    all_rollbacks_succeeded = all(
+        dag.nodes[rb_id].status == NodeStatus.COMPLETED
+        for rb_id in rollback_targets
+        if rb_id in dag.nodes and dag.nodes[rb_id].status != NodeStatus.PENDING
+    )
```

**注意**：此修复需谨慎——只有被实际执行的回滚节点（状态不是 PENDING）才应被纳入判断。未被执行的 PENDING 回滚节点应该被忽略。

---

## 六、评审总结

首次评审发现 5 个新问题（A-E），二次评审在此基础上：
- 升级问题 E 的严重度（Low → Critical）
- 发现 7 个新问题（F-M），其中 1 个 Critical（E 的补充验证）、1 个 High（H）、3 个 Medium（I/J/K）
- 完成状态机完整性交叉分析，确认 `RUNNING→SKIPPED` 是唯一被代码尝试触发但状态机拒绝的路径

**总需修复项**：2 个 P0 + 1 个 P1 + 3 个 P2 = 6 项实质性修复