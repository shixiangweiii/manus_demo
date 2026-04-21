# Manus Demo 更新日志

> **更新日期**: 2026-04-20
> **当前版本**: v6.0

## 概述

Manus Demo 是一个多智能体系统 Demo，经历了 6 个主要版本的演进，从简单的线性规划逐步发展为支持三种规划范式的混合系统。

## 版本演进

```
v1 → 线性规划 + 顺序执行 + 完整重规划
v2 → DAG 分层规划 + 并行 Super-step + 局部重规划 + 节点状态机 + 逐节点验证
v3 → 自适应规划（运行时 DAG 变更）+ 工具路由（基于失败的切换）+ 动态 DAG 增删改
v4 → 两阶段混合分类器（规则 + LLM）+ 自动 v1/v2 路径选择
v5 → Claude Code 风格隐式规划 + TODO 列表管理 + while(tool_use) 主循环
v6 → LLM 重试机制（指数退避）+ ReActEngine 统一引擎 Feature Flag
```

---

## v6.0 (2026-04-02)

### 核心特性：LLM 调用健壮性增强

#### 1. LLM Client Retry 机制

**文件**: `llm/client.py`

**实现细节**:
- **指数退避重试**: `wait_time = backoff_factor ** attempt`
- **可重试错误类型**: 
  - `RateLimitError` (速率限制)
  - `APITimeoutError` (超时)
  - `APIError` (通用 API 错误)
  - 以上错误类型均从 `openai` 包导入
- **配置项**:
  - `LLM_RETRY_ENABLED` (默认 `false`): 是否启用重试机制
  - `LLM_RETRY_MAX_ATTEMPTS` (默认 `3`): 最大重试次数
  - `LLM_RETRY_BACKOFF_FACTOR` (默认 `2.0`): 退避因子
- **向后兼容**: 默认关闭，不影响现有行为
- **支持方法**: `chat()` 和 `chat_with_tools()` 两个方法都支持 retry

**使用场景**:
- 应对 API 限流导致的临时失败
- 处理网络超时等瞬态错误
- 提高系统整体稳定性

#### 2. ReActEngine v2 Feature Flag

**配置项**: `ENABLE_REACT_ENGINE_V2` (默认 `false`)

**支持范围**:
- `ExecutorAgent`: 启用后使用统一的 ReActEngine 替代 `_react_loop`
- `EmergentPlannerAgent`: 启用后使用统一的 ReActEngine 替代 `_execute_todo`

**设计目标**:
- 统一两种 Agent 的 ReAct 循环实现
- 减少代码重复，提高可维护性
- 便于未来统一优化 ReAct 逻辑

**向后兼容**: 默认使用 legacy 实现，不影响现有行为

#### 3. 测试覆盖

**文件**: `tests/test_llm_integration.py`

**测试内容**:
- LLM Client 重试机制的各种场景
- 指数退避算法的正确性
- 不同错误类型的重试行为
- 最大重试次数的边界条件

---

## v5.0 (2026-02-28)

### 核心特性：Claude Code 风格的隐式规划系统

#### 1. EmergentPlannerAgent（隐式规划器）

**文件**: `agents/emergent_planner.py` (539行)

**核心思想**: 
- 无独立规划阶段，规划在执行过程中自然涌现
- 通过 TODO 列表管理任务分解和执行状态
- 采用 `while(tool_use)` 主循环持续调用工具直到所有 TODO 完成

**主要方法**:
- `execute()`: 主执行入口，初始化 TODO 列表并启动主循环
- `_init_todo_list()`: 根据用户任务初始化 TODO 列表
- `_execute_todo()`: 执行单个 TODO 项，包含 ReAct 循环
- `_update_todo_list()`: 根据执行结果动态更新 TODO 状态

**失败处理**:
- `mark_pending()`: 将失败的 TODO 回退为 `PENDING` 状态以便重试
- 支持依赖关系：只有依赖项完成后才能执行

**设计灵感**: 
- Claude Code 的隐式规划范式
- 强调执行过程中的动态调整
- 适合探索性、不确定性的任务

#### 2. TODO 列表数据结构

**文件**: `schema.py`

**TodoStatus 枚举**:
- `PENDING`: 待执行
- `IN_PROGRESS`: 执行中
- `COMPLETED`: 已完成
- `BLOCKED`: 被阻塞（依赖未满足）

**TodoItem 模型**:
- `id`: 唯一标识符
- `description`: 任务描述
- `status`: 当前状态
- `dependencies`: 依赖的 TODO ID 列表
- `result`: 执行结果
- `created_at`: 创建时间
- `updated_at`: 更新时间

