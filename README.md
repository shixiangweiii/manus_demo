# Manus Demo — DAG-based Multi-Agent System

一个面向教学的多智能体系统 Demo，帮助你理解自主 AI Agent 的核心原理：
**分层规划、DAG 并行执行、工具调用、状态机驱动、自我反思与纠错**。

> **v5 当前**：新增 Claude Code 风格的隐式规划（Emergent Planning），
> 通过 TODO 列表管理和 `while(tool_use)` 主循环实现规划涌现，
> 适合探索性、需求不明确的开放式任务。
>
> **v4**：混合规划路由（两阶段分类器自动选择 v1 扁平计划、v2 DAG 或 v5 隐式规划）、
> 简单任务走 v1 省 token，复杂任务走 v2 支持并行与容错，探索性任务走 v5 灵活应对。
>
> **v3**：执行期间动态自适应规划（超步间 LLM 评估 → DAG 增删改节点）、
> 工具路由（连续失败自动建议切换替代工具）、DAG 运行时变更 API。
>
> v2 已从「静态线性分步」升级为「动态任务图（DAG）+ 可执行状态机」。
> 核心设计借鉴了 LangGraph 的集中状态、Super-step 并行和 Checkpoint 理念，
> 同时保持极简实现以保证教学透明度。

---

## Architecture

```
User Task
   │
   ▼
┌─────────────────────────────────────────────────┐
│             Orchestrator Agent                   │
│  ┌──────────┐ ┌─────────────┐ ┌──────────────┐ │
│  │  Memory   │ │  Knowledge  │ │   Context    │ │
│  │ (ST + LT) │ │  Retriever  │ │   Manager    │ │
│  └──────────┘ └─────────────┘ └──────────────┘ │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│  (v4/v5) classify_task → simple | complex | emergent  │
│  simple: Planner.create_plan() → flat Plan      │
│  complex: Planner.create_dag() → TaskDAG        │
│  emergent: EmergentPlanner.execute() → TodoList │
│  Task → Goal → SubGoals → Actions               │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│         DAG Executor  (Super-step 模型)          │
│  while DAG 未完成:                               │
│    1. 找出所有就绪节点                            │
│    2. 并行执行 (asyncio.gather)                   │
│    3. 合并结果到 DAGState                         │
│    4. 验证 exit criteria                         │
│    5. 处理失败 (回滚 + 跳过子树)                   │
│    6. 评估条件分支                                │
│    7. Checkpoint 快照                            │
│                                                  │
│  (v3) 超步间自适应规划:                           │
│    8. Planner 评估中间结果 → REMOVE/MODIFY/ADD   │
│                                                  │
│  每个 ACTION 节点内部运行 ReAct 循环:             │
│    Thought → Tool Call → Observation → Repeat    │
│    Tools: web_search, execute_python, file_ops   │
│    (v3) Tool Router: 连续失败 → 建议替代工具      │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│         Reflector Agent  (v2)                    │
│  per-node: validate_exit_criteria()              │
│  full DAG: reflect_dag() → 通过 / 需要重做       │
│  若失败 → 局部重规划 (仅重建失败子树)              │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
              Final Answer
```

## Key Design Patterns

| 模式 | 说明 |
|------|------|
| **Hybrid Plan Routing** (v4) | 两阶段分类器（规则快筛 + LLM 兜底）自动选择 simple(v1)、complex(v2) 或 emergent(v5) 路径 |
| **Emergent Planning** (v5) | Claude Code 风格，通过 TODO 列表管理和 `while(tool_use)` 主循环实现规划涌现，适合探索性任务 |
| **Hierarchical Planning** | Goal → SubGoal → Action 三层分解，每个节点带 exit criteria + 风险评估 |
| **DAG Execution** | 节点按拓扑序执行，无依赖的节点自动**并行** (Super-step 模型) |
| **State Machine** | 节点生命周期 `PENDING → READY → RUNNING → COMPLETED / FAILED` 由状态机强制校验 |
| **Conditional Branch** | CONDITIONAL 边根据上游结果动态启用/跳过下游路径 |
| **Rollback** | ROLLBACK 边在节点失败时触发回滚操作，并自动跳过下游子树 |
| **ReAct** | 每个 ACTION 节点内部执行 Thought → Tool Call → Observation 循环 |
| **Partial Replan** | 反思不通过时仅重建失败子树，保留已完成的工作 |
| **Centralized State** | DAGState 作为唯一数据源，所有节点结果写入同一 dict (灵感来自 LangGraph) |
| **Checkpoint** | 每个 Super-step 结束后快照 DAG 状态，支持调试回溯 |
| **Adaptive Planning** (v3) | 每个 Super-step 后 Planner 评估中间结果，动态 REMOVE/MODIFY/ADD DAG 节点 |
| **Tool Router** (v3) | 追踪工具连续失败次数，达到阈值后向 LLM 注入替代工具建议 |
| **Dynamic DAG Mutation** (v3) | 运行时增删改节点和边，支持自适应规划的实际落地 |

