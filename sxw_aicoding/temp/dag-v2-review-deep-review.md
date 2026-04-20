# 深度代码评审：DAG 模块二次评审修复

> 评审时间：2026-04-20
> 评审依据：`.aone_copilot/plans/fix-dag-v2-review-issues/implementation_plan.md` + 源码 diff
> 涉及文件：`dag/executor.py`, `dag/graph.py`, `dag/state_machine.py`, `agents/orchestrator.py`, `schema.py`

---

## 一、修复项逐条核验

### P0-Critical（4项）—— 全部已正确实现

#### P0-1：`_check_exit_criteria()` 异常捕获 ✅

- **位置**：`executor.py:181-185`
- **修复**：try-except 包裹 `await self._check_exit_criteria()`，异常时 `passed=False`，防止节点卡在 RUNNING。
- **评价**：逻辑正确。当 `passed=False` 后节点被标记为 FAILED，然后 `_handle_failure` 执行回滚+跳过子树，行为链条完整。

#### P0-2：超时 StepResult 补充 `step_id` ✅

- **位置**：`executor.py:263`
- **修复**：`StepResult(step_id=node.id, success=False, output=f"Node execution timed out after {timeout}s")`
- **评价**：与 `StepResult` 的类型定义 `step_id: int | str` 完全匹配。

#### P0-3：统一状态机实例 ✅（有遗留风险）

- **位置**：`executor.py:121` `dag._sm = self._sm`；`graph.py:68` 构造函数接受 `state_machine` 参数。
- **遗留风险**：`is_complete()` (`graph.py:257`) 的终态集合 `{COMPLETED, SKIPPED, ROLLED_BACK}` 不含 FAILED。P1-4 在 `_complete_structural_nodes()` 中将 FAILED 加入终态跳过列表，但 `is_complete()` 仍不认可 FAILED 为终态。当前设计是安全的（FAILED 会被 `_handle_failure` 转为 ROLLED_BACK/SKIPPED），但若未来启用节点级重试（FAILED→PENDING），需同步调整 `is_complete()` 的退出条件。

#### P0-4：`get_ready_nodes()` 防御性检查 ✅

- **位置**：`graph.py:121-124`
- **修复**：`d in self.nodes and self.nodes[d].status == NodeStatus.COMPLETED`
- **评价**：正常流程下 `d in self.nodes` 不会为 False（反向邻接表的 key 来自 `self.nodes`），但动态添加边时 `add_dynamic_edge()` 的 `setdefault` 可能引入异常 key。防御性编程合理。

---

### P1-Major（7项）—— 6项正确，1项有语义隐患

#### P1-1：反向邻接表 + `get_dependency_ids()` O(1) ✅（有潜在 bug）

- **位置**：`graph.py:82-94` `_rebuild_adjacency()`，`graph.py:134` `get_dependency_ids()`，`graph.py:287, 317, 358-360` 维护逻辑。
- **评价**：反向邻接表构建正确，`get_dependency_ids()` 使用 O(1) 查询。动态方法中正向和反向邻接表同步维护。
- **潜在 bug**：`add_dynamic_edge()` 的环检测回滚（`graph.py:323-324`）使用 `list.remove()`，只移除第一个匹配项。建议改用列表推导式，与 `remove_pending_node()` 的风格一致。

#### P1-2：重规划后同步状态机 ✅

- **位置**：`orchestrator.py:384` `dag._sm = dag_executor._sm`

#### P1-3：`from_dict()` 支持状态机注入 ✅

- **位置**：`graph.py:459-474`，`state_machine: NodeStateMachine | None = None` 参数传递到构造函数。

#### P1-4：`_complete_structural_nodes()` 终态列表补充 FAILED ✅（有语义问题）

- **位置**：`executor.py:412`，加入 `NodeStatus.FAILED`
- **语义问题**：FAILED 被加入跳过列表后，`if node.status in (..., FAILED): continue` 确保已 FAILED 的结构节点不再被处理。但核心问题在于 **line 423 的 `terminal` 集合 `{COMPLETED, SKIPPED, ROLLED_BACK}` 不含 FAILED**。如果一个 ACTION 子节点处于 FAILED 且未被转为 ROLLED_BACK/SKIPPED，父 GOAL/SUBGOAL 的 `all(c.status in terminal)` 检查永远为 False，父节点无法自动完成。
- **根因分析**：正常流程中 `_handle_failure` 会将 FAILED→ROLLED_BACK/SKIPPED，所以 FAILED 不会停留。但防御性编程应将 FAILED 加入 terminal 集合（详见"新发现问题 A"）。

#### P1-5：FAILED→PENDING 重试路径 ✅（路径未被激活）

- **位置**：`state_machine.py:45` `NodeStatus.FAILED: {ROLLED_BACK, SKIPPED, PENDING}`
- **关键发现**：状态机声明了 FAILED→PENDING 的合法性，但没有任何代码实际执行这个转移。当前重试通过 `replan_subtree` 创建新 DAG 实现，而非节点级重试。此路径为未来预留。

#### P1-6：过滤回滚节点结果 ✅

- **位置**：`orchestrator.py:348` `if dag.nodes.get(nid) and dag.nodes[nid].node_type == NodeType.ACTION`

#### P1-7：`merge_result()` 覆盖时记录日志 ✅

- **位置**：`schema.py:242-244` `logger.debug` + `len()` 记录

---

### P2-Minor（5项）—— 全部正确

#### P2-1：`remove_pending_node()` 邻接表清理 ✅

- 列表推导式，与反向邻接表同步清理。

#### P2-2+P3-4：`_compile_output()` 拓扑排序降级 + 防御性 get ✅

