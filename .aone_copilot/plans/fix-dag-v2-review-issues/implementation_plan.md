
# 修复 DAG 模块二次代码评审问题

根据 `CODE_REVIEW_DAG_V2.md` 中发现的 21 个问题，按优先级分批修复。

## User Review Required

> [!IMPORTANT]
> **P1-5（FAILED→PENDING 重试路径）** 是一个设计决策：当前系统通过 `replan_subtree` 创建新 DAG 来实现"重试"，添加 `FAILED→PENDING` 会改变状态机语义。建议本次仅添加路径，不改变现有重试逻辑，为未来节点级重试预留能力。

> [!WARNING]
> **P0-3（统一状态机实例）** 的修复方案选择"在 `execute()` 入口将 Executor 的状态机注入 DAG"，这会改变 DAG 的 `_sm` 引用。如果有其他代码持有旧 `_sm` 的引用，需要同步更新。经检查，当前无此风险。

## Proposed Changes

### Component 1: dag/executor.py — 错误处理与状态一致性（P0-1, P0-2, P0-3, P1-4）

#### [MODIFY] [executor.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/executor.py)

**P0-1：`_check_exit_criteria()` 异常捕获**（第 175-185 行）

在主循环中为 `_check_exit_criteria()` 调用添加 try-except，防止 LLM 异常导致节点状态卡死在 RUNNING：

```diff
 if result.success:
-    passed = await self._check_exit_criteria(node, result)
+    try:
+        passed = await self._check_exit_criteria(node, result)
+    except Exception as exc:
+        logger.error("[DAGExecutor] Exit criteria check failed for %s: %s", node.id, exc)
+        passed = False
     if passed:
```

**P0-2：超时 StepResult 补充 step_id**（第 254-255 行）

```diff
-    return StepResult(success=False, output=f"Node execution timed out after {timeout}s")
+    return StepResult(step_id=node.id, success=False, output=f"Node execution timed out after {timeout}s")
```

**P0-3：统一 DAG 和 Executor 的状态机实例**（第 100 行 + 第 112 行）

在 `execute()` 方法入口处，将 Executor 的带回调状态机注入 DAG，确保所有状态转移（包括 DAG 内部的 `refresh_ready_states()`、`mark_subtree_skipped()`）都使用同一个状态机实例：

```diff
 async def execute(self, dag: TaskDAG) -> str:
+    # 统一状态机：将 Executor 的带回调状态机注入 DAG，
+    # 确保 DAG 内部的状态转移（refresh_ready_states、mark_subtree_skipped 等）
+    # 也能触发 UI 事件回调，避免双状态机不一致问题。
+    dag._sm = self._sm
     dag.refresh_ready_states()
```

**P1-4：`_complete_structural_nodes()` 终态列表补充 FAILED**（第 404 行）

```diff
-    if node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK):
+    if node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.ROLLED_BACK, NodeStatus.FAILED):
         continue  # 已处于终态，跳过
```

---

### Component 2: dag/graph.py — 防御性编程与性能优化（P0-4, P1-1, P1-3, P2-1, P2-6）

#### [MODIFY] [graph.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/graph.py)

**P2-6：顶部导入 config，移除 save_checkpoint 中的延迟导入**（第 1-20 行 + 第 418 行）

在文件顶部添加 `import config`，移除 `save_checkpoint()` 中的 `import config as _config`。

**P0-4：`get_ready_nodes()` 防御性检查**（第 120 行）

```diff
-    if all(self.nodes[d].status == NodeStatus.COMPLETED for d in deps):
+    if all(
+        d in self.nodes and self.nodes[d].status == NodeStatus.COMPLETED
+        for d in deps
+    ):
```

**P1-1：构建反向邻接表，优化 `get_dependency_ids()` 到 O(1)**

在 `_rebuild_adjacency()` 中同步构建反向邻接表 `_reverse_dep_adjacency`（target → [sources]），并改写 `get_dependency_ids()` 使用反向邻接表：

```diff
 def _rebuild_adjacency(self) -> None:
     self._dep_adjacency = {nid: [] for nid in self.nodes}
+    self._reverse_dep_adjacency: dict[str, list[str]] = {nid: [] for nid in self.nodes}
     for e in self.edges:
         if e.edge_type == EdgeType.DEPENDENCY:
             if e.source in self._dep_adjacency:
                 self._dep_adjacency[e.source].append(e.target)
+            if e.target in self._reverse_dep_adjacency:
+                self._reverse_dep_adjacency[e.target].append(e.source)
```

改写 `get_dependency_ids()`：
```diff
 def get_dependency_ids(self, node_id: str) -> list[str]:
-    return [
-        e.source for e in self.edges
-        if e.target == node_id and e.edge_type == EdgeType.DEPENDENCY
-    ]
+    return list(self._reverse_dep_adjacency.get(node_id, []))
```

同步维护反向邻接表：在 `add_dynamic_node()`、`add_dynamic_edge()`、`remove_pending_node()` 中同步更新 `_reverse_dep_adjacency`。

**P1-3：`from_dict()` 支持状态机注入**（第 457-464 行）

```diff
 @classmethod
-def from_dict(cls, data: dict[str, Any]) -> TaskDAG:
+def from_dict(cls, data: dict[str, Any], state_machine: NodeStateMachine | None = None) -> TaskDAG:
     nodes = {nid: TaskNode(**ndata) for nid, ndata in data["nodes"].items()}
     edges = [TaskEdge(**edata) for edata in data["edges"]]
     dag = cls(
         task=data["task"],
         nodes=nodes,
         edges=edges,
         context=data.get("context", ""),
+        state_machine=state_machine,
     )
     dag.state.node_results = data.get("node_results", {})
     return dag
```

