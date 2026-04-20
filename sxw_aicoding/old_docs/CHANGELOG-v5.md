# Manus Demo v5 更新日志

> **发布日期**: 2026-02-28
> **版本**: v5.0
> **核心特性**: Claude Code 风格的隐式规划系统

---

## 概述

Manus Demo v5 引入了全新的规划范式 —— **隐式规划**（Emergent Planning），与现有的 DAG 规划（v2/v4）形成互补，共同构成更强大的混合规划系统。

### 版本演进

```
v1 → 线性规划 + 顺序执行 + 完整重规划
     │
     ▼
v2 → DAG 分层规划 + 并行 Super-step + 
     局部重规划 + 节点状态机 + 逐节点验证
     │
     ▼
v3 → 自适应规划（运行时 DAG 变更）+ 
     工具路由（基于失败的切换）+ 
     动态 DAG 增删改
     │
     ▼
v4 → 两阶段混合分类器（规则 + LLM）+ 
     自动 v1/v2 路径选择
     │
     ▼
v5 → 隐式规划（Claude Code 风格）+ 
     TODO 列表管理 + while(tool_use) 主循环
     （新增第三条执行路径）
```

---

## 新增特性

### 1. EmergentPlannerAgent（隐式规划器）

**文件**: `agents/emergent_planner.py`

**核心思想**:
- 无独立规划阶段，规划在执行过程中自然涌现
- 通过 TODO 列表管理任务分解
- `while(tool_use)` 主循环持续调用工具直到所有 TODO 完成
- LLM 自主决定何时添加新 TODO、何时标记完成

**主要方法**:
```python
async execute(task: str, context: str) -> str
async _init_todo_list(task, context) -> None
async _update_todo_list(result: StepResult) -> None
async _execute_todo(todo: TodoItem) -> StepResult
```

**设计灵感**: Anthropic 的 Claude Code 编程助手

---

### 2. TODO 列表数据结构

**文件**: `schema.py`

**新增模型**:

```python
class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

class TodoItem(BaseModel):
    id: int
    description: str
    status: TodoStatus
    dependencies: list[int]
    result: str | None
    created_at: float
    updated_at: float

class TodoList(BaseModel):
    task: str
    todos: dict[int, TodoItem]
    next_id: int
    
    # 核心方法
    def add_todo(description, dependencies) -> TodoItem
    def get_ready_todos() -> list[TodoItem]
    def mark_completed(todo_id, result) -> None
    def is_complete() -> bool
```

---

### 3. Orchestrator 路由增强

**文件**: `agents/orchestrator.py`

**新增路由选项**:
```python
async run(task: str) -> str:
    complexity = await self.planner.classify_task(task)
    
    if complexity == "simple":
        # v1: 扁平计划路径
        plan = await self.planner.create_plan(task, context)
        return await self._execute_and_reflect_simple(...)
    elif complexity == "complex":
        # v2: DAG 路径
        dag = await self.planner.create_dag(task, context)
        return await self._execute_dag_and_reflect(dag)
    else:
        # v5: 隐式规划路径（新增）
        return await self._execute_emergent(task, context)
```

---

### 4. 配置项扩展

**文件**: `config.py`

**新增配置**:
```python
# 隐式规划 (v5)
EMERGENT_PLANNING_ENABLED = true
MAX_TODO_ITEMS = 20
TODO_COMPRESSION_THRESHOLD = 0.8
```

---

### 5. 测试套件

**新增测试文件**:
- `tests/test_emergent_planning.py` - pytest 单元测试
- `tests/test_emergent_simple.py` - 简单测试脚本（无需 pytest）

**测试覆盖**:
- TODO 项创建与依赖管理
- TODO 列表状态转换
- EmergentPlannerAgent 初始化
- 简单任务执行流程
- TODO 列表动态更新

---

## 使用指南

### 运行隐式规划任务

```bash
# 交互模式
PLAN_MODE=emergent python main.py

# 单次任务
PLAN_MODE=emergent python main.py "帮我分析这个项目的代码结构并提出改进建议"
```

### 典型适用场景

✅ **推荐使用隐式规划**:
- 探索性研究："调研 Python 机器学习生态"
- 需求不明确："我想优化这个系统的性能"
- 创意设计："为这个项目设计一个新功能"
- 边执行边发现："检查代码中的潜在 bug 并修复"

❌ **不推荐使用**:
- 简单查询："查询上海天气"（用 `simple`）
- 需要严格并行："同时分析 5 个项目"（用 `complex`）

---

## 与 DAG 规划的对比

| 维度 | DAG 规划 (v2/v4) | 隐式规划 (v5) |
|------|-----------------|--------------|
| **规划时机** | 执行前完整规划 | 执行中动态涌现 |
| **数据结构** | 三层层级 DAG | 扁平 TODO 列表 |
| **依赖管理** | 显式边（DEPENDENCY/CONDITIONAL/ROLLBACK） | 隐式依赖（仅 DEPENDENCY） |
| **执行模型** | Super-step 并行 | 顺序执行 |
| **变更方式** | 超步间自适应 | 随时增删改 TODO |
| **适用场景** | 目标明确的复杂任务 | 探索性、不确定性强的任务 |

