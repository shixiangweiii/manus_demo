# Manus Demo - 隐式规划系统详解

> **版本**: v6（含 ReActEngine 可选集成 + ShellTool）
> **更新日期**: 2026-05-10
> **目的**: 深入解析隐式规划系统的设计理念、实现细节和使用场景

## 目录
1. [设计理念](#1-设计理念)
2. [系统架构](#2-系统架构)
3. [核心算法](#3-核心算法)
4. [数据结构](#4-数据结构)
5. [与 DAG 规划的对比](#5-与-dag-规划的对比)
6. [使用场景指南](#6-使用场景指南)
7. [实现细节](#7-实现细节)
8. [性能特征](#8-性能特征)

---

## 1. 设计理念

### 为什么需要隐式规划？

#### 显式规划（v4及之前）的局限

显式 DAG 规划在处理结构化、可预知的任务时表现出色，但在以下场景存在明显局限：

- **探索性任务无法预知子领域**：当任务涉及未知的技术栈、陌生的代码库或需要深入研究时，无法在规划阶段准确列出所有子任务
- **需求模糊需先分析**：用户可能只给出一个模糊的目标（如"优化这个系统的性能"），需要先探索才能确定具体工作项
- **创意任务需发散**：某些任务需要根据中间结果动态调整方向，无法预先制定固定计划

#### Claude Code 的启示

Claude Code 采用了完全不同的规划哲学：

- **while(tool_use) 主循环**：核心是一个简单的循环，不断让 LLM 决定下一步做什么
- **TODO 列表管理**：通过维护一个 TODO 列表来跟踪进度，而非复杂的 DAG 结构
- **规划在执行中涌现**：没有独立的规划阶段，计划随着执行过程自然形成
- **无预定义计划结构**：不强制要求任何特定的计划格式，让 LLM 自由组织

#### v5 设计目标

基于这些启示，v5 版本引入隐式规划系统，核心目标是：

1. **灵活性**：能够适应不确定的任务，动态调整计划
2. **自然性**：让 LLM 以自然语言推理的方式自组织，而非遵循僵化的模板
3. **简单性**：减少系统复杂度，降低维护成本

---

## 2. 系统架构

### 整体流程

```
用户输入任务 → Orchestrator收集上下文 → 分类任务复杂度
  └─ emergent → EmergentPlannerAgent.execute(task, context)
    → 1. 初始化TODO列表（1-3个初始项）
    → 2. while has_pending_todos:
         - 选择下一个就绪TODO (get_ready_todos)
         - 执行ReAct循环（think_with_tools → 工具调用 → 观察）
         - 更新TODO状态（mark_completed 或 mark_pending重试）
         - 动态添加新TODO（_update_todo_list）
    → 3. 汇总所有已完成TODO的结果
    → 4. 返回最终答案 → 存储到长期记忆
```

### v6 ReActEngine 可选集成

v6.0 版本引入了统一 ReActEngine 的可选集成：

- **启用方式**：设置 `ENABLE_REACT_ENGINE_V2=true`
- **默认行为**：使用 legacy `_execute_todo` 实现（向后兼容）
- **优势**：代码复用、统一的工具路由、一致的错误处理

```python
# agents/emergent_planner.py (第 105-116 行)
use_engine = use_react_engine if use_react_engine is not None else config_module.ENABLE_REACT_ENGINE_V2
self._react_engine = None
if use_engine:
    from react.engine import ReActEngine
    self._react_engine = ReActEngine(
        llm_client=llm_client,
        tools=self.tools,
        max_iterations=self.max_iterations,
        tool_router=self.tool_router,
    )
```

---

## 3. 核心算法

### 3.1 TODO 列表初始化 (_init_todo_list)

**目的**：从任务描述创建 1-3 个初始 TODO 项，启动执行过程。

**算法流程**：

```python
async def _init_todo_list(self, task: str, context: str) -> None:
    # 1. 构造提示词，要求 LLM 生成 1-3 个高层 TODO
    prompt = (
        f"Initialize a TODO list for this task. Create 1-3 high-level TODO items "
        f"to get started. We will add more during execution if needed.\n\n"
        f"Task: {task}\n\n"
        f"Respond with JSON:\n"
        f"{{\n"
        f'  "todos": [\n'
        f"    {{\n"
        f'      "description": "First TODO item",\n'
        f'      "dependencies": []\n'
        f"    }}\n"
        f"  ]\n"
        f"}}"
    )
    
    # 2. 调用 think_json() 获取 LLM 响应
    try:
        data = await self.think_json(prompt, temperature=0.3)
        
        # 3. 解析并添加 TODO 项
        for todo_data in data.get("todos", []):
            self._todo_list.add_todo(
                description=todo_data.get("description", ""),
                dependencies=todo_data.get("dependencies", []),
            )
    
    # 4. 降级处理：解析失败时创建默认 TODO
    except Exception as exc:
        self._todo_list.add_todo(description=f"Complete task: {task}")
```

**关键设计**：
- 使用较低的 temperature (0.3) 确保输出稳定
- 初始 TODO 的 dependencies 为空（无前置依赖）
- 失败时自动降级，保证系统鲁棒性

---

### 3.2 主循环 (execute 方法的 while 循环)

**目的**：持续执行 TODO 直到所有任务完成。

**算法流程**：

```python
async def execute(self, task: str, context: str = "") -> str:
    # 初始化
    self._todo_list = TodoList(task=task)
    await self._init_todo_list(task, context)
    
    iteration = 0
    all_results: list[StepResult] = []
    
    # 主循环
    while self._todo_list.has_pending():
        iteration += 1
        
        # 迭代上限检查
        if iteration > self.max_iterations:
            break
        
        # 选择下一个就绪 TODO
        ready_todos = self._todo_list.get_ready_todos()
        if not ready_todos:
            # 阻塞检测：强制选择一个 PENDING 的 TODO
            pending = [t for t in self._todo_list.todos.values() 
                      if t.status == TodoStatus.PENDING]
            if pending:
                ready_todos = [pending[0]]
            else:
                break
        
        # 执行 TODO
        current_todo = ready_todos[0]
        result = await self._execute_todo(current_todo)
        all_results.append(result)
        
        # 更新状态
        if result.success:
            self._todo_list.mark_completed(current_todo.id, result.output)
        else:
            self._todo_list.mark_pending(current_todo.id)  # 重试
        
        # 动态更新 TODO 列表
        await self._update_todo_list(result)
    
    # 汇总结果
    return self._compile_answer(task, all_results)
```

**关键设计**：
- 循环条件：`has_pending()` 返回 True
- 迭代上限：`MAX_EMERGENT_OUTER_ITERATIONS`（默认 `MAX_TODO_ITEMS * MAX_TODO_RETRIES` = 60，可在 config.py 中配置）
- 阻塞处理：无就绪 TODO 时强制选择 PENDING 项，避免死锁

---

### 3.3 TODO 执行 (_execute_todo)

**目的**：使用 ReAct 循环执行单个 TODO。

**算法流程**：

```python
async def _execute_todo(self, todo: TodoItem) -> StepResult:
    # 1. 构造执行提示词
    prompt = f"Execute the following TODO:\n\nTODO {todo.id}: {todo.description}"
    
    # 2. 包含依赖项的结果
    if todo.dependencies:
        dep_results = []
        for dep_id in todo.dependencies:
            dep_todo = self._todo_list.todos.get(dep_id)
            if dep_todo and dep_todo.result:
                dep_results.append(f"[TODO {dep_id} result]:\n{dep_todo.result}")
        if dep_results:
            prompt += f"\n\nResults from dependencies:\n" + "\n".join(dep_results)
    
    # 3. ReAct 循环
    tool_calls_log: list[ToolCallRecord] = []
    iteration = 0
    
    while iteration < self.max_iterations:
        iteration += 1
        
        # 思考并调用工具
        continue_msg = "Continue executing the TODO based on the tool results above."
        router_hint = self.tool_router.get_hint(str(todo.id))
        if router_hint and iteration > 1:
            continue_msg += f"\n\nIMPORTANT: {router_hint}"
        
        response_msg = await self.think_with_tools(
            prompt if iteration == 1 else continue_msg,
            tools=self.tool_schemas,
            temperature=0.5,
        )
        
        # 4. 无工具调用则完成
        if not response_msg.tool_calls:
            final_output = response_msg.content or "TODO completed (no output)."
            return StepResult(
                step_id=todo.id,
                success=True,
                output=final_output,
                tool_calls_log=tool_calls_log,
            )
        
        # 5. 执行工具调用
        for tool_call in response_msg.tool_calls:
            func_name = tool_call.function.name
            func_args = self._parse_json(tool_call.function.arguments)
            
            tool = self.tools.get(func_name)
            if tool:
                try:
                    result = await tool.execute(**func_args)
                    self.tool_router.record_success(str(todo.id), func_name)
                except Exception as exc:
                    result = f"Tool execution error: {exc}"
                    self.tool_router.record_failure(str(todo.id), func_name)
            else:
                result = f"Error: Unknown tool '{func_name}'"
                self.tool_router.record_failure(str(todo.id), func_name)
            
            tool_calls_log.append(ToolCallRecord(...))
            self.add_tool_result(tool_call.id, result)
    
    # 6. 超时返回失败
    return StepResult(
        step_id=todo.id,
        success=False,
        output=f"TODO did not complete within {self.max_iterations} iterations.",
        tool_calls_log=tool_calls_log,
    )
```

**关键设计**：
- 内部 ReAct 循环，最大迭代次数受 `MAX_REACT_ITERATIONS` 限制（默认 10）
- 集成 ToolRouter（v3），记录工具调用成功/失败
- 依赖项结果作为上下文传递给 LLM

---

### 3.4 动态更新 (_update_todo_list)

**目的**：根据执行结果动态添加新 TODO，实现规划的"涌现"。

**算法流程**：

```python
async def _update_todo_list(self, last_result: StepResult) -> None:
    # 1. 构造提示词
    prompt = (
        f"Review the execution progress and determine if the TODO list needs updates.\n\n"
        f"Current task: {self._todo_list.task}\n\n"
        f"Last execution result:\n{last_result.output[:2000]}\n\n"
        f"Current TODO list:\n{self._get_todo_summary()}\n\n"
        f"Do you need to:\n"
        f"- Add new TODOs (discovered additional work)?\n"
        f"- Modify existing TODO descriptions?\n"
        f"- Mark any TODOs as blocked?\n\n"
        f"Respond with JSON:\n"
        f"{{\n"
        f'  "needs_update": true/false,\n'
        f'  "reason": "Why update is or is not needed",\n'
        f'  "new_todos": [\n'
        f"    {{\n"
        f'      "description": "New TODO description",\n'
        f'      "dependencies": [1, 2]\n'
        f"    }}\n"
        f"  ]\n"
        f"}}"
    )
    
    # 2. 调用 think_json()
    data = await self.think_json(prompt, temperature=0.3)
    
    # 3. 如果需要更新，添加新 TODO
    if data.get("needs_update", False):
        new_todos = data.get("new_todos", [])
        if new_todos:
            current_count = len(self._todo_list.todos)
            max_todos = getattr(config_module, 'MAX_TODO_ITEMS', 20)
            
            for todo_data in new_todos:
                # 检查数量限制
                if current_count >= max_todos:
                    break
                
                # 验证依赖ID存在
                raw_deps = todo_data.get("dependencies", [])
                valid_deps = [dep_id for dep_id in raw_deps 
                            if dep_id in self._todo_list.todos]
                if raw_deps and not valid_deps:
                    continue
                
                # 添加 TODO
                self._todo_list.add_todo(
                    description=todo_data.get("description", ""),
                    dependencies=valid_deps,
                )
                current_count += 1
```

**关键设计**：
- 这是规划"涌现"的核心：LLM 根据执行结果决定是否需要新工作
- 使用 `MAX_TODO_ITEMS`（默认 20）限制 TODO 数量，避免无限增长
- 验证依赖 ID 存在性，避免引用不存在的 TODO

---

### 3.5 失败处理

**目的**：处理 TODO 执行失败的情况，支持重试机制。

**算法流程**：

```python
# 在 execute() 方法中
if result.success:
    self._todo_list.mark_completed(current_todo.id, result.output)
    self._emit("todo_complete", {"todo": current_todo, "result": result})
else:
    # 修复 Critical #3: 失败时将 TODO 状态回退为 PENDING 以便重试
    logger.warning("[EmergentPlanner] TODO %d failed: %s", 
                   current_todo.id, result.output[:200])
    self._todo_list.mark_pending(current_todo.id)
    self._emit("todo_failed", {"todo": current_todo, "result": result})
```

**与 DAG 的失败处理对比**：

| 维度 | DAG 规划 | 隐式规划 |
|------|---------|---------|
| 失败策略 | 标记节点失败，停止下游执行 | 回退为 PENDING，允许重试 |
| 重试机制 | 需要手动重新执行 | 自动在下一轮重试 |
| 状态转换 | FAILED → BLOCKED | IN_PROGRESS → PENDING |
| 灵活性 | 低（严格依赖） | 高（可跳过阻塞项） |

---

### 3.6 结果汇总 (_compile_answer)

**目的**：将所有已完成 TODO 的结果汇总为最终答案。

**算法流程**：

```python
def _compile_answer(self, task: str, results: list[StepResult]) -> str:
    successful = [r for r in results if r.success]
    if not successful:
        return "Unfortunately, no TODOs were completed successfully."
    
    # 简单汇总所有结果
    parts = []
    for i, result in enumerate(successful, 1):
        parts.append(f"[Result {i}]:\n{result.output}")
    
    return "\n\n".join(parts)
```

**关键设计**：
- 只汇总成功的 TODO 结果
- 失败时的降级输出：返回明确的失败消息
- 简单的顺序拼接，未来可增强为智能摘要

---

## 4. 数据结构

基于 `schema.py` 最新代码（第 350-531 行）：

### TodoStatus 枚举

```python
class TodoStatus(str, Enum):
    """TODO 项的状态枚举"""
    PENDING = "pending"           # 等待执行
    IN_PROGRESS = "in_progress"   # 正在执行
    COMPLETED = "completed"       # 已完成
    BLOCKED = "blocked"           # 被阻塞（依赖未满足）
```

### TodoItem 模型

```python
class TodoItem(BaseModel):
    """
    隐式规划系统中的单个 TODO 项。
    
    与 DAG 规划中的 TaskNode 不同：
    - 扁平结构（无层级）
    - 执行过程中动态创建/更新
    - 在集中式 TODO 列表中管理
    - 通过 LLM 的自然语言推理自组织
    """
    id: int                           # TODO 唯一 ID
    description: str                  # TODO 描述
    status: TodoStatus = TodoStatus.PENDING
    dependencies: list[int]           # 前置 TODO ID 列表
    result: str | None = None         # 执行结果文本
    created_at: float                 # 创建时间戳
    updated_at: float                 # 最后更新时间戳
```

### TodoList 模型

```python
class TodoList(BaseModel):
    """
    隐式规划的集中式 TODO 列表。
    
    这是 Claude Code 风格规划的核心数据结构：
    - 存储所有 TODO 项
    - LLM 可在执行过程中添加、更新或完成 TODO
    - 执行器根据依赖关系和状态选择下一个 TODO
    """
    task: str                         # 原始用户任务
    todos: dict[int, TodoItem]        # 按 ID 索引的 TODO 项
    next_id: int = 1                  # 下一个可用 TODO ID
```

#### 核心方法及其时间复杂度

| 方法 | 功能 | 时间复杂度 | 说明 |
|------|------|-----------|------|
| `add_todo(description, dependencies)` | 添加新 TODO | O(1) | 字典插入操作 |
| `get_ready_todos()` | 获取可执行的 TODO | O(N) | 遍历所有 TODO 检查依赖 |
| `mark_completed(todo_id, result)` | 标记已完成 | O(1) | 字典查找和更新 |
| `mark_pending(todo_id)` | 标记为等待（重试） | O(1) | 字典查找和更新 |
| `is_complete()` | 检查是否全部完成 | O(N) | 遍历所有 TODO |
| `has_pending()` | 检查是否有待执行项 | O(N) | 遍历所有 TODO |

**性能优化点**：
- `get_ready_todos()` 是性能瓶颈，可通过缓存优化
- 对于大规模 TODO 列表（>100 项），建议使用优先队列

---

## 5. 与 DAG 规划的对比

| 维度 | DAG 规划 (v2/v4) | 隐式规划 (v5) |
|------|-----------------|--------------|
| **规划时机** | 执行前一次性生成完整 DAG | 执行过程中动态生成 TODO |
| **数据结构** | 有向无环图（DAG） | 扁平 TODO 列表 |
| **依赖管理** | 显式节点依赖关系 | 简化的前置 ID 列表 |
| **执行模型** | 拓扑排序 + 并发执行 | 顺序 ReAct 循环 |
| **变更方式** | 重新生成 DAG | 动态添加/修改 TODO |
| **适用场景** | 结构化、可预知的任务 | 探索性、模糊、创意任务 |
| **可预测性** | 高（完整计划可见） | 低（计划动态演化） |
| **灵活性** | 低（修改成本高） | 高（随时调整） |
| **失败处理** | 标记失败，停止下游 | 回退状态，允许重试 |
| **Token 消耗** | 规划阶段高，执行阶段低 | 执行阶段持续消耗 |
| **规划延迟** | 高（需生成完整计划） | 低（只需 1-3 个初始 TODO） |
| **并发能力** | 强（支持并行执行） | 弱（顺序执行） |

---

## 6. 使用场景指南

### ✅ 推荐使用隐式规划的场景

#### 1. 探索性任务

**示例**：
- "研究这个开源项目的架构，找出性能瓶颈"
- "分析这个错误日志，找出根本原因"

**理由**：无法预知需要探索哪些文件、调用哪些工具，规划必须在执行中涌现。

#### 2. 需求模糊的任务

**示例**：
- "优化这个系统的性能"
- "改进这个代码库的可维护性"

**理由**：需要先分析现状，才能确定具体工作项。

#### 3. 创意性任务

**示例**：
- "为这个项目写一份技术文档"
- "设计一个新的功能模块"

**理由**：中间结果可能改变方向，需要动态调整计划。

#### 4. 快速原型开发

**示例**：
- "实现一个简单的 web 爬虫"
- "写一个数据可视化脚本"

**理由**：简单任务不值得花费时间做详细规划。

### ❌ 不推荐使用隐式规划的场景

#### 1. 结构化、可预知的任务

**示例**：
- "从 API 获取数据，转换格式，保存到数据库"
- "运行测试套件，收集结果，生成报告"

**理由**：DAG 规划可以生成更优的执行计划（如并发执行）。

#### 2. 大规模任务

**示例**：
- "重构整个项目的代码结构"
- "迁移 100+ 个模块到新框架"

**理由**：TODO 列表会变得过大，难以管理。

#### 3. 需要严格依赖关系的任务

**示例**：
- "按顺序执行 A、B、C，每个步骤必须成功才能继续"

**理由**：DAG 的依赖管理更严格，隐式规划可能跳过阻塞项。

### 运行方式

```bash
# 方式 1：环境变量
PLAN_MODE=emergent python main.py

# 方式 2：启用 ReActEngine v2
ENABLE_REACT_ENGINE_V2=true PLAN_MODE=emergent python main.py
```

---

## 7. 实现细节

### EmergentPlannerAgent 类结构

```python
class EmergentPlannerAgent(BaseAgent):
    """
    Claude Code 风格的隐式规划器。
    
    关键特征：
    - 无预定义的计划结构
    - TODO 列表在执行过程中动态演化
    - 单一扁平消息历史（LLM 可见所有工具调用）
    - LLM 通过自然语言推理自组织
    """
    
    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        max_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
        use_react_engine: bool | None = None,
    ):
        # 初始化父类
        super().__init__(
            name="EmergentPlanner",
            system_prompt=EMERGENT_PLANNER_SYSTEM_PROMPT,
            llm_client=llm_client,
            context_manager=context_manager,
        )
        
        # 工具管理
        self.tools = {t.name: t for t in tools}
        self.tool_schemas = [t.to_openai_tool() for t in tools]
        
        # 配置
        self.max_iterations = max_iterations or config_module.MAX_REACT_ITERATIONS
        self.tool_router = tool_router or ToolRouter(...)
        
        # 可选 ReActEngine 集成
        self._react_engine = None
        if use_engine:
            from react.engine import ReActEngine
            self._react_engine = ReActEngine(...)
```

### EMERGENT_PLANNER_SYSTEM_PROMPT

```python
EMERGENT_PLANNER_SYSTEM_PROMPT = """\
You are an autonomous task execution agent that follows the ReAct paradigm.

You manage a TODO list that tracks what needs to be done. Your workflow:
1. Review the current TODO list and select the next actionable item
2. Reason about what to do and which tool to use
3. Call the appropriate tool with correct parameters
4. Observe the tool's output and record the result
5. Mark the TODO as completed or update it based on progress
6. Add new TODOs if you discover additional work is needed
7. Repeat until all TODOs are completed

Available tools will be provided via function calling. Use them wisely.
When you believe the overall task is complete, respond with a clear summary
of what was accomplished. Do NOT call any more tools once done.

IMPORTANT: You can dynamically modify the TODO list during execution:
- Add new TODOs when you discover additional work
- Mark TODOs as completed when their objectives are met
- Update TODO descriptions if the goal changes
"""
```

### 事件发射机制

EmergentPlannerAgent 通过 `_emit()` 方法向 UI 回调函数发送事件：

| 事件类型 | 触发时机 | 数据内容 |
|---------|---------|---------|
| `phase` | 阶段开始/结束 | 阶段名称 |
| `todo_start` | 开始执行 TODO | `{todo: TodoItem}` |
| `todo_complete` | TODO 成功完成 | `{todo: TodoItem, result: StepResult}` |
| `todo_failed` | TODO 执行失败 | `{todo: TodoItem, result: StepResult}` |
| `todo_list_update` | TODO 列表更新 | `{summary: str}` |
| `todo_list_initialized` | TODO 列表初始化完成 | `{summary: str}` |

**注意**：当前实现中 `_emit()` 方法为空桩，事件由 Orchestrator 处理。

### 配置项

| 配置项 | 默认值 | 说明 |
|-------|-------|------|
| `EMERGENT_PLANNING_ENABLED` | `True` | 是否启用隐式规划 |
| `MAX_TODO_ITEMS` | `20` | TODO 列表最大项数 |
| `MAX_TODO_RETRIES` | `3` | 单个 TODO 最大重试次数 |
| `MAX_EMERGENT_OUTER_ITERATIONS` | `60` | Emergent 主循环最大迭代数 |
| `TODO_COMPRESSION_THRESHOLD` | `0.8` | 上下文窗口使用率达到 80% 时压缩 TODO |
| `ENABLE_REACT_ENGINE_V2` | `False` | 是否使用统一 ReActEngine |
| `MAX_REACT_ITERATIONS` | `10` | 单个 TODO 最大迭代次数 |

---

## 8. 性能特征

### 时间复杂度表

| 操作 | 时间复杂度 | 说明 |
|------|-----------|------|
| TODO 列表初始化 | O(1) | 创建 1-3 个初始项 |
| 选择就绪 TODO | O(N) | 遍历所有 TODO 检查依赖 |
| 执行单个 TODO | O(M) | M = 最大迭代次数（默认 10） |
| 更新 TODO 列表 | O(K) | K = 新增 TODO 数量 |
| 结果汇总 | O(S) | S = 成功 TODO 数量 |
| 整体执行 | O(N × M) | N = TODO 总数 |

### 与 DAG 规划的性能对比

| 维度 | DAG 规划 | 隐式规划 |
|------|---------|---------|
| **规划延迟** | 高（需生成完整 DAG） | 低（只需 1-3 个初始 TODO） |
| **执行速度** | 快（支持并发） | 慢（顺序执行） |
| **Token 消耗** | 规划阶段高，执行阶段低 | 执行阶段持续消耗 |
| **灵活性** | 低（修改成本高） | 高（随时调整） |
| **内存占用** | 中（DAG 结构） | 低（扁平列表） |

### 已知局限性

1. **顺序执行**：当前实现不支持并发执行，无法利用多核优势
2. **TODO 数量限制**：`MAX_TODO_ITEMS` 限制为 20，可能不适合复杂任务
3. **无优先级**：TODO 列表不支持优先级，可能导致重要任务被延迟
4. **结果汇总简单**：当前只是简单拼接，缺乏智能摘要
5. **阻塞处理粗糙**：无就绪 TODO 时强制选择 PENDING 项，可能导致循环

### 未来增强方向

1. **并发执行**：支持多个 TODO 并行执行（需重新设计依赖管理）
2. **智能优先级**：基于任务重要性和紧急性排序 TODO
3. **TODO 压缩**：当 TODO 数量超过阈值时，合并相似项
4. **自适应迭代次数**：根据任务复杂度动态调整最大迭代次数
5. **增强结果汇总**：使用 LLM 生成智能摘要，而非简单拼接
6. **循环检测**：检测并打破循环依赖，避免无限重试
7. **进度可视化**：提供更丰富的进度跟踪和可视化

---

## 附录

### 相关文件

- `agents/emergent_planner.py`：EmergentPlannerAgent 实现（535 行）
- `schema.py`：TodoItem、TodoList、TodoStatus 定义（第 350-531 行）
- `agents/orchestrator.py`：任务分类和路由逻辑
- `config.py`：配置项定义

### 相关文档

- `docs/emergent-planning.md`：v5/v6 隐式规划文档
- `docs/emergent-planning-test-scenarios.md`：测试场景
- `docs/hybrid-plan-routing.md`：混合规划路由逻辑

### 版本历史

- **v6.0** (2026-05-05)：可选 ReActEngine 集成 + ShellTool 支持
- **v5.0** (2026-04-15)：初始版本，基于 Claude Code 理念

---

> **文档维护**：本文档基于 `agents/emergent_planner.py` (684 行) 和 `schema.py` (612 行) 的最新代码生成。