**P2-1：`remove_pending_node()` 邻接表清理效率优化**（第 347-349 行）

```diff
-    for source_targets in self._dep_adjacency.values():
-        while node_id in source_targets:
-            source_targets.remove(node_id)
+    for source in self._dep_adjacency:
+        self._dep_adjacency[source] = [t for t in self._dep_adjacency[source] if t != node_id]
```

---

### Component 3: dag/state_machine.py — 重试路径（P1-5）

#### [MODIFY] [state_machine.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/state_machine.py)

**P1-5：FAILED 状态添加 PENDING 重试路径**（第 49 行）

```diff
-    NodeStatus.FAILED:      {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED},
+    NodeStatus.FAILED:      {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED, NodeStatus.PENDING},
```

同步更新文件头部的转移图注释：
```diff
-    PENDING ──> READY ──> RUNNING ──> COMPLETED   (happy path / 正常路径)
-                                  ──> FAILED ──> ROLLED_BACK
+    PENDING ──> READY ──> RUNNING ──> COMPLETED   (happy path / 正常路径)
+                                  ──> FAILED ──> ROLLED_BACK
+                                            ──> PENDING (retry / 重试)
```

---

### Component 4: agents/orchestrator.py — 集成修复（P1-2, P1-6, P2-3）

#### [MODIFY] [orchestrator.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/agents/orchestrator.py)

**P1-2：重规划后同步状态机**（第 383-389 行）

在 `replan_subtree()` 返回新 DAG 后，将 Executor 的状态机注入新 DAG：

```diff
             dag = await self.planner.replan_subtree(
                 dag,
                 failed_node_id=failed_node.id,
                 feedback=reflection.feedback,
             )
+            # 将 Executor 的状态机注入新 DAG，确保 UI 事件不丢失
+            dag._sm = dag_executor._sm
             self._emit("dag_created", dag)
```

**P1-6：过滤回滚节点结果**（第 347-353 行）

```diff
         results = [
             r for r in [
                 self._node_to_result(nid, dag)
                 for nid in dag.state.node_results
+                if dag.nodes.get(nid) and dag.nodes[nid].node_type == NodeType.ACTION
             ]
             if r is not None
         ]
```

需要在文件顶部导入 `NodeType`：
```diff
-from schema import MemoryEntry, NodeStatus, Plan, StepResult, StepStatus
+from schema import MemoryEntry, NodeStatus, NodeType, Plan, StepResult, StepStatus
```

**P2-3：`_emit()` 异常记录日志**（第 427-429 行）

```diff
     except Exception:
-        pass  # UI errors should never crash the pipeline / UI 异常不能影响主流程
+        logger.debug("[Orchestrator] UI callback error for event '%s'", event, exc_info=True)
```

---

### Component 5: schema.py — 数据模型改进（P1-7, P2-5）

#### [MODIFY] [schema.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/schema.py)

**P1-7：`merge_result()` 覆盖时记录日志**（第 216-228 行）

```diff
 def merge_result(self, node_id: str, output: str) -> None:
     """
     Write a node's result into shared state.
     将节点的执行结果写入共享状态。
+
+    WARNING: This overwrites any previous result for the same node_id.
+    注意：对同一 node_id 的重复写入会覆盖旧结果。
     """
+    if node_id in self.node_results:
+        logger.debug("[DAGState] Overwriting result for node %s (previous length: %d)",
+                     node_id, len(self.node_results[node_id]))
     self.node_results[node_id] = output
```

需要在文件顶部添加 `import logging` 和 `logger = logging.getLogger(__name__)`。

**P2-5：`ExitCriteria` docstring 明确行为**（第 113-121 行）

```diff
 class ExitCriteria(BaseModel):
     """
     Defines what 'done' means for a node. Validated after execution.
     定义节点的「完成标准」，在节点执行完毕后由 Reflector 验证。
+
+    Behavior note:
+    - When `required=True` and `validation_prompt` is non-empty: LLM-based validation via Reflector.
+    - When `required=True` but `validation_prompt` is empty: falls back to `result.success` directly.
+    - When `required=False`: validation is skipped entirely, always returns True.
+
+    行为说明：
+    - `required=True` 且 `validation_prompt` 非空：通过 Reflector 进行 LLM 验证。
+    - `required=True` 但 `validation_prompt` 为空：直接以 `result.success` 为准，不做 LLM 验证。
+    - `required=False`：完全跳过验证，始终返回 True。
     """
```

---

### Component 6: dag/executor.py — 输出编译降级处理（P2-2, P3-4）

#### [MODIFY] [executor.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/executor.py)

**P2-2 + P3-4：`_compile_output()` 拓扑排序降级 + 防御性 get**（第 446-458 行）

```diff
 @staticmethod
 def _compile_output(dag: TaskDAG) -> str:
     parts = []
     topo_order = dag.topological_sort()
+    if len(topo_order) != len(dag.nodes):
+        logger.warning("[DAGExecutor] Topological sort incomplete, falling back to dict order")
+        topo_order = list(dag.nodes.keys())
     for node_id in topo_order:
-        node = dag.nodes[node_id]
+        node = dag.nodes.get(node_id)
+        if node is None:
+            continue
         if node.node_type == NodeType.ACTION and node.status == NodeStatus.COMPLETED:
             if node.result:
                 parts.append(f"[{node.id}] {node.description}:\n{node.result}")
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
2. 逐文件核对修改点与评审报告的对应关系
3. 确认反向邻接表在 `add_dynamic_node`、`add_dynamic_edge`、`remove_pending_node` 中正确维护


---
生成时间: 2026/4/20 19:18:25
planId: 1f9d823a-a116-4d6d-a16e-febc6090fac0
plan_status: review