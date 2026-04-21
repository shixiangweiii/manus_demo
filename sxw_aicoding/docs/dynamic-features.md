# v1 → v2 → v3 → v4 → v5 动态性对比分析

> 本文档结合具体代码，分析各版本在动态性方面的提升。
> **更新日期**: 2026-04-20

---

## 阅读指南

**适合谁看**：对本项目各版本区别感兴趣的学习者，不需要算法基础。

**核心结论先说**：

- **v1** 就像你出门前写了一张「买菜清单」（**静态线性执行 / Sequential Execution**），到了超市只能从第 1 项买到最后 1 项，中间一件买不到就得回家重新写清单（**全量重规划 / Full Replanning**）。
- **v2** 就像你到了超市打开手机导航（**动态图驱动执行 / DAG-driven Execution**），它告诉你「鸡蛋和牛奶在不同货架，你可以同时让两个人去拿」（**并行执行 / Parallel Execution**），买不到鸡蛋就跳过做蛋糕、改做其他菜（**条件分支跳过 / Conditional Branch Skip**），不用全部从头来（**局部重规划 / Partial Replanning**）。
- **v3** 就像一个有经验的项目经理（**自适应规划 / Adaptive Planning**），边做边根据实际情况调整计划：「这个工具老失败，换个试试」「做完了这一轮，看看需不需要调整后面的计划」。
- **v4** 就像一个智能分诊台（**混合路由 / Hybrid Routing**），自动判断该走哪条路：「简单任务走 v1 快速通道，复杂任务走 v2 DAG 通道」。
- **v5** 就像一个自由探索者（**隐式规划 / Implicit Planning**），边走边发现新任务：「本来只想查个天气，发现下雨了，临时决定加个买伞的任务」。

---

## 关键术语速查表

| 术语 | 大白话解释 | 英文 / 学术名称 |
|------|-----------|----------------|
| **DAG** | 一种「流程图」——箭头表示先后顺序，且不能绕回自己形成死循环 | Directed Acyclic Graph，有向无环图 |
| **节点 (Node)** | DAG 里的每个「任务方块」，比如「搜索资料」「写代码」 | Vertex / Node |
| **边 (Edge)** | DAG 里连接两个方块的「箭头」，表示"A 做完才能做 B" | Directed Edge，有向边 |
| **Super-step** | 一轮执行周期——找出当前能做的事 → 一起做 → 收集结果 → 进入下一轮 | 源自 BSP（Bulk Synchronous Parallel）模型 |
| **状态机** | 给节点定义了「人生轨迹」——只能按规定路线走，不能乱跳 | FSM, Finite State Machine，有限状态机 |
| **TODO 列表** | v5 的核心——一个动态的任务清单，可以随时添加、修改、删除任务 | Dynamic Task List |

---

## 目录

