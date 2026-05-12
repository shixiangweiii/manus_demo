# Manus Demo 更新日志

> **更新日期**: 2026-05-12
> **当前版本**: v8.0

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
v7 → 全链路 Tracing（OpenTelemetry 标准 Span 树）+ 内置 Web Viewer（FastAPI 树形可视化）
v8 → 目标驱动规划（ReflAct 风格「以终为始」）+ GoalDocument 持久锚定 + 逆向里程碑规划 + 目标状态反思 + 周期性重锚定 + 停滞检测
```

---

## v8.0 (2026-05-12)

### 核心特性：目标驱动规划 —— 「以终为始」

受 ReflAct（EMNLP 2025）目标状态反思和逆向规划启发，v8 在 v5 隐式规划基础上引入 GoalDocument 持久锚定机制，防止长流程任务中的目标漂移。

#### 1. GoalDrivenPlannerAgent（目标驱动规划器）

**新增文件**:
- `agents/goal_driven_planner.py`: GoalDrivenPlannerAgent — 「以终为始」执行引擎（894 行）

**核心思想**:
- **GoalDocument 持久锚定**：任务开始时定义「完成标准」，跨迭代持久化，目标永不丢失
- **逆向规划**：从终态推导里程碑，而非从任务描述正向规划，确保每个步骤都与目标对齐
- **目标状态反思（ReflAct 风格）**：每次行动前对比当前状态与目标文档，识别差距并指导下一步
- **有界消息上下文**：滑动窗口管理消息历史（最多保留 20 条），而非 v5 的无界扁平历史
- **反思驱动的主动 TODO 刷新**：基于目标反思结论主动调整 TODO，而非仅失败时被动刷新
- **周期性目标重锚定**：每隔 N 次迭代重新评估目标文档，检测目标偏移并纠正
- **停滞检测**：连续多轮无进度突破时提前终止，避免无限循环

**核心循环**:
```
1. 构建 GoalDocument（定义「完成标准」）
2. 逆向规划：从终态推导里程碑序列 → MilestonePlan
3. 里程碑转 TodoList（有序依赖链）
4. while has_pending and iteration < max:
   A. GoalReflection: 对比当前状态 vs 目标文档
   B. 选择 TODO（由反思的 next_milestone 指导）
   C. 执行 TODO（目标引导的 ReAct 循环，注入 GoalDocument）
   D. 更新 TODO 状态
   E. 目标重锚定（周期性或失败时触发）
   F. 主动 TODO 刷新（失败 / replan 建议 / 每 3 轮）
5. 对照目标文档汇编最终答案
```

**主要方法签名**:
```python
class GoalDrivenPlannerAgent(BaseAgent):
    async def execute(self, task: str, context: str = "") -> str
    async def _build_goal_document(self, task: str, context: str = "") -> GoalDocument
    async def _backward_plan(self, goal_doc: GoalDocument) -> MilestonePlan
    def _milestones_to_todos(self, plan: MilestonePlan, task: str) -> TodoList
    async def _goal_reflect(self, goal_doc: GoalDocument, todo_list: TodoList, iteration: int) -> GoalReflection
    def _select_todo_by_reflection(self, reflection: GoalReflection) -> TodoItem | None
    async def _execute_todo_goal_guided(self, todo: TodoItem, goal_doc: GoalDocument, reflection: GoalReflection) -> StepResult
    async def _reanchor_goal(self, goal_doc: GoalDocument, todo_list: TodoList, last_result: StepResult) -> GoalDocument
    async def _refresh_todo_list(self, goal_doc: GoalDocument, todo_list: TodoList, last_result: StepResult) -> None
    async def _compile_goal_anchored_answer(self, task: str, goal_doc: GoalDocument, results: list[StepResult]) -> str