## Project Structure

```
manus_demo/
├── main.py                         # CLI 入口 (交互模式 / 单任务模式)
├── config.py                       # 配置 (API Key、限制参数)
├── schema.py                       # Pydantic 数据模型 (TaskNode, DAGState, ...)
├── requirements.txt                # Python 依赖
│
├── agents/                         # 智能体
│   ├── base.py                     #   BaseAgent — LLM 调用、消息管理
│   ├── orchestrator.py             #   Orchestrator — 全流程协调
│   ├── planner.py                  #   Planner — 分层规划，输出 TaskDAG
│   ├── executor.py                 #   Executor — ReAct 循环 + 工具调用
│   ├── reflector.py                #   Reflector — 结果验证、质量评估
│   └── emergent_planner.py         #   EmergentPlanner (v5) — Claude Code 风格隐式规划
│
├── dag/                            # DAG 执行引擎
│   ├── graph.py                    #   TaskDAG — 图结构、拓扑排序、就绪检测
│   ├── state_machine.py            #   NodeStateMachine — 节点状态转移校验
│   └── executor.py                 #   DAGExecutor — Super-step 并行执行循环
│
├── tools/                          # 外部工具
│   ├── base.py                     #   BaseTool 抽象接口
│   ├── web_search.py               #   Web 搜索 (内置 mock 结果)
│   ├── code_executor.py            #   Python 代码执行 (subprocess 沙箱)
│   ├── file_ops.py                 #   文件操作 (沙箱目录)
│   └── router.py                   #   ToolRouter (v3) — 工具失败追踪 + 替代建议
│
├── memory/                         # 记忆系统
│   ├── short_term.py               #   短期记忆 — 滑动窗口
│   └── long_term.py                #   长期记忆 — JSON 持久化
│
├── context/
│   └── manager.py                  # 上下文管理 — Token 估算 + LLM 摘要压缩
│
├── knowledge/
│   ├── retriever.py                # TF-IDF 知识检索
│   └── docs/                       # 知识库文档 (.txt / .md)
│
├── llm/
│   └── client.py                   # OpenAI 兼容 API 封装
│
├── tests/
│   ├── test_dag_capabilities.py    # 单元测试 (规划、并行执行、条件分支/回滚、v3 自适应)
│   ├── test_emergent_planning.py   # v5 单元测试 (TODO 列表管理、EmergentPlanner)
│   └── test_emergent_simple.py     # v5 简单测试脚本 (无需 pytest)
│
└── docs/                           # 项目文档
    ├── upgrade-plan-v3.md          #   v3 升级计划 (含完成状态)
    ├── hybrid-plan-routing-v4.md   #   v4 混合规划路由说明
    ├── dynamic-features-v1-vs-v2.md#   v1→v2→v3 动态性对比分析
    ├── emergent-planning-v5.md     #   v5 隐式规划系统详解
    ├── emergent-planning-test-scenarios-v5.md  # v5 测试用例集
    ├── data-structures-and-algorithms.md  # 数据结构与算法详解
    ├── codemap-v4.md               #   完整代码地图 (已更新为 v5)
    └── planning-test-scenarios-v4.md  # v4 测试用例集
```

## Quick Start

### 1. 环境准备

需要 **Python 3.11+**。建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

运行测试还需要：

```bash
pip install pytest pytest-asyncio
```

### 3. 配置 LLM API

复制示例配置文件并填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# DeepSeek (默认)
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-your-key-here
LLM_MODEL=deepseek-chat

# 或 Ollama (本地模型)
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_API_KEY=ollama
# LLM_MODEL=llama3

