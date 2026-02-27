# v1 → v2 → v3 → v4 动态性对比分析

> 本文档结合具体代码，分析 v2 DAG 驱动架构相较于 v1 "一次性文本计划驱动" 在动态性方面的 6 大提升，v3 自适应规划带来的 3 项新动态能力，以及 v4 混合规划路由（两阶段分类器自动选择 v1/v2 路径）。

---

## 阅读指南

**适合谁看**：对本项目 v1 和 v2 的区别感兴趣的学习者，不需要算法基础。

**核心结论先说**：

- **v1** 就像你出门前写了一张「买菜清单」（**静态线性执行 / Sequential Execution**），到了超市只能从第 1 项买到最后 1 项，中间一件买不到就得回家重新写清单（**全量重规划 / Full Replanning**）。
- **v2** 就像你到了超市打开手机导航（**动态图驱动执行 / DAG-driven Execution**），它告诉你「鸡蛋和牛奶在不同货架，你可以同时让两个人去拿」（**并行执行 / Parallel Execution**），买不到鸡蛋就跳过做蛋糕、改做其他菜（**条件分支跳过 / Conditional Branch Skip**），不用全部从头来（**局部重规划 / Partial Replanning**）。

下面逐一拆解这 6 个「从静态到动态」的变化。

**关键术语速查**：

| 术语 | 大白话解释 | 英文 / 学术名称 |
|------|-----------|----------------|
| **DAG** | 一种「流程图」——箭头表示先后顺序，且不能绕回自己形成死循环 | Directed Acyclic Graph，有向无环图 |
| **节点 (Node)** | DAG 里的每个「任务方块」，比如「搜索资料」「写代码」 | Vertex / Node |
| **边 (Edge)** | DAG 里连接两个方块的「箭头」，表示"A 做完才能做 B" | Directed Edge，有向边 |
| **Super-step** | 一轮执行周期——找出当前能做的事 → 一起做 → 收集结果 → 进入下一轮 | 源自 BSP（Bulk Synchronous Parallel）模型 |
| **状态机** | 给节点定义了「人生轨迹」——只能按规定路线走，不能乱跳 | FSM, Finite State Machine，有限状态机 |

---

## 目录