```

#### 2. 数据模型（schema.py 新增 v8 模型）

| 模型 | 用途 |
|------|------|
| `Milestone` | 当前状态与目标之间的检查点（id, description, completion_criteria, estimated_complexity） |
| `MilestonePlan` | 从目标到当前状态的逆向规划里程碑序列 |
| `GoalDocument` | 持久化目标状态，锚定所有规划和执行（original_task, success_criteria, target_state_description, key_deliverables, constraints, progress_pct 等） |
| `GoalReflection` | 每次迭代的目标状态对比结果（current_state_summary, gap_analysis, next_milestone, progress_pct, suggested_action） |
| `GoalReanchorResult` | 周期性目标重锚定结果（updated_goal_doc, goal_drift_detected, correction_applied） |

#### 3. Orchestrator 路由增强

**文件**: `agents/orchestrator.py`

**变更**:
- 新增 v8 GoalDrivenPlannerAgent 初始化逻辑（`ENABLE_GOAL_DRIVEN_PLANNER=true` 时创建）
- `_execute_emergent()` 方法变更：当 `self.goal_driven_planner` 存在时路由到 v8，否则回退到 v5
- v8 路由的质量门控：检查是否有 BLOCKED TODO，若有则发出 Reflection 事件（score=0.4）

**路由逻辑**:
```python
# 当 ENABLE_GOAL_DRIVEN_PLANNER=true 时，emergent 路径升级为 v8
if self.goal_driven_planner:
    final_answer = await self.goal_driven_planner.execute(task, context)
else:
    final_answer = await self.emergent_planner.execute(task, context)
