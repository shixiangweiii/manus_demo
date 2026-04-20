
# 修复 DAG 分层规划代码评审问题

根据 `CODE_REVIEW_DAG.md` 中的评审结果，按优先级修复所有发现的代码质量问题。

## Proposed Changes

### P0 — 状态机一致性与校验修复

---

#### [MODIFY] [graph.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/graph.py)

**变更 1：注入 NodeStateMachine，统一状态修改入口**

当前 `TaskDAG` 中 `mark_subtree_skipped()` 和 `refresh_ready_states()` 直接赋值 `node.status = ...`，绕过了状态机校验。

修改方案：
1. `__init__` 新增可选参数 `state_machine: NodeStateMachine | None = None`，内部持有 `self._sm`
2. `mark_subtree_skipped()` 中将 `node.status = NodeStatus.SKIPPED` 改为 `self._sm.transition(node, NodeStatus.SKIPPED)`
3. `refresh_ready_states()` 中将 `node.status = NodeStatus.READY` 改为 `self._sm.transition(node, NodeStatus.READY)`

```diff
- def __init__(self, task, nodes, edges, context=""):
+ def __init__(self, task, nodes, edges, context="", state_machine=None):
      self.nodes = nodes
      self.edges = edges
      self.state = DAGState(task=task, context=context)
      self._checkpoints = []
+     self._sm = state_machine or NodeStateMachine()
      self._validate_dag()
```

```diff
  def mark_subtree_skipped(self, node_id):
      downstream = self.get_downstream(node_id)
      for nid in downstream:
          node = self.nodes[nid]
          if node.status in (NodeStatus.PENDING, NodeStatus.READY):
-             node.status = NodeStatus.SKIPPED
+             self._sm.transition(node, NodeStatus.SKIPPED)
              logger.info(...)
```

```diff
  def refresh_ready_states(self):
      for node in self.nodes.values():
          if node.status != NodeStatus.PENDING:
              continue
          deps = self.get_dependency_ids(node.id)
          if all(self.nodes[d].status == NodeStatus.COMPLETED for d in deps):
-             node.status = NodeStatus.READY
+             self._sm.transition(node, NodeStatus.READY)
```

**变更 2：`_validate_dag()` 校验失败抛出异常**

```diff
  def _validate_dag(self):
      node_ids = set(self.nodes.keys())
      for e in self.edges:
          if e.source not in node_ids:
-             logger.warning("[DAG] Edge source '%s' not found in nodes", e.source)
+             raise ValueError(f"[DAG] Edge source '{e.source}' not found in nodes")
          if e.target not in node_ids:
-             logger.warning("[DAG] Edge target '%s' not found in nodes", e.target)
+             raise ValueError(f"[DAG] Edge target '{e.target}' not found in nodes")
```

---

#### [MODIFY] [executor.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/executor.py)

**变更 1：`_process_conditions()` 使用状态机**

```diff
  if not condition_met:
-     target.status = NodeStatus.SKIPPED
+     self._sm.transition(target, NodeStatus.SKIPPED)
      dag.mark_subtree_skipped(target.id)
```

**变更 2：`_complete_structural_nodes()` 使用状态机**

```diff
  else:
      if node.status in (NodeStatus.PENDING, NodeStatus.READY):
-         node.status = NodeStatus.SKIPPED
+         if node.status == NodeStatus.PENDING:
+             self._sm.transition(node, NodeStatus.SKIPPED)
+         elif node.status == NodeStatus.READY:
+             self._sm.transition(node, NodeStatus.SKIPPED)
```

**变更 3：修复 `_handle_failure()` 回滚失败处理**

```diff
  for rb_id in rollback_targets:
      rb_node = dag.nodes.get(rb_id)
      if rb_node and rb_node.status == NodeStatus.PENDING:
          rb_result = await self._run_node(rb_node, dag)
          dag.state.merge_result(rb_id, rb_result.output)
          if rb_result.success:
              self._sm.transition(rb_node, NodeStatus.COMPLETED)
          else:
-             self._sm.transition(rb_node, NodeStatus.FAILED)
+             self._sm.transition(rb_node, NodeStatus.FAILED)
+             self._sm.transition(rb_node, NodeStatus.SKIPPED)
+             logger.warning("[DAGExecutor] Rollback node %s failed", rb_id)

- self._sm.transition(node, NodeStatus.ROLLED_BACK)
- self._emit("node_rollback", {"node": node})
+ all_rollbacks_ok = all(
+     dag.nodes[rb_id].status == NodeStatus.COMPLETED
+     for rb_id in rollback_targets
+     if rb_id in dag.nodes
+ )
+ if all_rollbacks_ok:
+     self._sm.transition(node, NodeStatus.ROLLED_BACK)
+     self._emit("node_rollback", {"node": node})
+ else:
+     self._sm.transition(node, NodeStatus.SKIPPED)
+     logger.warning("[DAGExecutor] Rollback partially failed for node %s, marking as SKIPPED", node.id)
```