**TodoList 模型**:
- `task`: 原始任务描述
- `todos`: TODO 字典（id → TodoItem）
- `next_id`: 下一个可用的 ID

**核心方法**:
- `add_todo()`: 添加新的 TODO 项
- `get_ready_todos()`: 获取所有可执行的 TODO（依赖已满足）
- `mark_completed()`: 标记 TODO 为已完成
- `mark_pending()`: 将 TODO 回退为待执行状态
- `is_complete()`: 检查是否所有 TODO 都已完成
- `has_pending()`: 检查是否还有待执行的 TODO

#### 3. Orchestrator 路由增强

**文件**: `agents/orchestrator.py`

**新增方法**:
- `_execute_emergent()`: 处理隐式规划任务的执行流程

**分类器扩展**:
- `classify_task()` 返回值新增 `"emergent"` 选项
- 支持将探索性任务路由到 EmergentPlannerAgent

#### 4. PlannerAgent 分类器增强

**文件**: `agents/planner.py`

**新增正则模式**:
- `_EXPLORATORY_PATTERN`: 检测探索性任务（如"探索"、"调研"等关键词）
- `_UNCERTAINTY_PATTERN`: 检测不确定性任务（如"不确定"、"可能"等关键词）

**规则分类逻辑**:
- `_rule_classify()` 新增探索性/不确定性检测
- 匹配到这些模式时倾向于返回 `"emergent"`

#### 5. 配置项

- `EMERGENT_PLANNING_ENABLED` (默认 `true`): 是否启用隐式规划功能
- `MAX_TODO_ITEMS` (默认 `20`): 最大 TODO 项数量限制
- `TODO_COMPRESSION_THRESHOLD` (默认 `0.8`): TODO 压缩阈值

#### 6. 测试覆盖

**文件**: 
- `tests/test_emergent_planning.py`: 完整的隐式规划测试套件
- `tests/test_emergent_simple.py`: 简单场景的快速测试

**测试场景**:
- TODO 列表的初始化和更新
- 依赖关系的正确处理
- 失败重试机制
- 主循环的终止条件

---

## v4.0

### 核心特性：两阶段混合分类器 + 自动路由

#### 1. 两阶段混合分类器

**设计目标**: 在准确性和性能之间取得最佳平衡

**Stage 1: 规则分类器** (`_rule_classify()`)
- **性能**: 零成本，<1ms
- **评分维度**:
  - 文本长度（长文本倾向于复杂任务）
  - 多步指示词（"然后"、"接下来"等）
  - 条件/分支词（"如果"、"否则"等）
  - 并行需求词（"同时"、"并行"等）
  - 动作动词数量（多个动作倾向于复杂任务）
- **决策阈值**:
  - `score ≤ -2`: 简单任务 → 返回 `"simple"`
  - `score ≥ 3`: 复杂任务 → 返回 `"complex"`
  - 其他情况 → 进入 Stage 2

**Stage 2: LLM 分类器** (`_llm_classify()`)
- **性能**: ~60 tokens, 0.3s（仅 ambiguous 触发）
- **使用场景**: 仅在规则分类器无法确定时调用
- **优势**: 
  - 减少不必要的 LLM 调用（约 60-70% 的任务在 Stage 1 完成）
  - 对模糊任务提供更准确的判断

#### 2. PLAN_MODE 配置

**配置项**: `PLAN_MODE`

**可选值**:
- `auto` (默认): 启用两阶段混合分类器，自动选择规划路径
- `simple`: 强制使用 v1 线性规划路径
- `complex`: 强制使用 v2 DAG 规划路径

**使用场景**:
- `auto`: 适用于大多数场景，自动选择最优路径
- `simple`: 适用于明确知道任务简单的场景
- `complex`: 适用于明确知道任务复杂且需要 DAG 的场景

**性能对比**:
- 规则分类: <1ms
- LLM 分类: ~300ms
- 混合模式: 平均 ~100ms（大部分任务走规则分类）

---

## v3.0

### 核心特性：自适应规划 + 工具路由 + 动态 DAG

#### 1. 执行中动态重规划

**核心文件**:
- `agents/planner.py`: `adapt_plan()`, `apply_adaptations()`
- `dag/executor.py`: `_should_adapt()`, `_adapt_plan()`
- `schema.py`: `AdaptAction`, `PlanAdaptation`, `AdaptationResult`