- `executor.py:457-463`，拓扑排序不完整时降级为 dict 顺序，`dag.nodes.get(node_id)` 防御性访问。

#### P2-3：`_emit()` 异常改为 `logger.debug` ✅

- `orchestrator.py:431` `logger.debug("[Orchestrator] UI callback error for event '%s'", event, exc_info=True)`

#### P2-5：`ExitCriteria` docstring 明确行为 ✅

- `schema.py:119-127` 三种行为模式说明清晰。

#### P2-6：顶部导入 config ✅

- `graph.py:36` `import config`，移除 `save_checkpoint()` 中的延迟导入。

---

## 二、新发现的问题（非评审计划覆盖）

### 问题 A（Critical）：`_complete_structural_nodes` 的 `terminal` 集合不含 FAILED

- **文件**：`executor.py:423`
- **现状**：`terminal = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}`
- **影响**：如果一个 ACTION 子节点处于 FAILED 状态（且未被 `_handle_failure` 转为 ROLLED_BACK/SKIPPED），其父 GOAL/SUBGOAL 的 `all(c.status in terminal)` 永远为 False，父节点无法自动完成。
- **根因**：`_handle_failure` 会将 FAILED 节点转为 ROLLED_BACK 或 SKIPPED，所以理论上不应有"永久 FAILED"的节点。但防御性编程应考虑异常场景（如状态机转移失败）。
- **建议修复**：
```diff
-    terminal = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK}
+    terminal = {NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK, NodeStatus.FAILED}
```

### 问题 B（Medium）：`mark_subtree_skipped` 和 `refresh_ready_states` 中 `self.nodes[d]` 无防御性检查

- **文件**：`graph.py:194, 209`
- **现状**：`mark_subtree_skipped` line 192: `node = self.nodes[nid]`；`refresh_ready_states` line 209: `self.nodes[d].status`。动态删除节点后邻接表可能残留无效 ID。
- **建议**：与 `get_ready_nodes` 保持一致的防御性风格：
```python
# mark_subtree_skipped
node = self.nodes.get(nid)
if node is None or node.status not in (NodeStatus.PENDING, NodeStatus.READY):
    continue

# refresh_ready_states
if all(d in self.nodes and self.nodes[d].status == NodeStatus.COMPLETED for d in deps):
```

### 问题 C（Low）：`add_dynamic_edge` 环检测回滚使用 `list.remove()`

- **文件**：`graph.py:323-324`
- **现状**：`self._dep_adjacency[edge.source].remove(edge.target)` 只移除第一个匹配项。
- **建议**：改用列表推导式，与 `remove_pending_node()` 风格统一：
```python
self._dep_adjacency[edge.source] = [t for t in self._dep_adjacency[edge.source] if t != edge.target]
self._reverse_dep_adjacency[edge.target] = [s for s in self._reverse_dep_adjacency[edge.target] if s != edge.source]
```

### 问题 D（Low）：`is_complete()` 的语义与 P1-5 潜在冲突

- **文件**：`graph.py:257`
- **现状**：`is_complete()` 不认可 FAILED 为终态。若未来启用 FAILED→PENDING 节点级重试，FAILED 可能暂时存在但不应阻塞循环退出判断。
- **建议**：启用节点级重试时，重新设计 `is_complete()` 的退出条件（如：所有节点要么在终态，要么在 FAILED 且有待重试计划）。

### 问题 E（Low）：`_complete_structural_nodes` 中 RUNNING→SKIPPED 转移路径缺失

- **文件**：`executor.py:435-439` + `state_machine.py:44`
- **现状**：修复后拆分为两个 `self._sm.transition()` 调用（PENDING→SKIPPED、READY→SKIPPED）。但状态机的 `VALID_TRANSITIONS` 中 `RUNNING` 状态的合法目标只有 `{COMPLETED, FAILED}`，不含 `SKIPPED`。如果结构节点意外处于 RUNNING 状态，`_complete_structural_nodes` 中的 SKIPPED 转移会抛出 `InvalidTransitionError`。
- **建议**：在状态机中为结构节点场景添加 `RUNNING→SKIPPED` 转移路径，或在 `_complete_structural_nodes` 中增加 RUNNING 状态的处理逻辑：
```python
# state_machine.py VALID_TRANSITIONS
NodeStatus.RUNNING: {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED},
# 或在 _complete_structural_nodes 中：
elif node.status == NodeStatus.RUNNING:
    self._sm.transition(node, NodeStatus.FAILED)
    self._sm.transition(node, NodeStatus.SKIPPED)
```

---

## 三、修复验证统计

| 类别 | 已修复 | 新发现 |
|------|--------|--------|
| P0-Critical | 4/4 ✅ | 1 (问题 A: terminal 集合不含 FAILED) |
| P1-Major | 7/7 ✅ (P1-4 有语义隐患) | 1 (问题 E: RUNNING→SKIPPED 转移缺失) |
| P2-Minor | 5/5 ✅ | 3 (问题 B/C/D) |

---

## 四、最高优先级修复建议

1. **问题 A（Critical）**：`executor.py:423` 的 `terminal` 集合应加入 `NodeStatus.FAILED`，否则 FAILED 子节点会阻塞父结构节点完成。
2. **问题 E（Low→Medium）**：状态机 `VALID_TRANSITIONS` 的 `RUNNING` 状态缺少 `SKIPPED` 转移路径，结构节点在 RUNNING→SKIPPED 时会触发 `InvalidTransitionError`。
3. **问题 B（Medium）**：`mark_subtree_skipped` 和 `refresh_ready_states` 中对 `self.nodes[d]` 的访问应增加防御性检查。
4. **问题 C/D（Low）**：环检测回滚风格统一 + `is_complete()` 未来兼容性规划。