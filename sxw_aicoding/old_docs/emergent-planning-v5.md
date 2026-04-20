# Manus Demo v5 - 隐式规划系统详解

> **生成时间**: 2026-02-28
> **版本**: v5（Claude Code 风格的涌现规划）
> **目的**: 深入解析隐式规划系统的设计理念、实现细节和使用场景

---

## 目录

1. [设计理念](#设计理念)
2. [系统架构](#系统架构)
3. [核心算法](#核心算法)
4. [数据结构](#数据结构)
5. [与 DAG 规划的对比](#与 dag 规划的对比)
6. [使用场景指南](#使用场景指南)
7. [实现细节](#实现细节)
8. [性能特征](#性能特征)

---

## 设计理念

### 为什么需要隐式规划？

在 v4 及之前的版本中，我们采用 **显式规划**（Explicit Planning）策略：

```
用户任务 → 完整规划（DAG） → 执行规划 → 反思 → 结果
```

这种方式在 **目标明确** 的任务上表现出色，但在以下场景中存在局限：

1. **探索性任务**: "帮我调研 Python 机器学习生态"
   - 无法预先知道会发现哪些子领域
   - 规划应在探索过程中自然形成

2. **需求模糊**: "我想优化这个系统的性能"
   - 需要先分析才能确定优化方向
   - 规划应随分析深入而演化

3. **创意任务**: "为这个项目设计一个新功能"
   - 创意过程本质上是发散的
   -  rigid 结构可能限制创造力

### Claude Code 的启示

Claude Code（Anthropic 的编程助手）采用了一种不同的方法：

```
用户任务 → 初始化少量 TODO → while(tool_use) 循环 → 结果
           ↓
      执行中发现新工作 → 添加 TODO
      完成当前 TODO → 标记完成
      所有 TODO 完成 → 汇总答案
```

**核心思想**:
- 规划不是预先完整的蓝图
- 规划在执行中 **涌现**（Emergent）
- LLM 通过自然语言推理自主管理任务分解

### v5 的设计目标

1. **灵活性**: 适应探索性、不确定性强的任务
2. **自然性**: 更接近人类解决问题的方式
3. **简单性**: 减少复杂的规划结构，依赖 LLM 的自组织能力

---

## 系统架构

### 整体流程

```
用户输入任务
    ↓
Orchestrator 收集上下文（记忆 + 知识）
    ↓
分类任务复杂度
    ├─ simple   → v1 扁平计划
    ├─ complex  → v2 DAG 规划
    └─ emergent → v5 隐式规划（新增）
    ↓
EmergentPlannerAgent.execute(task, context)
    ↓
1. 初始化 TODO 列表（1-3 个初始项）
    ↓
2. while has_pending_todos:
   - 选择下一个就绪 TODO
   - 执行 ReAct 循环（推理 → 工具调用 → 观察）
   - 更新 TODO 状态
   - 动态添加新 TODO（如发现新工作）
    ↓
3. 汇总所有已完成 TODO 的结果
    ↓
4. 返回最终答案
    ↓
存储到长期记忆
```

### 组件关系

```
┌─────────────────────────────────────┐
│  OrchestratorAgent                  │
│  - 收集上下文                        │
│  - 分类任务                          │
│  - 路由到 EmergentPlanner           │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│  EmergentPlannerAgent               │
│  - TODO 列表管理                     │
│  - while(tool_use) 主循环           │
│  - ReAct 循环执行 TODO              │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│  TodoList (集中式状态)              │
│  - todos: dict[int, TodoItem]       │
│  - next_id: int                     │
│  - 方法：add_todo, mark_completed  │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│  Tools (web_search, execute_python, │
│         file_ops)                   │
└─────────────────────────────────────┘
```

---

## 核心算法

### 1. 主循环算法

```python
async execute(task: str, context: str) -> str:
    # 步骤 1: 初始化 TODO 列表
    todo_list = TodoList(task=task)
    await _init_todo_list(task, context)  # LLM 生成 1-3 个初始 TODO
    
    # 步骤 2: while 循环执行
    while todo_list.has_pending():
        # 2.1 选择下一个就绪 TODO
        ready_todos = todo_list.get_ready_todos()
        if not ready_todos:
            break  # 无就绪 TODO，退出
        
        current_todo = ready_todos[0]
        
        # 2.2 执行该 TODO（ReAct 循环）
        result = await _execute_todo(current_todo)
        
        # 2.3 更新 TODO 状态
        if result.success:
            todo_list.mark_completed(current_todo.id, result.output)
        else:
            # 失败处理：标记为 PENDING 以便重试
            pass
        
        # 2.4 动态更新 TODO 列表
        await _update_todo_list(result)
        
        # 2.5 显示当前状态（UI 反馈）
        _emit("todo_list_update", _get_todo_summary())
    
    # 步骤 3: 汇总结果
    return _compile_answer(task, all_results)
```

### 2. TODO 初始化算法

```python
async _init_todo_list(task: str, context: str):
    # 提示 LLM 创建初始 TODO 列表
    prompt = f"""
    Initialize a TODO list for this task.
    Create 1-3 high-level TODO items to get started.
    We will add more during execution if needed.
    
    Task: {task}
    
    Respond with JSON:
    {{
      "todos": [
        {{
          "description": "First TODO item",
          "dependencies": []
        }}
      ]
    }}
    """
    
    # 调用 LLM 生成 TODO
    data = await think_json(prompt, temperature=0.3)
    
    # 添加到 TODO 列表
    for todo_data in data.get("todos", []):
        todo_list.add_todo(
            description=todo_data["description"],
            dependencies=todo_data.get("dependencies", []),
        )
```

### 3. TODO 动态更新算法

```python
async _update_todo_list(last_result: StepResult):
    # 提示 LLM 评估是否需要更新 TODO 列表
    prompt = f"""
    Review the execution progress and determine if the TODO list needs updates.
    
    Current task: {todo_list.task}
    Last execution result: {last_result.output[:500]}
    Current TODO list: {_get_todo_summary()}
    
    Do you need to:
    - Add new TODOs (discovered additional work)?
    - Modify existing TODO descriptions?
    - Mark any TODOs as blocked?
    
    Respond with JSON:
    {{
      "needs_update": true/false,
      "reason": "Why update is or is not needed",
      "new_todos": [
        {{
          "description": "New TODO description",
          "dependencies": [1, 2]
        }}
      ]
    }}
    """
    
    # 调用 LLM 决策
    data = await think_json(prompt, temperature=0.3)
    
    # 执行更新
    if data.get("needs_update", False):
        for todo_data in data.get("new_todos", []):
            if len(todo_list.todos) < MAX_TODO_ITEMS:
                todo_list.add_todo(
                    description=todo_data["description"],
                    dependencies=todo_data.get("dependencies", []),
                )
```

### 4. TODO 依赖解析算法

```python
def get_ready_todos(self) -> list[TodoItem]:
    """获取所有依赖已满足的 TODO 项"""
    ready = []
    for todo in self.todos.values():
        if todo.status != TodoStatus.PENDING:
            continue
        
        # 检查所有依赖是否已完成
        deps_completed = all(
            self.todos.get(dep_id, TodoItem(id=dep_id, description="")).status 
            == TodoStatus.COMPLETED
            for dep_id in todo.dependencies
        )
        
        if deps_completed:
            ready.append(todo)
    
    return ready
```

---

## 数据结构

### TodoItem

```python
class TodoItem(BaseModel):
    """隐式规划中的单个 TODO 项"""
    
    id: int                          # 唯一标识符（自增）
    description: str                 # 任务描述
    status: TodoStatus               # PENDING/IN_PROGRESS/COMPLETED/BLOCKED
    dependencies: list[int]          # 前置 TODO ID 列表
    result: str | None               # 执行结果文本
    created_at: float                # 创建时间戳
    updated_at: float                # 最后更新时间戳
```

### TodoStatus

```python
class TodoStatus(str, Enum):
    PENDING = "pending"           # 等待执行
    IN_PROGRESS = "in_progress"   # 正在执行
    COMPLETED = "completed"       # 已完成
    BLOCKED = "blocked"           # 被阻塞（依赖未完成）
```

### TodoList

```python
class TodoList(BaseModel):
    """集中式 TODO 列表（隐式规划的核心状态）"""
    
    task: str                                    # 原始用户任务
    todos: dict[int, TodoItem]                   # 按 ID 索引的 TODO 项
    next_id: int = 1                             # 下一个可用 ID
    
    # 核心方法
    def add_todo(description, dependencies) -> TodoItem
    def get_pending_todos() -> list[TodoItem]
    def get_ready_todos() -> list[TodoItem]      # 依赖已满足的 TODO
    def mark_completed(todo_id, result) -> None
    def mark_in_progress(todo_id) -> None
    def is_complete() -> bool
    def has_pending() -> bool
```

---

## 与 DAG 规划的对比

### 结构对比

| 维度 | DAG 规划 (v2/v4) | 隐式规划 (v5) |
|------|-----------------|--------------|
| **数据结构** | 三层层级 DAG<br>Goal → SubGoal → Action | 扁平 TODO 列表<br>dict[int, TodoItem] |
| **依赖管理** | 显式边（TaskEdge）<br>DEPENDENCY/CONDITIONAL/ROLLBACK | 隐式依赖（dependencies 字段）<br>仅支持 DEPENDENCY |
| **状态管理** | NodeStatus + FSM<br>7 种状态 + 严格转移 | TodoStatus<br>4 种状态 + 自由转换 |
| **执行模型** | Super-step 并行<br>屏障同步 | 顺序执行 TODO<br>无并行 |

### 规划过程对比

**DAG 规划**:
```
任务 → 完整规划（一次性生成所有节点和边）
     ↓
执行 → 按超步并行执行
     ↓
调整 → 超步间自适应（修改未执行节点）
```

**隐式规划**:
```
任务 → 初始 TODO（1-3 个）
     ↓
执行 → 选择就绪 TODO → 执行 → 更新列表
     ↓        ↑
     └────────┘ (循环直到所有 TODO 完成)
```

### 适用场景对比

| 场景类型 | DAG 规划 | 隐式规划 | 说明 |
|---------|---------|---------|------|
| **目标明确的多阶段任务** | ✅ 优秀 | ⚠️ 可用 | DAG 的预先规划更高效 |
| **探索性研究** | ⚠️ 可用 | ✅ 优秀 | 隐式规划的灵活性更适合 |
| **需要并行执行** | ✅ 支持 | ❌ 不支持 | DAG 的超步模型天然并行 |
| **需求模糊的任务** | ⚠️ 困难 | ✅ 优秀 | 隐式规划边执行边明确需求 |
| **创意发散** | ⚠️ 受限 | ✅ 优秀 | 隐式规划不限制思维 |
| **严格依赖管理** | ✅ 优秀 | ⚠️ 基础 | DAG 的边类型更丰富 |

---

## 使用场景指南

### 何时选择隐式规划

**强烈推荐使用** (`PLAN_MODE=emergent`):

1. **探索性研究任务**
   ```
   "帮我调研 Python 机器学习生态系统的最新进展"
   "分析当前 AI 编程助手的技术趋势"
   ```

2. **需求不明确的开放式问题**
   ```
   "我想优化这个项目的性能，该怎么做？"
   "这个代码库有哪些可以改进的地方？"
   ```

3. **创意设计与规划**
   ```
   "为 manus_demo 设计一个新功能，让它能够学习用户偏好"
   "规划一个完整的用户认证系统"
   ```

4. **边执行边发现的任务**
   ```
   "帮我分析这个项目的文档覆盖率，并补充缺失的文档"
   "检查代码中的潜在 bug 并修复它们"
   ```

**不推荐使用**:

1. **目标非常明确的线性任务** → 使用 `PLAN_MODE=simple`
   ```
   "查询上海今天天气"
   "计算 12345 + 67890"
   ```

2. **需要严格并行控制** → 使用 `PLAN_MODE=complex`
   ```
   "同时分析 5 个不同的开源项目并对比"
   "并行处理 10 个数据文件"
   ```

### 任务描述技巧

**好的隐式规划任务描述**:

✅ **明确目标，但不约束路径**:
```
"分析 manus_demo 项目的代码质量并提出改进建议"
（目标明确，但 LLM 可自主决定分析维度）
```

✅ **允许探索空间**:
```
"调研 Python 并发编程的最佳实践，并总结关键要点"
（LLM 可自主决定调研哪些实践）
```

✅ **接受阶段性发现**:
```
"帮我优化这个脚本的性能，找出瓶颈并改进"
（LLM 可能在分析中发现预期外的优化点）
```

**不好的隐式规划任务描述**:

❌ **过度约束步骤**:
```
"先搜索 A，然后分析 B，接着实现 C，最后验证 D"
（这种应该用 DAG 规划）
```

❌ **需要严格并行**:
```
"同时运行 10 个独立的实验并对比结果"
（隐式规划不支持真正并行）
```

---

## 实现细节

### TODO 列表的集中式状态管理

隐式规划采用 **集中式状态** 模式（类似 LangGraph 的 DAGState）：

```python
# 所有 TODO 项存储在单一数据结构中
class TodoList:
    todos: dict[int, TodoItem]  # 集中存储
    
# 优势:
# 1. 单一事实来源（Single Source of Truth）
# 2. 易于序列化和调试
# 3. LLM 可以"看到"完整状态
```

### 工具调用日志

每个 TODO 执行时都会记录工具调用：

```python
class StepResult:
    step_id: int | str
    success: bool
    output: str
    tool_calls_log: list[ToolCallRecord]  # 工具调用历史

class ToolCallRecord:
    tool_name: str           # "web_search"
    parameters: dict         # {"query": "..."}
    result: str              # 工具返回结果
```

**用途**:
- UI 展示（用户可以看到执行过程）
- 调试分析（理解 LLM 的决策）
- 错误追踪（定位失败原因）

### Tool Router 集成

隐式规划器集成了 v3 的 Tool Router 机制：

```python
# 在 ReAct 循环中检查工具路由器
router_hint = self.tool_router.get_hint(str(todo.id))
if router_hint and iteration > 1:
    # 如果某个工具连续失败 2 次，建议替代方案
    continue_msg += f"\n\nIMPORTANT: {router_hint}"
```

**效果**: 避免 LLM 重复调用失败的工具

### 上下文压缩

当 TODO 列表接近上限时，触发压缩机制：

```python
if len(todo_list.todos) >= MAX_TODO_ITEMS * TODO_COMPRESSION_THRESHOLD:
    # 提示 LLM 合并或完成部分 TODO
    # 避免列表无限增长
```

**默认阈值**: 80%（16/20 时触发）

---

## 性能特征

### 时间复杂度

| 操作 | 复杂度 | 说明 |
|------|--------|------|
| `add_todo` | O(1) | 字典插入 |
| `get_ready_todos` | O(N) | 遍历所有 TODO 检查依赖 |
| `mark_completed` | O(1) | 字典更新 |
| `has_pending` | O(N) | 遍历检查状态 |

**实际影响**: TODO 数量 < 50 时性能无感知

### 空间复杂度

- **内存**: O(N × M)，N=TODO 数量，M=平均描述长度
- **典型值**: 20 个 TODO × 100 字符 ≈ 2KB（可忽略）

### 与 DAG 规划的性能对比

| 指标 | DAG 规划 | 隐式规划 |
|------|---------|---------|
| **规划延迟** | ~0.5-1s（完整规划） | ~0.2-0.5s（初始规划） |
| **执行速度** | 快（并行） | 慢（顺序） |
| **Token 消耗** | 中等 | 中等偏高（多轮 LLM 调用） |
| **灵活性** | 中等 | 高 |

### 可扩展性限制

**当前限制**:
1. **无真正并行**: TODO 顺序执行
2. **TODO 数量上限**: 默认 20 个
3. **无长期规划**: 不适合需要数十步的任务

**未来增强方向**:
1. **TODO 分组并行**: 独立 TODO 可并行执行
2. **TODO 层次化**: 支持子 TODO 列表
3. **持久化检查点**: 长周期任务可暂停/恢复

---

## 总结

### v5 的核心贡献

1. **新的规划范式**: 从"预先完整规划"转向"执行中涌现"
2. **简化的数据结构**: 扁平 TODO 列表 vs 三层层级 DAG
3. **更高的灵活性**: 适应探索性、不确定性强的任务
4. **更接近人类思维**: 边做边规划，而非 rigid 蓝图

### 与 v4 的关系

**不是替代，而是互补**:
- v4 (DAG 规划): 适合目标明确的复杂任务
- v5 (隐式规划): 适合探索性、创意性任务

**混合路由** (`PLAN_MODE=auto`):
- 两阶段分类器自动选择 simple/complex
- 未来可扩展为 simple/complex/emergent 三路路由

### 最佳实践建议

1. **根据任务类型选择模式**:
   - 简单查询 → `simple`
   - 复杂但明确 → `complex`
   - 探索性/创意 → `emergent`

2. **观察 TODO 列表演化**:
   - 理解 LLM 如何自主分解任务
   - 从中学习更好的任务描述方式

3. **对比测试**:
   - 同一任务尝试 different 模式
   - 根据结果质量选择最优路径

---

**文档版本**: v5.0
**最后更新**: 2026-02-28
**作者**: Manus Demo Team
