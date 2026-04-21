# Manus Demo v6 隐式规划测试用例集

> **生成时间**: 2026-04-20
> **版本**: v6（集成 LLM retry 与 mark_pending 重试机制）
> **目的**: 验证隐式规划系统的正确性、灵活性和容错能力

---

## 目录

1. [隐式规划核心概念](#隐式规划核心概念)
2. [如何运行测试](#如何运行测试)
3. [测试维度与观测点](#测试维度与观测点)
4. [测试用例](#测试用例)
   - [基础功能验证](#基础功能验证)
   - [TODO 列表动态管理](#todo-列表动态管理)
   - [复杂探索性任务](#复杂探索性任务)
   - [容错与重试机制测试](#容错与重试机制测试)
   - [对比测试：DAG vs 隐式](#对比测试-dag-vs-隐式)

---

## 隐式规划核心概念

### 什么是隐式规划？

隐式规划（Emergent Planning）是一种 **无预定义结构** 的规划方式，规划在执行过程中自然涌现。

**核心特征**:
- **无独立规划阶段**: 不预先完整规划所有步骤
- **TODO 列表管理**: 通过动态增删改 TODO 项来组织任务
- **while(tool_use) 循环**: 持续调用工具直到所有 TODO 完成
- **LLM 自组织**: LLM 自主决定何时添加新 TODO、何时标记完成
- **mark_pending 重试机制**: 失败 TODO 自动回退为 PENDING 状态以便重试
- **TODO 压缩**: 当上下文使用率达到阈值时压缩历史记录

### 核心数据结构

#### TodoStatus（4 种状态）

```python
class TodoStatus(str, Enum):
    PENDING = "pending"       # 等待执行
    IN_PROGRESS = "in_progress"  # 正在执行
    COMPLETED = "completed"   # 已完成
    BLOCKED = "blocked"       # 被阻塞（依赖未满足）
```

#### TodoItem（TODO 项）

```python
class TodoItem(BaseModel):
    id: int                                    # 唯一标识
    description: str                           # 任务描述
    status: TodoStatus = TodoStatus.PENDING    # 当前状态
    dependencies: list[int] = []               # 前置依赖 ID 列表
    result: str | None = None                  # 执行结果
    created_at: float                          # 创建时间戳
    updated_at: float                          # 最后更新时间戳
```

#### TodoList（TODO 列表管理）

```python
class TodoList(BaseModel):
    task: str                                   # 原始用户任务
    todos: dict[int, TodoItem]                  # 按 ID 索引的 TODO 项
    next_id: int                                # 下一个可用 TODO ID
    
    def add_todo(self, description: str, dependencies: list[int] | None = None) -> TodoItem:
        """添加新 TODO 项"""
        
    def get_pending_todos(self) -> list[TodoItem]:
        """获取所有可执行的 TODO 项（状态为 PENDING 或 IN_PROGRESS）"""
        
    def get_ready_todos(self) -> list[TodoItem]:
        """获取所有依赖已满足的 TODO 项（仅 PENDING 状态且依赖已完成）"""
        
    def mark_completed(self, todo_id: int, result: str) -> None:
        """标记 TODO 为已完成"""
        
    def mark_in_progress(self, todo_id: int) -> None:
        """标记 TODO 为正在执行"""
        
    def mark_pending(self, todo_id: int) -> None:
        """标记 TODO 为等待执行（用于失败后重试）"""
        
    def is_complete(self) -> bool:
        """检查是否所有 TODO 都已完成"""
        
    def has_pending(self) -> bool:
        """检查是否有待执行的 TODO（PENDING 或 IN_PROGRESS）"""
```

### 执行流程

```
1. 初始化 TODO 列表（1-3 个初始项）
2. while has_pending_todos():
     - 选择下一个就绪 TODO
     - 执行 ReAct 循环（think_with_tools）
     - 成功 → mark_completed
     - 失败 → mark_pending（回退到 PENDING 以便重试）
     - 基于执行结果动态添加新 TODO
     - 检查是否需要压缩上下文
3. 汇总所有已完成 TODO 的结果
```

### 与 DAG 规划的对比

| 维度 | DAG 规划 (v2/v4) | 隐式规划 (v6) |
|------|-----------------|--------------|
| **规划时机** | 执行前完整规划 | 执行中动态涌现 |
| **结构** | 三层层级 DAG (Goal→SubGoal→Action) | 扁平 TODO 列表 |
| **状态管理** | 7 种节点状态 | 4 种 TODO 状态 |
| **变更方式** | 超步间自适应调整 | 随时增删改 TODO |
| **重试机制** | 失败后局部重规划 | mark_pending 自动重试 |
| **LLM 容错** | 依赖上层重试 | 内置 LLM retry + mark_pending |
| **适用场景** | 目标明确的复杂多阶段任务 | 探索性、不确定性强的任务 |
| **可预测性** | 高（预先知道所有步骤） | 低（根据执行发现新工作） |
| **灵活性** | 中等 | 极高 |

### 典型适用场景

✅ **适合隐式规划的任务**:
- 探索性研究（"帮我调研这个领域的最新进展"）
- 代码审查与改进建议（"分析这个项目的代码质量并提出改进建议"）
- 开放式问题解决（"我想提高这个系统的性能，该怎么做？"）
- 需求不明确的创意任务
- 需要高容错能力的任务（网络不稳定环境）

✅ **适合 DAG 规划的任务**:
- 目标明确的多阶段任务（"调研 X→实现 Y→验证 Z"）
- 需要严格依赖管理的任务
- 需要并行执行的场景

---

## 如何运行测试

### 运行方式

```bash
# 交互模式（推荐）
PLAN_MODE=emergent python main.py

# 单次任务模式
PLAN_MODE=emergent python main.py "任务描述"

# 启用 LLM retry 机制（推荐用于网络不稳定环境）
LLM_RETRY_ENABLED=true PLAN_MODE=emergent python main.py

# 调整 TODO 上限测试
MAX_TODO_ITEMS=30 PLAN_MODE=emergent python main.py "复杂任务"

# 对比测试：同一任务分别用 DAG 和隐式规划
PLAN_MODE=complex python main.py "任务描述"   # DAG 路径
PLAN_MODE=emergent python main.py "任务描述"  # 隐式路径
```

### 关键配置项

```bash
# 隐式规划开关
EMERGENT_PLANNING_ENABLED=true

# TODO 列表最大项数（默认 20）
MAX_TODO_ITEMS=20

# 上下文 Token 上限（默认 8000）
MAX_CONTEXT_TOKENS=8000

# 最大迭代次数（默认 10，控制 mark_pending 重试次数）
MAX_REACT_ITERATIONS=10

# LLM 重试机制（v6 新增）
LLM_RETRY_ENABLED=false              # 默认关闭（向后兼容）
LLM_RETRY_MAX_ATTEMPTS=3             # 最大重试次数
LLM_RETRY_BACKOFF_FACTOR=2.0         # 退避因子（2^attempt 秒）

# ReAct Engine v2（可选）
ENABLE_REACT_ENGINE_V2=false         # 默认使用 legacy 实现
```

### 终端观测信号

在交互模式下，关注以下输出：

1. **TODO 列表初始化**:
   ```
   ⏳ TODO 1: 初始任务项
   ⏳ TODO 2: 另一初始项
   ```

2. **TODO 执行过程**:
   ```
   🔄 TODO 1: 正在执行...
   ✅ TODO 1: 已完成
   ⏳ TODO 3: 新发现的 TODO（动态添加）
   ```

3. **TODO 失败与重试**:
   ```
   ❌ TODO 2: 执行失败（网络错误）
   ⏳ TODO 2: 重置为 PENDING（准备重试）
   🔄 TODO 2: 重新执行...
   ✅ TODO 2: 已完成
   ```

4. **LLM 重试日志**（当 `LLM_RETRY_ENABLED=true`）:
   ```
   [LLMClient] Retryable error on attempt 1: RateLimitError. Waiting 2.0s...
   [LLMClient] Retryable error on attempt 2: APITimeoutError. Waiting 4.0s...
   ```

5. **工具调用日志**:
   - 查看 `web_search` / `execute_python` / `file_ops` 的使用
   - 观察 LLM 如何自主选择工具

6. **TODO 列表更新**:
   - 每轮迭代后显示当前 TODO 列表状态
   - 观察 TODO 的增删改行为

---

## 测试维度与观测点

### 1. TODO 列表管理

**观测点**:
- 初始 TODO 数量是否合理（通常 1-3 个）
- TODO 依赖关系是否正确建模
- 动态添加的 TODO 是否与任务相关
- TODO 完成判据是否清晰
- `get_pending_todos()` 和 `get_ready_todos()` 的正确性

**预期行为**:
```
初始：⏳ TODO 1: 理解任务
执行中：
  ✅ TODO 1 完成
  ⏳ TODO 2: 搜索相关信息（动态添加）
  ⏳ TODO 3: 分析结果（动态添加）
完成：所有 TODO ✅
```

### 2. 工具使用策略

**观测点**:
- 工具选择是否合理
- 是否在失败后尝试替代方案
- 工具调用参数是否正确

**预期行为**:
- 研究性任务 → 优先 `web_search`
- 代码相关 → 优先 `execute_python` / `file_ops`
- 失败 2 次后尝试其他工具（Tool Router 机制）

### 3. 任务完成质量

**观测点**:
- 最终答案是否完整
- 是否覆盖所有关键方面
- 解释是否清晰有条理

### 4. 容错与重试机制（v6 新增）

**观测点**:
- TODO 失败后是否正确回退为 PENDING
- LLM retry 是否在网络错误时生效
- 重试是否最终成功
- 重试次数是否在限制内

**预期行为**:
```
网络不稳定场景：
  TODO 执行 → LLM 调用失败
  → mark_pending 回退
  → 下一轮重试
  → LLM retry 触发（如果启用）
  → 最终成功
```

### 5. 与 DAG 路径的对比

**对比维度**:
- 执行时间
- 步骤数量
- 结果质量
- 灵活性（应对意外发现的能力）
- 容错能力（网络不稳定时的表现）

---

## 测试用例

### 基础功能验证

#### E1: 简单查询任务

**任务描述**:
```
查询 Python 最新的版本号
```

**预期行为**:
- 初始 TODO: 1-2 个（理解任务 → 搜索 → 回答）
- 工具调用：`web_search`
- 执行轮次：1-2 轮
- TODO 动态添加：可能无（简单任务）

**观测重点**:
- TODO 列表是否正确初始化
- 工具调用是否准确
- 结果是否正确

**评价标准**:
- ✅ 快速完成（<30 秒）
- ✅ TODO 状态流转正确（PENDING → IN_PROGRESS → COMPLETED）
- ✅ 答案准确

---

#### E2: 简单计算与文件操作

**任务描述**:
```
创建一个 Python 脚本，计算 1 到 100 的和，然后运行它
```

**预期行为**:
- 初始 TODO: 2 个左右
- 工具调用：`file_ops`（写文件）→ `execute_python`（运行）
- TODO 动态更新：可能在执行中发现需要调试

**观测重点**:
- TODO 依赖是否正确（先写文件，再运行）
- 文件是否正确创建
- 执行结果是否正确（5050）

---

### TODO 列表动态管理

#### E3: 探索性研究任务

**任务描述**:
```
帮我调研 Python 中的异步编程模型，并总结主要概念
```

**预期行为**:
- 初始 TODO: 2-3 个（搜索 → 整理）
- 动态添加 TODO:
  - 发现重要子话题（如 asyncio、await/async）
  - 添加对应的研究 TODO
- 执行轮次：3-5 轮

**观测重点**:
- TODO 列表如何随探索过程扩展
- 新增 TODO 是否合理
- 最终总结是否全面

**评价标准**:
- ✅ TODO 数量适中（不超过 10 个）
- ✅ 覆盖核心概念（async/await、asyncio、事件循环）
- ✅ 解释清晰易懂

---

#### E4: 代码分析与改进建议

**任务描述**:
```
分析 manus_demo 项目的代码结构，指出潜在问题并提出改进建议
```

**预期行为**:
- 初始 TODO: 2-3 个
- 动态添加 TODO:
  - 发现特定模块需要深入分析
  - 添加针对该模块的检查 TODO
  - 可能添加编写示例代码的 TODO
- 工具调用：`file_ops`（读文件）频繁

**观测重点**:
- TODO 列表如何反映分析深度
- 发现的问题是否切中要害
- 改进建议是否可行

**预期行为示例**:
```
初始 TODO:
  ⏳ TODO 1: 浏览项目结构
  ⏳ TODO 2: 分析主要模块

执行中发现:
  ✅ TODO 1 完成（发现 agents/ 目录复杂）
  ⏳ TODO 3: 深入分析 agents/orchestrator.py（动态添加）
  ⏳ TODO 4: 检查错误处理（动态添加）
  ⏳ TODO 5: 提出重构建议（动态添加）
```

---

### 复杂探索性任务

#### E5: 性能优化建议

**任务描述**:
```
我想优化 manus_demo 项目的执行性能，请分析瓶颈并给出具体的优化方案
```

**预期行为**:
- 初始 TODO: 理解需求 → 分析代码 → 识别瓶颈
- 动态添加:
  - 针对发现的每个瓶颈添加分析 TODO
  - 可能添加编写基准测试的 TODO
  - 添加实现优化方案的 TODO
- 工具调用：`file_ops`（读代码）+ `execute_python`（性能测试）

**观测重点**:
- TODO 列表如何反映分析的系统性
- 优化建议是否具体可行
- 是否提供代码示例

**评价标准**:
- ✅ 识别出真实瓶颈（如 I/O 等待、串行执行）
- ✅ 提供具体的优化代码或策略
- ✅ 解释优化原理

---

#### E6: 开放式创意任务

**任务描述**:
```
为 manus_demo 项目设计一个新功能，让它能够自动学习用户的偏好并调整执行策略
```

**预期行为**:
- 初始 TODO: 理解需求 → 调研 → 设计
- 动态添加:
  - 用户偏好存储方案调研
  - 学习算法选择
  - 与原系统的集成方案
  - 可能添加原型实现 TODO
- 执行轮次：5-8 轮

**观测重点**:
- TODO 列表如何支撑创意发散
- 设计方案是否完整
- 是否考虑实际可行性

---

### 容错与重试机制测试（v6 新增）

#### E7: mark_pending 重试机制测试

**任务描述**:
```
执行一个可能失败的任务（如访问不存在的网页），观察 TODO 失败后的重试行为
```

**预期行为**:
- TODO 执行失败
- 自动调用 `mark_pending()` 将状态回退为 PENDING
- 下一轮循环重新选择该 TODO
- 最多重试 `MAX_REACT_ITERATIONS` 次（默认 10 次）
- 每次重试都是完整的 ReAct 循环

**观测重点**:
- TODO 状态是否正确回退（IN_PROGRESS → PENDING）
- 重试是否在下一轮自动触发
- 重试次数是否合理

**终端信号**:
```
🔄 TODO 1: 正在执行...
⏳ TODO 1: 重置为 PENDING（失败后回退）
🔄 TODO 1: 重新执行...
✅ TODO 1: 已完成
```

**观测重点**:
- 查看 `_emit("todo_failed")` 事件日志
- 确认 TODO 状态从 IN_PROGRESS 回退到 PENDING

---

#### E8: LLM Retry 机制测试（网络不稳定场景）

**前置条件**:
```bash
LLM_RETRY_ENABLED=true LLM_RETRY_MAX_ATTEMPTS=3 PLAN_MODE=emergent python main.py
```

**任务描述**:
```
执行一个需要多次 LLM 调用的复杂任务，模拟网络不稳定环境
```

**预期行为**:
- 某次 LLM 调用失败（RateLimitError、APITimeoutError）
- LLMClient 自动触发重试
- 指数退避等待（2^attempt 秒）
- 最多重试 3 次

**观测重点**:
- 是否看到重试日志
- 退避时间是否符合预期（2s、4s、8s）
- 重试后是否成功

**终端信号**:
```
[LLMClient] Retryable error on attempt 1: RateLimitError. Waiting 2.0s...
[LLMClient] Retryable error on attempt 2: APITimeoutError. Waiting 4.0s...
[LLMClient] Retry attempt 3 succeeded
```

---

#### E9: 双重容错测试（mark_pending + LLM Retry）

**前置条件**:
```bash
LLM_RETRY_ENABLED=true PLAN_MODE=emergent python main.py
```

**任务描述**:
```
执行一个容易失败的任务，测试 mark_pending 和 LLM retry 的协同工作
```

**预期行为**:
- LLM 调用失败 → LLM retry 触发（最多 3 次）
- 如果 LLM retry 仍失败 → TODO mark_pending 回退
- 下一轮循环重新执行 TODO
- 再次尝试 LLM retry

**观测重点**:
- 两层容错机制是否正常工作
- 是否最终成功完成任务
- 重试总次数是否合理（mark_pending × LLM retry）

**预期流程**:
```
第 1 轮：
  LLM 调用失败 → retry 1 → retry 2 → retry 3 → 仍失败
  → TODO mark_pending

第 2 轮：
  重新执行 TODO → LLM 调用失败 → retry 1 → 成功
  → TODO mark_completed
```

---

#### E10: TODO 压缩机制测试

**前置条件**:
```bash
# 降低 Token 上限以快速触发压缩（默认 8000）
MAX_CONTEXT_TOKENS=3000 PLAN_MODE=emergent python main.py "复杂长任务"
```

**任务描述**:
```
执行一个需要大量工具调用的任务，观察 TODO 压缩是否触发
```

**预期行为**:
- 上下文 Token 数量超过 `MAX_CONTEXT_TOKENS`（默认 8000）
- ContextManager 自动触发压缩
- 保留 system prompt 和最近 6 条消息
- 将旧消息压缩为单条摘要
- 压缩后继续执行，TODO 列表状态保持一致

**观测重点**:
- 是否看到压缩日志：`"Compressed context: X tokens -> ~Y tokens"`
- 压缩后 TODO 执行是否正常
- 最终结果是否完整
- TODO 状态是否丢失

**压缩策略**:
```
原始上下文: [system_prompt] + [旧消息 N 条] + [最近 6 条消息]
压缩后:     [system_prompt] + [摘要消息] + [最近 6 条消息]
```

---

### 对比测试：DAG vs 隐式

#### E11: 同一任务的两种规划方式对比

**任务描述**（分别用 `PLAN_MODE=complex` 和 `PLAN_MODE=emergent` 运行）:
```
调研 Python 并发模型（线程、多进程、asyncio），分别总结优缺点，并编写示例代码展示 asyncio 的并发下载能力
```

**对比维度**:

| 维度 | DAG 规划 (complex) | 隐式规划 (emergent) |
|------|-------------------|---------------------|
| **规划结构** | Goal→SubGoals→Actions 三层 | 扁平 TODO 列表 |
| **状态管理** | 7 种节点状态 | 4 种 TODO 状态 |
| **初始步骤** | 预先完整规划所有子目标 | 仅 2-3 个初始 TODO |
| **执行过程** | 按超步并行执行 | 动态发现新 TODO |
| **重试机制** | 失败后局部重规划 | mark_pending 自动重试 |
| **灵活性** | 中等（需自适应规划调整） | 高（随时添加 TODO） |
| **执行时间** | 较快（并行优势） | 可能较慢（探索性） |
| **结果质量** | 结构化强 | 可能更有创意 |
| **网络容错** | 依赖上层重试 | 双重容错（LLM retry + mark_pending） |

**观测重点**:
- DAG 路径：查看 DAG 树形结构、超步并行度
- 隐式路径：查看 TODO 列表演化过程
- 对比最终结果的完整性和深度

**预期差异**:
- DAG 路径更快完成（并行执行）
- 隐式路径可能发现更多边缘话题
- 两者结果质量应相当，但风格不同
- 网络不稳定时隐式路径容错能力更强

---

#### E12: 条件分支任务对比

**任务描述**:
```
搜索一个流行的 Python Web 框架，如果它的 GitHub stars 大于 10000，就分析它的架构设计；否则分析一个 stars 大于 5000 的备选框架
```

**DAG 规划预期**:
- 预先规划条件分支（CONDITIONAL 边）
- 明确的主路径和备选路径
- 条件评估后跳过对应分支

**隐式规划预期**:
- 初始 TODO: 搜索框架
- 根据搜索结果动态决定下一步
- 可能添加"检查 stars 数"的 TODO
- 自然流向主路径或备选路径

**对比重点**:
- DAG 的条件建模更明确（CONDITIONAL 边）
- 隐式规划的条件判断更自然（LLM 自主决定）
- 两种方式都应正确处理条件分支

---

### 边界与压力测试

#### E13: TODO 列表上限测试

**任务描述**:
```
尽可能全面地分析 manus_demo 项目的所有方面：代码结构、设计模式、依赖关系、测试覆盖、文档完整性、性能瓶颈、安全性、可维护性...
```

**预期行为**:
- TODO 列表快速增长
- 达到 `MAX_TODO_ITEMS` (默认 20) 后停止添加
- LLM 需要优先处理重要 TODO

**观测重点**:
- TODO 列表满时的处理策略
- 是否合理压缩或合并 TODO
- 最终结果的完整性

**配置调整**:
```bash
# 临时提高上限测试
MAX_TODO_ITEMS=50 PLAN_MODE=emergent python main.py "..."
```

---

#### E14: 长周期任务测试

**任务描述**:
```
完整调研 Python 机器学习生态，包括主流框架（PyTorch、TensorFlow、scikit-learn）、各自优缺点、适用场景，并为每个框架编写一个简单的示例代码
```

**预期行为**:
- 大量 TODO 动态添加
- 多轮工具调用
- 执行时间较长（>2 分钟）

**观测重点**:
- TODO 列表管理的稳定性
- 是否出现重复 TODO
- 长期执行后是否仍能保持连贯性

---

#### E15: 极端网络环境测试

**前置条件**:
```bash
LLM_RETRY_ENABLED=true LLM_RETRY_MAX_ATTEMPTS=5 PLAN_MODE=emergent python main.py
```

**任务描述**:
```
模拟网络极不稳定的场景，执行一个中等复杂度的任务
```

**预期行为**:
- 频繁的 LLM 调用失败
- LLM retry 频繁触发
- mark_pending 频繁回退
- 最终仍能完成任务（容错能力验证）

**观测重点**:
- 重试次数是否在限制内
- 是否最终成功
- 执行时间是否可接受

---

## 测试结果记录模板

### 单次测试记录

```markdown
**用例 ID**: E#
**运行时间**: YYYY-MM-DD HH:MM
**PLAN_MODE**: emergent
**任务描述**: ...

**配置参数**:
- MAX_TODO_ITEMS: 20
- MAX_CONTEXT_TOKENS: 8000
- MAX_REACT_ITERATIONS: 10
- LLM_RETRY_ENABLED: false

**TODO 列表演化**:
- 初始 TODO 数量: X
- 最大 TODO 数量: Y
- 最终完成 TODO: Z
- 动态添加次数: N

**工具调用统计**:
- web_search: A 次
- execute_python: B 次
- file_ops: C 次

**执行时间**: T 秒

**重试统计**（v6 新增）:
- TODO 失败次数: X
- mark_pending 调用次数: Y
- LLM retry 次数: Z（如果启用）

**结果质量**:
- [ ] 优秀
- [ ] 良好
- [ ] 一般
- [ ] 较差

**观察到的问题**:
...

**改进建议**:
...
```

---

## 故障排查指南

### 常见问题

#### 1. TODO 列表增长过快

**现象**: TODO 数量迅速达到上限（20 个）

**可能原因**:
- LLM 过度拆分任务
- 每个 TODO 粒度过细

**排查方法**:
- 查看 TODO 描述是否过于具体
- 检查是否有重复 TODO

**解决建议**:
- 调整任务描述，使其更聚焦
- 暂时降低 `MAX_TODO_ITEMS` 观察行为

---

#### 2. TODO 列表停滞

**现象**: TODO 列表长时间不更新

**可能原因**:
- LLM 无法决定下一步
- 工具调用失败导致阻塞
- 所有 TODO 处于 BLOCKED 状态

**排查方法**:
- 查看工具调用日志
- 检查是否有 TODO 一直处于 IN_PROGRESS
- 检查依赖关系是否形成循环

**解决建议**:
- 简化任务描述
- 检查工具配置（API Key 等）
- 检查 `get_ready_todos()` 是否正确返回

---

#### 3. 结果质量不佳

**现象**: 最终答案零散、不连贯

**可能原因**:
- TODO 之间缺乏逻辑关联
- 缺少汇总步骤

**解决建议**:
- 在任务描述中明确要求"最后总结"
- 考虑使用 DAG 路径（更适合结构化任务）

---

#### 4. LLM 重试频繁失败（v6 新增）

**现象**: 大量 LLM retry 日志，最终任务失败

**可能原因**:
- 网络持续不稳定
- API Key 配额不足
- 模型服务异常

**排查方法**:
- 检查网络连接
- 检查 API Key 状态
- 查看错误类型（RateLimitError、APITimeoutError）

**解决建议**:
- 检查网络环境
- 增加 `LLM_RETRY_MAX_ATTEMPTS`
- 检查 API 配额
- 临时切换到备用模型

---

#### 5. TODO 压缩后状态丢失（v6 新增）

**现象**: 上下文压缩后 TODO 状态异常

**可能原因**:
- 压缩逻辑错误
- TodoList 序列化问题

**排查方法**:
- 查看压缩日志
- 检查压缩前后的 TODO 状态

**解决建议**:
- 检查 `ContextManager` 实现
- 确认 TodoList 的 `model_dump()` 和 `model_validate()` 正确

---

## 总结与建议

### 何时使用隐式规划

**推荐使用**:
- ✅ 探索性、研究性任务
- ✅ 需求不明确的开放式问题
- ✅ 需要创意发散的場景
- ✅ 边执行边发现新信息的任务
- ✅ 网络不稳定环境（配合 LLM_RETRY_ENABLED=true）

**不推荐使用**:
- ❌ 目标非常明确的线性任务（用 Simple 路径）
- ❌ 需要严格并行控制的任务（用 Complex/DAG 路径）
- ❌ 对执行时间敏感的任务

### 最佳实践

1. **任务描述清晰但不过度约束**:
   - 说明目标，但给 LLM 留出发掘空间
   - 示例："调研 X 领域" vs "先搜索 A，然后分析 B，最后总结 C"

2. **合理设置 TODO 上限**:
   - 默认 20 个适合大多数任务
   - 复杂研究任务可提高到 30-50

3. **与 DAG 路径对比使用**:
   - 同一任务分别尝试两种方式
   - 根据结果选择更适合的路径

4. **关注 TODO 列表演化**:
   - 观察 LLM 如何自主管理任务
   - 从中理解模型的决策逻辑

5. **网络不稳定环境启用容错**:
   ```bash
   LLM_RETRY_ENABLED=true PLAN_MODE=emergent python main.py
   ```

6. **监控重试行为**:
   - 关注 mark_pending 调用次数
   - 关注 LLM retry 成功率
   - 避免无限重试浪费资源

### v6 新特性使用建议

1. **LLM Retry 机制**:
   - 生产环境建议启用（`LLM_RETRY_ENABLED=true`）
   - 根据网络质量调整重试参数（`LLM_RETRY_MAX_ATTEMPTS`、`LLM_RETRY_BACKOFF_FACTOR`）
   - 注意监控重试成本和延迟

2. **mark_pending 重试**:
   - 自动启用，无需配置
   - 配合最大迭代次数控制（`MAX_REACT_ITERATIONS`，默认 10）
   - 适合处理临时性错误（网络波动、API 限流）
   - 每次重试都是完整的 ReAct 循环，消耗 Token 较多

3. **TODO 压缩**:
   - 长任务自动触发（当上下文超过 `MAX_CONTEXT_TOKENS` 时）
   - ContextManager 保留 system prompt 和最近 6 条消息
   - 可调整最大 Token 限制（`MAX_CONTEXT_TOKENS`）
   - 确保压缩不丢失关键状态（TODO 状态通过序列化保留）

---

**文档版本**: v6.0
**最后更新**: 2026-04-20
**基于源码**: agents/emergent_planner.py, schema.py, config.py, agents/orchestrator.py, llm/client.py