```

#### 4. Tracing 集成（v8 Span 事件）

**文件**: `tracing/spans.py`（新增常量）

**Span 名称**:
- `execution.goal_driven`: v8 目标驱动执行阶段
- `goal.anchor`: 目标锚定
- `goal.reflect`: 目标状态反思
- `goal.reanchor`: 目标重锚定

**Attribute 键名**:
- `goal.success_criteria`, `goal.progress_pct`, `goal.current_milestone`
- `goal.drift_detected`, `goal.target_state`
- `goal.reflection.action`, `goal.reflection.gap`

**文件**: `tracing/bridge.py`（新增事件处理器）

**事件处理器**:
- `goal_anchor`: 记录初始目标文档（success_criteria, progress_pct）
- `goal_reflection`: 记录目标状态反思（progress_pct, suggested_action, gap_analysis, next_milestone）
- `goal_reanchor`: 记录目标重锚定事件（goal_drift_detected, progress_pct）

**阶段映射** (`_phase_to_span_name` 新增):
- "goal-driven" / "v8" → `execution.goal_driven`
- "building goal" / "backward planning" → `goal.anchor`
- "compiling final answer against goal" → `goal.anchor`

#### 5. 配置项

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| ENABLE_GOAL_DRIVEN_PLANNER | false | v8 目标驱动规划总开关（默认关闭，向后兼容） |
| GOAL_REANCHOR_INTERVAL | 5 | 每隔多少次外层迭代重新锚定目标文档 |
| GOAL_REFLECTION_INTERVAL | 1 | 每隔多少次外层迭代执行目标反思（1=每次都反思） |
| MAX_GOAL_DRIVEN_ITERATIONS | MAX_TODO_ITEMS × MAX_TODO_RETRIES | v8 主循环最大迭代数 |
| GOAL_DRIVEN_STAGNATION_WINDOW | 3 | 连续多少轮无进度突破则提前终止 |

#### 6. 与 v5 EmergentPlanner 的关键区别

| 特性 | v5 EmergentPlanner | v8 GoalDrivenPlanner |
|------|--------------------|-----------------------|
| 目标锚定 | 无持久目标，容易漂移 | GoalDocument 跨迭代持久化 |
| 规划方式 | 从任务描述正向规划 | 从终态逆向规划里程碑 |
| 反思机制 | 通用"think"步骤 | ReflAct 风格结构化目标对比 |
| 消息管理 | 无界扁平历史 | 滑动窗口（最多 24 条） |
| TODO 刷新 | 仅失败时被动刷新 | 反思驱动的主动刷新 |
| 目标偏移检测 | 无 | 周期性重锚定 + drift 检测 |
| 停滞检测 | 无 | 连续 N 轮无进度突破时终止 |
| 触发方式 | 自动分类（emergent 路径） | 特性开关覆盖 emergent 路径 |

#### 7. 测试覆盖

**文件**: `tests/test_goal_driven_planner.py`（约 40 个测试用例）

**测试内容**:
- 数据模型测试：GoalDocument、MilestonePlan、GoalReflection、GoalReanchorResult
- Agent 核心逻辑测试：初始化、构建目标文档、逆向规划、目标反思、TODO 选择、停滞检测、重锚定、答案汇编
- Orchestrator 路由测试：v8 启用时路由到 GoalDrivenPlannerAgent，禁用时回退到 v5
- 事件测试：验证 v8 事件序列（goal_anchor、todo_list_initialized、goal_reflection 等）和 TracingBridge 兼容性
- 目标引导 ReAct 循环测试：无工具调用完成、最大迭代限制、目标注入验证

---

## v7.0 (2026-05-11)

### 核心特性：全链路 Tracing + Web 可视化

#### 1. Tracing 模块（全链路追踪）

**新增文件**:
- `tracing/__init__.py`: 模块入口，TRACING_ENABLED=false 时提供 no-op stubs
- `tracing/config.py`: 集中管理 tracing 配置常量
- `tracing/spans.py`: Span 名称、Attribute 键名、Event 名称语义常量
- `tracing/provider.py`: TracerProvider 工厂（Resource + Exporter + Sampler）
- `tracing/exporters.py`: FileSpanExporter（JSON 文件）+ RichConsoleExporter（树形控制台）
- `tracing/decorators.py`: @traced、@traced_llm_call、@traced_tool_call 声明式装饰器
- `tracing/bridge.py`: TracingBridge 事件桥接器（_emit 事件 → OTel Span）

**修改文件**:
- `llm/client.py`: 新增 _start_llm_span/_end_llm_span，LLM 调用自动创建 Span
- `tools/base.py`: 新增 traced_execute() 和 _sanitize_params()，工具调用自动 trace + 敏感参数脱敏
- `agents/orchestrator.py`: 集成 TracingBridge 到多播事件系统
- `config.py`: 新增 TRACING_* 系列配置项

**设计原则**:
- **零侵入**：通过事件桥接 + 装饰器集成，核心 Agent 业务逻辑零改动
- **零开销**：TRACING_ENABLED=false 时不创建 Span、不加载 OpenTelemetry
- **多后端支持**：console/file/rich/otlp/phoenix
- **隐私保护**：默认不记录 prompt，敏感字段自动脱敏

**Span 层级结构**:
```
task_execution
├── orchestrator.gather_context
├── planner.classify_task
│   └── llm.chat
├── execution.simple / execution.dag / execution.emergent
│   ├── llm.chat_with_tools
│   └── tool.execute.{name}
├── reflector.reflect
└── memory.store
```

#### 2. Trace Web Viewer（内置可视化）

**新增文件**:
- `tracing/server.py`: FastAPI 应用（API + 页面路由 + Span 树构建）
- `tracing/__main__.py`: CLI 入口（python -m tracing 启动 Web 服务）
- `tracing/templates/base.html`: 暗色主题基础模板
- `tracing/templates/trace_list.html`: Trace 列表页
- `tracing/templates/trace_detail.html`: Trace 详情页（可折叠树形 + 属性面板）

**功能**:
- **列表页**：展示所有 trace 文件（状态、根 Span、Span 数量、耗时）
- **详情页**：可折叠树形结构展示 Span 层级，点击节点展开 attributes/events
- **JSON API**：/api/traces、/api/traces/{file_id}
- **启动方式**：`python -m tracing --port 8600`

**安全措施**:
- Jinja2 tojson 过滤器防 XSS
- file_id 路径遍历防御
- Span 树循环引用检测

#### 3. 配置项

| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| TRACING_ENABLED | false | 总开关 |
| TRACING_BACKEND | console | 导出后端 |
| TRACING_ENDPOINT | http://localhost:4318 | OTLP 端点 |
| TRACING_SERVICE_NAME | manus-demo | 服务标识 |
| TRACING_SAMPLE_RATE | 1.0 | 采样率（自动 clamp 到 0.0-1.0） |
| TRACING_LOG_PROMPTS | false | 是否记录 prompt |
| TRACING_MAX_ATTR_LENGTH | 1000 | 属性值最大长度 |

#### 4. 依赖

新增：
- opentelemetry-api>=1.27.0
- opentelemetry-sdk>=1.27.0
- opentelemetry-exporter-otlp>=1.27.0
- fastapi>=0.100.0
- uvicorn[standard]>=0.20.0
- jinja2>=3.1.0

#### 5. 测试覆盖

**文件**: tests/test_tracing.py (27 个测试用例)

测试内容：Feature Flag 开关、装饰器行为、敏感数据脱敏、Span 属性截断、Bridge 事件处理、FileSpanExporter 输出格式等

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

#### 2. ReActEngine v2 统一引擎

**文件**: `react/engine.py`（245 行）

**架构改进**:
从 `ExecutorAgent._react_loop()` 和 `EmergentPlannerAgent._execute_todo()` 中抽取出公共的 ReAct（Reasoning + Acting）循环逻辑，形成统一的 `ReActEngine` 类，消除两处实现之间的代码重复。

**配置项**: `ENABLE_REACT_ENGINE_V2` (默认 `false`)

**支持范围**:
- `ExecutorAgent`: 启用后使用统一的 ReActEngine 替代 `_react_loop`
- `EmergentPlannerAgent`: 启用后使用统一的 ReActEngine 替代 `_execute_todo`

**核心能力**:
- 标准化的 ReAct 循环实现（Thought → Action → Observe）
- 集成 ToolRouter 实现基于失败的工具切换
- 可配置的迭代次数限制
- 工具调用结果记录（ToolCallRecord）
- 详细的错误处理和日志

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

**文件**: `agents/emergent_planner.py` (683行)

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
  - `score ≤ -1`: 简单任务 → 返回 `"simple"`
  - `score ≥ 2`: 复杂任务 → 返回 `"complex"`
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
- `ADAPT_PLAN_INTERVAL` (默认 1): 每执行 N 个节点检查一次
- `ADAPT_PLAN_MIN_COMPLETED` (默认 1): 至少完成 N 个节点后才考虑重规划

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
- `to_dict()` / `from_dict()`: 序列化/反序列化 DAG 状态，用于持久化和恢复

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
- `StepStatus`: PENDING, IN_PROGRESS, COMPLETED, FAILED, SKIPPED

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
- `file_ops`: 沙箱环境下的文件操作工具（支持 read/write/list）

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
│  PlannerAgent │ │ EmergentPlan │ │  GoalDriven  │
│              │ │    nerAgent  │ │    Planner   │
│  (v1/v2)     │ │    (v5)      │ │    (v8)      │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       │                │                │
       ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ ExecutorAgent│ │ EmergentPlan │ │ GoalDriven   │
│              │ │    nerAgent  │ │    Planner   │
│ (v1/v2/v6)   │ │    (v5/v6)   │ │    (v8)      │
└──────────────┘ └──────────────┘ └──────────────┘
       │                │                │
       │                │                │
       ▼                ▼                ▼
┌──────────────────────────────────────────────────────────┐
│           ReActEngine / GoalGuidedReAct                  │
│  (统一 ReAct 循环引擎 - v6 / 目标引导 ReAct - v8)       │
└──────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│           Tools                                          │
│  (web_search, code_executor, file_ops, shell, etc.)     │
└──────────────────────────────────────────────────────────┘
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
    ├─→ complex ──→ PlannerAgent (v2) ──→ DAGExecutor (v2)
    │
    ├─→ emergent ──→ { ENABLE_GOAL_DRIVEN_PLANNER? }
    │                   │
    │                   ├─→ true ──→ GoalDrivenPlannerAgent (v8)
    │                   │
    │                   └─→ false ─→ EmergentPlannerAgent (v5)
    │
    └─→ 其他路径 ──→ 相应处理
```