# 或通义千问
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_API_KEY=your-api-key-here
# LLM_MODEL=qwen-turbo
```

支持任何 **OpenAI 兼容接口** — 修改 `LLM_BASE_URL` 和 `LLM_MODEL` 即可。

### 4. 运行 Demo

**交互模式** — 多轮对话：

```bash
python main.py
```

启动后输入任务，如：

```
You > 帮我调研 Python 的异步编程模型，并生成一份简要报告保存到文件
```

系统将：
1. 检索历史记忆和知识库
2. 生成分层 DAG 计划（Goal → SubGoals → Actions）并可视化展示
3. 按 Super-step 并行执行各 Action 节点（调用 web_search / execute_python / file_ops）
4. 逐节点验证 exit criteria
5. 反思整体结果，必要时局部重规划
6. 输出最终答案并存入长期记忆

**单任务模式** — 直接执行一个任务后退出：

```bash
python main.py "计算前 10 个斐波那契数并保存到文件"
```

**强制规划路径**（调试用）— 通过环境变量指定 v1/v2/v5：

```bash
PLAN_MODE=simple python main.py    # 始终使用扁平计划 (v1)
PLAN_MODE=complex python main.py   # 始终使用 DAG 计划 (v2)
PLAN_MODE=emergent python main.py  # 始终使用隐式规划 (v5)
```

**详细日志模式** — 显示 DEBUG 级别日志：

```bash
python main.py -v
python main.py -v "搜索 Python 最新版本"
```

### 5. 运行测试

测试不依赖 LLM API，通过 Mock 验证 DAG 基础设施：

```bash
# v2/v3/v4 DAG 测试
python -m pytest tests/test_dag_capabilities.py -v

# v5 隐式规划测试
python -m pytest tests/test_emergent_planning.py -v

# v5 简单测试（无需 pytest）
python tests/test_emergent_simple.py
```

输出示例：

```
tests/test_dag_capabilities.py::TestHierarchicalPlanning::test_hierarchy_structure              PASSED
tests/test_dag_capabilities.py::TestHierarchicalPlanning::test_topological_order                 PASSED
tests/test_dag_capabilities.py::TestHierarchicalPlanning::test_parallel_ready_detection          PASSED
tests/test_dag_capabilities.py::TestHierarchicalPlanning::test_exit_criteria_and_risk            PASSED
tests/test_dag_capabilities.py::TestParallelExecutionWithTools::test_superstep_parallel_with_tools  PASSED
tests/test_dag_capabilities.py::TestConditionalBranchAndRollback::test_conditional_branch_and_rollback  PASSED
tests/test_dag_capabilities.py::TestDynamicDAGMutation::test_add_dynamic_node                   PASSED
tests/test_dag_capabilities.py::TestDynamicDAGMutation::test_add_dynamic_edge                   PASSED
tests/test_dag_capabilities.py::TestDynamicDAGMutation::test_remove_pending_node                PASSED
tests/test_dag_capabilities.py::TestDynamicDAGMutation::test_modify_node                        PASSED
tests/test_dag_capabilities.py::TestDynamicDAGMutation::test_cannot_remove_completed_node       PASSED
tests/test_dag_capabilities.py::TestDynamicDAGMutation::test_dynamic_node_ready_detection       PASSED
tests/test_dag_capabilities.py::TestDynamicDAGMutation::test_get_pending_and_completed_counts   PASSED
tests/test_dag_capabilities.py::TestToolRouter::test_initial_state_no_hint                      PASSED
tests/test_dag_capabilities.py::TestToolRouter::test_failure_threshold_triggers_suggestion       PASSED
tests/test_dag_capabilities.py::TestToolRouter::test_success_resets_consecutive_failures         PASSED
tests/test_dag_capabilities.py::TestToolRouter::test_alternative_tools_excludes_failed           PASSED
tests/test_dag_capabilities.py::TestToolRouter::test_per_node_isolation                         PASSED
tests/test_dag_capabilities.py::TestAdaptivePlanningIntegration::test_adaptive_planning_integration  PASSED