**工作流程**:
1. **触发条件判断** (`_should_adapt()`):
   - 定期检查（由 `ADAPT_PLAN_INTERVAL` 控制）
   - 已完成节点数达到阈值（由 `ADAPT_PLAN_MIN_COMPLETED` 控制）
   - 检测到执行失败或异常情况

2. **生成适应方案** (`adapt_plan()`):
   - 分析当前执行状态
   - 识别需要调整的部分
   - 生成 `PlanAdaptation` 对象

3. **应用适应方案** (`apply_adaptations()`):
   - 执行具体的 DAG 变更操作
   - 更新节点状态和依赖关系
   - 返回 `AdaptationResult`

**配置项**:
- `ADAPTIVE_PLANNING_ENABLED` (默认 `true`): 是否启用自适应规划
- `ADAPT_PLAN_INTERVAL` (默认 3): 每执行 N 个节点检查一次
- `ADAPT_PLAN_MIN_COMPLETED` (默认 2): 至少完成 N 个节点后才考虑重规划

#### 2. 工具选择智能路由

**文件**: `tools/router.py`

**ToolRouter 类**:
- **功能**: 监控工具执行失败情况，自动建议切换工具
- **工作原理**:
  1. 记录每个工具的连续失败次数
  2. 当失败次数达到阈值时生成切换建议
  3. 返回可替代的工具列表

**配置项**:
- `TOOL_FAILURE_THRESHOLD` (默认 2): 连续失败多少次后触发切换建议

**使用场景**:
- 某个搜索工具频繁超时 → 建议切换到其他搜索工具
- 代码执行工具失败 → 建议使用解释器模式
- 文件操作权限问题 → 建议使用备用路径或工具

#### 3. 子任务动态生成

**文件**: `dag/graph.py`

**新增方法**:
- `add_dynamic_node()`: 在运行时添加新节点
- `add_dynamic_edge()`: 在运行时添加新边（依赖关系）
- `remove_pending_node()`: 移除尚未执行的节点
- `modify_node()`: 修改已存在节点的属性
- `get_pending_action_nodes()`: 获取所有待执行的动作节点
- `get_completed_action_count()`: 获取已完成动作节点的数量

**应用场景**:
- 执行过程中发现新需求 → 动态添加节点
- 某个分支不再需要 → 移除相关节点
- 执行策略调整 → 修改节点参数

**技术挑战**:
- 维护 DAG 的有效性（无环、依赖关系正确）
- 确保新增节点的状态正确初始化
- 处理与现有执行状态的同步

---

## v2.0

### 核心特性：DAG 分层规划 + 并行执行

#### 1. DAG 分层规划

**三层层级结构**:
```
Goal (目标)
  ↓
SubGoal (子目标)
  ↓
Action (动作)
```

**核心数据结构** (`schema.py`):
- `NodeType`: GOAL, SUBGOAL, ACTION
- `NodeStatus`: PENDING, READY, RUNNING, COMPLETED, FAILED, ROLLED_BACK, SKIPPED
- `EdgeType`: DEPENDENCY, CONDITIONAL, ROLLBACK
- `ExitCriteria`: 节点完成条件
- `RiskAssessment`: 节点风险评估
- `TaskNode`: DAG 节点（包含类型、状态、内容等）
- `TaskEdge`: DAG 边（包含类型、源节点、目标节点）
- `DAGState`: 整体执行状态

**优势**:
- 清晰的层次结构，便于理解和维护
- 支持复杂任务的分解和抽象
- 便于进行风险管理和条件分支

#### 2. Super-step 并行执行

**文件**: `dag/executor.py`

**DAGExecutor 类**:
- **核心机制**: Super-step 并行执行
- **实现方式**: `asyncio.gather()` 并行执行所有就绪节点
- **并行度控制**: `MAX_PARALLEL_NODES` 限制同时执行的节点数

**执行流程**:
1. 识别所有就绪节点（依赖已满足）
2. 从中选择最多 `MAX_PARALLEL_NODES` 个节点
3. 并行执行这些节点
4. 等待所有节点完成
5. 更新节点状态和依赖关系
6. 重复直到所有节点完成

**性能提升**:
- 相比顺序执行，理论加速比可达并行度
- 实际加速比受限于 I/O 等待时间和任务依赖关系

#### 3. 节点状态机

**文件**: `dag/state_machine.py`

**NodeStateMachine 类**:
- **状态转换规则**: `VALID_TRANSITIONS` 定义了合法的状态转换
- **状态流转**:
  ```
  PENDING → READY → RUNNING → COMPLETED
                        ↓
                     FAILED
                        ↓
                   ROLLED_BACK / SKIPPED
  ```