---

## 配置项汇总

### v8.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `ENABLE_GOAL_DRIVEN_PLANNER` | `false` | v8.0 | v8 目标驱动规划总开关（默认关闭，向后兼容） |
| `GOAL_REANCHOR_INTERVAL` | `5` | v8.0 | 每隔多少次外层迭代重新锚定目标文档 |
| `GOAL_REFLECTION_INTERVAL` | `1` | v8.0 | 每隔多少次外层迭代执行目标反思（1=每次都反思） |
| `MAX_GOAL_DRIVEN_ITERATIONS` | `MAX_TODO_ITEMS × MAX_TODO_RETRIES` | v8.0 | v8 主循环最大迭代数 |
| `GOAL_DRIVEN_STAGNATION_WINDOW` | `3` | v8.0 | 连续多少轮无进度突破则提前终止 |

### v7.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `TRACING_ENABLED` | `false` | v7.0 | Tracing 总开关 |
| `TRACING_BACKEND` | `console` | v7.0 | Tracing 导出后端（console/file/rich/otlp/phoenix） |
| `TRACING_ENDPOINT` | `http://localhost:4318` | v7.0 | OTLP 端点地址 |
| `TRACING_SERVICE_NAME` | `manus-demo` | v7.0 | 服务标识名称 |
| `TRACING_SAMPLE_RATE` | `1.0` | v7.0 | 采样率（自动 clamp 到 0.0-1.0） |
| `TRACING_LOG_PROMPTS` | `false` | v7.0 | 是否记录 LLM prompt 内容 |
| `TRACING_MAX_ATTR_LENGTH` | `1000` | v7.0 | Span 属性值最大长度 |

