# 数据结构与算法详解

> 本文档系统梳理 DAG 驱动的多智能体 Demo（v6：含隐式规划路由 + ShellTool + ReActEngine）中用到的全部数据结构与算法，
> 按 **基础 → 核心 → 组合应用** 的顺序排列，每个知识点都配有大白话解释和生活类比。
> 大白话后面的 **（括号内容）** 是对应的专业术语，方便你将直觉理解与正式知识体系对接。

---

## 阅读指南

**适合谁看**：对数据结构和算法有基本了解（知道数组、循环），想理解这个 Demo 底层原理的学习者。

**怎么读**：
- 每个知识点都有「大白话理解」和「对应代码」两部分
- 大白话后的 **（粗体括号）** 是专业术语——先理解白话，再记住术语
- 文末有推荐学习路径和 LeetCode 练习题

**全文知识点地图**：

```
基础数据结构（砖头）                   核心算法（建造方法）              组合应用（建好的房子）
┌─────────────────────┐        ┌────────────────────┐        ┌──────────────────────┐
│ 1. 字典 (dict)       │        │ 6. Kahn 拓扑排序    │        │ 10. Super-step 并行   │
│ 2. 集合 (set)        │  ───→  │ 7. BFS 广度优先搜索  │  ───→  │ 11. 图合并/局部重规划  │
│ 3. 有向图 (DAG)      │        │ 8. 就绪节点发现      │        │ 12. TF-IDF 文本检索   │
│ 4. 多重边            │        │ 9. 有限状态机 (FSM)  │        │ 13. while(tool_use)   │
│ 5. 树 + 队列         │        │                    │        │ 14. 指数退避重试      │
│ 6. TodoItem/TodoList │        │                    │        │ 15. 三路由分类器      │
└─────────────────────┘        └────────────────────┘        └──────────────────────┘
```

---

## 目录