**状态验证**:
- 每次状态转换前检查合法性
- 防止非法状态转换导致系统不一致
- 提供状态回滚机制

#### 4. 逐节点验证

**文件**: `agents/reflector.py`

**validate_exit_criteria() 方法**:
- **功能**: 验证节点是否满足完成条件
- **检查项**:
  - 输出是否符合预期
  - 是否达到退出标准
  - 是否存在异常情况

**ExitCriteria** (`schema.py`):
- 定义节点的完成条件
- 可包含成功标准和失败标准
- 支持自定义验证逻辑

#### 5. 条件分支 + 回滚

**EdgeType 类型**:
- `DEPENDENCY`: 普通依赖关系
- `CONDITIONAL`: 条件分支（根据前序节点的结果决定是否执行）
- `ROLLBACK`: 回滚边（失败时触发回滚操作）

**DAGExecutor 方法**:
- `_process_conditions()`: 处理条件分支逻辑
- `_handle_failure()`: 处理节点失败，包括回滚

**应用场景**:
- 条件分支: 某个验证失败 → 跳过后续步骤
- 回滚: 关键步骤失败 → 回滚已执行的修改

#### 6. 局部重规划

**文件**: `agents/planner.py`

**replan_subtree() 方法**:
- **功能**: 仅重规划失败的子树，而非整个 DAG
- **优势**:
  - 减少重规划成本
  - 保留已成功执行的部分
  - 提高执行效率

**工作流程**:
1. 识别失败的节点及其子树
2. 生成该子树的替代方案
3. 替换原有子树
4. 继续执行

#### 7. 集中式状态管理

**文件**: `schema.py`

**DAGState 类**:
- **设计灵感**: LangGraph 的状态管理机制
- **核心功能**:
  - `merge_result()`: 合并节点执行结果到全局状态
  - `get_node_context()`: 获取节点的执行上下文

**优势**:
- 统一的状态管理，避免分散
- 便于节点间共享信息
- 支持状态持久化和恢复

#### 8. Checkpoint 机制

**文件**: `dag/graph.py`

**方法**:
- `save_checkpoint()`: 保存当前执行状态到检查点
- `restore_checkpoint()`: 从检查点恢复执行状态

**应用场景**:
- 长时间执行任务的断点续传
- 系统崩溃后的恢复
- 调试和回溯

**实现**:
- 序列化 DAGState 和所有节点状态
- 保存到持久化存储（文件或数据库）
- 恢复时反序列化并重建执行上下文

---

## v1.0

### 核心特性：线性规划 + 顺序执行

#### 1. 线性规划

**文件**: 
- `schema.py`: `Plan`, `Step`, `StepStatus`
- `agents/planner.py`: `create_plan()`

**数据结构**:
- `Plan`: 包含多个 Step 的扁平计划
- `Step`: 单个执行步骤（包含描述、状态等）
- `StepStatus`: PENDING, IN_PROGRESS, COMPLETED, FAILED

**规划特点**:
- 生成 2-6 步的扁平计划
- 步骤之间是线性顺序关系
- 不支持并行或条件分支

#### 2. 顺序执行

**文件**: `agents/executor.py`

**方法**:
- `execute_step()`: 执行单个步骤
- `_react_loop()`: ReAct 循环

**ReAct 循环**:
```
Thought → Action → Observe
```

**执行流程**:
1. 分析当前步骤，生成 Thought
2. 选择并执行工具（Action）
3. 观察 Action 的结果（Observe）
4. 判断是否完成，否则回到步骤 1

#### 3. 完整重规划

**文件**: `agents/planner.py`

**replan() 方法**:
- **触发条件**: 步骤执行失败
- **策略**: 丢弃整个计划，重新生成新计划
- **缺点**: 
  - 浪费已成功执行的部分
  - 重规划成本高
  - 不适合复杂任务

**改进方向**: v2 的局部重规划解决了这个问题

#### 4. 基础工具

**工具列表**:
- `web_search`: 模拟的网络搜索工具
- `execute_python`: 使用 subprocess 执行 Python 代码
- `file_ops`: 沙箱环境下的文件操作工具

**工具特点**:
- 简单但功能完整
- 支持基本的任务需求
- 为后续版本的工具扩展奠定基础

#### 5. 记忆系统

**ShortTermMemory**:
- **机制**: 滑动窗口
- **功能**: 保存最近的对话和执行历史
- **容量**: 固定大小，超过则移除最旧的内容