---

### P1 — 性能优化与健壮性增强

---

#### [MODIFY] [graph.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/graph.py)

**变更 3：预构建邻接表，优化 BFS 性能**

新增 `_adjacency` 字段和 `_rebuild_adjacency()` 方法，将 `get_downstream()` 和 `topological_sort()` 的复杂度从 O(V*E) 降至 O(V+E)。

```diff
  def __init__(self, task, nodes, edges, context="", state_machine=None):
      self.nodes = nodes
      self.edges = edges
      self.state = DAGState(task=task, context=context)
      self._checkpoints = []
      self._sm = state_machine or NodeStateMachine()
+     self._dep_adjacency: dict[str, list[str]] = {}  # source -> [targets] (DEPENDENCY edges only)
+     self._rebuild_adjacency()
      self._validate_dag()

+ def _rebuild_adjacency(self) -> None:
+     self._dep_adjacency = {nid: [] for nid in self.nodes}
+     for e in self.edges:
+         if e.edge_type == EdgeType.DEPENDENCY:
+             if e.source in self._dep_adjacency:
+                 self._dep_adjacency[e.source].append(e.target)
```

更新 `get_downstream()` 使用邻接表：

```diff
  def get_downstream(self, node_id):
      visited = set()
      queue = deque()
-     children = [e.target for e in self.edges if e.source == node_id and e.edge_type == EdgeType.DEPENDENCY]
+     children = self._dep_adjacency.get(node_id, [])
      queue.extend(children)
      while queue:
          nid = queue.popleft()
          if nid in visited:
              continue
          visited.add(nid)
-         for e in self.edges:
-             if e.source == nid and e.edge_type == EdgeType.DEPENDENCY:
-                 queue.append(e.target)
+         for target in self._dep_adjacency.get(nid, []):
+             queue.append(target)
      return list(visited)
```

同时更新 `add_dynamic_edge()` 和 `remove_pending_node()` 维护邻接表。

**变更 4：`add_dynamic_edge()` 添加环检测**

```diff
  def add_dynamic_edge(self, edge):
      # ... 现有校验 ...
      self.edges.append(edge)
+     if edge.edge_type == EdgeType.DEPENDENCY:
+         self._dep_adjacency.setdefault(edge.source, []).append(edge.target)
+         # 环检测
+         topo = self.topological_sort()
+         if len(topo) != len(self.nodes):
+             # 回滚
+             self.edges.pop()
+             self._dep_adjacency[edge.source].remove(edge.target)
+             logger.warning("[DAG] Edge %s->%s would create a cycle, rejected", edge.source, edge.target)
+             return False
      return True
```

**变更 5：Checkpoint 数量限制**

```diff
+ MAX_CHECKPOINTS = 10

  def save_checkpoint(self):
      self._checkpoints.append(self.to_dict())
+     if len(self._checkpoints) > MAX_CHECKPOINTS:
+         self._checkpoints = self._checkpoints[-MAX_CHECKPOINTS:]
```

---

#### [MODIFY] [executor.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/executor.py)

**变更 4：添加节点执行超时控制**

```diff
+ NODE_EXECUTION_TIMEOUT = getattr(config, 'NODE_EXECUTION_TIMEOUT', 300)  # 默认 5 分钟

  results = await asyncio.gather(*[
-     self._run_node(node, dag) for node in batch
+     self._run_node_with_timeout(node, dag) for node in batch
  ])

+ async def _run_node_with_timeout(self, node: TaskNode, dag: TaskDAG) -> StepResult:
+     try:
+         return await asyncio.wait_for(
+             self._run_node(node, dag),
+             timeout=NODE_EXECUTION_TIMEOUT,
+         )
+     except asyncio.TimeoutError:
+         logger.error("[DAGExecutor] Node %s timed out after %ds", node.id, NODE_EXECUTION_TIMEOUT)
+         return StepResult(success=False, output=f"Node execution timed out after {NODE_EXECUTION_TIMEOUT}s")
```