- [对比基线：v1 的静态瓶颈](#对比基线v1-的静态瓶颈)
- [动态性 1：运行时就绪发现](#动态性-1运行时就绪发现)
- [动态性 2：并行执行](#动态性-2并行执行)
- [动态性 3：条件分支](#动态性-3条件分支)
- [动态性 4：失败感知 + 回滚 + 子树级联跳过](#动态性-4失败感知--回滚--子树级联跳过)
- [动态性 5：局部重规划](#动态性-5局部重规划)
- [动态性 6：状态机强制合法转移](#动态性-6状态机强制合法转移)
- [总结对照表](#总结对照表)
- [v3 新增：自适应规划](#v3-新增自适应规划adaptive-planning)
  - [动态性 7：超步间自适应规划](#动态性-7超步间自适应规划)
  - [动态性 8：工具智能路由](#动态性-8工具智能路由)
  - [动态性 9：DAG 运行时变更](#动态性-9dag-运行时变更)
  - [v1 → v2 → v3 总结对照表](#v1--v2--v3-总结对照表)

---

## 对比基线：v1 的静态瓶颈

### 大白话理解

v1 就像一个**固执的项目经理**（**静态顺序执行模型 / Static Sequential Execution**）：

1. 开会时就把所有步骤写死在白板上（**一次性规划 / One-shot Planning**）：1 → 2 → 3 → 4 → 5
2. 每次只能做第 1 件事，做完才做第 2 件（**串行执行 / Serial Execution**）——即使第 3 件和第 4 件之间毫无关系
3. 第 3 步失败了？擦掉整个白板，从头开始规划（**全量重规划 / Full Replanning**）

### 对应代码

```python
# v1 执行模型（伪代码）
plan = planner.create_plan(task)           # 一次性生成 [Step1, Step2, ..., Step6]
for step in plan.steps:                     # 固定顺序，逐个执行
    result = executor.execute_step(step)
    if not result.success:
        plan = planner.replan(task, feedback)  # 失败 → 丢弃全部，从头重规划
        break
```

### 三个静态瓶颈

| 瓶颈 | 大白话 | 对应概念 |
|------|--------|---------|
| 执行顺序在规划时锁死 | 写好的清单不能调整顺序，`current_step_index` 只能往前走 | 静态调度（Static Scheduling） |
| 无并行能力 | 即使两件事互不相关，也只能排队一件一件做 | 串行执行（Serial Execution） |
| 失败时全部推倒重来 | 前面做好的 3 步白费了，全部丢弃从零开始 | 全量重规划（Full Replanning） |

---

## 动态性 1：运行时就绪发现

### 大白话理解

v2 不再提前排好固定顺序，而是每一轮都「环顾四周」（**运行时依赖检查 / Runtime Dependency Check**）：**谁的前置条件满足了，谁就可以开始**。

就像外卖调度系统（**动态任务调度 / Dynamic Task Scheduling**）——不是按单号顺序派单，而是每隔几秒看一眼：哪些订单的食物已经做好了、哪个骑手有空，然后动态分配。

### 对应代码

`dag/graph.py` — `get_ready_nodes()` 方法：

```python
def get_ready_nodes(self) -> list[TaskNode]:
    eligible = {NodeStatus.PENDING, NodeStatus.READY}  # 候选状态：还没做的
    ready = []
    for node in self.nodes.values():         # 扫描所有节点
        if node.status not in eligible:
            continue                          # 已完成/已失败的，跳过
        deps = self.get_dependency_ids(node.id)  # 找出"我依赖谁"（入边查询）
        # 核心逻辑（约束满足检查 / Constraint Satisfaction）：我依赖的所有任务都做完了吗？
        if all(self.nodes[d].status == NodeStatus.COMPLETED for d in deps):
            ready.append(node)                # 是的 → 我可以执行了！
    return ready
```

`dag/executor.py` — 主循环每轮调用：

```python
while not dag.is_complete():     # 所有节点都到终态（Terminal State）了吗？
    step += 1
    ready = dag.get_ready_nodes() # 每轮重新看：现在谁能做了？
```

### 图解

```
第 1 轮：A 和 B 没有依赖（入度 / In-degree = 0）→ 都能做
第 2 轮：A 做完了，C 依赖 A → C 能做了；B 还在做 → D（依赖 A+B）还不行
第 3 轮：B 也做完了 → D 终于能做了

    A ──→ C
    │     │
    ▼     ▼
    B ──→ D

  轮次1: [A, B] 就绪      ← 入度=0 的节点
  轮次2: [C] 就绪          ← A 完成，C 的入度降为 0
  轮次3: [D] 就绪          ← A、B 都完成，D 的入度降为 0
```

### v1 vs v2 对比

| | v1 | v2 |
|--|----|----|
| 决定执行顺序的时机 | 规划时（一次性排序）| **每一轮运行时**（动态发现） |
| 依据 | 固定的步骤编号（静态序列） | **当前所有节点的实时状态**（运行时约束满足） |
| 学术术语 | 静态调度（Static Scheduling） | 动态调度（Dynamic Scheduling） |

---

## 动态性 2：并行执行

### 大白话理解

v1 像一个人在做饭（**串行执行 / Serial Execution**）——切完菜才能炒菜，炒完菜才能煲汤，一件一件来。

v2 像一个厨房团队（**并发执行 / Concurrent Execution**）——一个人切菜、一个人煲汤、一个人热锅，互不干扰的事情**同时进行**。

### 对应代码

`dag/executor.py` — 用 `asyncio.gather`（**Python 协程并发原语**）同时启动多个任务：

```python
# asyncio.gather = "同时发起多个协程（Coroutine），全部完成后继续"
results = await asyncio.gather(*[
    self._run_node(node, dag) for node in batch
])
# batch 里可能有 [搜索资料, 运行代码, 读文件] 三个任务
# 它们没有先后依赖，所以同时执行！
```

### 图解

```
v1 串行（总耗时 = 3秒 + 2秒 + 1秒 = 6秒）：

  搜索资料 ████████ 3s
                      运行代码 █████ 2s
                                     读文件 ██ 1s
  ──────────────────────────────────────────→ 时间

v2 并行（总耗时 = max(3, 2, 1) = 3秒）：

  搜索资料 ████████ 3s
  运行代码 █████ 2s          ← 三个任务同时开始（并发 / Concurrency）
  读文件   ██ 1s
  ─────────────────→ 时间

  快了一倍！
```

### 关键点

哪些任务能并行，**不是人为指定的**，而是系统自动发现的（**隐式并行 / Implicit Parallelism**）——「动态性 1」发现了多个就绪节点（入度=0），自然就可以并行跑。如果某个节点失败导致下游被跳过，下一轮的并行分组会自动调整。

---

## 动态性 3：条件分支

### 大白话理解

v1 的计划是一条**没有岔路的直线**（**线性执行路径 / Linear Execution Path**）——从头走到尾。

v2 的计划是一张**有岔路口的地图**（**条件分支 / Conditional Branching**）——走到某个路口时，根据实际情况决定走左边还是右边。

就像导航软件（**运行时条件评估 / Runtime Condition Evaluation**）：正常情况走高速，但如果前方发现堵车（运行时条件），就自动切到国道。

### 对应代码

`dag/executor.py` — 每轮结束后检查条件边（**CONDITIONAL Edge**）：

```python
def _process_conditions(self, dag: TaskDAG) -> None:
    for node in list(dag.nodes.values()):
        if node.status != NodeStatus.COMPLETED:
            continue  # 只看已完成的节点
        for edge in dag.get_conditional_edges(node.id):  # 条件边（Conditional Edge）
            target = dag.nodes.get(edge.target)
            if target is None or target.status != NodeStatus.PENDING:
                continue

            # 核心：检查条件是否满足（条件评估 / Condition Evaluation）
            condition_met = self._evaluate_condition(edge, dag)

            if not condition_met:
                # 条件不满足 → 跳过这条路 + 跳过后面所有任务（级联跳过 / Cascade Skip）
                target.status = NodeStatus.SKIPPED
                dag.mark_subtree_skipped(target.id)
```

条件评估逻辑（简单的关键词匹配）：

```python
@staticmethod
def _evaluate_condition(edge, dag: TaskDAG) -> bool:
    if not edge.condition:
        return True  # 没有条件限制，默认通过
    source_result = dag.state.node_results.get(edge.source, "")
    # 检查前序节点的结果里有没有这个关键词（关键词匹配 / Keyword Matching）
    return edge.condition.lower() in source_result.lower()
```

### 图解 — 一个完整的例子

```
场景："分析 Python 并发模型"

DAG 规划（带条件边）：
  [分析检查] ──(条件边: 结果包含"需要深入")──→ [深入研究]     ← CONDITIONAL Edge
             ──(依赖边)──→ [写总结报告]                       ← DEPENDENCY Edge

情况 A：分析结果 = "需要深入研究并发模型的 GIL 机制"
  → "需要深入" 出现在结果中 ✅ （条件满足 / Condition Met）
  → [深入研究] 正常执行
  → [写总结报告] 正常执行

情况 B：分析结果 = "Python 并发模型比较简单，无需深入"
  → "需要深入" 没有出现在结果中 ❌ （条件不满足 / Condition Not Met）
  → [深入研究] 被 SKIPPED（跳过）
  → [写总结报告] 正常执行（它不依赖深入研究）
```

**同一份 DAG 计划，不同的运行时输出，走出完全不同的执行路径**（**动态路由 / Dynamic Routing**）——这是 v1 线性步骤列表根本做不到的。

---

## 动态性 4：失败感知 + 回滚 + 子树级联跳过

### 大白话理解

v1 像一串多米诺骨牌（**单点故障导致全局失败 / Single Point of Failure**）——中间一个倒了，唯一的选择是把所有骨牌重新摆一遍。

v2 像一棵树（**依赖子树 / Dependency Subtree**）——一根树枝断了（**失败节点 / Failed Node**），只需要处理这根断枝和它上面的叶子（**下游节点 / Downstream Nodes**），其他树枝完全不受影响（**故障隔离 / Fault Isolation**）。而且如果断枝上有果子可以抢救（**回滚操作 / Rollback**），系统会先抢救一下。

### 对应代码

`dag/executor.py` — 三层决策逻辑（**分层错误处理 / Layered Error Handling**）：

```python
async def _handle_failure(self, node: TaskNode, dag: TaskDAG) -> None:
    # 第 1 层：有没有"善后方案"（回滚边 / Rollback Edge）？
    rollback_targets = dag.get_rollback_targets(node.id)
    if rollback_targets:
        # 有 → 先执行善后节点（比如删除临时文件、释放资源）
        for rb_id in rollback_targets:
            rb_node = dag.nodes.get(rb_id)
            if rb_node and rb_node.status == NodeStatus.PENDING:
                rb_result = await self._run_node(rb_node, dag)
                # ...

        # 第 2 层：善后完成，标记为"已回滚"（ROLLED_BACK 终态 / Terminal State）
        self._sm.transition(node, NodeStatus.ROLLED_BACK)
    else:
        # 没有善后方案 → 直接标记为"跳过"（SKIPPED 终态）
        self._sm.transition(node, NodeStatus.SKIPPED)

    # 第 3 层：不管怎样，这个节点后面的所有任务都不能做了
    # （级联跳过 / Cascade Skip，通过 BFS 找出整个下游子树）
    dag.mark_subtree_skipped(node.id)
```

`dag/graph.py` — 通过 BFS（**广度优先搜索 / Breadth-First Search**）找出所有下游任务并跳过：

```python
def mark_subtree_skipped(self, node_id: str) -> None:
    downstream = self.get_downstream(node_id)  # BFS 遍历（可达性分析 / Reachability Analysis）
    for nid in downstream:
        node = self.nodes[nid]
        if node.status in (NodeStatus.PENDING, NodeStatus.READY):
            node.status = NodeStatus.SKIPPED  # 全部标记为"不用做了"
```

### 图解 — 失败的精准影响范围（故障隔离 / Fault Isolation）

```
假设 DAG 长这样：

  [搜索资料] ──→ [写分析报告] ──→ [最终汇总]
  [运行代码] ──→ [整理数据]   ──→ [最终汇总]

如果 [运行代码] 失败了：

  [搜索资料] ✅ ──→ [写分析报告] ✅ ──→ [最终汇总] ❌ 跳过（依赖链被切断）
  [运行代码] ❌ ──→ [整理数据]   ❌ 跳过    ← BFS 找出的下游子树
                     └→ [清理临时文件] ← 回滚节点（Rollback Node），自动执行善后

注意：[搜索资料] 和 [写分析报告] 完全不受影响（故障隔离）！
v1 会把这些已完成的工作也全部丢弃，v2 精准保留。
```

### 三层决策总结

| 层次 | 问题 | 决策 | 对应概念 |
|------|------|------|---------|
| 1 | 有善后方案吗？ | 有 → 先执行回滚节点；没有 → 直接跳过 | 回滚机制（Rollback Mechanism） |
| 2 | 失败节点标记成什么？ | 有回滚 → `ROLLED_BACK`；没有 → `SKIPPED` | 终态转移（Terminal State Transition） |
| 3 | 下游任务怎么办？ | 全部自动标记 `SKIPPED` | 级联跳过（Cascade Skip，基于 BFS） |

---

## 动态性 5：局部重规划

### 大白话理解

v1：考试没考好 → 留级重读整个学期（**全量重规划 / Full Replanning**）
v2：考试某一科没考好 → 只补考那一科，其他科的成绩保留（**局部重规划 / Partial Replanning**）

### 对应代码

`agents/orchestrator.py` — 发现失败节点后，只重建失败的那部分（**子树替换 / Subtree Replacement**）：

```python
# 找出所有失败的节点
failed_nodes = [
    n for n in dag.nodes.values()
    if n.status == NodeStatus.FAILED
]
# 只针对失败节点重新规划（局部重规划 / Partial Replanning）
if attempt < self.max_replan and failed_nodes:
    failed_node = failed_nodes[0]
    dag = await self.planner.replan_subtree(
        dag,
        failed_node_id=failed_node.id,    # 只重规划这个节点的子树
        feedback=reflection.feedback,
    )
```

`agents/planner.py` — 合并新旧 DAG 的逻辑（**图合并 / Graph Merge**）：

```python
@staticmethod
def _merge_dags(old_dag, new_dag, parent_id):
    merged_nodes = {}

    # 第 1 步：算出哪些节点属于"失败子树"（通过 BFS / 可达性分析）
    failed_subtree = set(old_dag.get_downstream(parent_id)) | {parent_id}

    # 第 2 步：旧 DAG 中——不在失败子树里的 + 已完成的，全部保留（差集 / Set Difference）
    for nid, node in old_dag.nodes.items():
        if nid not in failed_subtree or node.status == NodeStatus.COMPLETED:
            merged_nodes[nid] = node

    # 第 3 步：新 DAG 中的节点补进来（并集 / Set Union）
    for nid, node in new_dag.nodes.items():
        if nid not in merged_nodes:
            merged_nodes[nid] = node

    # 第 4 步：带上之前的执行结果，不用重新做（状态保留 / State Preservation）
    result_dag.state.node_results = dict(old_dag.state.node_results)
```

### 图解

```
原始 DAG（第一轮执行后）：

  [搜索] ✅ ──→ [分析] ✅ ──→ [写代码] ❌ 失败     ← 失败子树的根
                            ──→ [测试] 还没做       ← 失败子树的下游

局部重规划后的 DAG（Graph Merge 的结果）：

  [搜索] ✅ ──→ [分析] ✅ ──→ [换个方式写代码] 新的！ ← 替换后的新子树
       保留          保留    ──→ [测试] 还没做

关键：[搜索] 和 [分析] 的结果都带过来了（状态保留），不用重做！
```

**DAG 不是一份「写完就不能改的文件」，而是一个「活的、可以局部修补的运行时数据结构」（Mutable Runtime Data Structure）**。

---

## 动态性 6：状态机强制合法转移

### 大白话理解

v1 的节点状态就像一个没有规则的便利贴（**无约束状态变量 / Unconstrained State Variable**）——你想写什么就写什么，想擦就擦，随意修改。

v2 的节点状态就像一个**人生阶段**（**有限状态机 / Finite State Machine, FSM**）——你只能按规定路线走：

```
婴儿(PENDING) → 儿童(READY) → 青年(RUNNING) → 成年(COMPLETED)
                                             → 也可能生病(FAILED) → 康复(ROLLED_BACK)
不能从"成年(COMPLETED)"跳回"婴儿(PENDING)"！（非法转移 / Invalid Transition）
```

### 对应代码

`dag/state_machine.py` — 转移规则表（**状态转移表 / Transition Table**）：

```python
# 每个状态只能转移到特定的下一个状态，不能乱跳
VALID_TRANSITIONS = {
    PENDING:     {READY, SKIPPED},       # 等待中 → 可以变就绪，或者被跳过
    READY:       {RUNNING, SKIPPED},     # 就绪 → 开始执行，或者被跳过
    RUNNING:     {COMPLETED, FAILED},    # 执行中 → 成功 或 失败
    FAILED:      {ROLLED_BACK, SKIPPED}, # 失败 → 回滚 或 跳过
    COMPLETED:   {},                      # 终态（Terminal State）！不能再变了
    SKIPPED:     {},                      # 终态！
    ROLLED_BACK: {},                      # 终态！
}
```

校验逻辑（**转移校验 / Transition Validation**）——每次改状态前都检查：

```python
def transition(self, node, new_status):
    if not self.can_transition(node, new_status):
        raise InvalidTransitionError(...)  # 非法转移（Invalid Transition）！直接报错
    node.status = new_status               # 合法（Valid Transition）→ 允许修改
```

### 状态转移图（State Transition Diagram）— 节点的「人生轨迹」

```
正常路线（Happy Path / 正常路径）：
  等待中(PENDING) → 就绪(READY) → 执行中(RUNNING) → 成功完成(COMPLETED) 🎉

失败路线（有善后）：
  等待中 → 就绪 → 执行中 → 失败(FAILED) → 已回滚(ROLLED_BACK) 🔄

跳过路线（被迫放弃）：
  等待中 → 跳过(SKIPPED) ⏭️     ← 前面有节点失败，或条件不满足
  就绪 → 跳过 ⏭️
  失败 → 跳过 ⏭️

三个终态（Terminal State / 吸收态 Absorbing State，到了就不能再走了）：
  成功完成 🎉  |  跳过 ⏭️  |  已回滚 🔄
```

### 为什么需要状态机？

如果没有状态机（**状态一致性保护 / State Consistency Guard**），代码可能会意外地把一个「已完成」的节点改成「等待中」，导致它被重复执行（**幂等性破坏 / Idempotency Violation**）。状态机像一个**交通警察**——确保所有节点都沿着合法路线走，防止系统进入不一致状态（**Inconsistent State**）。

---

## 总结对照表

| 维度 | v1（固执的项目经理） | v2（灵活的导航系统） | 关键代码 | 对应概念 |
|------|---------------------|---------------------|---------|---------|
| **执行顺序** | 规划时写死 `[1→2→3→4]` | 每轮动态发现谁能做 | `get_ready_nodes()` | 动态调度 / Dynamic Scheduling |
| **并行能力** | 无，一件一件来 | 没有依赖的任务同时做 | `asyncio.gather` | 并发执行 / Concurrent Execution |
| **执行路径** | 一条直线，没有岔路 | 有岔路口，根据结果选路 | `_process_conditions()` | 条件分支 / Conditional Branching |
| **失败影响** | 全部推倒重来 | 只影响下游，其他不受影响 | `_handle_failure()` | 故障隔离 / Fault Isolation |
| **计划可变性** | 写完不能改 | 可以局部修补替换 | `_merge_dags()` | 局部重规划 / Partial Replanning |
| **状态管理** | 随意改，没有约束 | 状态机强制合法路线 | `NodeStateMachine` | 有限状态机 / FSM |

### 一句话总结

> v1 的计划是一份**写好就不能改的购物清单**（静态线性执行）——按顺序买，买不到就重新写。
>
> v2 的计划是一张**实时导航地图**（动态图驱动执行）——根据路况动态选路（条件分支）、多人并行（并发执行）、堵了就绕（故障隔离）、坏了就修补（局部重规划），已走过的路不用重走（状态保留）。

---

## v3 新增：自适应规划（Adaptive Planning）

> v3 在 v2 基础上，将**规划层本身也变为动态的**——计划不再是执行前的一次性产物，而是在执行过程中持续演化。

### v2 的规划限制

v2 虽然在**执行层**有 6 种动态性（上述全部），但**规划层**仍然是静态的：

```
v2 时间线：
  Plan(一次) → Execute(整个DAG) → Reflect → 失败才重规划
  ↑ Planner 只在这里出现过一次
```

即使中间结果显示原计划后半部分方向完全错了，Executor 也会继续按原计划跑完所有节点。

### 动态性 7：超步间自适应规划

#### 大白话理解

v2 像一个做了手术计划就直接执行的医生——不管手术中发现了什么新情况，都按原计划切下去。

v3 像一个**边做边调整的外科医生**——打开肚子发现跟 CT 显示不一样？立即调整后续手术步骤，该拿掉的拿掉（**REMOVE**），该改方案的改方案（**MODIFY**），发现新问题就加操作（**ADD**）。

```
v3 时间线：
  Plan → [Step1 → Adapt? → Step2 → Adapt? → ...] → Reflect
               ↑                    ↑
          Planner 看到 step1 结果  Planner 看到 step2 结果
          可能移除/修改/添加节点    继续调整
```

#### 对应代码

`dag/executor.py` — 超步循环中的自适应检查：

```python
# 在每个 super-step 结束后、下一轮开始前
if self._adaptive_enabled and self._should_adapt(step, dag):
    await self._adapt_plan(step, dag)
```

`agents/planner.py` — 自适应评估：

```python
async def adapt_plan(self, dag: TaskDAG) -> AdaptationResult:
    # 将已完成结果 + 待执行节点提交给 LLM
    # LLM 返回: should_adapt + adaptations (KEEP/MODIFY/REMOVE/ADD)
```

`agents/planner.py` — 应用调整：

```python
def apply_adaptations(self, dag, adaptations):
    for adapt in adaptations:
        if adapt.action == AdaptAction.REMOVE:
            dag.remove_pending_node(adapt.target_node_id)
        elif adapt.action == AdaptAction.MODIFY:
            dag.modify_node(adapt.target_node_id, ...)
        elif adapt.action == AdaptAction.ADD:
            dag.add_dynamic_node(new_node)
```

### 动态性 8：工具智能路由

#### 大白话理解

v2 的工具调用像**只会用锤子的人**——失败了还是用锤子再试。

v3 的工具调用像一个**聪明的手艺人**——锤子连续砸了两次都不行？换个螺丝刀试试！

#### 对应代码

`tools/router.py` — 失败追踪和建议：

```python
class ToolRouter:
    def record_failure(self, node_id, tool_name):
        stats.consecutive_failures += 1

    def get_hint(self, node_id) -> str:
        # "Tool 'web_search' has failed 2 times. Consider using execute_python."
```

`agents/executor.py` — ReAct 循环中注入提示：

```python
router_hint = self.tool_router.get_hint(node_id)
if router_hint and iteration > 1:
    continue_msg += f"\nIMPORTANT: {router_hint}"
```

### 动态性 9：DAG 运行时变更

#### 大白话理解

v2 的 DAG 像一栋**已经封顶的楼**——能住进去（执行），但不能加层或拆墙。失败了只能推倒那一翼重建（局部重规划）。

v3 的 DAG 像一栋**活的建筑**——执行过程中随时可以：
- 加房间（`add_dynamic_node`）
- 拆掉不需要的房间（`remove_pending_node`）
- 改造房间用途（`modify_node`）
- 添加新走廊连接（`add_dynamic_edge`）

#### 对应代码

`dag/graph.py` — 6 个新增的运行时变更方法：

```python
dag.add_dynamic_node(new_node)    # 添加新节点
dag.add_dynamic_edge(new_edge)    # 添加新边
dag.remove_pending_node(node_id)  # 移除 PENDING 节点
dag.modify_node(node_id, ...)     # 修改节点描述/判据
```

### v1 → v2 → v3 → v4 总结对照表

| 维度 | v1 | v2 | v3 | v4 |
|------|----|----|-----|-----|
| **规划路径选择** | 仅扁平 | 仅 DAG | 仅 DAG | **两阶段分类器自动选 v1 或 v2** |
| **执行顺序** | 规划时写死 | 运行时动态发现 | 运行时动态发现 | 同 v3 |
| **并行能力** | 无 | 自动并行 | 自动并行 | 同 v3（complex 路径） |
| **执行路径** | 一条直线 | 有条件分支 | 有条件分支 | 同 v3 |
| **失败处理** | 全部重来 | 局部重规划 | 局部重规划 | 同 v3 |
| **计划可变性** | 写完不能改 | 失败后才改 | **每步都可能调整** | 同 v3 |
| **工具失败** | 重试同一工具 | 重试同一工具 | **智能建议替代工具** | 同 v3 |
| **DAG 结构** | 无 DAG | 静态 DAG | **运行时可增删改节点** | 同 v3 |
| **Planner 介入** | 开头一次 | 开头 + 失败后 | **开头 + 每步检查 + 失败后** | **先 classify_task，再按路径规划** |
