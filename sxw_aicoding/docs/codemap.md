# Manus Demo - 代码地图

> **生成时间**: 2026-05-05
> **版本**: v6（含 LLM 重试机制 + ReActEngine Feature Flag + ShellTool）
> **目的**: 当前代码库的综合架构地图

## 目录
1. [系统概览](#系统概览)
2. [模块结构](#模块结构)
3. [组件详情](#组件详情)
4. [数据流](#数据流)
5. [关键设计模式](#关键设计模式)
6. [文件参考](#文件参考)

## 系统概览

### 架构图

```mermaid
graph TB
    User[用户输入] --> Main[Main<br/>main.py CLI入口]
    Main --> Orchestrator[OrchestratorAgent<br/>中央协调者]
    
    Orchestrator --> Context[Context收集<br/>Memory+Knowledge]
    Context --> Classifier[Classifier<br/>v4两阶段分类器]
    
    Classifier -->|规则快筛| RuleCheck{规则分类}
    RuleCheck -->|Simple| SimplePath[Simple路径]
    RuleCheck -->|Complex| ComplexPath[Complex路径]
    RuleCheck -->|模糊| LLMClassify{LLM分类}
    
    LLMClassify -->|Simple| SimplePath
    LLMClassify -->|Complex| ComplexPath
    LLMClassify -->|Emergent| EmergentPath[Emergent路径]
    
    SimplePath --> PlannerV1[PlannerV1<br/>create_plan]
    PlannerV1 --> FlatPlan[扁平计划<br/>2-6步]
    FlatPlan --> ExecutorV1[ExecutorV1<br/>顺序执行]
    ExecutorV1 --> ReflectorV1[ReflectorV1<br/>reflect]
    
    ComplexPath --> PlannerV2[PlannerV2<br/>create_dag]
    PlannerV2 --> DAG[分层DAG<br/>3层结构]
    DAG --> DAGExecutor[DAGExecutor<br/>Super-step并行]
    DAGExecutor --> ReflectorV2[ReflectorV2<br/>reflect_dag]
    
    EmergentPath --> EmergentPlanner[EmergentPlanner<br/>TODO列表管理]
    EmergentPlanner --> ToolLoop[while tool_use循环]
    ToolLoop --> Compile[编译结果]
    
    ReflectorV1 --> ReflectCheck{反思验证}
    ReflectorV2 --> ReflectCheck
    Compile --> ReflectCheck
    
    ReflectCheck -->|通过| Success[最终答案]
    ReflectCheck -->|失败| Replan[重规划]
    
    Replan --> ReplanV1[v1完整重规划]
    Replan --> ReplanV2[v2局部重规划子树]
    
    ReplanV1 --> Orchestrator
    ReplanV2 --> DAGExecutor
    
    Success --> Memory[存储长期记忆]
```

### 版本演进

```
v1 → 线性规划 + 顺序执行 + 完整重规划
v2 → DAG 分层规划 + 并行 Super-step + 局部重规划 + 节点状态机 + 逐节点验证
v3 → 自适应规划（运行时 DAG 变更）+ 工具路由（基于失败的切换）+ 动态 DAG 增删改
v4 → 两阶段混合分类器（规则 + LLM）+ 自动 v1/v2 路径选择
v5 → Claude Code 风格隐式规划 + TODO 列表管理 + while(tool_use) 主循环（新增第三条执行路径）
v6 → LLM 重试机制（指数退避）+ ReActEngine 统一引擎 Feature Flag
```

### 核心特性

- **混合路由系统**：自动选择 Simple/Complex/Emergent 三条执行路径
- **两阶段分类器**：规则快筛（零成本）+ LLM 兜底（高准确率）
- **Super-step 并行**：借鉴 LangGraph Pregel 运行时，支持节点并行执行
- **状态机管控**：严格节点生命周期管理，防止非法状态转移
- **隐式规划**：Claude Code 风格，通过 TODO 列表动态涌现规划
- **LLM 重试**：v6 新增指数退避重试机制，提升稳定性

## 模块结构

### 目录布局

```
manus_demo/
├── agents/                    # 智能体模块
│   ├── __init__.py
│   ├── base.py               # BaseAgent 基类 (182行)
│   ├── orchestrator.py       # OrchestratorAgent 中央协调者 (490行)
│   ├── planner.py            # PlannerAgent 混合规划器 (909行)
│   ├── executor.py           # ExecutorAgent ReAct执行器 (321行)
│   ├── reflector.py          # ReflectorAgent 反思验证器 (254行)
│   └── emergent_planner.py   # EmergentPlannerAgent 隐式规划器 (683行)
│
├── dag/                       # DAG 执行引擎
│   ├── __init__.py
│   ├── graph.py              # TaskDAG 有向无环图 (626行)
│   ├── executor.py           # DAGExecutor Super-step执行器 (647行)
│   └── state_machine.py      # NodeStateMachine 节点状态机 (113行)
│
├── llm/                       # LLM 客户端
│   ├── __init__.py
│   └── client.py             # LLMClient OpenAI兼容封装 (227行)
│
├── react/                     # ReAct 统一引擎 (v6)
│   ├── __init__.py
│   └── engine.py             # ReActEngine 统一 ReAct 循环引擎 (245行)
│
├── tools/                     # 工具系统
│   ├── __init__.py
│   ├── base.py               # BaseTool 工具基类 (86行)
│   ├── router.py             # ToolRouter 智能路由器 (167行)
│   ├── web_search.py         # WebSearchTool 网络搜索 (112行)
│   ├── code_executor.py      # CodeExecutorTool 代码执行 (108行)
│   ├── file_ops.py           # FileOpsTool 文件操作 (137行)
│   └── shell_tool.py         # ShellTool Shell 命令执行 (130行)
│
├── memory/                    # 记忆系统
│   ├── __init__.py
│   ├── short_term.py         # ShortTermMemory 短期记忆 (90行)
│   └── long_term.py          # LongTermMemory 长期记忆 (141行)
│
├── context/                   # 上下文管理
│   ├── __init__.py
│   └── manager.py            # ContextManager 上下文压缩 (186行)
│
├── knowledge/                 # 知识检索
│   ├── __init__.py
│   ├── retriever.py          # KnowledgeRetriever TF-IDF检索 (228行)
│   └── docs/
│       └── sample.txt        # 示例知识文档
│
├── tests/                     # 测试模块
│   ├── __init__.py
│   ├── test_dag_capabilities.py
│   ├── test_emergent_planning.py
│   ├── test_emergent_simple.py
│   ├── test_llm_integration.py
│   ├── test_optimizations.py
│   ├── test_real_tools.py
│   ├── test_shell_tool.py
│   ├── test_concurrent_execution.py
│   └── test_cycle_detection.py
│
├── sxw_aicoding/              # 用户工作目录 / 文档目录
│   ├── docs/
│   │   ├── codemap.md              # 本代码地图文档
│   │   ├── CHANGELOG.md            # 版本更新日志
│   │   ├── data-structures-and-algorithms.md
│   │   ├── dynamic-features.md
│   │   ├── emergent-planning.md
│   │   ├── emergent-planning-test-scenarios.md
│   │   ├── hybrid-plan-routing.md
│   │   ├── llm-integration.md
│   │   ├── planning-gap-analysis.md
│   │   ├── planning-test-scenarios.md
│   │   ├── related-papers.md
│   │   └── upgrade-plan.md
│   └── temp/
│
├── .env                       # 环境变量配置
├── .env.example              # 环境变量示例
├── config.py                  # 全局配置
├── schema.py                  # 数据模型定义
├── main.py                    # CLI 入口
├── requirements.txt           # Python 依赖
└── README.md                  # 项目说明
```

## 组件详情

### 1. OrchestratorAgent

**文件**: `agents/orchestrator.py` (490行)

**目的**: 管理完整混合规划生命周期的中央协调者，负责任务分类、路由选择和执行协调。

**主要职责**:
- 检索相关记忆和知识
- 使用两阶段混合分类器对任务复杂度进行分类
- 路由到三种执行路径：simple/complex/emergent
- 协调执行和反思过程
- 处理重规划逻辑
- 存储学习成果到长期记忆

**主要方法签名**:
```python
class OrchestratorAgent:
    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tools: list[BaseTool] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> None
    
    async def run(self, task: str) -> str
    async def _gather_context(self, task: str) -> str
    async def _execute_and_reflect_simple(self, task: str, context: str) -> str
    async def _execute_dag_and_reflect(self, task: str, context: str) -> str
    async def _execute_emergent(self, task: str, context: str) -> str
```

**架构流程**:
```mermaid
graph LR
    A[用户任务] --> B[Orchestrator.run]
    B --> C[_gather_context]
    C --> D[Planner.classify_task]
    D --> E{分类结果}
    E -->|simple| F[_execute_and_reflect_simple]
    E -->|complex| G[_execute_dag_and_reflect]
    E -->|emergent| H[_execute_emergent]
    F --> I[存储长期记忆]
    G --> I
    H --> I
    I --> J[返回结果]
```

### 2. PlannerAgent

**文件**: `agents/planner.py` (909行)

**目的**: 两阶段混合分类器 + v5探索性模式检测，负责任务分类和计划生成。

**主要职责**:
- 两阶段任务分类：规则快筛 + LLM 兜底
- v1 扁平计划生成（2-6步）
- v2 DAG 分层计划生成（3层结构）
- 重规划逻辑（完整重规划/局部子树重规划）
- 自适应规划（运行时 DAG 变更）

**6个正则模式**:
```python
_MULTI_STEP_PATTERN = r"(?:step|phase|stage|then|next|after|first|second|third)"
_CONDITIONAL_PATTERN = r"(?:if|when|case|depending|based on|otherwise|else)"
_PARALLEL_PATTERN = r"(?:parallel|concurrent|simultaneously|together|at the same time)"
_ACTION_VERB_PATTERN = r"(?:create|write|implement|build|generate|develop)"
_EXPLORATORY_PATTERN = r"(?:explore|investigate|research|find|discover|analyze)"
_UNCERTAINTY_PATTERN = r"(?:maybe|possibly|might|could|try|attempt)"
```

**主要方法签名**:
```python
class PlannerAgent(BaseAgent):
    async def classify_task(self, task: str) -> str
    def _rule_classify(self, task: str) -> str | None
    async def _llm_classify(self, task: str) -> str
    async def create_plan(self, task: str, context: str = "") -> Plan
    async def create_dag(self, task: str, context: str = "") -> TaskDAG
    async def replan(self, task: str, failed_step_id: str, context: str) -> Plan
    async def replan_subtree(self, dag: TaskDAG, failed_node_id: str, context: str) -> TaskDAG
    async def adapt_plan(self, dag: TaskDAG, adaptations: list[PlanAdaptation]) -> AdaptationResult
    async def apply_adaptations(self, dag: TaskDAG, actions: list[AdaptAction]) -> None
```

**两阶段分类器架构**:
```mermaid
graph TD
    A[用户任务] --> B[规则快筛]
    B -->|明确分类| C[返回结果]
    B -->|模糊| D[LLM分类]
    D --> E[返回结果]
    
    style B fill:#90EE90
    style D fill:#FFB6C1
```

### 3. ExecutorAgent

**文件**: `agents/executor.py` (321行)

**目的**: ReAct循环执行器，负责逐步执行计划中的每个步骤/节点。

**主要职责**:
- 实现 ReAct（推理 + 行动）模式
- 使用 OpenAI 兼容的 function calling
- 支持步骤执行和节点执行两种模式
- v6 集成 ReActEngine Feature Flag
- v3 集成 ToolRouter 智能路由

**主要方法签名**:
```python
class ExecutorAgent(BaseAgent):
    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        max_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
        use_react_engine: bool | None = None,
    ) -> None
    
    async def execute_step(self, step: Step, context: str = "") -> StepResult
    async def execute_node(self, node: TaskNode, context: str = "") -> StepResult
    async def _react_loop(self, messages: list[dict], tools: list[BaseTool]) -> StepResult
```

**ReAct 循环流程**:
```mermaid
graph TD
    A[开始] --> B[Thought: LLM推理]
    B --> C{需要工具?}
    C -->|是| D[Action: 调用工具]
    D --> E[Observe: 获取结果]
    E --> F{完成?}
    F -->|否| B
    F -->|是| G[返回最终答案]
    C -->|否| G
```

### 4. ReflectorAgent

**文件**: `agents/reflector.py` (254行)

**目的**: 质量验证与反馈，负责评估执行结果的质量。

**主要职责**:
- 验证退出条件（exit criteria）
- 提供执行质量评估
- 生成改进建议
- 决定是否需要重规划

**主要方法签名**:
```python
class ReflectorAgent(BaseAgent):
    async def validate_exit_criteria(self, node: TaskNode, result: StepResult) -> bool
    async def reflect_dag(self, dag: TaskDAG, final_result: str) -> tuple[bool, str]
    async def reflect(self, task: str, result: str) -> tuple[bool, str]
```

### 5. EmergentPlannerAgent

**文件**: `agents/emergent_planner.py` (683行)

**目的**: Claude Code 风格隐式规划器，通过 TODO 列表管理实现动态规划。

**主要职责**:
- 无独立规划阶段，规划自然涌现
- TODO 列表动态创建、更新、完成
- 单一扁平消息历史
- while(tool_use) 主循环
- v6 集成 ReActEngine Feature Flag

**主要方法签名**:
```python
class EmergentPlannerAgent(BaseAgent):
    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[BaseTool],
        max_iterations: int | None = None,
        context_manager: ContextManager | None = None,
        tool_router: ToolRouter | None = None,
        use_react_engine: bool | None = None,
    ) -> None
    
    async def execute(self, task: str, context: str = "") -> str
    async def _init_todo_list(self, task: str) -> TodoList
    async def _execute_todo(self, todo: TodoItem, context: str) -> TodoItem
    async def _update_todo_list(self, current_todos: TodoList, context: str) -> TodoList
    async def _compile_answer(self, completed_todos: list[TodoItem], context: str) -> str
```

**隐式规划流程**:
```mermaid
graph TD
    A[初始化TODO列表] --> B{有待办项?}
    B -->|是| C[选择下一个待办]
    C --> D[think_with_tools]
    D --> E[更新TODO列表]
    E --> B
    B -->|否| F[编译最终答案]
```

### 6. BaseAgent

**文件**: `agents/base.py` (182行)

**目的**: 所有智能体的基类，提供统一的 LLM 交互能力。

**主要职责**:
- 统一的 LLM 调用接口
- 工具调用结果管理
- 消息历史管理
- 系统提示词管理

**主要方法签名**:
```python
class BaseAgent:
    def __init__(
        self,
        name: str,
        system_prompt: str,
        llm_client: LLMClient,
        context_manager: ContextManager | None = None,
    ) -> None
    
    async def think(self, user_input: str, **kwargs: Any) -> str
    async def think_json(self, user_input: str, schema: dict, **kwargs: Any) -> dict
    async def think_with_tools(self, user_input: str, tools: list[BaseTool], **kwargs: Any) -> tuple[str, list[ToolCallRecord]]
    def add_message(self, role: str, content: str) -> None
    def get_messages(self) -> list[dict[str, Any]]
    def reset(self) -> None
```

### 7. DAGExecutor

**文件**: `dag/executor.py` (647行)

**目的**: Super-step 并行执行引擎，替代原 Orchestrator 的顺序 for 循环。

**主要职责**:
- Super-step 并行执行模型
- 节点状态管理
- 失败处理（回滚 + 跳过下游子树）
- 条件边评估
- v3 自适应规划支持
- 逐节点 exit criteria 验证

**主要方法签名**:
```python
class DAGExecutor:
    def __init__(
        self,
        executor_agent: ExecutorAgent,
        reflector_agent: ReflectorAgent,
        planner_agent: PlannerAgent | None = None,
        max_parallel: int | None = None,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> None
    
    async def execute(self, dag: TaskDAG) -> tuple[bool, str]
    async def _run_node(self, node: TaskNode, context: str) -> StepResult
    async def _run_node_with_timeout(self, node: TaskNode, context: str, timeout: int) -> StepResult
    async def _handle_failure(self, node: TaskNode, result: StepResult) -> None
    async def _process_conditions(self, dag: TaskDAG, completed_nodes: list[TaskNode]) -> None
    async def _adapt_plan(self, dag: TaskDAG) -> None
    async def _complete_structural_nodes(self, dag: TaskDAG) -> None
```

**Super-step 执行流程**:
```mermaid
graph TD
    A[开始] --> B[找出所有就绪节点]
    B --> C[并行执行节点]
    C --> D[合并结果到DAGState]
    D --> E[验证exit criteria]
    E --> F[处理失败节点]
    F --> G[评估条件边]
    G --> H{DAG完成?}
    H -->|否| B
    H -->|是| I[返回结果]
```

### 8. TaskDAG

**文件**: `dag/graph.py` (626行)

**目的**: DAG 数据结构与操作，提供分层任务规划的图结构支持。

**主要职责**:
- 节点和边的管理
- 就绪节点发现
- 拓扑排序
- 子树跳过标记
- 状态刷新
- v3 动态变更支持

**主要方法签名**:
```python
class TaskDAG:
    def __init__(
        self,
        task: str,
        nodes: dict[str, TaskNode],
        edges: list[TaskEdge],
        context: str = "",
        state_machine: NodeStateMachine | None = None,
    ) -> None
    
    def get_ready_nodes(self) -> list[TaskNode]
    def topological_sort(self) -> list[str]
    def mark_subtree_skipped(self, node_id: str) -> None
    def refresh_ready_states(self) -> None
    def get_downstream(self, node_id: str) -> set[str]
    def is_complete(self) -> bool
    
    # v3 动态变更方法
    def add_dynamic_node(self, node: TaskNode) -> None
    def remove_pending_node(self, node_id: str) -> None
    def modify_node(self, node_id: str, updates: dict) -> None
    def add_dynamic_edge(self, edge: TaskEdge) -> None
```

**DAG 结构示例**:
```mermaid
graph TB
    Goal[GOAL] --> SG1[SUBGOAL_1]
    Goal --> SG2[SUBGOAL_2]
    SG1 --> A1[ACTION_1]
    SG1 --> A2[ACTION_2]
    SG2 --> A3[ACTION_3]
    A1 --> A4[ACTION_4]
    A2 --> A4
    A3 --> A4
```

### 9. NodeStateMachine

**文件**: `dag/state_machine.py` (113行)

**目的**: 节点状态机，校验并强制执行节点生命周期的合法状态转移。

**主要职责**:
- 状态转移表管理
- 合法性校验
- 状态转移应用
- 事件回调触发

**状态转移图**:
```mermaid
graph LR
    PENDING[PENDING] --> READY[READY]
    READY --> RUNNING[RUNNING]
    RUNNING --> COMPLETED[COMPLETED]
    RUNNING --> FAILED[FAILED]
    RUNNING --> SKIPPED[SKIPPED]
    FAILED --> ROLLED_BACK[ROLLED_BACK]
    FAILED --> PENDING
    FAILED --> SKIPPED
    PENDING -.-> SKIPPED
    READY -.-> SKIPPED
```

**主要方法签名**:
```python
class NodeStateMachine:
    def __init__(self, on_transition: Callable[[str, NodeStatus, NodeStatus], None] | None = None)
    def can_transition(self, node: TaskNode, new_status: NodeStatus) -> bool
    def transition(self, node: TaskNode, new_status: NodeStatus) -> None
```

### 10. LLMClient

**文件**: `llm/client.py` (227行)

**目的**: OpenAI 兼容 API 的统一封装，支持多种 LLM 服务商。

**主要职责**:
- 统一的 chat completions 接口
- Function calling 支持
- v6 指数退避重试机制
- JSON 响应解析

**主要方法签名**:
```python
class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        retry_enabled: bool | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
    )
    
    async def chat(self, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096, **kwargs) -> str
    async def chat_with_tools(self, messages: list[dict], tools: list[dict], temperature: float = 0.7, **kwargs) -> tuple[str, list[ToolCallRecord]]
    async def chat_json(self, messages: list[dict], schema: dict, temperature: float = 0.7, **kwargs) -> dict
    def _parse_json(self, text: str) -> dict
```

**v6 重试机制**:
```mermaid
graph TD
    A[调用LLM] --> B{成功?}
    B -->|是| C[返回结果]
    B -->|否| D{可重试错误?}
    D -->|否| E[抛出异常]
    D -->|是| F{达到最大重试?}
    F -->|是| E
    F -->|否| G[等待退避时间]
    G --> A
```

### 11. 工具系统

#### BaseTool
**文件**: `tools/base.py` (86行)

**目的**: 工具基类，定义统一的工具接口。

**主要方法签名**:
```python
class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str
    
    @property
    @abstractmethod
    def description(self) -> str
    
    @property
    def parameters(self) -> dict
    
    @abstractmethod
    async def execute(self, **kwargs) -> str
```

#### ToolRouter
**文件**: `tools/router.py` (167行)

**目的**: 智能工具选择与基于失败的自动切换。

**主要职责**:
- 追踪每个节点的工具使用统计
- 失败计数和连续失败检测
- 超过阈值时建议替代工具
- 使用统计记录

**主要方法签名**:
```python
class ToolRouter:
    def __init__(self, available_tools: list[str], failure_threshold: int | None = None)
    def record_success(self, node_id: str, tool_name: str) -> None
    def record_failure(self, node_id: str, tool_name: str) -> None
    def get_hint(self, node_id: str) -> str | None
    def get_stats(self) -> dict
```

#### WebSearchTool
**文件**: `tools/web_search.py` (112行)

**目的**: 网络搜索工具，提供 mock 搜索功能。

**主要方法签名**:
```python
class WebSearchTool(BaseTool):
    async def execute(self, query: str, max_results: int = 5) -> str
```

#### CodeExecutorTool
**文件**: `tools/code_executor.py` (108行)

**目的**: 代码执行工具，通过 subprocess 沙箱执行 Python 代码。

**主要方法签名**:
```python
class CodeExecutorTool(BaseTool):
    async def execute(self, code: str, timeout: int = 30) -> str
```

#### FileOpsTool
**文件**: `tools/file_ops.py` (137行)

**目的**: 文件操作工具，提供安全的文件读写功能。

**主要方法签名**:
```python
class FileOpsTool(BaseTool):
    async def execute(self, action: str, **kwargs) -> str
    # 支持的操作: read, write, list, delete
```

#### ShellTool
**文件**: `tools/shell_tool.py` (130行)

**目的**: Shell 命令执行工具，在沙箱子进程中执行 bash 命令。

**主要职责**:
- 基于 `asyncio.create_subprocess_exec` 实现，支持超时、流式输出捕获
- 在 sandbox 目录下执行，通过黑名单限制危险命令（`rm -rf`, `mkfs`, `dd` 等）
- 支持工作目录切换、环境变量传递

**主要方法签名**:
```python
class ShellTool(BaseTool):
    async def execute(self, command: str, timeout: int = 30) -> str
```

### 12. 记忆系统

#### ShortTermMemory
**文件**: `memory/short_term.py` (90行)

**目的**: 短期记忆，使用滑动窗口缓冲区保留最近对话消息。

**主要职责**:
- 滑动窗口管理（默认 20 条）
- FIFO 自动淘汰
- 快速获取最近消息

**主要方法签名**:
```python
class ShortTermMemory:
    def __init__(self, window_size: int | None = None)
    def add(self, message: dict) -> None
    def get_messages(self) -> list[dict]
    def get_recent(self, n: int = 5) -> list[dict]
    def clear(self) -> None
    def to_text(self) -> str
```

#### LongTermMemory
**文件**: `memory/long_term.py` (141行)

**目的**: 长期记忆，基于 JSON 文件的持久化存储。

**主要职责**:
- JSON 文件持久化
- 关键词重叠度检索
- 任务摘要和学习成果存储

**主要方法签名**:
```python
class LongTermMemory:
    def __init__(self, memory_dir: str | None = None) -> None
    def _load(self) -> list[MemoryEntry]
    def _save(self) -> None
    def store(self, entry: MemoryEntry) -> None
    def search(self, query: str, top_k: int = 3) -> list[MemoryEntry]
```

### 13. 上下文与知识

#### ContextManager
**文件**: `context/manager.py` (186行)

**目的**: 上下文管理器，带 Token 感知的上下文窗口管理。

**主要职责**:
- Token 使用量估算
- 自动上下文压缩
- 保留 system prompt 和最近消息

**主要方法签名**:
```python
class ContextManager:
    def __init__(self, max_tokens: int | None = None, reserve_recent: int = 6)
    @staticmethod
    def estimate_tokens(text: str) -> int
    def estimate_messages_tokens(self, messages: list[dict]) -> int
    async def compress_if_needed(self, messages: list[dict], llm_client: Any) -> list[dict]
```

#### KnowledgeRetriever
**文件**: `knowledge/retriever.py` (228行)

**目的**: 知识检索器，使用 TF-IDF + 余弦相似度检索相关知识。

**主要职责**:
- TF-IDF 向量化
- 余弦相似度计算
- Top-K 相关文档检索

**主要方法签名**:
```python
class KnowledgeRetriever:
    def __init__(self, docs_dir: str | None = None, chunk_size: int | None = None) -> None
    def _build_index(self) -> None
    def _split_text(self, text: str, chunk_size: int) -> list[str]
    def _tokenize(self, text: str) -> list[str]
    def _compute_tf(self, text: str) -> dict[str, float]
    def retrieve(self, query: str, top_k: int | None = None) -> list[tuple[str, float]]
```

## 数据流

### 完整任务执行流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant Main as Main
    participant Orchestrator as Orchestrator
    participant Planner as Planner
    participant Memory as Memory
    participant Knowledge as Knowledge
    participant Executor as Executor
    participant Reflector as Reflector
    
    User->>Main: 输入任务
    Main->>Orchestrator: run(task)
    
    Orchestrator->>Memory: 检索相关记忆
    Memory-->>Orchestrator: 返回记忆条目
    
    Orchestrator->>Knowledge: 检索相关知识
    Knowledge-->>Orchestrator: 返回相关文档
    
    Orchestrator->>Planner: classify_task(task)
    Planner->>Planner: 规则快筛
    alt 规则明确
        Planner-->>Orchestrator: 返回分类结果
    else 规则模糊
        Planner->>Planner: LLM分类
        Planner-->>Orchestrator: 返回分类结果
    end
    
    alt Simple路径
        Orchestrator->>Planner: create_plan()
        Planner-->>Orchestrator: 返回扁平计划
        Orchestrator->>Executor: 顺序执行计划
        loop 每个步骤
            Executor->>Executor: ReAct循环
        end
        Orchestrator->>Reflector: reflect()
    else Complex路径
        Orchestrator->>Planner: create_dag()
        Planner-->>Orchestrator: 返回DAG
        Orchestrator->>Executor: 执行DAG
        loop 每个Super-step
            Executor->>Executor: 并行执行就绪节点
        end
        Orchestrator->>Reflector: reflect_dag()
    else Emergent路径
        Orchestrator->>Planner: 隐式规划
        Planner-->>Orchestrator: 返回TODO列表
        loop while(tool_use)
            Executor->>Executor: 执行待办项
            Executor->>Planner: 更新TODO列表
        end
    end
    
    Reflector-->>Orchestrator: 返回验证结果
    
    alt 验证通过
        Orchestrator->>Memory: 存储学习成果
        Orchestrator-->>User: 返回最终答案
    else 验证失败
        Orchestrator->>Planner: 重规划
        Planner-->>Orchestrator: 返回新计划
        Orchestrator->>Executor: 重新执行
    end
```

### 数据流转关键节点

1. **上下文收集阶段**
   - 从 LongTermMemory 检索相关历史经验
   - 从 KnowledgeRetriever 检索相关知识文档
   - 合并为 context 字符串传递给后续组件

2. **任务分类阶段**
   - 规则快筛：使用 6 个正则模式快速分类
   - LLM 兜底：对模糊任务进行轻量级 LLM 分类

3. **计划生成阶段**
   - Simple: 生成 2-6 步扁平计划
   - Complex: 生成 3 层分层 DAG（Goal -> SubGoals -> Actions）
   - Emergent: 初始化 TODO 列表（1-3 项）

4. **执行阶段**
   - Simple: Executor 顺序执行，每步独立 ReAct 循环
   - Complex: DAGExecutor Super-step 并行执行
   - Emergent: while(tool_use) 循环，动态更新 TODO

5. **反思阶段**
   - 验证 exit criteria 是否满足
   - 评估执行质量
   - 决定是否需要重规划

6. **持久化阶段**
   - 将任务摘要和学习成果存入 LongTermMemory
   - 为未来类似任务提供经验参考

## 关键设计模式

### 1. ReAct (Reasoning + Acting)

**描述**: Executor 和 EmergentPlanner 都采用 ReAct 模式，通过 Thought-Action-Observe 循环逐步完成任务。

**实现位置**: 
- `agents/executor.py`: `_react_loop()` 方法
- `agents/emergent_planner.py`: `think_with_tools()` 调用

**优势**:
- 让 LLM 能够自然地推理和行动
- 通过工具调用扩展 LLM 能力
- 支持多轮迭代和自我修正

### 2. Super-step / BSP 并行模型

**描述**: DAGExecutor 采用 Super-step 模型，每轮并行执行所有就绪节点，借鉴 LangGraph 的 Pregel 运行时。

**实现位置**: `dag/executor.py`: `execute()` 方法

**优势**:
- 提高执行效率，充分利用并行性
- 集中式状态管理，简化数据流
- 支持条件边和动态 DAG 变更

**示例**:
```python
# 每个 Super-step:
ready_nodes = dag.get_ready_nodes()
results = await asyncio.gather(*[run_node(n) for n in ready_nodes])
# 合并结果、验证、评估条件边
```

### 3. 集中式状态管理

**描述**: TaskDAG 使用 DAGState 作为集中式共享状态，所有节点通过 state 共享数据，灵感来自 LangGraph。

**实现位置**: `dag/graph.py`: `DAGState` 类

**优势**:
- 简化节点间数据传递
- 支持时间旅行调试（checkpoints）
- 便于状态快照和恢复

**对比**:
- LangGraph: 使用 TypedDict 定义状态
- 本实现: 使用简单的 dict，更轻量

### 4. 有限状态机 (FSM)

**描述**: NodeStateMachine 严格管控节点生命周期，防止非法状态转移。

**实现位置**: `dag/state_machine.py`: `NodeStateMachine` 类

**优势**:
- 确保状态一致性
- 提供清晰的转移路径
- 支持事件驱动 UI 更新

**状态转移表**:
```python
VALID_TRANSITIONS = {
    NodeStatus.PENDING: {NodeStatus.READY, NodeStatus.SKIPPED},
    NodeStatus.READY: {NodeStatus.RUNNING, NodeStatus.SKIPPED},
    NodeStatus.RUNNING: {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED},
    NodeStatus.FAILED: {NodeStatus.ROLLED_BACK, NodeStatus.SKIPPED, NodeStatus.PENDING},
    NodeStatus.COMPLETED: set(),
    NodeStatus.SKIPPED: set(),
    NodeStatus.ROLLED_BACK: set(),
}
```

### 5. 两阶段混合分类器

**描述**: Planner 使用规则快筛 + LLM 兜底的两阶段分类器，平衡效率和准确率。

**实现位置**: `agents/planner.py`: `classify_task()` 方法

**优势**:
- 规则快筛零成本，处理 60-70% 显然任务
- LLM 兜底处理模糊区间，保证准确率
- 节省 token 成本，参考 DAAO 和 RouteLLM (ICLR 2025)

**流程**:
```python
# Stage 1: 规则快筛（< 1ms）
result = self._rule_classify(task)
if result:
    return result

# Stage 2: LLM 分类（仅对模糊区间，~60 tokens）
return await self._llm_classify(task)
```

### 6. 隐式涌现规划

**描述**: EmergentPlanner 采用 Claude Code 风格的隐式规划，通过 TODO 列表管理动态涌现计划。

**实现位置**: `agents/emergent_planner.py`: `EmergentPlannerAgent` 类

**优势**:
- 无独立规划阶段，减少 token 消耗
- TODO 列表动态演化，适应不确定性
- 单一扁平消息历史，LLM 可见所有上下文

**核心循环**:
```python
while has_pending_todos(todo_list):
    todo = select_next_todo(todo_list)
    result = await think_with_tools(todo)
    todo_list = await update_todo_list(todo_list, result)
```

### 7. 事件驱动 UI

**描述**: 所有组件支持 `on_event` 回调，实现事件驱动的 UI 实时更新。

**实现位置**: 
- `dag/executor.py`: `_on_node_transition()` 回调
- `dag/state_machine.py`: `on_transition` 参数

**优势**:
- 解耦业务逻辑和 UI 更新
- 支持实时状态可视化
- 便于调试和监控

**示例**:
```python
def on_event(event_type: str, data: Any):
    if event_type == "node_transition":
        update_ui_node_status(data["node_id"], data["new_status"])
```

## 文件参考

### 核心文件汇总表

| 文件路径 | 行数 | 核心职责 | 关键类/方法 |
|---------|------|---------|-----------|
| `agents/orchestrator.py` | 490 | 中央协调者，三路由管理 | `OrchestratorAgent.run()` |
| `agents/planner.py` | 909 | 混合分类器 + 计划生成 | `PlannerAgent.classify_task()` |
| `agents/executor.py` | 321 | ReAct 执行器 | `ExecutorAgent._react_loop()` |
| `agents/reflector.py` | 254 | 质量验证与反馈 | `ReflectorAgent.reflect()` |
| `agents/emergent_planner.py` | 683 | 隐式规划器 | `EmergentPlannerAgent.execute()` |
| `agents/base.py` | 182 | 智能体基类 | `BaseAgent.think()` |
| `dag/executor.py` | 647 | Super-step 并行执行 | `DAGExecutor.execute()` |
| `dag/graph.py` | 626 | DAG 数据结构 | `TaskDAG.get_ready_nodes()` |
| `dag/state_machine.py` | 113 | 节点状态机 | `NodeStateMachine.transition()` |
| `llm/client.py` | 227 | LLM 客户端 | `LLMClient.chat()` |
| `react/engine.py` | 245 | 统一 ReAct 引擎 | `ReActEngine.execute()` |
| `tools/base.py` | 86 | 工具基类 | `BaseTool.execute()` |
| `tools/router.py` | 167 | 智能工具路由 | `ToolRouter.get_hint()` |
| `tools/web_search.py` | 112 | 网络搜索工具 | `WebSearchTool.execute()` |
| `tools/code_executor.py` | 108 | 代码执行工具 | `CodeExecutorTool.execute()` |
| `tools/file_ops.py` | 137 | 文件操作工具 | `FileOpsTool.execute()` |
| `tools/shell_tool.py` | 130 | Shell 命令执行工具 | `ShellTool.execute()` |
| `memory/short_term.py` | 90 | 短期记忆 | `ShortTermMemory.add()` |
| `memory/long_term.py` | 141 | 长期记忆 | `LongTermMemory.search()` |
| `context/manager.py` | 186 | 上下文管理 | `ContextManager.compress_if_needed()` |
| `knowledge/retriever.py` | 228 | 知识检索器 | `KnowledgeRetriever.retrieve()` |
| `schema.py` | 573 | 数据模型定义 | `Plan`, `TaskDAG`, `TaskNode` |
| `config.py` | 82 | 全局配置 | `LLM_MODEL`, `MAX_PARALLEL_NODES` |
| `main.py` | 439 | CLI 入口 | `main()` |

### 版本演进关键文件

| 版本 | 新增/修改文件 | 核心变更 |
|------|-------------|---------|
| v1 | `agents/planner.py` | 线性规划器，2-6 步扁平计划 |
| v2 | `dag/executor.py`, `dag/graph.py`, `dag/state_machine.py` | DAG 分层规划 + Super-step 并行 |
| v3 | `tools/router.py`, `dag/graph.py` | 工具路由 + 动态 DAG 变更 |
| v4 | `agents/planner.py` | 两阶段混合分类器 |
| v5 | `agents/emergent_planner.py` | 隐式规划 + TODO 列表管理 |
| v6 | `llm/client.py`, `agents/executor.py`, `agents/emergent_planner.py`, `tools/shell_tool.py`, `react/engine.py` | LLM 重试 + ReActEngine + ShellTool + 统一 ReAct 引擎 |

### 测试文件

| 文件路径 | 测试内容 |
|---------|---------|
| `tests/test_dag_capabilities.py` | DAG 功能测试 |
| `tests/test_emergent_planning.py` | 隐式规划测试 |
| `tests/test_emergent_simple.py` | 隐式规划简单场景测试 |
| `tests/test_llm_integration.py` | LLM 集成测试 |
| `tests/test_optimizations.py` | 性能优化测试 |
| `tests/test_real_tools.py` | 真实工具测试 |
| `tests/test_shell_tool.py` | Shell 工具测试 |
| `tests/test_concurrent_execution.py` | 并发执行测试 |
| `tests/test_cycle_detection.py` | 循环检测测试 |

### 文档文件

| 文件路径 | 内容 |
|---------|------|
| `docs/codemap.md` | 本代码地图文档 |
| `docs/CHANGELOG.md` | 版本更新日志 |
| `docs/data-structures-and-algorithms.md` | 数据结构与算法说明 |
| `docs/dynamic-features.md` | v1~v5 动态特性对比 |
| `docs/emergent-planning.md` | v5/v6 隐式规划文档 |
| `docs/emergent-planning-test-scenarios.md` | 隐式规划测试场景 |
| `docs/hybrid-plan-routing.md` | v4 混合路由文档 |
| `docs/llm-integration.md` | v6 LLM 集成文档 |
| `docs/planning-gap-analysis.md` | 规划缺口分析 |
| `docs/planning-test-scenarios.md` | 规划测试场景 |
| `docs/related-papers.md` | 相关论文（DAAO, RouteLLM） |
| `docs/upgrade-plan.md` | v6 升级计划 |

---

## 总结

Manus Demo 是一个功能完整的多智能体任务执行系统，具有以下核心特点：

1. **混合路由架构**：自动选择 Simple/Complex/Emergent 三条执行路径，适应不同复杂度的任务
2. **两阶段分类器**：规则快筛 + LLM 兜底，在效率和准确率之间取得平衡
3. **Super-step 并行**：借鉴 LangGraph 的 Pregel 运行时，支持高效的并行执行
4. **状态机管控**：严格节点生命周期管理，确保系统一致性
5. **隐式规划**：Claude Code 风格的动态规划，适应不确定性任务
6. **LLM 重试机制**：v6 新增的指数退避重试，提升系统稳定性
7. **Shell 命令执行**：v6 新增 `ShellTool`，支持在沙箱中执行 bash 命令，增强真实环境交互能力

该系统展示了如何将多种先进技术（ReAct、DAG、状态机、混合路由等）整合为一个实用的多智能体系统，为复杂任务的自动化执行提供了完整的解决方案。