### v6.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `LLM_RETRY_ENABLED` | `false` | v6.0 | 是否启用 LLM 调用重试机制 |
| `LLM_RETRY_MAX_ATTEMPTS` | `3` | v6.0 | LLM 调用最大重试次数 |
| `LLM_RETRY_BACKOFF_FACTOR` | `2.0` | v6.0 | LLM 重试退避因子 |
| `ENABLE_REACT_ENGINE_V2` | `false` | v6.0 | 是否启用 ReActEngine v2 统一引擎 |
| `SHELL_EXEC_TIMEOUT` | `30` | v6.0 | Shell 命令执行超时时间（秒） |
| `CODE_EXEC_TIMEOUT` | `30` | v6.0 | Python 代码执行超时时间（秒） |
| `SUBPROCESS_MAX_OUTPUT_BYTES` | `524288` | v6.0 | 子进程最大输出字节数（默认 512KB） |
| `SHELL_MAX_CONCURRENT` | `3` | v6.0 | 最大并发 Shell 子进程数 |
| `CODE_MAX_CONCURRENT` | `3` | v6.0 | 最大并发代码执行子进程数 |

### v5.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `EMERGENT_PLANNING_ENABLED` | `true` | v5.0 | 是否启用隐式规划功能 |
| `MAX_TODO_ITEMS` | `20` | v5.0 | 最大 TODO 项数量限制 |
| `MAX_TODO_RETRIES` | `3` | v5.0 | 单个 TODO 最大重试次数 |
| `MAX_EMERGENT_OUTER_ITERATIONS` | `60` | v5.0 | Emergent 主循环最大迭代数 |
| `TODO_COMPRESSION_THRESHOLD` | `0.8` | v5.0 | TODO 压缩阈值 |

### v4.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `PLAN_MODE` | `auto` | v4.0 | 规划模式：auto/simple/complex |

### v3.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `ADAPTIVE_PLANNING_ENABLED` | `true` | v3.0 | 是否启用自适应规划 |
| `ADAPT_PLAN_INTERVAL` | `1` | v3.0 | 自适应规划检查间隔（节点数） |
| `ADAPT_PLAN_MIN_COMPLETED` | `1` | v3.0 | 触发自适应规划的最小完成节点数 |
| `TOOL_FAILURE_THRESHOLD` | `2` | v3.0 | 工具失败阈值（达到后建议切换） |

### v2.0 新增配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `MAX_PARALLEL_NODES` | `3` | v2.0 | 最大并行执行节点数 |

### v1.0 基础配置

