# 修复 DAG 模块二次深度评审问题

根据 `dag-v2-review-round2.md` 中发现的问题，经核验后采纳 3 个实质性修复 + 1 个优化。

## User Review Required

> [!IMPORTANT]
> **问题 E（P0）**：在状态机中添加 `RUNNING→SKIPPED` 转移路径。这是一个**状态机语义变更**——原设计中 RUNNING 只能转向 COMPLETED 或 FAILED。新增 SKIPPED 路径仅用于结构节点（GOAL/SUBGOAL）在所有子节点被跳过时的场景。ACTION 节点不应使用此路径（ACTION 节点一旦 RUNNING 必须走向 COMPLETED 或 FAILED）。状态机本身无法区分节点类型，此约束由调用方保证。

> [!WARNING]
> **问题 H（P2）**：修改回滚成功判断逻辑，只检查实际被执行过的回滚节点。如果一个回滚节点已经是 COMPLETED/SKIPPED（被其他逻辑处理过），不应影响 `all_rollbacks_succeeded` 的判断。

## Proposed Changes

### Component 1: dag/state_machine.py — 添加 RUNNING→SKIPPED 转移路径（问题 E）

#### [MODIFY] [state_machine.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/state_machine.py)

**P0：RUNNING 状态添加 SKIPPED 转移路径**（第 44 行）

```diff
-    NodeStatus.RUNNING:     {NodeStatus.COMPLETED, NodeStatus.FAILED},
+    NodeStatus.RUNNING:     {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED},
```

**同步更新转移图注释**（第 13-16 行）

```diff
     PENDING ──> READY ──> RUNNING ──> COMPLETED   (happy path / 正常路径)
                                   ──> FAILED ──> ROLLED_BACK
                                             ──> PENDING (retry / 重试)
+                                  ──> SKIPPED  (structural node: all children skipped / 结构节点：子节点全被跳过)
     Any non-terminal ──────────────> SKIPPED       (conditional branch not taken / 条件分支未满足)
```

---

### Component 2: dag/executor.py — 简化结构节点跳过逻辑 + 回滚判断修复（问题 E 优化 + 问题 H）

#### [MODIFY] [executor.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/executor.py)

**问题 E 优化：简化 `_complete_structural_nodes` 的 RUNNING→SKIPPED 绕行逻辑**（第 437-443 行）

有了状态机的 RUNNING→SKIPPED 路径后，不再需要先 FAILED 再 SKIPPED 的绕行：

```diff
                 else:
                     # 所有子节点均被跳过/回滚/失败：通过状态机将结构节点也跳过
                     if node.status == NodeStatus.PENDING:
                         self._sm.transition(node, NodeStatus.SKIPPED)
                     elif node.status == NodeStatus.READY:
                         self._sm.transition(node, NodeStatus.SKIPPED)
                     elif node.status == NodeStatus.RUNNING:
-                        # 结构节点意外处于 RUNNING 状态时，先标记 FAILED 再 SKIPPED
-                        self._sm.transition(node, NodeStatus.FAILED)
-                        self._sm.transition(node, NodeStatus.SKIPPED)
+                        # 结构节点处于 RUNNING 状态时，直接标记为 SKIPPED
+                        # （结构节点的 RUNNING 只是状态占位符，不代表真正的执行）
+                        self._sm.transition(node, NodeStatus.SKIPPED)
```

**问题 H：`_handle_failure` 中回滚成功判断排除未执行的回滚节点**（第 322-326 行）

```diff
             all_rollbacks_succeeded = all(
                 dag.nodes[rb_id].status == NodeStatus.COMPLETED
                 for rb_id in rollback_targets
-                if rb_id in dag.nodes
+                if rb_id in dag.nodes and dag.nodes[rb_id].status != NodeStatus.PENDING
             )
```

逻辑说明：只检查实际被执行过的回滚节点（状态不是 PENDING 的）。未被执行的 PENDING 回滚节点被排除在判断之外，不会影响 `all_rollbacks_succeeded` 的结果。

---

### Component 3: agents/planner.py — replan 继承 checkpoints（问题 I）

#### [MODIFY] [planner.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/agents/planner.py)

**问题 I：`_merge_dags` 中继承旧 DAG 的 checkpoints**（第 878 行后）

```diff
         result_dag.state.node_results = {
             k: v for k, v in old_dag.state.node_results.items()
             if k in valid_node_ids
         }

+        # 继承旧 DAG 的 checkpoints，保留时间旅行调试能力
+        result_dag._checkpoints = list(old_dag._checkpoints)
+
         return result_dag
```

---

## Verification Plan

### Automated Tests

```bash
cd /Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo
.venv/bin/python -m pytest tests/test_dag_capabilities.py -v
```

### Manual Verification

1. 使用 `read_lints` 检查所有修改文件无新增编译错误
2. 核验状态机转移表与代码实际触发路径的一致性
3. 确认 `_complete_structural_nodes` 中 RUNNING→SKIPPED 不再需要绕行

---
生成时间: 2026/4/20 19:57:36
planId: f32dac19-6967-4490-9218-2905200ff4a8
plan_status: review