- [对比基线：v1 的静态瓶颈](#对比基线v1-的静态瓶颈)
- [动态性 1：运行时就绪发现](#动态性-1运行时就绪发现)
- [动态性 2：并行执行](#动态性-2并行执行)
- [动态性 3：条件分支](#动态性-3条件分支)
- [动态性 4：失败感知 + 回滚 + 子树级联跳过](#动态性-4失败感知--回滚--子树级联跳过)
- [动态性 5：局部重规划](#动态性-5局部重规划)
- [动态性 6：状态机强制合法转移](#动态性-6状态机强制合法转移)
- [v3 新增：自适应规划](#v3-新增自适应规划adaptive-planning)
  - [动态性 7：超步间自适应规划](#动态性-7超步间自适应规划)
  - [动态性 8：工具智能路由](#动态性-8工具智能路由)
  - [动态性 9：DAG 运行时变更](#动态性-9dag-运行时变更)
- [v4 新增：混合路由的动态性](#v4-新增混合路由的动态性hybrid-routing)
  - [动态性 10：任务复杂度自动分类](#动态性-10任务复杂度自动分类)
- [v5 新增：隐式规划的动态性](#v5-新增隐式规划的动态性implicit-planning)
  - [动态性 11：无预定义结构的动态规划](#动态性-11无预定义结构的动态规划)
  - [动态性 12：失败重试而非重规划](#动态性-12失败重试而非重规划)
- [总结对照表](#总结对照表)

---

## 对比基线：v1 的静态瓶颈

### 大白话理解

v1 就像一个**固执的项目经理**（**静态顺序执行模型 / Static Sequential Execution**）：

1. 开会时就把所有步骤写死在白板上（**一次性规划 / One-shot Planning**）：1 → 2 → 3 → 4 → 5
2. 每次只能做第 1 件事，做完才做第 2 件（**串行执行 / Serial Execution**）——即使第 3 件和第 4 件之间毫无关系
3. 第 3 步失败了？擦掉整个白板，从头开始规划（**全量重规划 / Full Replanning**）

### 对应代码（伪代码）

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
        if all(self.nodes[dep_id].status == NodeStatus.COMPLETED for dep_id in deps):
            ready.append(node)               # 条件满足 → 加入就绪队列
    return ready
```

### 与 v1 的对比

| 维度 | v1 | v2 |
|------|----|----|
| 调度时机 | 规划时一次性排好 | 每轮运行时动态计算 |
| 依赖检查 | 无（固定顺序） | 实时检查所有前置条件 |
| 灵活性 | 零（顺序锁死） | 高（谁准备好了谁先上） |

---

## 动态性 2：并行执行

### 大白话理解

v2 发现了「就绪节点」后，不是排队一个一个做，而是**让它们同时开工**（**并行执行 / Parallel Execution**）。

就像超市购物：你一个人只能一件一件拿（v1），但 v2 可以同时让三个人去不同货架拿鸡蛋、牛奶、面包（**多任务并行 / Multi-task Parallelism**）。

### 对应代码

`dag/executor.py` — `asyncio.gather` 并行执行：

```python
# DAGExecutor._execute_super_step() 核心逻辑
ready_nodes = dag.get_ready_nodes()  # 找出所有就绪节点
ready_nodes = ready_nodes[:self._max_parallel]  # 限制最大并行数

# 关键：使用 asyncio.gather 同时执行多个节点
results = await asyncio.gather(
    *[self._execute_single_node(node) for node in ready_nodes],
    return_exceptions=True  # 即使某个失败，其他继续
)
```

### MAX_PARALLEL_NODES 限制

就像超市只有 3 个购物篮（`MAX_PARALLEL_NODES=3`），就算有 10 件东西要买，也只能同时拿 3 件，拿完再拿下一批。

配置位置：`config.MAX_PARALLEL_NODES`

---

## 动态性 3：条件分支

### 大白话理解

v2 支持根据执行结果动态跳过某些任务（**条件分支跳过 / Conditional Branch Skip**）。

就像做菜：「如果鸡蛋买到了就做蛋糕，否则做其他菜」。v1 无论买没买到鸡蛋都会尝试做蛋糕，导致失败；v2 会根据实际结果调整后续任务。

### 对应代码

CONDITIONAL 边类型 + `_process_conditions()`：

```python
# schema.py 定义边类型
class EdgeType(Enum):
    DEPENDENCY = "dependency"      # A 做完才能做 B
    CONDITIONAL = "conditional"    # 条件边：A 的结果决定是否执行 B
    ROLLBACK = "rollback"          # A 失败时回滚 B

# dag/executor.py 处理条件边
def _process_conditions(self, dag: TaskDAG) -> None:
    for edge in dag.edges:
        if edge.edge_type != EdgeType.CONDITIONAL:
            continue
        source = dag.nodes[edge.source]
        target = dag.nodes[edge.target]
        
        # 检查条件是否满足（基于 DAGState 或源节点结果）
        condition_met = self._evaluate_condition(edge.condition, dag.state)
        
        if not condition_met:
            # 条件不满足 → 跳过目标节点及其整个子树
            dag.mark_subtree_skipped(target.id)
```

---

## 动态性 4：失败感知 + 回滚 + 子树级联跳过

### 大白话理解

v2 能够感知节点失败，并智能处理后续影响（**失败感知 + 回滚 / Failure Awareness + Rollback**）。

就像生产线：一个环节出问题，只影响依赖它的后续环节，不会让整个工厂停摆。v1 则是任何失败都导致全盘重来。

### 对应代码

`_handle_failure()` + `mark_subtree_skipped()` + ROLLBACK 边：

```python
# dag/executor.py 处理失败节点
def _handle_failure(self, dag: TaskDAG, node: TaskNode, error: Exception) -> None:
    # 1. 将节点标记为 FAILED
    self._sm.transition(node, NodeStatus.FAILED)
    
    # 2. 查找所有 ROLLBACK 边
    for edge in dag.edges:
        if edge.edge_type == EdgeType.ROLLBACK and edge.target == node.id:
            rollback_node = dag.nodes[edge.source]
            # 执行回滚操作（如撤销已完成的依赖节点）
            self._execute_rollback(rollback_node)
    
    # 3. 级联跳过依赖此节点的所有子树
    downstream_ids = self._get_downstream_nodes(dag, node.id)
    for dep_id in downstream_ids:
        dag.mark_subtree_skipped(dep_id)
```

---

## 动态性 5：局部重规划

### 大白话理解

v2 失败时只重规划失败的部分（**局部重规划 / Partial Replanning**），而不是全部重来。

就像装修：发现客厅的灯坏了，只换客厅的灯，不会把整个房子重新装修。v1 则是任何小问题都导致全盘重来。

### 对应代码

`replan_subtree()`：

```python
# dag/executor.py 局部重规划
def replan_subtree(self, dag: TaskDAG, failed_node: TaskNode) -> None:
    # 1. 找出失败节点的子树（所有依赖它的节点）
    subtree_ids = self._get_subtree_ids(dag, failed_node.id)
    
    # 2. 保留已完成的工作（COMPLETED 节点不动）
    # 3. 只对 PENDING/FAILED 的子树节点重新规划
    new_subgraph = self._planner.replan_subgraph(
        task=dag.state.task,
        context=dag.state.context,
        failed_node=failed_node,
        subtree_ids=subtree_ids
    )
    
    # 4. 将新的子图替换到 DAG 中
    dag.replace_subtree(failed_node.id, new_subgraph)
```

---

## 动态性 6：状态机强制合法转移

### 大白话理解

v2 给每个节点定义了「人生轨迹」（**状态机强制合法转移 / State Machine Validation**），不能乱跳。

就像交通规则：红灯必须停，绿灯才能行。v1 的节点状态只是一个普通枚举字段，代码可以随意赋值（如直接从 PENDING 跳到 COMPLETED），可能导致系统状态不一致。

### 对应代码

`dag/state_machine.py` — `VALID_TRANSITIONS`：

```python
# 完整的状态转移表——一目了然地看清所有合法转移路径
VALID_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    NodeStatus.PENDING:     {NodeStatus.READY, NodeStatus.SKIPPED},
    NodeStatus.READY:       {NodeStatus.RUNNING, NodeStatus.SKIPPED},
    NodeStatus.RUNNING:     {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED},
    NodeStatus.FAILED:      {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED, NodeStatus.PENDING},
    # 终态——不允许任何进一步转移
    NodeStatus.COMPLETED:   set(),
    NodeStatus.SKIPPED:     set(),
    NodeStatus.ROLLED_BACK: set(),
}

# 状态转移校验
def transition(self, node: TaskNode, new_status: NodeStatus) -> None:
    if not self.can_transition(node, new_status):
        raise InvalidTransitionError(
            f"Node '{node.id}': cannot transition from {node.status.value} to {new_status.value}. "
            f"Valid transitions from {node.status.value}: {VALID_TRANSITIONS.get(node.status, set())}"
        )
    # 应用状态转移
    node.status = new_status
```

---

## v3 新增：自适应规划

### 动态性 7：超步间自适应规划

#### 大白话理解

v3 在每做完一轮（Super-step）后，会「回头看看」（**超步间自适应规划 / Inter-step Adaptive Planning**）：需不需要调整后面的计划？

就像项目经理：每完成一个里程碑就开个会，看看进度如何、需不需要调整后续计划。

#### 对应代码

`adapt_plan()` + `apply_adaptations()`：

```python
# dag/executor.py 超步间自适应规划
def _execute_super_step(self, dag: TaskDAG) -> None:
    # ... 执行当前轮次 ...
    
    # v3 新增：检查是否需要自适应规划
    if config.ADAPTIVE_PLANNING_ENABLED:
        if self._should_adapt(dag):  # 检查适应条件（如失败率过高）
            adaptations = self._planner.adapt_plan(
                dag=dag,
                state=dag.state,
                feedback=self._collect_feedback(dag)
            )
            self._apply_adaptations(dag, adaptations)
```

#### 配置

- `config.ADAPTIVE_PLANNING_ENABLED`: 是否启用自适应规划
- `config.ADAPT_PLAN_INTERVAL`: 自适应规划的间隔（每 N 个 Super-step 检查一次）
- `config.ADAPT_PLAN_MIN_COMPLETED`: 至少完成多少个 ACTION 节点后才启动自适应

---

### 动态性 8：工具智能路由

#### 大白话理解

v3 能够智能选择工具（**工具智能路由 / Intelligent Tool Routing**）：一个工具老是失败，就建议换一个试试。

就像修车：用扳手拧不动螺丝，系统会提示「试试改锥」。避免陷入工具失败的死循环。

#### 对应代码

`tools/router.py` — `ToolRouter`：

```python
class ToolRouter:
    """追踪工具使用情况，在连续失败时建议替代工具"""
    
    def __init__(self, available_tools: list[str], failure_threshold: int = 3):
        self._available_tools = available_tools
        self._threshold = failure_threshold
        self._stats: dict[str, dict[str, ToolStats]] = {}  # 节点→工具→统计
    
    def record_failure(self, node_id: str, tool_name: str) -> None:
        stats = self._get_stats(node_id, tool_name)
        stats.consecutive_failures += 1
        
        # 连续失败超过阈值 → 建议替代工具
        if stats.consecutive_failures >= self._threshold:
            alternatives = self._suggest_alternatives(tool_name)
            logger.warning(f"Tool {tool_name} failed {stats.consecutive_failures} times. "
                          f"Try alternatives: {alternatives}")
    
    def get_hint(self, node_id: str) -> str | None:
        """为 LLM 提供工具选择建议"""
        stats = self._stats.get(node_id, {})
        for tool_name, stat in stats.items():
            if stat.consecutive_failures >= self._threshold:
                return f"注意：{tool_name} 已连续失败 {stat.consecutive_failures} 次，建议尝试其他工具"
        return None
```

#### 配置

- `config.TOOL_FAILURE_THRESHOLD`: 工具连续失败阈值（默认 3 次）

---

### 动态性 9：DAG 运行时变更

#### 大白话理解

v3 支持在执行过程中动态修改 DAG（**DAG 运行时变更 / Runtime DAG Mutation**）：可以加新任务、删旧任务、改任务描述。

就像装修：装修到一半，业主突然想加个壁橱，可以直接在现有计划里添加，不用全部重来。

#### 对应代码

`add_dynamic_node()`, `remove_pending_node()`, `modify_node()`：

```python
# dag/graph.py DAG 运行时变更
class TaskDAG:
    def add_dynamic_node(self, node: TaskNode, dependencies: list[str]) -> None:
        """在运行时添加新节点"""
        self.nodes[node.id] = node
        # 添加依赖边
        for dep_id in dependencies:
            self.edges.append(TaskEdge(
                source=dep_id,
                target=node.id,
                edge_type=EdgeType.DEPENDENCY
            ))
        self._rebuild_adjacency()  # 重建邻接表
    
    def remove_pending_node(self, node_id: str) -> None:
        """删除尚未执行的节点"""
        node = self.nodes[node_id]
        if node.status not in {NodeStatus.PENDING, NodeStatus.READY}:
            raise ValueError("只能删除未执行的节点")
        
        # 删除节点和相关的边
        del self.nodes[node_id]
        self.edges = [e for e in self.edges if e.source != node_id and e.target != node_id]
        self._rebuild_adjacency()
    
    def modify_node(self, node_id: str, **updates) -> None:
        """修改节点属性（如任务描述）"""
        node = self.nodes[node_id]
        for key, value in updates.items():
            setattr(node, key, value)
```

---

## v4 新增：混合路由的动态性

### 动态性 10：任务复杂度自动分类

#### 大白话理解

v4 像智能分诊台（**任务复杂度自动分类 / Automatic Task Complexity Classification**），自动判断该走哪条路：简单任务走 v1 快速通道，复杂任务走 v2 DAG 通道。

就像医院：感冒发烧走普通门诊，复杂手术走专科通道。避免简单任务也走复杂的 DAG 流程。

#### 对应代码

`agents/planner.py` — `classify_task()` → `_rule_classify()` + `_llm_classify()`：

```python
# agents/planner.py
class PlannerAgent(BaseAgent):
    """混合规划智能体，自动路由 v1 扁平计划 / v2 DAG 分层计划 / v5 隐式规划"""
    
    async def classify_task(self, task: str) -> str:
        """
        三阶段混合分类器：
        1. config.PLAN_MODE 强制覆盖（用于测试/调试）
        2. Stage 1：规则快筛 -> simple / complex / emergent / ambiguous
        3. Stage 2：轻量 LLM 调用（仅当 Stage 1 返回 ambiguous 时触发）
        
        返回：
          - "simple"   → v1 扁平计划
          - "complex"  → v2 DAG 分层计划
          - "emergent" → v5 隐式规划
        """
        # 0. 配置强制覆盖
        if config.PLAN_MODE in ("simple", "complex"):
            return config.PLAN_MODE
        
        # 1. 规则分类（零成本）
        rule_result = self._rule_classify(task)
        if rule_result != "ambiguous":
            return rule_result
        
        # 2. LLM 分类（仅对模糊区间）
        return await self._llm_classify(task)
    
    def _rule_classify(self, task: str) -> str:
        """
        Stage 1：基于规则启发式的快速分类器。
        
        按多个维度对任务文本打分。返回：
          - "simple"    分数 <= -2（强简单信号）
          - "complex"   分数 >= 3 （强复杂信号）
          - "emergent"  探索性/不确定性模式检测到时（v5 路由）
          - "ambiguous" 其他（需要 LLM 裁决）
        """
        score = 0
        
        # 文本长度维度
        text_len = len(task)
        if text_len < 30:
            score -= 2  # 短任务倾向于简单
        elif text_len > 200:
            score += 2  # 长任务倾向于复杂
        
        # 多步骤模式检测（如"首先...然后...最后..."）
        multi_step_hits = len(self._MULTI_STEP_PATTERN.findall(task))
        if multi_step_hits >= 2:
            score += 3  # 明确的多步骤任务
        
        # 条件分支模式检测（如"如果...就..."）
        if self._CONDITIONAL_PATTERN.search(task):
            score += 2
        
        # 探索性模式检测（如"探索"、"发现"、"研究"等）
        if self._EXPLORATORY_PATTERN.search(task):
            return "emergent"  # 直接路由到 v5
        
        # 根据分数返回分类结果
        if score <= -2:
            return "simple"
        elif score >= 3:
            return "complex"
        return "ambiguous"
    
    async def _llm_classify(self, task: str) -> str:
        """
        Stage 2：针对模糊任务的轻量级 LLM 分类。
        
        Prompt 保持极简（约 60 输入 tokens），temperature=0.0 确保确定性。
        """
        prompt = f"""
        判断以下任务的复杂度：
        任务：{task}
        
        返回 JSON：
        {{
          "classification": "simple" 或 "complex"
        }}
        """
        data = await self.think_json(prompt, temperature=0.0)
        return data.get("classification", "complex")
```

#### 三阶段设计优势

- **规则快筛（零成本）**：明确的简单/复杂/探索性任务直接路由，无需调用 LLM
- **LLM 兜底（仅模糊区间）**：不确定的任务才用 LLM 判断，节省成本
- **探索性检测（v5 路由）**：自动识别需要自由探索的任务，路由到隐式规划

---

## v5 新增：隐式规划的动态性

### 动态性 11：无预定义结构的动态规划

#### 大白话理解

v5 像自由探索者（**无预定义结构的动态规划 / Dynamic Planning without Predefined Structure**），边走边发现新任务。

本来只想查个天气，发现下雨了，临时决定加个买伞的任务。没有预先的 DAG 结构，所有计划在执行过程中自然涌现。

#### 对应代码

`EmergentPlannerAgent.execute()` — `while(has_pending_todos)`：

```python
# agents/emergent_planner.py
class EmergentPlannerAgent(BaseAgent):
    """Claude Code 风格的隐式规划器"""
    
    def execute(self, task: str) -> StepResult:
        # 1. 初始化 TODO 列表（1-3 个初始任务）
        todos = self._initialize_todos(task)
        
        # 2. 主循环：只要有待办任务就继续
        while self._has_pending_todos(todos):
            # 选择下一个可执行的 TODO
            current_todo = self._select_next_todo(todos)
            
            # 使用 ReAct 循环推理 + 调用工具
            result = self._think_with_tools(current_todo, todos)
            
            # 动态更新 TODO 列表
            self._update_todos(todos, current_todo, result)
            # 可能添加新 TODO（如发现需要额外工作）
            # 可能删除 TODO（如发现任务不必要）
            # 可能修改 TODO（如任务描述变化）
        
        # 3. 所有 TODO 完成 → 汇总结果
        return self._compile_final_result(todos)
    
    def _update_todos(self, todos: TodoList, current_todo: TodoItem, result: StepResult) -> None:
        """动态更新 TODO 列表"""
        if result.success:
            # 标记当前 TODO 为完成
            current_todo.status = TodoStatus.COMPLETED
            
            # 根据结果动态添加新 TODO
            if result.discovered_tasks:
                for new_task in result.discovered_tasks:
                    todos.add(TodoItem(
                        description=new_task.description,
                        status=TodoStatus.PENDING
                    ))
        else:
            # 失败 → 重新标记为 PENDING（稍后重试）
            current_todo.status = TodoStatus.PENDING
```

#### TODO 列表随时增删改

- **添加**：执行过程中发现新任务，直接加入 TODO 列表
- **删除**：发现某些任务不必要，直接删除
- **修改**：任务描述变化，动态更新

---

### 动态性 12：失败重试而非重规划

#### 大白话理解

v5 失败时重做（**失败重试而非重规划 / Retry Instead of Replan**），不用重新制定整个计划。

就像做菜：盐放多了，就重新做这一道菜，不用重新设计整个菜单。v2/v3 需要重新规划子图，v5 只需将失败的 TODO 回退为 PENDING。

#### 对应代码

`TodoList.mark_pending()` 将失败 TODO 回退为 PENDING：

```python
# agents/emergent_planner.py
async def execute(self, task: str) -> StepResult:
    # ... 主循环 ...
    for todo in todos_to_execute:
        result = await self._execute_todo(todo)
        
        if result.success:
            self._todo_list.mark_completed(todo.id)
            # 处理新发现的任务
            await self._update_todo_list(result)
        else:
            # 失败 → 重新标记为 PENDING（稍后重试）
            self._todo_list.mark_pending(todo.id)
            logger.warning("[EmergentPlanner] TODO %d failed, marked as pending for retry", todo.id)
```

---

## 总结对照表

| 动态性 | 描述 | v1 | v2 | v3 | v4 | v5 |
|--------|------|----|----|----|----|----|
| **运行时就绪发现** | 每轮动态计算可执行任务 | ❌ | ✅ | ✅ | ✅ | ✅ |
| **并行执行** | 同时执行多个任务 | ❌ | ✅ | ✅ | ✅ | ✅ |
| **条件分支** | 根据结果动态跳过任务 | ❌ | ✅ | ✅ | ✅ | ✅ |
| **失败感知 + 回滚** | 失败时智能处理后续影响 | ❌ | ✅ | ✅ | ✅ | ✅ |
| **局部重规划** | 只重规划失败部分 | ❌ | ✅ | ✅ | ✅ | ❌ |
| **状态机强制** | 节点状态转移严格校验 | ❌ | ✅ | ✅ | ✅ | ✅ |
| **超步间自适应规划** | 每轮后调整计划 | ❌ | ❌ | ✅ | ✅ | ✅ |
| **工具智能路由** | 失败时建议替代工具 | ❌ | ❌ | ✅ | ✅ | ✅ |
| **DAG 运行时变更** | 执行中动态修改 DAG | ❌ | ❌ | ✅ | ✅ | ❌ |
| **任务复杂度自动分类** | 自动选择 v1/v2 路径 | ❌ | ❌ | ❌ | ✅ | ❌ |
| **无预定义结构规划** | TODO 列表动态演化 | ❌ | ❌ | ❌ | ❌ | ✅ |
| **失败重试** | 失败时重做而非重规划 | ❌ | ❌ | ❌ | ❌ | ✅ |

### 版本演进总结

- **v1 → v2**：从静态线性执行到动态图驱动执行，引入并行、条件分支、局部重规划
- **v2 → v3**：增加自适应能力，超步间调整计划、工具智能路由、DAG 运行时变更
- **v3 → v4**：增加混合路由，自动判断任务复杂度，选择最优执行路径
- **v4 → v5**：从显式规划到隐式规划，TODO 列表动态演化，失败重试而非重规划

---

> 本文档基于源码分析生成，如需了解具体实现细节，请参考对应的源码文件。