| 参数名 | 默认值 | 版本 | 说明 |
|--------|--------|------|------|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | v1.0 | OpenAI 兼容 API 地址 |
| `LLM_MODEL` | `deepseek-chat` | v1.0 | 模型名称 |
| `MAX_CONTEXT_TOKENS` | `8000` | v1.0 | 上下文 Token 上限 |
| `MAX_REACT_ITERATIONS` | `10` | v1.0 | ReAct 循环最大迭代次数 |
| `SHORT_TERM_WINDOW` | `20` | v1.0 | 短期记忆窗口大小 |
| `SANDBOX_DIR` | `~/.manus_demo/sandbox` | v1.0 | 沙箱目录 |

---

## 迁移指南

### 从 v7.0 升级到 v8.0

**新增功能**:
- GoalDrivenPlannerAgent 目标驱动规划器（ReflAct 风格「以终为始」）
- GoalDocument 持久锚定 + 逆向里程碑规划 + 目标状态反思 + 周期性重锚定 + 停滞检测

**迁移步骤**:
1. 安装新增依赖（无新增 Python 包依赖，使用现有 openai/pydantic/rich）
2. 更新配置文件，添加 v8 配置项（可选）
3. 启用 v8 目标驱动规划：设置 `ENABLE_GOAL_DRIVEN_PLANNER=true`
4. 验证现有功能正常（v8 默认关闭，不影响现有 v5 隐式规划路径）
5. 运行测试：`python -m pytest tests/test_goal_driven_planner.py -v`

**注意事项**:
- v8 功能默认关闭（`ENABLE_GOAL_DRIVEN_PLANNER=false`），零开销运行
- 启用 v8 后，emergent 分类路由会自动使用 GoalDrivenPlannerAgent 替代 EmergentPlannerAgent
- v8 与 v5 共享相同的事件格式（todo_start/todo_complete/todo_failed/todo_blocked），TracingBridge 兼容
- 新增 v8 专用事件：goal_anchor、goal_reflection、goal_reanchor
- v8 的消息管理采用滑动窗口（最多 24 条），与 v5 的无界历史不同

### 从 v6.0 升级到 v7.0

**新增功能**:
- 全链路 Tracing（基于 OpenTelemetry）
- Trace Web Viewer（内置 FastAPI 可视化查看器）

**迁移步骤**:
1. 安装新增依赖：`pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp fastapi uvicorn jinja2`
2. 更新配置文件，添加 Tracing 相关配置项（可选）
3. 启用 Tracing：设置 `TRACING_ENABLED=true`
4. 选择导出后端：设置 `TRACING_BACKEND=console/file/rich/otlp/phoenix`
5. 启动 Web Viewer：`python -m tracing --port 8600`
6. 验证现有功能是否正常（Tracing 默认关闭，不影响现有行为）

**注意事项**:
- Tracing 功能默认关闭（`TRACING_ENABLED=false`），零开销运行
- 敏感参数（如 API Key、密码）会自动脱敏
- 默认不记录 LLM prompt 内容，如需记录需设置 `TRACING_LOG_PROMPTS=true`
- Web Viewer 仅用于本地开发调试，生产环境建议禁用或使用专业 APM 工具

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

Manus Demo 从 v1.0 到 v8.0 的演进过程，展现了多智能体系统在规划能力、执行效率、健壮性、可观测性等方面的持续改进：

- **v1.0**: 奠定基础，实现简单的线性规划和顺序执行
- **v2.0**: 引入 DAG 和并行执行，大幅提升复杂任务处理能力
- **v3.0**: 增加自适应性和动态性，提高系统灵活性
- **v4.0**: 优化任务分类和路由，提升性能和准确性
- **v5.0**: 引入隐式规划范式，拓展任务处理范围
- **v6.0**: 增强系统健壮性，提高 LLM 调用的可靠性
- **v7.0**: 建立全链路可观测性，提供 OpenTelemetry 标准 Tracing + 内置 Web 可视化查看器
- **v8.0**: 引入目标驱动规划（ReflAct 风格），通过 GoalDocument 持久锚定防止目标漂移，逆向规划确保步骤与目标对齐

每个版本都在前一个版本的基础上进行改进，同时保持向后兼容性，为用户提供平滑的升级体验。