---

## 架构变化

### 组件关系图

```
┌─────────────────────────────────────┐
│  OrchestratorAgent                  │
│  - 收集上下文                        │
│  - 分类任务（simple/complex/emergent）│
│  - 路由到对应规划器                  │
└─────────────┬───────────────────────┘
              │
              ├──────────────┐
              │              │
              ▼              ▼
┌──────────────────┐  ┌──────────────────┐
│  PlannerAgent    │  │  EmergentPlanner │
│  (DAG 规划)       │  │  (隐式规划)       │
│  - create_dag()  │  │  - execute()     │
│  - replan()      │  │  - TODO 管理      │
└──────────────────┘  └──────────────────┘
```

### 执行流程对比

**DAG 规划**:
```
任务 → 完整规划（DAG） → Super-step 并行执行 → 反思 → 结果
```

**隐式规划**:
```
任务 → 初始 TODO（1-3 个） → while(has_pending):
                              - 选择就绪 TODO
                              - 执行（ReAct 循环）
                              - 更新 TODO 列表
                              - 动态添加新 TODO
                           → 汇总结果
```

---

## 性能特征

### 时间复杂度

| 操作 | 复杂度 | 说明 |
|------|--------|------|
| `add_todo` | O(1) | 字典插入 |
| `get_ready_todos` | O(N) | 遍历检查依赖 |
| `mark_completed` | O(1) | 字典更新 |

**实际影响**: TODO 数量 < 50 时性能无感知

### 与 DAG 规划对比

| 指标 | DAG 规划 | 隐式规划 |
|------|---------|---------|
| **规划延迟** | ~0.5-1s | ~0.2-0.5s |
| **执行速度** | 快（并行） | 慢（顺序） |
| **Token 消耗** | 中等 | 中等偏高 |
| **灵活性** | 中等 | 高 |

---

## 最佳实践

### 任务描述技巧

**好的隐式规划任务描述**:
```
✅ "分析 manus_demo 项目的代码质量并提出改进建议"
   （目标明确，LLM 可自主决定分析维度）

✅ "调研 Python 并发编程的最佳实践，并总结关键要点"
   （允许探索空间）
```

**不好的隐式规划任务描述**:
```
❌ "先搜索 A，然后分析 B，接着实现 C，最后验证 D"
   （这种应该用 DAG 规划）

❌ "同时运行 10 个独立的实验并对比结果"
   （隐式规划不支持真正并行）
```

### 配置调整

```bash
# 提高 TODO 上限（复杂探索任务）
MAX_TODO_ITEMS=50 PLAN_MODE=emergent python main.py "..."

# 降低 TODO 上限（简单任务）
MAX_TODO_ITEMS=10 PLAN_MODE=emergent python main.py "..."
```

---

## 已知局限性

1. **无真正并行**: TODO 顺序执行，不适合需要大量并行的任务
2. **TODO 数量上限**: 默认 20 个，复杂任务需手动调整
3. **无长期规划**: 不适合需要数十步的超长任务
4. **依赖管理简化**: 仅支持 DEPENDENCY，不支持 CONDITIONAL/ROLLBACK

### 未来增强方向

1. **TODO 分组并行**: 独立 TODO 可并行执行
2. **TODO 层次化**: 支持子 TODO 列表
3. **持久化检查点**: 长周期任务可暂停/恢复
4. **混合模式**: DAG + 隐式规划的深度融合

---

## 迁移指南

### 从 v4 升级到 v5

**向后兼容**: v5 完全向后兼容，v4 的所有功能保持不变。

**新增配置**:
```bash
# 在 .env 中添加（可选）
EMERGENT_PLANNING_ENABLED=true
MAX_TODO_ITEMS=20
TODO_COMPRESSION_THRESHOLD=0.8
```

**测试现有功能**:
```bash
# v4 功能应保持不变
PLAN_MODE=auto python main.py "查询天气"      # 应路由到 simple
PLAN_MODE=complex python main.py "调研并实现"  # 应路由到 DAG

# 测试新功能
PLAN_MODE=emergent python main.py "分析代码"  # 隐式规划
```

---

## 新增文档

| 文档 | 说明 |
|------|------|
| `docs/emergent-planning-v5.md` | 隐式规划系统详解（设计理念、实现细节） |
| `docs/emergent-planning-test-scenarios-v5.md` | 隐式规划测试用例集 |
| `docs/codemap-v4.md` | 代码地图（已更新为 v5） |
| `README.md` | 项目说明（已添加 v5 内容） |

---

## 贡献者

- **设计与实现**: Manus Demo Team
- **灵感来源**: Anthropic Claude Code
- **测试与文档**: Manus Demo Team

---

## 总结

v5 通过引入隐式规划，完善了 Manus Demo 的规划能力：

- **v1 (simple)**: 简单任务的轻量级路径
- **v2/v4 (complex)**: 复杂任务的结构化路径
- **v5 (emergent)**: 探索性任务的灵活路径

三种路径各有优劣，用户可根据任务类型选择最合适的模式，或使用 `PLAN_MODE=auto` 让系统自动选择。

---

**版本**: v5.0  
**发布日期**: 2026-02-28  
**许可证**: MIT