19 passed
```

六组测试分别验证：

| 测试 | 覆盖能力 |
|------|---------|
| `TestHierarchicalPlanning` (4 项) | 三层层级、拓扑排序、并行就绪检测、exit criteria / risk |
| `TestParallelExecutionWithTools` | Super-step 并行执行、工具调用记录 (web_search / execute_python / file_ops)、状态合并、Checkpoint |
| `TestConditionalBranchAndRollback` | 条件分支评估、失败回滚、下游子树跳过、状态机合法性校验 |
| `TestDynamicDAGMutation` (7 项) (v3) | 动态增删改节点/边、就绪状态检测、已完成节点保护 |
| `TestToolRouter` (5 项) (v3) | 失败统计、阈值触发、替代建议、成功重置、节点间隔离 |
| `TestAdaptivePlanningIntegration` (1 项) (v3) | 超步间自适应全流程：Mock Planner + DAG 变更验证 |

## Configuration Reference

所有配置项均可通过 `.env` 文件或环境变量覆盖：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | LLM API 地址 |
| `LLM_API_KEY` | — | API Key |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `MAX_CONTEXT_TOKENS` | `8000` | 上下文窗口 token 上限 |
| `MAX_REACT_ITERATIONS` | `10` | 每个 Action 节点 ReAct 最大迭代次数 |
| `MAX_REPLAN_ATTEMPTS` | `3` | 反思失败后最大重规划次数 |
| `MAX_PARALLEL_NODES` | `3` | 每个 Super-step 最大并行节点数 |
| `SHORT_TERM_WINDOW` | `20` | 短期记忆滑动窗口大小 |
| `CODE_EXEC_TIMEOUT` | `30` | Python 代码执行超时 (秒) |
| `SANDBOX_DIR` | `~/.manus_demo/sandbox` | 文件操作沙箱目录 |
| `MEMORY_DIR` | `~/.manus_demo` | 长期记忆存储目录 |
| `PLAN_MODE` | `auto` | (v4) 规划路由：`auto`=混合分类 / `simple`=强制 v1 / `complex`=强制 v2 / `emergent`=强制 v5 |
| `EMERGENT_PLANNING_ENABLED` | `true` | (v5) 是否启用隐式规划模式 |
| `MAX_TODO_ITEMS` | `20` | (v5) TODO 列表最大项数 |
| `TODO_COMPRESSION_THRESHOLD` | `0.8` | (v5) 上下文窗口使用率达到 80% 时压缩 TODO |
| `ADAPTIVE_PLANNING_ENABLED` | `true` | (v3) 是否启用超步间自适应规划 |
| `ADAPT_PLAN_INTERVAL` | `1` | (v3) 每隔几个超步执行一次自适应检查 |
| `ADAPT_PLAN_MIN_COMPLETED` | `1` | (v3) 至少完成几个 ACTION 节点后才启动自适应 |
| `TOOL_FAILURE_THRESHOLD` | `2` | (v3) 工具连续失败多少次后建议切换 |

## Extending the Demo

### 添加新工具

1. 在 `tools/` 目录下创建新文件，继承 `BaseTool`
2. 实现 `name`、`description`、`parameters_schema` 和 `execute()`
3. 在 `main.py` 的 `tools` 列表中注册

```python
# tools/my_tool.py
from tools.base import BaseTool

class MyTool(BaseTool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "Description for the LLM to understand when to use this tool"

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."},
            },
            "required": ["param1"],
        }

    async def execute(self, **kwargs) -> str:
        # Your tool logic here
        return "result"
```

### 添加知识库文档

将 `.txt` 或 `.md` 文件放入 `knowledge/docs/` 目录，启动时自动索引。

### 切换 LLM 模型

修改 `.env` 中的 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，
支持 DeepSeek、OpenAI、通义千问、Ollama 等任何 OpenAI 兼容接口。

## v1 → v2 Upgrade Summary

| 维度 | v1 (备份于 `manus_demo_backup_before_dag.zip`) | v2 (当前) |
|------|-------|------|
| 计划结构 | 扁平 2-6 步线性 | Goal → SubGoal → Action 三层 DAG |
| 执行模型 | 顺序 for 循环 | Super-step 并行 (asyncio.gather) |
| 状态管理 | step.status 字段 | NodeStateMachine 强制合法转移 |
| 失败处理 | 整体重规划 | 局部重规划 (仅失败子树) + 回滚 |
| 条件逻辑 | 无 | CONDITIONAL 边 + 动态跳过 |
| 完成判定 | 步骤级 success 布尔 | 每节点 exit criteria (LLM 验证) |
| 风险评估 | 无 | 每节点 confidence + risk_level |
| 数据流 | 隐式上下文拼接 | DAGState 集中状态 (LangGraph 风格) |
| 可追溯性 | 无 | 每 Super-step Checkpoint 快照 |

## v2 → v3 Upgrade Summary

| 维度 | v2 | v3 (当前) |
|------|-----|-----------|
| 规划时机 | 执行前一次性规划 + 失败后局部重规划 | 执行前 + **每个 Super-step 后** Planner 自适应评估 |
| DAG 可变性 | 执行期间结构冻结（仅状态流转） | 执行期间可动态增删改节点和边 |
| 工具失败策略 | ReAct 循环内重试同一工具 | ToolRouter 追踪连续失败，向 LLM 注入替代工具建议 |
| 新增数据模型 | — | `AdaptAction`、`PlanAdaptation`、`AdaptationResult` |
| 新增模块 | — | `tools/router.py` (ToolRouter) |
| 新增配置 | — | `ADAPTIVE_PLANNING_ENABLED`、`ADAPT_PLAN_INTERVAL`、`ADAPT_PLAN_MIN_COMPLETED`、`TOOL_FAILURE_THRESHOLD` |
| 测试覆盖 | 6 项 | **19 项**（+7 DAG 变更 +5 工具路由 +1 自适应集成） |
