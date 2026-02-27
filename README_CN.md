# Manus Demo — 基于 DAG 的多智能体系统

一个面向**学习与教学**的多智能体系统演示项目。通过阅读和运行这个 Demo，你可以深入理解现代自主 AI Agent 的核心技术原理：

- **分层规划**：将复杂任务自动分解为 Goal → SubGoal → Action 的三层结构
- **DAG 驱动执行**：基于有向无环图的并行执行，替代传统的顺序步骤循环
- **可执行状态机**：节点的完整生命周期由状态机严格管控，杜绝非法状态转移
- **工具调用（ReAct）**：每个动作节点内部执行「思考 → 工具调用 → 观察」循环
- **自我反思与纠错**：执行完毕后由 Reflector 评估质量，失败时局部重规划
- **跨会话记忆**：短期滑动窗口 + 长期 JSON 持久化，积累任务经验

> **版本说明**：当前为 v4。
> - **v4 新增**：混合规划路由（两阶段分类器自动选择 v1 扁平计划或 v2 DAG），简单任务省 token，复杂任务支持并行与容错。
> - v2 从「静态线性分步」全面升级为「动态任务图 + 可执行状态机」
> - **v3**：超步间动态自适应规划、工具智能路由、DAG 运行时增删改节点/边
>
> 设计上借鉴了 [LangGraph](https://github.com/langchain-ai/langgraph) 的集中状态、Super-step 并行、Checkpoint 等核心理念，  
> 但采用极简的自定义实现，每个模块的逻辑都清晰可读，方便学习。

---

## 目录

- [系统架构](#系统架构)
- [核心设计模式](#核心设计模式)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [运行测试](#运行测试)
- [配置参考](#配置参考)
- [扩展指南](#扩展指南)
- [v1 → v2 升级对比](#v1--v2-升级对比)
- [v2 → v3 升级对比](#v2--v3-升级对比)
- [常见问题](#常见问题)

---

## 系统架构

```
用户输入任务
     │
     ▼
┌─────────────────────────────────────────────────────┐
│                  Orchestrator（编排者）               │
│                                                      │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │  短期记忆   │  │   长期记忆   │  │  知识库检索  │  │
│  │ (滑动窗口) │  │ (JSON 持久化)│  │ (TF-IDF)   │  │
│  └────────────┘  └──────────────┘  └─────────────┘  │
└───────────────────────┬─────────────────────────────┘
                        │ 携带记忆 + 知识上下文
                        ▼
┌─────────────────────────────────────────────────────┐
│  (v4) classify_task → simple | complex              │
│  simple: create_plan() → 扁平 Plan (v1)             │
│  complex: create_dag() → TaskDAG (v2)               │
│  Task → Goal → SubGoals → Actions                   │
│  每个节点：exit_criteria + risk_assessment            │
│  边类型：DEPENDENCY / CONDITIONAL / ROLLBACK          │
└───────────────────────┬─────────────────────────────┘
                        │ TaskDAG 对象
                        ▼
┌─────────────────────────────────────────────────────┐
│            DAG Executor（执行引擎）Super-step 模型    │
│                                                      │
│  while DAG 未完成:                                   │
│    ① 找出所有就绪节点（依赖已满足的 PENDING/READY）   │
│    ② asyncio.gather 并行执行（上限 MAX_PARALLEL）     │
│    ③ 结果写入集中式 DAGState（类 LangGraph Reducer）  │
│    ④ 逐节点验证 exit criteria（Reflector LLM 校验）  │
│    ⑤ 失败处理：执行 ROLLBACK 节点 → 跳过下游子树     │
│    ⑥ 评估 CONDITIONAL 边，动态启用/跳过分支          │
│    ⑦ (v3) 自适应规划：Planner 评估中间结果 → 增删改  │
│    ⑧ Checkpoint 快照当前状态（类 LangGraph 持久化）  │
│                                                      │
│  每个 ACTION 节点内部运行 ReAct 循环：               │
│    思考（Thought） → 工具调用（Action）              │
│    → 观察结果（Observe） → 重复                      │
│                                                      │
│  可用工具：web_search / execute_python / file_ops    │
│  (v3) Tool Router: 连续失败 → 建议替代工具           │
└───────────────────────┬─────────────────────────────┘
                        │ 执行结果
                        ▼
┌─────────────────────────────────────────────────────┐
│              Reflector（反思者）v3                    │
│                                                      │
│  逐节点：validate_exit_criteria()                    │
│    └─ 节点完成后即时验证，不满足则触发节点失败         │
│                                                      │
│  全局：reflect_dag()                                 │
│    └─ 评估整体结果质量，输出评分 + 反馈 + 建议        │
│    └─ 若不通过 → 触发局部重规划（仅失败子树）          │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
              最终答案 + 存入长期记忆
```

---

## 核心设计模式

| 设计模式 | 说明 |
|---------|------|
| **混合规划路由** (v4) | 两阶段分类器（规则快筛 + LLM 兜底）自动选择 simple(v1) 或 complex(v2) 路径 |
| **分层规划** | Planner 将任务分解为 Goal → SubGoal → Action 三层 DAG，每个节点都携带完成判据（exit criteria）和风险评估（confidence + risk_level） |
| **DAG 并行执行** | 节点按拓扑序执行，互相无依赖的节点在同一 Super-step 中**并行**运行，天然支持任务加速 |
| **节点状态机** | `PENDING → READY → RUNNING → COMPLETED / FAILED` 的完整生命周期由 `NodeStateMachine` 强制校验，任何非法转移立即抛出异常 |
| **条件分支** | CONDITIONAL 边在上游节点完成后评估关键词条件，条件不满足时自动跳过目标节点及整个下游子树 |
| **失败回滚** | ROLLBACK 边在节点失败时触发清理操作，已设定回滚动作的节点失败后转为 ROLLED_BACK 状态 |
| **ReAct 循环** | Executor 对每个 ACTION 节点执行「思考 → 工具调用 → 观察」循环，LLM 通过 function calling 自主选择工具 |
| **局部重规划** | 反思失败时，Planner 仅重新规划失败子树，所有已完成的节点和结果完整保留，避免重复工作 |
| **集中式状态** | `DAGState.node_results` 是所有节点结果的唯一数据源（Single Source of Truth），并行写入天然无冲突，对应 LangGraph 的 Channel 机制 |
| **Checkpoint 快照** | 每个 Super-step 结束时保存完整 DAG 状态快照，支持事后调试回溯，对应 LangGraph 的持久化机制 |
| **自适应规划** (v3) | 每个 Super-step 后 Planner 评估中间结果，动态 REMOVE/MODIFY/ADD DAG 节点，实现执行期间的计划演化 |
| **工具路由** (v3) | `ToolRouter` 追踪每个工具的连续失败次数，达到阈值后自动向 LLM 注入替代工具建议，减少无效重试 |
| **DAG 运行时变更** (v3) | 支持在执行期间动态增加、删除、修改节点和边，为自适应规划提供底层能力 |

---

## 项目结构

```
manus_demo/
│
├── main.py                     # 程序入口（交互模式 / 单任务模式 / 详细日志模式）
├── config.py                   # 全局配置（从 .env 或环境变量加载）
├── schema.py                   # 所有 Pydantic 数据模型（TaskNode、DAGState 等）
├── requirements.txt            # 运行时依赖
│
├── agents/                     # 智能体层
│   ├── base.py                 #   BaseAgent：LLM 调用封装、消息管理、上下文压缩
│   ├── orchestrator.py         #   Orchestrator：全流程编排（记忆检索 → 规划 → 执行 → 反思）
│   ├── planner.py              #   Planner：分层规划，一次调用生成完整 TaskDAG
│   ├── executor.py             #   Executor：ReAct 循环，执行 ACTION 节点
│   └── reflector.py            #   Reflector：逐节点验证 + 全局质量评估
│
├── dag/                        # DAG 执行引擎层
│   ├── __init__.py             #   模块导出
│   ├── graph.py                #   TaskDAG：图结构、拓扑排序、就绪检测、序列化
│   ├── state_machine.py        #   NodeStateMachine：节点状态转移表 + 校验
│   └── executor.py             #   DAGExecutor：Super-step 主循环（并行 + 条件 + 回滚）
│
├── tools/                      # 工具层（供 Executor 通过 function calling 调用）
│   ├── base.py                 #   BaseTool：抽象接口 + OpenAI function schema 转换
│   ├── web_search.py           #   WebSearchTool：网络搜索（内置 mock，可接真实 API）
│   ├── code_executor.py        #   CodeExecutorTool：Python 代码执行（subprocess 沙箱）
│   ├── file_ops.py             #   FileOpsTool：文件读写列出（路径穿越攻击防护）
│   └── router.py               #   ToolRouter (v3)：工具失败追踪 + 替代建议生成
│
├── memory/                     # 记忆层
│   ├── short_term.py           #   ShortTermMemory：滑动窗口缓冲（内存）
│   └── long_term.py            #   LongTermMemory：JSON 文件持久化 + 关键词检索
│
├── context/
│   └── manager.py              # ContextManager：Token 估算 + LLM 摘要压缩
│
├── knowledge/
│   ├── retriever.py            # KnowledgeRetriever：TF-IDF 检索（纯 Python，无 ML 依赖）
│   └── docs/                   # 知识库文档目录（.txt / .md，启动时自动索引）
│
├── llm/
│   └── client.py               # LLMClient：OpenAI 兼容 API 的统一封装（支持多服务商）
│
├── tests/
│   └── test_dag_capabilities.py  # 单元测试（不依赖真实 LLM，全部 Mock，19 项）
│
└── docs/                         # 项目文档
    ├── upgrade-plan-v3.md        #   v3 升级计划（含完成状态标注）
    ├── hybrid-plan-routing-v4.md #   v4 混合规划路由说明
    ├── dynamic-features-v1-vs-v2.md  # v1→v2→v3 动态性逐层对比分析
    └── data-structures-and-algorithms.md  # 数据结构与算法详解
```

---

## 快速开始

### 第一步：准备环境

需要 **Python 3.11 或更高版本**。

```bash
# 创建虚拟环境（推荐）
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 第二步：安装依赖

```bash
pip install -r requirements.txt
```

运行单元测试还需要：

```bash
pip install pytest pytest-asyncio
```

### 第三步：配置 LLM API

```bash
# 复制示例配置文件
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API 凭证：

```env
# ===== 选项 1：DeepSeek（默认配置）=====
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-your-key-here
LLM_MODEL=deepseek-chat

# ===== 选项 2：通义千问（阿里云 DashScope）=====
# LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# LLM_API_KEY=your-api-key-here
# LLM_MODEL=qwen-turbo

# ===== 选项 3：OpenAI =====
# LLM_BASE_URL=https://api.openai.com/v1
# LLM_API_KEY=sk-your-key-here
# LLM_MODEL=gpt-4o-mini

# ===== 选项 4：Ollama（本地部署）=====
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_API_KEY=ollama
# LLM_MODEL=llama3
```

> 任何支持 **OpenAI 兼容 Chat Completions 接口**的服务都可以使用，修改以上三个变量即可。

### 第四步：运行 Demo

**交互模式**（推荐，支持多轮对话）：

```bash
python main.py
```

启动后会看到欢迎界面，直接输入任务即可，例如：

```
You > 帮我调研 Python 的异步编程模型，并生成一份简要报告保存到文件
You > 计算前 20 个斐波那契数，用 Python 执行并把结果写入 fib.txt
You > 搜索最新的大语言模型进展并整理摘要
```

每个任务执行时，控制台会实时展示：

1. 🔍 **长期记忆检索** — 是否有相关历史经验
2. 📚 **知识库检索** — 是否有相关本地文档
3. 🌳 **DAG 规划可视化** — 树形展示 Goal → SubGoals → Actions 的层级结构
4. ⚡ **Super-step 并行执行** — 每一轮执行哪些节点、是否并行
5. 🔧 **工具调用详情** — 调用了什么工具、传入什么参数、返回了什么结果
6. ✅ **反思评估** — 整体质量评分、是否通过、改进建议
7. 💾 **最终答案** — 汇总所有已完成节点的输出

**单任务模式**（执行一次后退出）：

```bash
python main.py "计算前 10 个斐波那契数并保存到文件"
python main.py "用 Python 生成一个冒泡排序示例并执行"
```

**详细日志模式**（显示 DEBUG 级别调试信息）：

```bash
python main.py -v                          # 交互模式 + 详细日志
python main.py -v "搜索 Python 最新版本"   # 单任务 + 详细日志
```

**强制规划路径**（调试用）— 通过环境变量指定 v1 或 v2：

```bash
PLAN_MODE=simple python main.py   # 始终使用扁平计划 (v1)
PLAN_MODE=complex python main.py  # 始终使用 DAG 计划 (v2)
```

---

## 运行测试

测试完全**不依赖真实 LLM API**，通过 Mock 模拟 LLM 响应，验证 DAG 基础设施的正确性：

```bash
python -m pytest tests/test_dag_capabilities.py -v
```

预期输出：

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

六组测试覆盖的核心能力：

| 测试类 | 测试内容 |
|--------|---------|
| `TestHierarchicalPlanning`（4 个子测试） | ① Goal→SubGoal→Action 三层结构正确性<br>② 拓扑排序保证执行顺序<br>③ 并行就绪节点识别（同一 Super-step）<br>④ 每节点都携带 exit criteria 和 risk assessment |
| `TestParallelExecutionWithTools`（1 个子测试） | ① `web_search` + `execute_python` 在同一 Super-step 并行执行<br>② ToolCallRecord 正确记录工具调用详情<br>③ 并行结果正确合并到 DAGState<br>④ 每个 Super-step 产生 Checkpoint 快照 |
| `TestConditionalBranchAndRollback`（1 个子测试） | ① 条件边评估（关键词匹配）<br>② 节点失败时触发 ROLLBACK 节点执行<br>③ 下游子树自动级联跳过<br>④ 状态机终态节点不可再转移（抛出异常） |
| `TestDynamicDAGMutation`（7 个子测试）(v3) | ① 动态添加节点和边<br>② 移除 PENDING 节点（含关联边清理）<br>③ 修改节点描述和完成判据<br>④ 已完成节点不可移除保护<br>⑤ 新增节点的就绪检测<br>⑥ 待执行/已完成计数 |
| `TestToolRouter`（5 个子测试）(v3) | ① 初始状态无提示<br>② 连续失败达阈值触发建议<br>③ 成功调用重置连续失败计数<br>④ 替代工具排除已失败工具<br>⑤ 不同节点之间统计隔离 |
| `TestAdaptivePlanningIntegration`（1 个子测试）(v3) | 完整超步间自适应规划流程：Mock Planner 返回 REMOVE + ADD → 验证 DAG 结构变更 |

---

## 配置参考

所有配置项均可通过 `.env` 文件或系统环境变量设置，`.env` 文件优先级低于系统环境变量：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | LLM 服务的 API 地址（OpenAI 兼容格式） |
| `LLM_API_KEY` | — | API 密钥（必填） |
| `LLM_MODEL` | `deepseek-chat` | 使用的模型名称 |
| `MAX_CONTEXT_TOKENS` | `8000` | 上下文 Token 上限，超出后自动触发 LLM 摘要压缩 |
| `MAX_REACT_ITERATIONS` | `10` | 每个 ACTION 节点的 ReAct 最大迭代轮次 |
| `MAX_REPLAN_ATTEMPTS` | `3` | 反思失败后的最大重规划次数 |
| `MAX_PARALLEL_NODES` | `3` | 每个 Super-step 最多并行执行的节点数 |
| `SHORT_TERM_WINDOW` | `20` | 短期记忆滑动窗口大小（条数） |
| `CODE_EXEC_TIMEOUT` | `30` | Python 代码执行超时时间（秒） |
| `SANDBOX_DIR` | `~/.manus_demo/sandbox` | 文件操作的沙箱目录（防止越权访问） |
| `MEMORY_DIR` | `~/.manus_demo` | 长期记忆 JSON 文件的存储目录 |
| `KNOWLEDGE_CHUNK_SIZE` | `500` | 知识库文档的切片大小（字符数） |
| `KNOWLEDGE_TOP_K` | `3` | 知识检索返回的最相关片段数量 |
| `PLAN_MODE` | `auto` | (v4) 规划路由：`auto`=混合分类 / `simple`=强制 v1 / `complex`=强制 v2 |
| `ADAPTIVE_PLANNING_ENABLED` | `true` | (v3) 是否启用超步间自适应规划 |
| `ADAPT_PLAN_INTERVAL` | `1` | (v3) 每隔几个超步执行一次自适应检查（1=每步都检查） |
| `ADAPT_PLAN_MIN_COMPLETED` | `1` | (v3) 至少完成几个 ACTION 节点后才启动自适应 |
| `TOOL_FAILURE_THRESHOLD` | `2` | (v3) 工具连续失败多少次后建议切换替代工具 |

---

## 扩展指南

### 添加新工具

1. 在 `tools/` 目录下新建文件，继承 `BaseTool`
2. 实现四个抽象属性/方法：`name`、`description`、`parameters_schema`、`execute()`
3. 在 `main.py` 的 `tools` 列表中注册

```python
# tools/calculator.py
from tools.base import BaseTool
from typing import Any

class CalculatorTool(BaseTool):
    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return "计算数学表达式并返回结果。支持基本运算和 Python math 模块函数。"

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "要计算的数学表达式，如 '2 + 3 * 4' 或 'math.sqrt(16)'",
                },
            },
            "required": ["expression"],
        }

    async def execute(self, **kwargs: Any) -> str:
        import math
        expression = kwargs.get("expression", "")
        try:
            result = eval(expression, {"math": math, "__builtins__": {}})
            return f"计算结果：{expression} = {result}"
        except Exception as e:
            return f"计算错误：{e}"
```

然后在 `main.py` 中注册：

```python
from tools.calculator import CalculatorTool

tools = [WebSearchTool(), CodeExecutorTool(), FileOpsTool(), CalculatorTool()]
```

### 添加知识库文档

将 `.txt` 或 `.md` 格式的文档放入 `knowledge/docs/` 目录，下次启动时自动完成 TF-IDF 索引。  
适合注入领域特定知识，如 API 文档、操作手册、领域术语表等。

```bash
echo "Python asyncio 是 Python 标准库中的异步 I/O 框架..." > knowledge/docs/python_asyncio.txt
```

### 切换 LLM 模型

只需修改 `.env` 中三个变量，无需改动任何代码：

```env
# 切换到 Ollama 本地 Qwen 模型
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:7b
```

### 对接真实网络搜索

将 `tools/web_search.py` 中的 `_mock_search` 方法替换为真实 API 调用：

```python
# 以 Tavily API 为例
import httpx

@staticmethod
def _mock_search(query: str) -> list[dict[str, str]]:
    resp = httpx.get(
        "https://api.tavily.com/search",
        params={"query": query, "api_key": "tvly-your-key"},
    )
    return resp.json().get("results", [])
```

---

## v1 → v2 升级对比

> v1 代码已备份为 `manus_demo_backup_before_dag.zip`，可解压后对比学习。

| 维度 | v1（旧版，静态线性） | v2（动态 DAG） |
|------|---------------------|---------------------|
| **计划结构** | 扁平 2-6 步线性列表 | Goal → SubGoal → Action 三层 DAG |
| **执行模型** | `for step in steps` 顺序循环 | Super-step 并行（`asyncio.gather`） |
| **状态管理** | `step.status` 枚举字段，无校验 | `NodeStateMachine` 强制合法转移，非法转移抛异常 |
| **失败处理** | 整体丢弃计划，全部重规划 | 局部重规划（仅失败子树）+ ROLLBACK 回滚 |
| **条件逻辑** | 无 | CONDITIONAL 边，根据上游结果动态跳过分支 |
| **完成判定** | 步骤级 `success: bool` | 每节点 exit criteria，支持 LLM 语义验证 |
| **风险评估** | 无 | 每节点 `confidence` + `risk_level` + `fallback_strategy` |
| **数据流** | 隐式拼接上下文字符串 | 集中式 `DAGState`，类 LangGraph Channel 机制 |
| **可追溯性** | 无 | 每 Super-step 保存 Checkpoint 快照 |
| **节点粒度** | 粗粒度步骤 | 三层层级，支持并行子任务 |

---

## v2 → v3 升级对比

> v2 代码已备份为 `manus_demo_backup_before_v3.zip`，可解压后对比学习。

| 维度 | v2 | v3（当前） |
|------|-----|-----------|
| **规划时机** | 执行前一次性规划 + 失败后局部重规划 | 执行前 + **每个 Super-step 后** Planner 自适应评估 |
| **DAG 可变性** | 执行期间结构冻结（仅状态流转） | 执行期间可动态增删改节点和边 |
| **工具失败策略** | ReAct 循环内重试同一工具 | `ToolRouter` 追踪连续失败，向 LLM 注入替代工具建议 |
| **新增数据模型** | — | `AdaptAction`、`PlanAdaptation`、`AdaptationResult` |
| **新增模块** | — | `tools/router.py`（ToolRouter） |
| **新增配置** | — | `ADAPTIVE_PLANNING_ENABLED`、`ADAPT_PLAN_INTERVAL`、`ADAPT_PLAN_MIN_COMPLETED`、`TOOL_FAILURE_THRESHOLD` |
| **测试覆盖** | 6 项 | **19 项**（+7 DAG 变更 +5 工具路由 +1 自适应集成） |
| **核心差异** | Planner 是一次性的「建筑设计师」 | Planner 是持续跟进的「项目顾问」，每一步都可能调整后续方案 |

---

## 常见问题

**Q：运行时报 `ModuleNotFoundError`？**  
A：确认已激活虚拟环境并安装依赖：`source .venv/bin/activate && pip install -r requirements.txt`

**Q：如何确认 API Key 配置正确？**  
A：执行 `python -c "import config; print(config.LLM_BASE_URL, config.LLM_MODEL)"` 查看加载的配置值。

**Q：测试不需要联网或 API Key 吗？**  
A：是的，所有测试均通过 Mock 模拟 LLM，完全离线运行，无任何网络请求。

**Q：生成的文件保存在哪里？**  
A：Agent 通过 `file_ops` 工具写入的文件保存在 `~/.manus_demo/sandbox/` 目录下（可通过 `SANDBOX_DIR` 配置修改）。

**Q：长期记忆存储在哪里？**  
A：保存在 `~/.manus_demo/memory.json` 文件中，跨会话自动加载（可通过 `MEMORY_DIR` 配置修改）。

**Q：如何清空记忆重新开始？**  
A：删除 `~/.manus_demo/memory.json` 文件即可：`rm ~/.manus_demo/memory.json`

**Q：Planner 生成的计划结构固定吗？**  
A：不固定。Planner 每次调用 LLM 生成，具体的 SubGoal 数量和 Action 内容会根据任务内容动态变化，这正是「自主规划」的体现。

**Q：如何理解 LangGraph 借鉴了什么？**  
A：主要借鉴了三个设计理念：① 集中式状态（`DAGState` 对应 LangGraph 的 `StateGraph`）；② Super-step 并行执行模型（对应 Pregel 运行时）；③ Checkpoint 快照（对应 LangGraph 的 Checkpointer）。但全部采用自定义简化实现，不依赖 LangGraph 库，代码量极少，便于理解原理。