**变更 5：增强死锁检测**

```diff
  if not ready:
-     logger.warning("[DAGExecutor] No ready nodes at super-step %d. %s", step, dag.summary())
+     if dag.has_failed_nodes():
+         logger.error("[DAGExecutor] DAG stuck at super-step %d: failed nodes blocking progress. %s", step, dag.summary())
+     else:
+         logger.warning("[DAGExecutor] No ready nodes at super-step %d (possible cycle). %s", step, dag.summary())
      break
```

---

#### [MODIFY] [config.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/config.py)

**新增配置项**

```diff
+ # Node execution timeout (seconds)
+ NODE_EXECUTION_TIMEOUT = int(os.getenv("NODE_EXECUTION_TIMEOUT", "300"))
+
+ # Maximum checkpoints to keep in memory
+ MAX_CHECKPOINTS = int(os.getenv("MAX_CHECKPOINTS", "10"))
```

---

### P2 — 代码质量改进

---

#### [MODIFY] [state_machine.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/state_machine.py)

**变更：回调异常记录日志**

```diff
  if self._on_transition:
      try:
          self._on_transition(node.id, old_status, new_status)
      except Exception:
-         pass
+         logger.debug("[SM] UI callback error for node %s", node.id, exc_info=True)
```

---

#### [MODIFY] [graph.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/graph.py)

**变更 6：修复 `topological_sort()` 入度计算冗余**

```diff
  for e in self.edges:
      if e.edge_type == EdgeType.DEPENDENCY:
-         in_degree[e.target] = in_degree.get(e.target, 0) + 1
+         in_degree[e.target] += 1
```

**变更 7：`summary()` 按状态枚举顺序输出**

```diff
  def summary(self):
-     status_counts = {}
-     for n in self.nodes.values():
-         status_counts[n.status.value] = status_counts.get(n.status.value, 0) + 1
-     parts = [f"{v} {k}" for k, v in status_counts.items()]
+     from collections import Counter
+     counts = Counter(n.status.value for n in self.nodes.values())
+     parts = [f"{counts[s.value]} {s.value}" for s in NodeStatus if counts.get(s.value, 0) > 0]
      return f"DAG[{len(self.nodes)} nodes: {', '.join(parts)}]"
```

---

#### [MODIFY] [executor.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/dag/executor.py)

**变更 6：`_compile_output()` 按拓扑序输出并添加节点标识**

```diff
  @staticmethod
  def _compile_output(dag: TaskDAG) -> str:
      parts = []
-     for node in dag.nodes.values():
+     topo_order = dag.topological_sort()
+     for node_id in topo_order:
+         node = dag.nodes[node_id]
          if node.node_type == NodeType.ACTION and node.status == NodeStatus.COMPLETED:
              if node.result:
-                 parts.append(node.result)
+                 parts.append(f"[{node.id}] {node.description}:\n{node.result}")
      if not parts:
          return "No action nodes completed successfully."
      return "\n\n".join(parts)
```

---

#### [MODIFY] [config.py](file:///Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo/config.py)

**变更：移除硬编码 API Key 默认值**

```diff
- LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-55470978f1044b70955df04ab6908c02")
+ LLM_API_KEY = os.getenv("LLM_API_KEY", "")
```

---

## Verification Plan

### Automated Tests

运行现有测试套件确认修改未引入回归：

```bash
cd /Users/shixiangweii/PycharmProjects/manus_learn_proj/manus_demo
python -m pytest tests/ -v
```

重点验证：
1. `tests/test_dag_capabilities.py` — DAG 核心能力测试
2. 手动构造包含悬空边的 DAG，验证 `_validate_dag()` 抛出 `ValueError`
3. 手动构造回滚失败场景，验证原节点被正确标记为 `SKIPPED`

### Manual Verification

1. 运行 `python main.py`，输入一个复杂任务（如"搜索 Python 最新版本，分析其新特性，然后写一份总结报告"），观察：
   - DAG 生成和执行流程是否正常
   - 状态转移日志是否全部通过状态机
   - 输出是否按拓扑序排列
2. 检查 `read_lints` 确认无新增编译错误


---
生成时间: 2026/4/20 17:54:38
planId: 3235042c-ff73-49f9-9325-8053200336e5
plan_status: review