**LongTermMemory**:
- **机制**: JSON 持久化 + 关键词检索
- **功能**: 长期存储重要信息
- **检索**: 基于关键词匹配

#### 6. 知识检索

**KnowledgeRetriever**:
- **算法**: TF-IDF + 余弦相似度
- **功能**: 从知识库中检索相关信息
- **应用**: 为任务执行提供背景知识

#### 7. Rich CLI UI

**文件**: `main.py`

**设计特点**:
- 事件驱动的 Rich 控制台界面
- 实时显示执行进度和状态
- 美观的格式化输出

**用户体验**:
- 清晰的视觉反馈
- 彩色输出，易于区分不同类型的信息
- 进度条和状态指示器

---

## 架构变化

### 三路由组件关系图

```
┌─────────────────────────────────────────────────────────┐
│                      Orchestrator                        │
│                   (任务分类与路由)                        │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  PlannerAgent │ │ EmergentPlan │ │  (其他路径)  │
│              │ │    nerAgent  │ │              │
│  (v1/v2)     │ │    (v5)      │ │              │
└──────┬───────┘ └──────┬───────┘ └──────────────┘
       │                │
       │                │
       ▼                ▼
┌──────────────┐ ┌──────────────┐
│ ExecutorAgent│ │ EmergentPlan │
│              │ │    nerAgent  │
│ (v1/v2/v6)   │ │    (v5/v6)   │
└──────────────┘ └──────────────┘
       │                │
       │                │
       ▼                ▼
┌──────────────────────────────────────┐
│           ReActEngine                │
│     (统一 ReAct 循环引擎 - v6)       │
└──────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│           Tools                      │
│  (web_search, code_executor, etc.)   │
└──────────────────────────────────────┘
```

### 路由决策流程

```
用户任务
    │
    ▼
Orchestrator.classify_task()
    │
    ├─→ simple ──→ PlannerAgent (v1) ──→ ExecutorAgent (v1)
    │
    ├─→ complex ──→ PlannerAgent (v2) ──→ ExecutorAgent (v2)
    │
    ├─→ emergent ──→ EmergentPlannerAgent (v5)
    │
    └─→ 其他路径 ──→ 相应处理
```

---

## 配置项汇总

### v6.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `LLM_RETRY_ENABLED` | `false` | v6.0 | 是否启用 LLM 调用重试机制 |
| `LLM_RETRY_MAX_ATTEMPTS` | `3` | v6.0 | LLM 调用最大重试次数 |
| `LLM_RETRY_BACKOFF_FACTOR` | `2.0` | v6.0 | LLM 重试退避因子 |
| `ENABLE_REACT_ENGINE_V2` | `false` | v6.0 | 是否启用 ReActEngine v2 统一引擎 |

### v5.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `EMERGENT_PLANNING_ENABLED` | `true` | v5.0 | 是否启用隐式规划功能 |
| `MAX_TODO_ITEMS` | `20` | v5.0 | 最大 TODO 项数量限制 |
| `TODO_COMPRESSION_THRESHOLD` | `0.8` | v5.0 | TODO 压缩阈值 |

### v4.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `PLAN_MODE` | `auto` | v4.0 | 规划模式：auto/simple/complex |

### v3.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `ADAPTIVE_PLANNING_ENABLED` | `true` | v3.0 | 是否启用自适应规划 |
| `ADAPT_PLAN_INTERVAL` | `3` | v3.0 | 自适应规划检查间隔（节点数） |
| `ADAPT_PLAN_MIN_COMPLETED` | `2` | v3.0 | 触发自适应规划的最小完成节点数 |
| `TOOL_FAILURE_THRESHOLD` | `2` | v3.0 | 工具失败阈值（达到后建议切换） |

### v2.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `MAX_PARALLEL_NODES` | `5` | v2.0 | 最大并行执行节点数 |

### v1.0 基础配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `MAX_STEPS` | `6` | v1.0 | 线性规划最大步数 |
| `MEMORY_WINDOW_SIZE` | `10` | v1.0 | 短期记忆窗口大小 |

---

## 迁移指南

### 从 v5.0 升级到 v6.0

**新增功能**:
- LLM 调用重试机制
- ReActEngine v2 统一引擎

**迁移步骤**:
1. 更新配置文件，添加新的配置项（可选）
2. 测试 LLM 重试机制：设置 `LLM_RETRY_ENABLED=true`
3. 测试 ReActEngine v2：设置 `ENABLE_REACT_ENGINE_V2=true`
4. 验证现有功能是否正常（默认使用 legacy 实现）