- [一、基础数据结构](#一基础数据结构)
  - [1. 字典 dict — 万能查找表](#1-字典-dict--万能查找表)
  - [2. 集合 set — 去重和快速查找](#2-集合-set--去重和快速查找)
  - [3. 有向图 — 节点字典 + 边列表](#3-有向图--节点字典--边列表)
  - [4. 带类型的多重边](#4-带类型的多重边)
  - [5. 有根树 — parent_id 父指针](#5-有根树--parentid-父指针)
  - [快照列表 — Checkpoint 机制](#快照列表--checkpoint-机制)
  - [队列 / 双端队列 (Deque)](#队列--双端队列-deque)
  - [TodoItem/TodoList — v5 隐式规划的数据结构](#todoitemtodolist--v5-隐式规划的数据结构)
- [二、核心算法](#二核心算法)
  - [6. Kahn 算法 (拓扑排序)](#6-kahn-算法-拓扑排序)
  - [7. BFS (广度优先搜索)](#7-bfs-广度优先搜索)
  - [8. 运行时就绪发现](#8-运行时就绪发现)
  - [9. 有限状态机 (FSM)](#9-有限状态机-fsm)
- [三、组合应用](#三组合应用)
  - [10. Super-step 并行执行模型](#10-super-step-并行执行模型)
  - [11. 图合并 — 局部重规划](#11-图合并--局部重规划)
  - [12. TF-IDF + 余弦相似度](#12-tf-idf--余弦相似度)
  - [13. while(tool_use) 主循环 — v5 隐式规划](#13-whiletool_use-主循环--v5-隐式规划)
  - [14. 指数退避重试算法 — v6 LLM 容错](#14-指数退避重试算法--v6-llm-容错)
  - [15. 三路由分类器 — simple/complex/emergent](#15-三路由分类器--simplecomplexemergent)
- [四、算法调用关系全景图](#四算法调用关系全景图)
- [五、学习路径建议](#五学习路径建议)

---

## 一、基础数据结构

### 1. 字典 dict — 万能查找表

#### 大白话理解

字典就像**通讯录**（**哈希表 / Hash Table**）——你知道一个人的名字（**键 / Key**），就能立刻找到他的电话号码（**值 / Value**），不需要从头翻到尾。

Python 的 `dict` 内部使用**哈希表**（Hash Table）实现，查找速度几乎不受数据量影响——不管你通讯录里有 10 个人还是 10 万个人，查找一个人的速度几乎一样快（**常数时间 O(1)**）。

#### 在 Demo 中的使用

整个 Demo 最核心的两个字典：

```python
# 1. 节点字典 — 通过节点 ID（Key）瞬间找到节点对象（Value）
self.nodes: dict[str, TaskNode] = {
    "goal_1":   TaskNode(...),
    "sub_1":    TaskNode(...),
    "act_1_1":  TaskNode(...),
}
# 查找 act_1_1 → O(1)（常数时间），不管有多少节点

# 2. 结果字典 — 通过节点 ID（Key）瞬间找到它的执行结果（Value）
node_results: dict[str, str] = {
    "act_1_1": "搜索到了 Python 并发相关资料...",
    "act_1_2": "代码运行结果: fibonacci(10) = 55",
}
```

**代码位置**：`dag/graph.py` — `TaskDAG.__init__()` / `schema.py` — `DAGState`

#### 速度对比

| 操作 | dict（哈希表 / Hash Table） | list（数组 / Array） |
|------|---------------------------|---------------------|
| 通过 key 查找 | **O(1)** 瞬间找到 | O(n) 从头翻到尾 |
| 判断 key 是否存在 | **O(1)** | O(n) |

> **O(1)**（读作「大 O 1」，**常数时间复杂度**）—— 不管数据多大，用时基本不变，像查字典直接翻到那一页。
>
> **O(n)**（读作「大 O n」，**线性时间复杂度**）—— 数据量翻倍，用时也翻倍，像一行一行找。

---

### 2. 集合 set — 去重和快速查找

#### 大白话理解

集合就像一个**签到表**（**哈希集合 / Hash Set**）——只记录"谁来过"（**元素唯一性 / Uniqueness**），不关心来了几次，也不关心顺序（**无序 / Unordered**）。最大的优点：查「某人是否来过」非常快（**O(1) 常数时间**）。

#### 在 Demo 中的使用

**用途 1 — BFS 中的已访问记录**（**防止重复访问 / Cycle Prevention**）：

```python
# dag/graph.py — get_downstream()
visited: set[str] = set()    # 签到表（visited 集合）：记录哪些节点已经看过了
while queue:
    nid = queue.popleft()
    if nid in visited:        # 这个节点签过到了？跳过！（O(1) 查询）
        continue
    visited.add(nid)          # 签到（O(1) 插入）
```

**用途 2 — 边去重**（**去重 / Deduplication**，防止重复的边干扰算法）：

```python
# agents/planner.py — _parse_dag()
seen: set[tuple] = set()      # 用 set 去重，利用元素唯一性
unique_edges = []
for e in edges:
    key = (e.source, e.target, e.edge_type.value)  # 构造唯一标识（复合键 / Composite Key）
    if key not in seen:        # O(1) 判重
        seen.add(key)
        unique_edges.append(e)
```

**用途 3 — 状态机的合法转移集**（**合法状态集合 / Valid State Set**）：

```python
# dag/state_machine.py
NodeStatus.PENDING: {NodeStatus.READY, NodeStatus.SKIPPED}
# ↑ set，查"能不能转移到 READY" → O(1)（集合的 in 操作）
```

---

### 3. 有向图 — 节点字典 + 边列表

#### 大白话理解

**图**（**Graph**）就像一张城市地图：

- **节点**（**Vertex / Node，顶点**）= 城市（比如北京、上海、广州）
- **边**（**Edge，边**）= 城市之间的路（比如北京→上海的高铁）
- **有向**（**Directed**）= 路是单行道（北京→上海 ≠ 上海→北京）
- **无环**（**Acyclic**）= 不能绕一圈回到起点（不能 A→B→C→A）

合在一起就是 **DAG**（**Directed Acyclic Graph，有向无环图**）。

在这个 Demo 里：
- 节点（Node）= 每个任务（搜索资料、写代码、跑测试）
- 边（Edge）= 任务之间的先后关系（先搜索资料，才能写分析报告）

#### 图有三种常见存储方式

想象你要在电脑里存储一张 5 个城市的地图：

| 存储方式 | 大白话 | 空间复杂度 | 查"北京的邻居" | Demo 选择 |
|----------|--------|-----------|---------------|----------|
| **邻接矩阵**（Adjacency Matrix） | 一张 5×5 的表格，格子里填"有路/没路" | O(V²) | O(V) 扫一整行 | ❌ |
| **邻接表**（Adjacency List） | 每个城市挂一个"邻居列表" | O(V+E) | O(出度) 直接看列表 | ✅ v2 优化 |
| **边列表**（Edge List） | 把所有路写成一个清单 | O(E) | O(E) 翻整个清单 | ✅ v1 原版 |

> V = 节点数（Vertex count），E = 边数（Edge count），出度（Out-degree）= 一个节点有几条出去的边

#### 对应代码

**代码位置**：`dag/graph.py` — `TaskDAG.__init__()`

```python
def __init__(self, task, nodes, edges, context=""):
    self.nodes = nodes    # dict（哈希表）：节点字典，O(1) 随机访问
    self.edges = edges    # list（列表）：边列表，遍历 O(E)
    self.state = DAGState(task=task, context=context)
    
    # v2 优化：预构建 DEPENDENCY 边的邻接表，将 BFS/拓扑排序从 O(V*E) 优化到 O(V+E)
    self._dep_adjacency: dict[str, list[str]] = {}  # source -> [targets]
    self._reverse_dep_adjacency: dict[str, list[str]] = {}  # target -> [sources]
    self._rebuild_adjacency()
```

查找某个节点的依赖（**入边查询 / Inbound Edge Query**）——v1 需要翻整个边列表过滤，v2 使用反向邻接表 O(1)：

```python
def get_dependency_ids(self, node_id: str) -> list[str]:
    # v1: O(E) 翻遍所有边
    # return [e.source for e in self.edges if e.target == node_id and e.edge_type == EdgeType.DEPENDENCY]
    
    # v2: O(1) 直接从反向邻接表获取
    return list(self._reverse_dep_adjacency.get(node_id, []))
```

> **为什么 v1 选边列表？** Demo 的节点和边一般只有几十个（**小规模图 / Small-scale Graph**），翻整个清单也很快。就像你只有 10 个好友，翻通讯录和查字典差别不大。v2 优化后，节点上百也能高效运行。

---

### 4. 带类型的多重边

#### 大白话理解

普通地图上，两个城市之间只有一条路（**简单图 / Simple Graph**）。但在这个 Demo 里，两个任务之间可以有**多种不同关系**——形成**多重图**（**Multigraph / Labeled Multigraph，有标签的多重有向图**）。就像北京和上海之间既有高铁（依赖关系），又有航班（条件关系），还有退票渠道（回滚关系）。

#### 三种边类型（Edge Types）

**代码位置**：`schema.py` — `EdgeType`

```python
class EdgeType(str, Enum):
    DEPENDENCY = "dependency"    # 依赖边："我做完你才能做"（前置依赖 / Prerequisite）
    CONDITIONAL = "conditional"  # 条件边："我做完，看结果再决定你做不做"（条件分支 / Conditional Branch）
    ROLLBACK = "rollback"        # 回滚边："我失败了，你来善后"（回滚机制 / Rollback Mechanism）
```

**关键设计**：不同的算法只关注不同类型的边（**按类型过滤遍历 / Type-filtered Traversal**）。

| 我在做什么 | 只看哪种边 | 类比 | 专业术语 |
|-----------|-----------|------|---------|
| 算执行顺序 | DEPENDENCY | 只看「谁先谁后」 | 拓扑排序（Topological Sort） |
| 找谁能执行了 | DEPENDENCY | 只检查「前置任务做完了吗」 | 约束满足（Constraint Satisfaction） |
| 决定分支走不走 | CONDITIONAL | 只看「条件边的结果」 | 条件评估（Condition Evaluation） |
| 失败后谁来善后 | ROLLBACK | 只看「善后方案」 | 回滚查找（Rollback Lookup） |
| 找下游子树 | DEPENDENCY | 只顺着「依赖方向」往下找 | 可达性分析（Reachability Analysis） |

---

### 5. 有根树 — parent_id 父指针

#### 大白话理解

公司的组织架构就是一棵**树**（**Rooted Tree，有根树**）——每个人只有**一个直属上级**（**父节点 / Parent Node**），但可以有多个下属（**子节点 / Child Node**）。

在 Demo 里，任务被组织成三层树结构（**层级结构 / Hierarchy**）：
- **Goal**（目标层）= CEO（**根节点 / Root Node**），只有一个
- **SubGoal**（子目标层）= 部门经理（**中间节点 / Internal Node**），有好几个
- **Action**（动作层）= 基层员工（**叶节点 / Leaf Node**），是真正干活的人

#### 对应代码

**代码位置**：`schema.py` — `TaskNode`

```python
class TaskNode(BaseModel):
    id: str
    node_type: NodeType        # 三种角色：GOAL / SUBGOAL / ACTION
    parent_id: str | None      # 我的上级是谁（父指针 / Parent Pointer）
```

这叫**父指针表示法**（**Parent Pointer Representation**）——每个节点只记「我的上级是谁」，不记「我有哪些下属」。

| 操作 | 速度 | 为什么 | 专业说法 |
|------|------|--------|---------|
| 找我的上级 | 瞬间 O(1) | 直接读 `parent_id` | 父节点查询（Parent Lookup） |
| 找我的所有下属 | 较慢 O(V) | 要问所有人："你上级是不是我？" | 子节点扫描（Child Scan） |

找下属的代码（`dag/executor.py`）：

```python
# 找 node 的所有直接下属（子节点 / Children）
children = [
    n for n in dag.nodes.values()    # 扫描所有节点 O(V)
    if n.parent_id == node.id         # 你的上级（Parent）是我吗？
]
```

#### 两个用途

**用途 1 — 自底向上汇报**（**自底向上聚合 / Bottom-up Aggregation**，`_complete_structural_nodes`）：当所有员工（叶节点）都完成了，自动标记部门经理（中间节点）完成；所有部门经理完成了，自动标记 CEO（根节点）完成。

**用途 2 — 可视化展示**（**树的层序遍历 / Level-order Traversal**，`main.py` — `_build_dag_tree()`）：在终端画出漂亮的树形结构。

```
根节点(Goal)：分析 Python 并发模型 (completed)
├── 中间节点(SubGoal)：收集资料 (completed) conf=0.9 risk=low
│   ├── 叶节点(Action)：搜索论文 (completed)
│   └── 叶节点(Action)：搜索博客 (completed)
└── 中间节点(SubGoal)：编写报告 (running) conf=0.7 risk=medium
    └── 叶节点(Action)：撰写初稿 (running)
```

---

### 快照列表 — Checkpoint 机制

#### 大白话理解

就像游戏里的**存档**（**状态快照 / State Snapshot**）——每打完一关（**每个 Super-step 结束后**）就自动保存，如果后面的关卡挂了，可以读档回到之前的状态（**状态恢复 / State Recovery**）。

#### 对应代码

**代码位置**：`dag/graph.py`

```python
class TaskDAG:
    def __init__(self, ...):
        self._checkpoints: list[dict] = []  # 存档列表（有序快照列表 / Ordered Snapshot List）

    def save_checkpoint(self) -> None:
        """每完成一轮 Super-step，自动存一次档（序列化 / Serialization）"""
        self._checkpoints.append(self.to_dict())  # 把当前状态打包成字典存起来
```

`to_dict()` 把整个 DAG（所有节点的状态、所有执行结果）打包成一个 Python 字典（**序列化 / Serialization**）。

数据结构上就是一个 `list`（**列表 / Array**），每个元素是一次完整快照（**Snapshot**）。越新的快照在越后面，类似**时间线**（**Append-only Log / 追加日志**）。这也是 LangGraph 用于「时间旅行调试」（**Time-travel Debugging**）的灵感来源。

---

### 队列 / 双端队列 (Deque)

#### 大白话理解

**队列**（**Queue**）就像排队买奶茶——**先来的先买，后来的后买**（**FIFO / First In First Out，先进先出**）。

Python 里用 `collections.deque`（**双端队列 / Double-ended Queue**）而不是普通 `list` 做队列，原因很简单：

| 操作 | `deque`（双端队列） | `list`（数组） |
|------|-------------------|---------------|
| 从右边加入（`append`，**入队 / Enqueue**） | 瞬间 O(1) | 瞬间 O(1) |
| 从左边取出（`popleft`，**出队 / Dequeue**） | **瞬间 O(1)** | **很慢 O(n)**——取走第一个后，后面所有元素都要往前挪一位（**数组平移 / Array Shift**） |

想象排队：`deque` 是取号排队（叫到号直接走，**O(1) 出队**），`list` 是肩并肩排队（前面走了后面全部往前挪，**O(n) 出队**）。

BFS 和 Kahn 算法都需要频繁「从左边取出」（**出队操作**），所以**必须用 `deque`**。

---

### TodoItem/TodoList — v5 隐式规划的数据结构

#### 大白话理解

v5 引入了**隐式规划**（**Emergent Planning**）模式，灵感来自 Claude Code。与 v2/v3 的显式 DAG 规划不同，v5 不预先生成完整的执行图，而是通过一个**动态 TODO 列表**来跟踪任务进度。

就像你写论文：
- **显式规划**（v2/v3）：先写完整大纲（第一章、第二章、第三章...），然后逐章完成
- **隐式规划**（v5）：先写 1-3 个高层 TODO（"调研相关文献"、"撰写初稿"、"润色修改"），在执行过程中根据需要动态添加新 TODO

#### 数据结构定义

**代码位置**：`schema.py`

```python
class TodoStatus(str, Enum):
    """
    TODO 项的状态枚举。
    """
    PENDING = "pending"           # 等待执行
    IN_PROGRESS = "in_progress"   # 正在执行
    COMPLETED = "completed"       # 已完成
    BLOCKED = "blocked"           # 被阻塞（依赖未完成）

class TodoItem(BaseModel):
    """
    TODO 列表中的单个任务项。
    """
    id: int = Field(description="TODO 唯一 ID")                     # TODO 唯一标识
    description: str = Field(description="TODO 描述")                # TODO 的具体内容
    status: TodoStatus = TodoStatus.PENDING                         # 当前状态
    dependencies: list[int] = Field(default_factory=list, description="前置 TODO ID 列表")  # 依赖的其他 TODO
    result: str | None = None                                        # 执行结果
    created_at: float = Field(default_factory=time.time, description="创建时间戳")  # 创建时间戳
    updated_at: float = Field(default_factory=time.time, description="最后更新时间戳")  # 最后更新时间戳

class TodoList(BaseModel):
    """
    TODO 列表容器，管理所有 TODO 项。
    """
    task: str = Field(description="原始任务描述")                     # 原始用户任务
    todos: dict[int, TodoItem] = Field(default_factory=dict, description="所有 TODO 项，key 为 id")  # TODO 字典
    next_id: int = Field(default=1, description="下一个可用 TODO ID")  # 下一个可用 TODO ID

    def get_pending_todos(self) -> list[TodoItem]:
        """获取所有可执行的 TODO 项（状态为 PENDING 或 IN_PROGRESS）"""
        return [
            todo for todo in self.todos.values()
            if todo.status in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS)
        ]

    def get_ready_todos(self) -> list[TodoItem]:
        """返回所有依赖已满足的 TODO（可用于执行）"""
        ready = []
        for todo in self.todos.values():
            if todo.status != TodoStatus.PENDING:
                continue
            # 检查所有依赖是否都已完成
            if not all(dep_id in self.todos for dep_id in todo.dependencies):
                continue  # 依赖不存在，跳过此TODO
            deps_completed = all(
                self.todos[dep_id].status == TodoStatus.COMPLETED
                for dep_id in todo.dependencies
            )
            if deps_completed:
                ready.append(todo)
        return ready
```

#### 关键设计点

1. **字典存储**：使用 `dict[int, TodoItem]` 而非 `list`，通过 ID O(1) 快速查找
2. **依赖关系**：每个 TODO 有 `dependencies` 列表，类似 DAG 的依赖边
3. **动态演化**：TODO 列表在执行过程中可以动态添加新项，不像 DAG 预先固定

#### 与 DAG 的对比

| 特性 | DAG 规划（v2/v3） | TODO 隐式规划（v5） |
|------|------------------|-------------------|
| 规划时机 | 执行前一次性生成完整图 | 执行中动态演化 |
| 数据结构 | TaskNode + TaskEdge | TodoItem（扁平列表） |
| 状态管理 | NodeStateMachine（7 种状态） | TodoStatus（4 种状态） |
| 适用场景 | 复杂多层级任务 | 简单线性或轻微分支任务 |
| 代码复杂度 | 高（需要图算法） | 低（只需列表操作） |

---

## 二、核心算法

### 6. Kahn 算法 (拓扑排序)

#### 大白话理解

**场景**：你这学期要选 6 门课，但有些课需要先修课（**前置依赖 / Prerequisite**）。比如：

- 「高等数学」没有前置要求（**入度=0 / In-degree=0**）
- 「线性代数」需要先修「高等数学」（**入度=1**）
- 「机器学习」需要先修「线性代数」和「概率论」（**入度=2**）

问题：**怎么安排选课顺序，保证每门课的前置要求都已经修过？**

这就是**拓扑排序**（**Topological Sort**）要解决的问题。Kahn 算法的核心思路非常直觉：

1. 找出所有「没有前置要求」的课（**入度=0 / In-degree=0**）→ 这些可以先选
2. 选完后，它们「解锁」的课前置要求少了一个（**入度减一 / Decrement In-degree**）
3. 如果某门课所有前置要求都被解锁了（**入度降为 0**）→ 它也可以选了
4. 重复，直到所有课都被选完

> **入度**（**In-degree**）= "有几条边指向我" = "有几门前置课"。入度为 0 就是"没有前置要求，随时可以修"。

#### 对应代码

**代码位置**：`dag/graph.py` — `topological_sort()`

```python
def topological_sort(self) -> list[str]:
    """
    Kahn's algorithm — returns node IDs in a valid execution order.
    Only considers DEPENDENCY edges. Uses pre-built adjacency list for O(V+E).

    Kahn 算法 —— 返回节点 ID 的合法拓扑执行顺序。
    仅考虑 DEPENDENCY 类型的边，使用预构建邻接表实现 O(V+E) 复杂度。
    """
    # 统计每个节点的入度（有多少 DEPENDENCY 边指向它）
    in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
    for source, targets in self._dep_adjacency.items():
        for target in targets:
            in_degree[target] += 1

    # 将入度为 0 的节点（无前置依赖）加入队列
    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    result: list[str] = []

    while queue:
        nid = queue.popleft()
        result.append(nid)
        # 通过邻接表找出下游节点，将其入度减 1
        for target in self._dep_adjacency.get(nid, []):
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)

    if len(result) != len(self.nodes):
        logger.warning("[DAG] Cycle detected! Topological sort incomplete.")
    return result
```

#### 算法复杂度

- **时间复杂度**：O(V + E)，V 是节点数，E 是边数（使用邻接表）
- **空间复杂度**：O(V)，用于存储入度表和队列

#### 实际应用

在 Demo 中，拓扑排序主要用于：
1. **验证 DAG 合法性**：如果排序结果节点数 < 总节点数，说明有环（非法）
2. **可视化展示**：按拓扑顺序打印任务，让用户理解执行流程
3. **调试**：快速找出哪些节点"阻塞"了执行

> **注意**：实际执行时，DAGExecutor 使用的是「运行时就绪发现」（见下文），而非预先生成的拓扑序列。拓扑排序主要用于验证和可视化。

---

### 7. BFS (广度优先搜索)

#### 大白话理解

**BFS**（**Breadth-First Search，广度优先搜索**）就像**波浪扩散**——从起点出发，先访问所有距离为 1 的节点（第一层），再访问所有距离为 2 的节点（第二层），依此类推。

类比：
- **找快递员**：先问邻居（距离 1），邻居不知道，再问邻居的邻居（距离 2），一层层往外扩散
- **社交网络**：找「朋友的朋友的朋友」——先找你的直接朋友，再找你朋友的朋友

#### 对应代码

**代码位置**：`dag/graph.py` — `get_downstream()`

```python
def get_downstream(self, node_id: str) -> list[str]:
    """
    Return all node IDs downstream of `node_id` via BFS on DEPENDENCY edges.
    通过 BFS 遍历 DEPENDENCY 边，返回 `node_id` 所有下游节点 ID。
    用于失败时级联跳过整个子树。
    使用预构建的邻接表，时间复杂度 O(V+E)。
    """
    visited: set[str] = set()
    queue: deque[str] = deque()

    # 通过邻接表找直接子节点
    queue.extend(self._dep_adjacency.get(node_id, []))

    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        for target in self._dep_adjacency.get(nid, []):
            queue.append(target)

    return list(visited)
```

#### 算法复杂度

- **时间复杂度**：O(V + E），每个节点和边最多访问一次
- **空间复杂度**：O(V)，用于存储 `visited` 集合和队列

#### 实际应用

在 Demo 中，BFS 主要用于：
1. **找下游子树**：当某个节点失败或被跳过时，级联标记所有下游节点为 SKIPPED
2. **可达性分析**：判断节点 A 是否能到达节点 B
3. **依赖传播**：追踪某个节点的结果会影响哪些后续节点

---

### 8. 运行时就绪发现

#### 大白话理解

**运行时就绪发现**（**Runtime Ready Discovery**）是 DAGExecutor 的核心动态特性——**不预先生成固定的执行顺序表，而是在每一轮 Super-step 开始时，扫描当前所有节点的状态，找出哪些已经"准备好了"**。

类比：
- **显式调度**（传统）：提前写好日程表（9:00-10:00 写代码，10:00-11:00 写文档），严格按表执行
- **就绪发现**（Demo）：每轮问自己"现在哪些任务的前置条件都满足了？"，然后并行执行这些任务

#### 对应代码

**代码位置**：`dag/graph.py` — `get_ready_nodes()`

```python
def get_ready_nodes(self) -> list[TaskNode]:
    """
    Return nodes that can execute now: PENDING or READY with all
    DEPENDENCY predecessors COMPLETED.

    返回当前可以执行的节点：状态为 PENDING 或 READY，
    且所有 DEPENDENCY 类型的前置节点均已 COMPLETED。

    In LangGraph terms, these are the nodes that would run in the
    next "super-step" — a round of parallel execution.
    在 LangGraph 的术语中，这些节点将在下一个「Super-step」（并行执行轮次）中运行。
    """
    eligible = {NodeStatus.PENDING, NodeStatus.READY}  # 可被调度的状态集合
    ready = []
    for node in self.nodes.values():
        if node.status not in eligible:
            continue
        # 检查所有依赖是否都已完成
        # 核心逻辑是：不查看任何预定义的执行顺序表，而是在运行时扫描当前所有节点状态，发现谁的依赖已经全部满足。
        deps = self.get_dependency_ids(node.id)
        if all(
            d in self.nodes and self.nodes[d].status == NodeStatus.COMPLETED
            for d in deps
        ):
            ready.append(node)
    return ready
```

#### 算法复杂度

- **时间复杂度**：O(V × D)，V 是节点数，D 是平均依赖数
- **空间复杂度**：O(1)，不需要额外存储

#### 动态性体现

**场景**：假设有 3 个任务：
- `act_1_1`：搜索论文（无依赖）
- `act_1_2`：搜索博客（无依赖）
- `act_2_1`：撰写报告（依赖 `act_1_1` 和 `act_1_2`）

**Super-step 1**：
- 扫描发现 `act_1_1` 和 `act_1_2` 就绪（无依赖）
- 并行执行这两个任务

**Super-step 2**：
- 假设 `act_1_1` 完成，`act_1_2` 还在跑
- 扫描发现 `act_2_1` **未就绪**（还差 `act_1_2`）
- 没有新任务执行，等待

**Super-step 3**：
- `act_1_2` 完成
- 扫描发现 `act_2_1` 就绪（两个依赖都完成了）
- 执行 `act_2_1`

> **关键点**：每轮的执行列表都是**动态计算**的，不是预先生成的。这允许系统适应运行时的各种情况（失败、跳过、超时等）。

---

### 9. 有限状态机 (FSM)

#### 大白话理解

**有限状态机**（**Finite State Machine，FSM**）就像**红绿灯**——只能按规定的顺序切换状态（绿 → 黄 → 红 → 绿），不能跳变（绿直接变红）。

在 Demo 中，每个任务节点都有严格的生命周期状态转移规则，防止进入非法状态。

#### 状态转移图

**代码位置**：`dag/state_machine.py`

```
    PENDING ──> READY ──> RUNNING ──> COMPLETED   (happy path / 正常路径)
                                  ──> FAILED ──> ROLLED_BACK
                                            ──> PENDING (retry / 重试)
                                  ──> SKIPPED  (structural node: all children skipped / 结构节点：子节点全被跳过)
    Any non-terminal ──────────────> SKIPPED       (conditional branch not taken / 条件分支未满足)
```

#### 对应代码

**代码位置**：`dag/state_machine.py`

```python
# 完整的状态转移表——一目了然地看清所有合法转移路径。
VALID_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    NodeStatus.PENDING:     {NodeStatus.READY, NodeStatus.SKIPPED},
    NodeStatus.READY:       {NodeStatus.RUNNING, NodeStatus.SKIPPED},
    NodeStatus.RUNNING:     {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED},
    NodeStatus.FAILED:      {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED, NodeStatus.PENDING},
    # Terminal states — no further transitions allowed
    # 终态——不允许任何进一步转移
    NodeStatus.COMPLETED:   set(),
    NodeStatus.SKIPPED:     set(),
    NodeStatus.ROLLED_BACK: set(),
}

class NodeStateMachine:
    def transition(self, node: TaskNode, new_status: NodeStatus) -> None:
        """
        Apply a state transition. Raises InvalidTransitionError if illegal.
        应用状态转移。若转移非法则抛出 InvalidTransitionError。

        这等价于 LangGraph 内部 Pregel 运行时所做的事——
        确保节点只能经过合法状态路径。
        """
        if not self.can_transition(node, new_status):
            raise InvalidTransitionError(
                f"Node '{node.id}': cannot transition from {node.status.value} to {new_status.value}. "
                f"Valid targets: {sorted(s.value for s in VALID_TRANSITIONS.get(node.status, set()))}"
            )

        old_status = node.status
        node.status = new_status  # 应用状态变更

        logger.debug("[SM] %s: %s -> %s", node.id, old_status.value, new_status.value)

        if self._on_transition:
            try:
                self._on_transition(node.id, old_status, new_status)
            except Exception:
                logger.debug("[SM] UI callback error for node %s", node.id, exc_info=True)
```

#### 实际应用

在 Demo 中，状态机确保：
1. **合法性**：防止代码错误导致节点进入非法状态
2. **一致性**：所有状态变更都通过同一个入口，便于日志和监控
3. **事件驱动**：每次状态转移都会触发 UI 更新（通过 `on_transition` 回调）

---

## 三、组合应用

### 10. Super-step 并行执行模型

#### 大白话理解

**Super-step**（**超级步**）是 Pregel 算法（Google 的图计算框架）的核心概念——将图计算分成多个**离散轮次**，每轮并行执行所有"就绪"的节点，然后同步状态，进入下一轮。

类比：
- **传统串行**：一个人做饭，切菜 → 炒菜 → 煮饭，一步步来
- **Super-step 并行**：三个人分工，第一轮：A 切菜、B 炒菜、C 煮饭（同时进行），第二轮：A 装盘、B 摆桌、C 倒水（同时进行）

#### 对应代码

**代码位置**：`dag/executor.py` — `execute()`

```python
async def execute(self, dag: TaskDAG) -> str:
    """
    Execute the full DAG and return the compiled output.
    执行完整 DAG 并返回汇总输出字符串。

    The loop runs in discrete super-steps until all nodes reach
    a terminal state (COMPLETED / SKIPPED / ROLLED_BACK).
    循环以离散 Super-step 方式运行，直到所有节点到达终态。
    """
    dag._sm = self._sm
    dag.refresh_ready_states()  # 初始化：将满足条件的 PENDING 节点提升为 READY
    step = 0
    
    while not dag.is_complete():
        step += 1
        ready = dag.get_ready_nodes()
        
        # 只执行 ACTION 节点（GOAL/SUBGOAL 是结构性分组，不直接执行）
        actionable = [n for n in ready if n.node_type == NodeType.ACTION]
        if not actionable:
            # 结构节点处理...
            continue

        # 限制每轮并行节点数，避免资源竞争
        batch = actionable[:self._max_parallel]

        # --- Super-step: parallel execution with timeout ---
        # 通过 asyncio.gather 并行执行当前批次节点
        results = await asyncio.gather(*[
            self._run_node_with_timeout(node, dag) for node in batch
        ])

        # --- Merge results + validate + handle failures ---
        for node, result in zip(batch, results):
            dag.state.merge_result(node.id, result.output)
            node.result = result.output
            
            if result.success:
                passed = await self._check_exit_criteria(node, result)
                if passed:
                    self._sm.transition(node, NodeStatus.COMPLETED)
                else:
                    self._sm.transition(node, NodeStatus.FAILED)
                    await self._handle_failure(node, dag)
            else:
                self._sm.transition(node, NodeStatus.FAILED)
                await self._handle_failure(node, dag)

        # --- Evaluate conditional edges ---
        self._process_conditions(dag)

        # --- 为下一轮 Super-step 提升就绪节点 ---
        dag.refresh_ready_states()

        # --- 自动完成所有子节点已终态的结构性父节点 ---
        self._complete_structural_nodes(dag)

        # --- 保存检查点（灵感来自 LangGraph）---
        dag.save_checkpoint()

        logger.info("[DAGExecutor] Super-step %d done. %s", step, dag.summary())

    return self._compile_output(dag)
```

#### 算法流程

```
每轮 Super-step 的流程：
  1. 找出所有就绪节点（get_ready_nodes）
  2. 过滤出 ACTION 节点（GOAL/SUBGOAL 不执行）
  3. 限制并行数（MAX_PARALLEL_NODES）
  4. 通过 asyncio.gather 并行执行
  5. 将结果合并到 DAGState（集中式状态）
  6. 验证每个节点的完成判据（exit criteria）
  7. 处理失败节点（回滚 + 跳过下游子树）
  8. 评估条件边（决定分支是否激活）
  9. 提升新的就绪节点（refresh_ready_states）
  10. 自动完成结构性父节点（所有子节点完成则父节点完成）
  11. 保存 Checkpoint
  12. 重复直到所有节点到达终态
```

#### 关键特性

1. **动态性**：每轮的执行列表都是运行时计算的，适应失败、跳过等情况
2. **并行性**：同一 Super-step 内多个节点同时执行，利用异步 I/O
3. **容错性**：失败节点自动触发回滚和跳过下游子树
4. **可观测性**：每轮 Super-step 都有日志和事件回调

---

### 11. 图合并 — 局部重规划

#### 大白话理解

**图合并**（**Graph Merging**）是 v3 的核心特性——当某个节点失败或执行结果不理想时，**局部替换该节点的下游子图**，而不是重新规划整个任务。

类比：
- **全局重规划**（v2）：写论文时，发现第二章有问题，直接扔掉整篇论文重新写
- **局部重规划**（v3）：只重写第二章，其他章节保持不变

#### 对应代码

**代码位置**：`agents/planner.py` — `replan_subtree()`

```python
async def replan_subtree(
    self,
    original_dag: TaskDAG,
    failed_node_id: str,
    feedback: str,
) -> TaskDAG:
    """
    Re-plan the subtree rooted at the failed node.
    重新规划以失败节点为根的子树。

    This is called by the Orchestrator when reflection fails.
    Only the failed subtree is replanned, preserving all completed work.
    这由 Orchestrator 在反思失败时调用。
    仅重规划失败的子树，保留所有已完成的工作。

    Args:
        original_dag: The original DAG with some completed nodes
        failed_node_id: The ID of the failed node (root of subtree to replan)
        feedback: Feedback from the Reflector to guide the replanning

    Returns:
        A new DAG with the failed subtree replaced
    """
    # 实现细节：调用 LLM 重新规划失败的子树，然后合并到原 DAG 中
    # 保留所有已完成节点的状态和结果
    # ... (具体实现略)
```

#### 算法复杂度

- **时间复杂度**：O(V + E)，需要遍历所有节点和边
- **空间复杂度**：O(V + E)，需要创建新的 DAG 实例

#### 实际应用

在 Demo 中，图合并用于：
1. **自适应规划**：当某个子目标失败时，重新规划该子目标的下游任务
2. **任务调整**：用户中途修改需求，局部替换相关子图
3. **容错恢复**：某个节点失败后，生成替代方案

---

### 12. TF-IDF + 余弦相似度

#### 大白话理解

**TF-IDF**（**Term Frequency - Inverse Document Frequency，词频-逆文档频率**）是信息检索的经典算法——通过计算词语在文档中的重要性，找出与查询最相关的文档。

类比：
- **TF（词频）**：某词在当前文档中出现次数越多，越重要（比如"Python"在编程文档中出现频繁）
- **IDF（逆文档频率）**：某词在所有文档中出现次数越少，越独特（比如"并发"比"的"更有区分度）
- **TF-IDF**：TF × IDF，综合考量"频繁度"和"独特性"

**余弦相似度**（**Cosine Similarity**）衡量两个向量的方向相似度，用于比较查询和文档的匹配程度。

#### 对应代码

**代码位置**：`knowledge/retriever.py`

```python
def _compute_tf(text: str) -> dict[str, float]:
    """
    Compute term frequency (normalized).
    计算词频（归一化）：词频 / 最高词频，使 TF 值在 [0,1] 之间。
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    if not words:
        return {}
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    max_freq = max(freq.values())
    return {w: c / max_freq for w, c in freq.items()}  # 归一化

def _build_index(self) -> None:
    """
    Load documents, chunk them, and compute TF-IDF.
    加载文档，切分为块，计算 TF-IDF 索引。
    """
    # ... 加载文档并切分 ...
    
    # Compute IDF（计算 IDF：log((总文档数+1) / (含该词的文档数+1)) + 1，加 1 做平滑）
    n_docs = len(self._chunks)
    doc_freq: dict[str, int] = {}
    for chunk in self._chunks:
        words = set(self._tokenize(chunk["text"]))  # 用集合去重，每块中每个词只计一次
        for w in words:
            doc_freq[w] = doc_freq.get(w, 0) + 1

    self._idf = {
        w: math.log((n_docs + 1) / (df + 1)) + 1
        for w, df in doc_freq.items()
    }

    # Compute TF-IDF vectors for each chunk（为每个块计算 TF-IDF 向量）
    for chunk in self._chunks:
        tf = self._compute_tf(chunk["text"])
        tfidf = {w: freq * self._idf.get(w, 1.0) for w, freq in tf.items()}
        self._tf_idf.append(tfidf)

def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """
    Cosine similarity between two sparse vectors.
    计算两个稀疏向量的余弦相似度。
    余弦相似度 = 点积 / (向量A的模 * 向量B的模)。
    只计算共同词（稀疏向量优化），未出现的词贡献为 0。
    """
    common = set(a.keys()) & set(b.keys())
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)           # 点积（只对共同词）
    norm_a = math.sqrt(sum(v * v for v in a.values()))  # 向量 A 的 L2 范数
    norm_b = math.sqrt(sum(v * v for v in b.values()))  # 向量 B 的 L2 范数
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
    """
    Retrieve the top-K most relevant chunks for a query.
    检索与查询最相关的 top-K 个文档块。
    """
    if not self._chunks:
        return []

    top_k = top_k or self.top_k
    # 计算查询向量的 TF-IDF 表示
    query_tf = self._compute_tf(query)
    query_vec = {w: freq * self._idf.get(w, 1.0) for w, freq in query_tf.items()}

    # 与每个文档块计算余弦相似度
    scores: list[tuple[float, int]] = []
    for idx, chunk_vec in enumerate(self._tf_idf):
        score = self._cosine_similarity(query_vec, chunk_vec)
        if score > 0:
            scores.append((score, idx))

    scores.sort(key=lambda x: x[0], reverse=True)  # 按相似度降序排列

    results = []
    for score, idx in scores[:top_k]:
        results.append({
            "text": self._chunks[idx]["text"],
            "source": self._chunks[idx]["source"],
            "score": round(score, 4),
        })
    return results
```

#### 算法复杂度

- **索引构建**：O(N × L)，N 是文档块数，L 是平均块长度
- **查询**：O(N × L)，需要计算查询向量与每个文档块的相似度

#### 实际应用

在 Demo 中，TF-IDF 用于：
1. **知识检索**：从本地文档库中检索与任务相关的知识片段
2. **上下文注入**：将检索到的知识注入到 LLM prompt 中，增强智能体能力
3. **RAG（检索增强生成）**：结合 LLM 生成能力，提供更准确的答案

---

### 13. while(tool_use) 主循环 — v5 隐式规划

#### 大白话理解

**while(tool_use) 主循环**是 v5 隐式规划器的核心——不预先生成完整的执行计划，而是通过一个**持续的主循环**，让 LLM 自主决定下一步做什么，并在执行过程中动态管理 TODO 列表。

类比：
- **显式规划**（v2/v3）：写论文前先写完整大纲，然后逐章完成
- **隐式规划**（v5）：只写 1-3 个高层 TODO，然后开始写，写的过程中发现需要查资料就加一个 TODO，发现需要画图就再加一个 TODO

#### 对应代码

**代码位置**：`agents/emergent_planner.py` — `execute()`

```python
async def execute(self, task: str, context: str = "") -> str:
    """
    Claude Code-style emergent planning and execution.

    Flow:
      1. Initialize TODO list from task description
      2. while has_pending_todos:
         - Select next ready TODO
         - Run ReAct loop for that TODO
         - Update TODO list based on progress
         - Add new TODOs if discovered
      3. Compile final answer from all completed TODOs

    流程：
      1. 从任务描述初始化 TODO 列表
      2. 当有待执行 TODO 时循环：
         - 选择下一个就绪 TODO
         - 为该 TODO 运行 ReAct 循环
         - 根据进度更新 TODO 列表
         - 发现新工作时添加 TODO
      3. 从所有已完成的 TODO 汇总最终答案
    """
    self._emit("phase", "Initializing emergent planning...")

    # 初始化 TODO 列表
    self._todo_list = TodoList(task=task)
    await self._init_todo_list(task, context)

    iteration = 0
    all_results: list[StepResult] = []

    # 主循环：while(has_pending_todos)
    while self._todo_list.get_pending_todos():
        iteration += 1
        self._emit("phase", f"Emergent planning iteration {iteration}...")

        # 检查是否超过最大迭代次数
        if iteration > self.max_iterations:
            logger.warning("[EmergentPlanner] Hit max iterations (%d)", self.max_iterations)
            break

        # 选择下一个就绪 TODO
        ready_todos = self._todo_list.get_ready_todos()
        if not ready_todos:
            # 没有就绪 TODO 但还有待执行的 -> 有阻塞
            logger.warning(
                "[EmergentPlanner] No ready TODOs but %d pending. Blocked?",
                len([t for t in self._todo_list.todos.values() if t.status == TodoStatus.PENDING])
            )
            # 强制选择一个 PENDING 的 TODO
            pending = [t for t in self._todo_list.todos.values() if t.status == TodoStatus.PENDING]
            if pending:
                ready_todos = [pending[0]]
            else:
                break

        # 选择第一个就绪 TODO
        current_todo = ready_todos[0]
        self._emit("todo_start", {"todo": current_todo})

        # 为该 TODO 执行 ReAct 循环
        result = await self._execute_todo(current_todo)
        all_results.append(result)

        # 更新 TODO 状态
        if result.success:
            self._todo_list.mark_completed(current_todo.id, result.output)
            self._emit("todo_complete", {"todo": current_todo, "result": result})
        else:
            # 失败时将 TODO 状态回退为 PENDING 以便重试
            logger.warning("[EmergentPlanner] TODO %d failed: %s", current_todo.id, result.output[:200])
            self._todo_list.mark_pending(current_todo.id)
            self._emit("todo_failed", {"todo": current_todo, "result": result})

        # 检查是否需要添加新 TODO（基于执行结果）
        await self._update_todo_list(result)

        # 显示当前 TODO 列表状态
        self._emit("todo_list_update", self._get_todo_summary())

    # 汇总所有已完成 TODO 的结果
    final_answer = self._compile_answer(task, all_results)
    self._emit("phase", "Emergent planning completed.")
    return final_answer
```

#### 算法流程

```
隐式规划主循环流程：
  1. 初始化 TODO 列表（1-3 个高层 TODO）
  2. while has_pending_todos:
     a. 选择下一个就绪 TODO（依赖已满足）
     b. 为该 TODO 运行 ReAct 循环（think_with_tools）
     c. 根据执行结果更新 TODO 状态
     d. 如果发现新工作，动态添加新 TODO
     e. 检查是否所有 TODO 都完成
  3. 汇总所有已完成 TODO 的结果，生成最终答案
```

#### 关键特性

1. **动态性**：TODO 列表在执行过程中动态演化，可以随时添加新项
2. **灵活性**：LLM 自主决定下一步做什么，不需要预先规划
3. **简单性**：不需要复杂的图算法，只需简单的列表操作
4. **适应性**：适合简单线性或轻微分支的任务

#### 与 DAG 规划的对比

| 特性 | DAG 规划（v2/v3） | TODO 隐式规划（v5） |
|------|------------------|-------------------|
| 规划时机 | 执行前一次性生成 | 执行中动态演化 |
| 数据结构 | TaskNode + TaskEdge | TodoItem（扁平列表） |
| 主循环 | Super-step 并行 | while(tool_use) 串行 |
| 适用场景 | 复杂多层级任务 | 简单线性任务 |
| 代码复杂度 | 高（需要图算法） | 低（只需列表操作） |

---

### 14. 指数退避重试算法 — v6 LLM 容错

#### 大白话理解

**指数退避**（**Exponential Backoff**）是网络编程中的经典容错策略——当请求失败时，不是立即重试，而是等待一段时间后再试，且每次等待时间**指数增长**。

类比：
- **固定间隔重试**：失败后每 1 秒重试一次 → 可能导致服务器雪崩（大量请求同时到达）
- **指数退避**：第一次等 1 秒，第二次等 2 秒，第三次等 4 秒，第四次等 8 秒 → 给服务器"喘息"时间，避免雪崩

#### 对应代码

**代码位置**：`llm/client.py` — `chat()` 和 `chat_with_tools()`

```python
RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIError)

class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        retry_enabled: bool | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
    ):
        self.model = model or config.LLM_MODEL
        self._client = AsyncOpenAI(
            base_url=base_url or config.LLM_BASE_URL,
            api_key=api_key or config.LLM_API_KEY,
        )

        self.retry_enabled = retry_enabled if retry_enabled is not None else config.LLM_RETRY_ENABLED
        self.max_retries = max_retries if max_retries is not None else config.LLM_RETRY_MAX_ATTEMPTS
        self.backoff_factor = backoff_factor if backoff_factor is not None else config.LLM_RETRY_BACKOFF_FACTOR

    async def chat(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        """
        Simple chat completion that returns the assistant's text.
        简单文本对话，返回 assistant 的文本响应。

        v6.0: Supports retry with exponential backoff if LLM_RETRY_ENABLED=true.
        """
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1 if self.retry_enabled else 1):
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                return resp.choices[0].message.content or ""
            except RETRYABLE_ERRORS as exc:
                last_error = exc
                if self.retry_enabled and attempt < self.max_retries:
                    wait_time = self.backoff_factor ** attempt
                    logger.warning("[LLMClient] Retryable error on attempt %d: %s. Waiting %.1fs...", attempt + 1, exc, wait_time)
                    await asyncio.sleep(wait_time)
                else:
                    raise
        raise last_error or RuntimeError("LLM call failed")
```

#### 算法流程

```
指数退避重试流程：
  1. 尝试调用 LLM API
  2. 如果成功，返回结果
  3. 如果失败且是可重试错误（RateLimitError / APITimeoutError / APIError）：
     a. 计算等待时间：backoff_factor ^ attempt
     b. 等待该时间
     c. 重试
  4. 如果达到最大重试次数，抛出异常
```

#### 等待时间计算

假设 `backoff_factor = 2`，`max_retries = 3`：

| 尝试次数 | 等待时间 | 累计等待 |
|---------|---------|---------|
| 第 1 次 | 0 秒（立即） | 0 秒 |
| 第 2 次 | 2^0 = 1 秒 | 1 秒 |
| 第 3 次 | 2^1 = 2 秒 | 3 秒 |
| 第 4 次 | 2^2 = 4 秒 | 7 秒 |

#### 关键特性

1. **容错性**：自动重试可恢复的错误（如限流、超时）
2. **防雪崩**：指数增长的等待时间避免大量请求同时到达
3. **可配置**：通过 `LLM_RETRY_ENABLED`、`LLM_RETRY_MAX_ATTEMPTS`、`LLM_RETRY_BACKOFF_FACTOR` 配置
4. **向后兼容**：默认关闭，需要显式启用

---

### 15. 三路由分类器 — simple/complex/emergent

#### 大白话理解

**三路由分类器**（**Three-way Router**）是 v6 的核心特性——根据任务的复杂度，自动选择最适合的规划器：
- **Simple**（简单）：任务可以直接执行，不需要规划
- **Complex**（复杂）：任务需要显式 DAG 规划（v2/v3）
- **Emergent**（隐式）：任务需要隐式规划（v5）

类比：
- **Simple**：买一瓶水 → 直接买，不需要计划
- **Complex**：装修房子 → 需要详细规划（设计图、施工流程）
- **Emergent**：写一篇博客 → 先写大纲，边写边调整

#### 对应代码

**代码位置**：`agents/orchestrator.py` — `run()` 和 `agents/planner.py` — `classify_task()`

```python
# 在 Orchestrator.run() 中的路由逻辑
async def run(self, task: str) -> str:
    """
    Execute a user task through the hybrid multi-agent pipeline.
    通过混合多智能体流水线执行用户任务。
    """
    # ... 收集上下文 ...
    
    # --- Phase 2: Classify & Route ---
    # --- 阶段 2：分类 & 路由 ---
    complexity = await self.planner.classify_task(task)
    self._emit("task_complexity", {"complexity": complexity, "task": task[:100]})

    # --- Phase 3: Plan & Execute (routed by complexity) ---
    # --- 阶段 3：规划 & 执行（按复杂度路由）---
    if complexity == "simple":
        # v1 路径：扁平计划 + 顺序执行
        plan = await self.planner.create_plan(task, combined_context)
        final_answer = await self._execute_and_reflect_simple(task, plan, combined_context)
    elif complexity == "complex":
        # v2/v3 路径：DAG 规划 + 并行执行
        dag = await self.planner.create_dag(task, combined_context)
        final_answer = await self._execute_dag_and_reflect(dag)
    else:
        # v5 路径：隐式规划（TODO 列表管理）
        final_answer = await self._execute_emergent(task, combined_context)
```

#### 分类标准

| 类别 | 特征 | 规划器 | 示例 |
|------|------|--------|------|
| **Simple** | 单步任务，无需规划 | v1 扁平计划 + 顺序执行 | "计算 1+1"、"查询天气" |
| **Complex** | 多层级、多智能体、复杂依赖 | v2/v3 DAG 规划器 | "开发一个完整应用"、"写一篇技术报告" |
| **Emergent** | 探索性、不确定、需要动态调整 | v5 隐式规划器 | "研究某个新技术"、"调研市场" |

#### 关键特性

1. **自动化**：通过 `PlannerAgent.classify_task()` 自动选择规划器
2. **两阶段分类**：规则快筛（零成本）+ LLM 兜底（仅对模糊区间）
3. **容错性**：分类失败时降级到 DAG 规划器
4. **智能化**：结合规则和 LLM，平衡效率和准确率

---

## 四、算法调用关系全景图

```
用户任务
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ Orchestrator (三路由分类器)                                  │
│  - LLM 分类任务复杂度                                        │
│  - 路由到 Simple/Complex/Emergent                            │
└─────────────────────────────────────────────────────────────┘
  │              │                    │
  │ Simple       │ Complex            │ Emergent
  ▼              ▼                    ▼
直接执行    ┌──────────────┐    ┌──────────────────────────┐
           │ DAG 规划器    │    │ 隐式规划器                │
           │ (v2/v3)      │    │ (v5)                     │
           └──────────────┘    └──────────────────────────┘
                 │                        │
                 ▼                        ▼
          ┌──────────────┐      ┌──────────────────────────┐
          │ TaskDAG      │      │ TodoList                 │
          │ - nodes: dict│      │ - todos: dict            │
          │ - edges: list│      │ - 动态演化               │
          └──────────────┘      └──────────────────────────┘
                 │                        │
                 ▼                        ▼
          ┌──────────────┐      ┌──────────────────────────┐
          │ DAGExecutor  │      │ while(tool_use) 主循环   │
          │ - Super-step │      │ - ReAct 循环             │
          │ - 并行执行   │      │ - 动态管理 TODO          │
          └──────────────┘      └──────────────────────────┘
                 │                        │
                 ▼                        ▼
          ┌──────────────┐      ┌──────────────────────────┐
          │ 核心算法     │      │ 核心算法                 │
          │ - Kahn 拓扑  │      │ - TF-IDF 检索            │
          │ - BFS 遍历   │      │ - LLM 容错（指数退避）   │
          │ - FSM 状态机 │      │ - 工具路由器             │
          └──────────────┘      └──────────────────────────┘
                 │                        │
                 └──────────┬─────────────┘
                            ▼
                 ┌──────────────────────────┐
                 │ LLM Client (v6)          │
                 │ - 指数退避重试           │
                 │ - 容错机制               │
                 └──────────────────────────┘
                            │
                            ▼
                 ┌──────────────────────────┐
                 │ 工具路由器               │
                 │ - 连续失败阈值           │
                 │ - 替代工具建议           │
                 └──────────────────────────┘
                            │
                            ▼
                 ┌──────────────────────────┐
                 │ 工具执行                 │
                 │ - file_ops               │
                 │ - code_executor          │
                 │ - web_search             │
                 └──────────────────────────┘
```

---

## 五、学习路径建议

### 推荐学习顺序

#### 第一阶段：基础数据结构（1-2 周）

1. **字典和集合**（dict/set）
   - 理解哈希表原理
   - 练习：LeetCode 1（两数之和）、3（无重复字符的最长子串）
   
2. **图的基础**（Graph）
   - 理解节点、边、有向、无环的概念
   - 练习：LeetCode 207（课程表）、210（课程表 II）

3. **树的遍历**（Tree Traversal）
   - 理解父指针、层级结构
   - 练习：LeetCode 102（二叉树的层序遍历）

#### 第二阶段：核心算法（2-3 周）

1. **BFS 广度优先搜索**
   - 理解队列的使用
   - 练习：LeetCode 127（单词阶梯）、752（打开转盘锁）

2. **Kahn 拓扑排序**
   - 理解入度、邻接表
   - 练习：LeetCode 207（课程表）、210（课程表 II）

3. **有限状态机（FSM）**
   - 理解状态转移表
   - 练习：实现一个简单的红绿灯状态机

#### 第三阶段：组合应用（3-4 周）

1. **Super-step 并行执行**
   - 理解异步编程（asyncio）
   - 练习：实现一个简单的任务调度器

2. **TF-IDF 文本检索**
   - 理解词频、逆文档频率、余弦相似度
   - 练习：实现一个简单的文档检索系统

3. **指数退避重试**
   - 理解容错机制
   - 练习：实现一个带重试的 HTTP 客户端

#### 第四阶段：进阶主题（2-3 周）

1. **DAG 规划 vs 隐式规划**
   - 理解两种规划模式的优劣
   - 练习：实现一个简单的规划器

2. **三路由分类器**
   - 理解任务复杂度分类
   - 练习：实现一个简单的分类器

3. **工具路由器**
   - 理解连续失败阈值、熔断器模式
   - 练习：实现一个简单的工具路由器

### 推荐资源

#### 书籍

1. **《算法导论》**（Introduction to Algorithms）
   - 经典的算法教材，涵盖所有基础算法

2. **《算法（第4版）》**（Algorithms, 4th Edition）
   - Java 实现，适合实践

3. **《数据结构与算法分析》**（Data Structures and Algorithm Analysis）
   - 侧重分析，适合深入理解

#### 在线课程

1. **Coursera - Algorithms Specialization**
   - 斯坦福大学，涵盖基础算法

2. **LeetCode**
   - 刷题平台，大量练习题

3. **VisuAlgo**
   - 算法可视化，帮助理解

#### 论文

1. **Pregel: A System for Large-Scale Graph Processing**
   - Google 的图计算框架论文，Super-step 概念来源

2. **LangGraph: Building Stateful Agents with Graphs**
   - LangGraph 的设计理念

3. **Claude Code: Implicit Planning with TODO Lists**
   - Claude Code 的隐式规划理念

### 实践建议

1. **从简单开始**：先理解字典、集合、队列等基础数据结构
2. **动手实现**：不要只看理论，要自己写代码实现
3. **可视化**：使用工具（如 VisuAlgo）可视化算法执行过程
4. **刷题巩固**：在 LeetCode 上刷相关题目
5. **阅读源码**：阅读 Demo 的源码，理解实际应用

### 常见误区

1. **过度优化**：不要一开始就追求最优解，先实现再优化
2. **忽视边界情况**：注意处理空输入、单节点等边界情况
3. **过度依赖库**：先理解原理，再使用库函数
4. **忽视复杂度**：注意算法的时间和空间复杂度
5. **缺乏实践**：理论很重要，但实践更重要

---

## 附录：术语表

| 术语 | 英文 | 解释 |
|------|------|------|
| 哈希表 | Hash Table | O(1) 查找的数据结构 |
| 拓扑排序 | Topological Sort | 对 DAG 节点排序的算法 |
| 广度优先搜索 | BFS | 按层遍历图的算法 |
| 有限状态机 | FSM | 状态转移模型 |
| Super-step | Super-step | 并行执行的离散轮次 |
| TF-IDF | TF-IDF | 文本检索算法 |
| 余弦相似度 | Cosine Similarity | 向量相似度度量 |
| 指数退避 | Exponential Backoff | 重试等待策略 |
| DAG | DAG | 有向无环图 |
| 隐式规划 | Emergent Planning | 动态演化的规划方式 |
| 显式规划 | Explicit Planning | 预先生成的规划方式 |
| 三路由分类器 | Three-way Router | 任务复杂度分类器 |
| 工具路由器 | Tool Router | 工具选择和切换机制 |

---

**文档版本**：v6.0  
**最后更新**：2026-05-10
**维护者**：Manus Demo Team