**注意事项**:
- 新功能默认关闭，不影响现有行为
- 建议先在测试环境验证后再启用
- ReActEngine v2 可能与现有实现有细微差异

### 从 v4.0 升级到 v5.0

**新增功能**:
- EmergentPlannerAgent 隐式规划器
- TODO 列表管理

**迁移步骤**:
1. 更新 `agents/orchestrator.py`，添加 `_execute_emergent()` 方法
2. 更新 `agents/planner.py`，添加新的正则模式
3. 更新 `schema.py`，添加 TODO 相关数据结构
4. 配置 `EMERGENT_PLANNING_ENABLED=true`（默认已启用）
5. 运行测试验证隐式规划功能

**注意事项**:
- 隐式规划适合探索性任务，不适合确定性任务
- 分类器会自动识别适合隐式规划的任务
- 可以通过 `PLAN_MODE` 强制使用特定规划方式

### 从 v3.0 升级到 v4.0

**新增功能**:
- 两阶段混合分类器
- 自动路由

**迁移步骤**:
1. 更新 `agents/planner.py`，实现两阶段分类器
2. 更新 `agents/orchestrator.py`，支持新的路由逻辑
3. 配置 `PLAN_MODE=auto`（默认已启用）
4. 测试规则分类器和 LLM 分类器的准确性

**注意事项**:
- 规则分类器覆盖约 60-70% 的任务
- 模糊任务会触发 LLM 分类器
- 可以强制使用 simple 或 complex 模式

### 从 v2.0 升级到 v3.0

**新增功能**:
- 自适应规划
- 工具路由
- 动态 DAG

**迁移步骤**:
1. 更新 `agents/planner.py`，添加 `adapt_plan()` 等方法
2. 更新 `dag/executor.py`，添加自适应规划触发逻辑
3. 更新 `dag/graph.py`，添加动态节点管理方法
4. 更新 `schema.py`，添加自适应规划相关数据结构
5. 创建 `tools/router.py`，实现工具路由功能
6. 配置 `ADAPTIVE_PLANNING_ENABLED=true`（默认已启用）

**注意事项**:
- 自适应规划会增加一定的执行开销
- 动态 DAG 需要确保无环和依赖关系正确
- 工具路由建议仅供参考，最终决策由 Agent 负责

### 从 v1.0 升级到 v2.0

**新增功能**:
- DAG 分层规划
- 并行执行
- 节点状态机
- 局部重规划

**迁移步骤**:
1. 重构 `schema.py`，引入 DAG 相关数据结构
2. 创建 `dag/graph.py`，实现 DAG 管理功能
3. 创建 `dag/executor.py`，实现并行执行器
4. 创建 `dag/state_machine.py`，实现节点状态机
5. 更新 `agents/planner.py`，支持 DAG 规划和局部重规划
6. 更新 `agents/executor.py`，适配 DAG 执行模式
7. 更新 `agents/reflector.py`，支持逐节点验证
8. 配置 `MAX_PARALLEL_NODES`

**注意事项**:
- 这是一个重大架构变更，需要充分测试
- 线性规划任务会自动转换为简单 DAG
- 并行执行需要注意线程安全和资源竞争

### 通用迁移建议

1. **备份现有配置**: 升级前备份所有配置文件
2. **渐进式升级**: 先在测试环境验证，再在生产环境部署
3. **监控日志**: 升级后密切监控系统日志，关注错误和警告
4. **性能测试**: 新功能可能影响性能，需要进行性能测试
5. **回滚计划**: 准备好回滚方案，以防升级失败
6. **文档更新**: 及时更新相关文档和用户手册

---

## 总结

Manus Demo 从 v1.0 到 v6.0 的演进过程，展现了多智能体系统在规划能力、执行效率、健壮性等方面的持续改进：

- **v1.0**: 奠定基础，实现简单的线性规划和顺序执行
- **v2.0**: 引入 DAG 和并行执行，大幅提升复杂任务处理能力
- **v3.0**: 增加自适应性和动态性，提高系统灵活性
- **v4.0**: 优化任务分类和路由，提升性能和准确性
- **v5.0**: 引入隐式规划范式，拓展任务处理范围
- **v6.0**: 增强系统健壮性，提高 LLM 调用的可靠性

每个版本都在前一个版本的基础上进行改进，同时保持向后兼容性，为用户提供平滑的升级体验。
