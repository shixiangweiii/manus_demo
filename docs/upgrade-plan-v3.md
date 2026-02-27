# Agent Demo V3 升级计划

> 目标：将现有 manus_demo 从一个 DAG 执行的概念验证升级为更接近 Manus / Claude Code / OpenClaw 的通用 Agent，重点增强真实工具能力、动态规划、流式交互、沙箱环境和可观测性。

---

## v4 混合规划路由（已实现）

**v4** 在 v3 基础上增加了**混合规划路由**：通过两阶段分类器（规则快筛 + LLM 兜底）自动判断任务复杂度，简单任务走 v1 扁平计划（省 token、低延迟），复杂任务走 v2 DAG 路径（支持并行、回滚、自适应）。详见 [hybrid-plan-routing-v4.md](hybrid-plan-routing-v4.md)。

---

## 当前架构回顾

**已有能力**：v4 混合路由、层级规划（Goal→SubGoal→Action DAG）、超步并行执行、ReAct 工具调用、状态机、短期/长期记忆、TF-IDF 知识检索、上下文压缩、自反思+局部重规划、Rich CLI UI。

**核心差距**：工具是 mock 的、无真实 shell/浏览器能力、无流式输出、无 human-in-the-loop、无动态规划调整、缺乏可观测性。

---

## 升级方向一：真实工具生态 (Tools Ecosystem)

目标：从 mock 工具升级为真实可用的工具集，对标 Claude Code / Manus 的核心能力。

### 1.1 Shell 命令执行工具

- 新增 `tools/shell.py`，支持执行任意 shell 命令（bash/zsh）
- 基于 `asyncio.create_subprocess_exec` 实现，支持超时、流式输出捕获
- 在 sandbox 目录下执行，通过白名单/黑名单限制危险命令（如 `rm -rf /`）
- 支持工作目录切换、环境变量传递

### 1.2 真实 Web 搜索

- 升级 `tools/web_search.py`，接入真实搜索 API（Tavily / SerpAPI / DuckDuckGo）
- 保留 mock 模式作为 fallback（无 API key 时自动降级）
- 结构化返回：标题、摘要、URL、发布日期

### 1.3 网页浏览 / 内容抓取

- 新增 `tools/web_browser.py`
- 基于 `httpx` + `BeautifulSoup` / `markdownify` 抓取网页内容并转为 Markdown
- 支持 JavaScript 渲染（可选集成 Playwright）
- 自动截断过长内容，提取关键段落

### 1.4 增强文件操作

- 升级 `tools/file_ops.py`：增加 `mkdir`、`delete`、`move`、`copy`、`find`（glob）、`grep`（内容搜索）
- 支持 diff-based 编辑（类似 Claude Code 的 `str_replace` 模式），而非全量覆写
- 对标路径：`tools/file_ops.py`

### 1.5 MCP 协议支持（可选高级功能）

- 新增 `tools/mcp_client.py`，实现 MCP (Model Context Protocol) 客户端
- 支持动态发现和调用外部 MCP 工具服务器
- 标准化工具接口，使任何 MCP 兼容工具可即插即用

---

## 升级方向二：动态自适应规划 (Adaptive Planning) ✅ 已完成

目标：从静态"先规划-再执行"升级为执行过程中可动态调整计划的模式。

> **实现状态**：已于 v3 中完整实现并通过测试（19/19 pass）。

### 2.1 执行中动态重规划 ✅

- `agents/planner.py` 新增 `adapt_plan(dag)` 方法：将已完成结果和待执行节点提交 LLM，判断是否需要 REMOVE/MODIFY/ADD
- `agents/planner.py` 新增 `apply_adaptations(dag, adaptations)` 方法：将调整操作应用到 DAG
- `dag/executor.py` 在超步循环中插入 `_should_adapt()` + `_adapt_plan()` 调用
- `dag/executor.py` 构造函数新增 `planner_agent` 参数，由 Orchestrator 传入
- `schema.py` 新增 `AdaptAction`、`PlanAdaptation`、`AdaptationResult` 数据模型
- `config.py` 新增 `ADAPTIVE_PLANNING_ENABLED`、`ADAPT_PLAN_INTERVAL`、`ADAPT_PLAN_MIN_COMPLETED` 配置

### 2.2 工具选择智能路由 ✅

- 新增 `tools/router.py`：`ToolRouter` 类追踪每个节点每个工具的调用/失败统计
- 连续失败达到阈值（`TOOL_FAILURE_THRESHOLD`，默认 2 次）后生成切换建议
- `agents/executor.py` ReAct 循环中集成：成功 `record_success()`、失败 `record_failure()`、LLM 调用前注入 `get_hint()`
- `config.py` 新增 `TOOL_FAILURE_THRESHOLD` 配置

### 2.3 子任务动态生成 ✅

- `dag/graph.py` 新增 6 个方法：
  - `add_dynamic_node()` — 运行时添加节点（ID 去重校验）
  - `add_dynamic_edge()` — 运行时添加边（端点存在性校验 + 边去重）
  - `remove_pending_node()` — 移除 PENDING 节点及关联边
  - `modify_node()` — 修改待执行节点描述/完成判据
  - `get_pending_action_nodes()` — 查询所有待执行 ACTION 节点
  - `get_completed_action_count()` — 统计已完成 ACTION 节点数

### 测试覆盖

- `TestDynamicDAGMutation`（7 项）：节点增删改、边添加、就绪检测、状态保护
- `TestToolRouter`（5 项）：成功重置、阈值触发、替代建议、提示生成、节点隔离
- `TestAdaptivePlanningIntegration`（1 项）：完整超步间自适应规划流程（含 Mock Planner）

---

## 升级方向三：流式交互与 Human-in-the-Loop

目标：从"提交任务-等待结果"升级为实时流式反馈 + 人类干预能力。

### 3.1 LLM 流式输出

- 在 `llm/client.py` 中增加 `chat_stream()` 方法
- 在 ReAct 循环中支持逐 token 输出思考过程
- UI 实时展示 Agent 的推理链（Thought → Action → Observation）
- 对标路径：`llm/client.py`、`agents/executor.py`

### 3.2 Human-in-the-Loop 审批门

- 在 DAG 节点上支持 `requires_approval: bool` 属性
- 执行到需要审批的节点时暂停，等待用户确认
- 在 `schema.py` 中扩展 `TaskNode`，在 `dag/executor.py` 中实现审批逻辑
- 对标路径：`schema.py`、`dag/executor.py`

### 3.3 执行过程实时可视化

- 升级 `main.py` 中的 Rich UI：使用 `rich.live` 实现 DAG 状态实时刷新
- 每个超步后刷新 DAG 树（节点颜色随状态变化）
- 展示当前正在执行的工具调用及其输出
- 对标路径：`main.py`

---

## 升级方向四：增强沙箱环境 (Enhanced Sandbox)

目标：从简单的 subprocess 隔离升级为更安全、更强大的执行环境。

### 4.1 Docker 沙箱（可选）

- 新增 `sandbox/docker_sandbox.py`
- 支持在 Docker 容器中执行代码和 shell 命令
- 自动构建包含常用工具的镜像
- 会话级容器复用，保持工作空间状态

### 4.2 工作空间管理

- 新增 `workspace/manager.py`
- 管理文件工作空间的创建、持久化和清理
- 支持项目级别的工作空间隔离

### 4.3 环境自动配置

- 在代码执行前自动检测和安装依赖（pip install）
- 支持 requirements.txt 解析和虚拟环境管理
- 对标路径：`tools/code_executor.py`

---

## 升级方向五：多模型路由 (Multi-Model Router)

目标：不同任务使用不同模型，优化成本和质量。

### 5.1 模型路由器

- 新增 `llm/router.py`
- 根据任务类型选择模型：
  - 规划 → 强推理模型（如 DeepSeek-R1 / Claude）
  - 简单工具调用 → 轻量模型（如 DeepSeek-Chat / GPT-4o-mini）
  - 代码生成 → 代码模型（如 DeepSeek-Coder）
- 在 `config.py` 中支持多模型配置
- 对标路径：`llm/client.py`、`config.py`

### 5.2 多 Provider 支持

- 扩展 `llm/client.py` 支持同时配置多个 LLM 提供商
- 自动 fallback：主模型失败时切换到备用模型
- 对标路径：`llm/client.py`

---

## 升级方向六：可观测性与调试 (Observability)

目标：完整的执行追踪、成本统计和调试支持。

### 6.1 结构化 Trace

- 新增 `observability/tracer.py`
- 记录完整的执行链路：每个 Agent 调用、工具执行、LLM 请求
- 支持导出为 JSON 格式（兼容 LangSmith / Phoenix 等平台）

### 6.2 Token 用量与成本追踪

- 在 `llm/client.py` 中记录每次调用的 token 使用量
- 汇总统计：总 token、各阶段分布、估算费用
- 执行结束后在 UI 中展示成本报告

### 6.3 执行回放

- 利用现有的 checkpoint 机制，支持执行过程回放
- 可以从任意 checkpoint 重新开始执行
- 对标路径：`dag/executor.py`（已有 checkpoint 基础）

---

## 升级方向七：记忆与学习增强 (Memory Enhancement)

目标：从简单关键词匹配升级为语义记忆 + 技能积累。

### 7.1 向量化长期记忆

- 升级 `memory/long_term.py`，用 embedding 替换关键词匹配
- 支持本地 embedding 模型或 API（如 OpenAI embedding / BGE）
- 对标路径：`memory/long_term.py`

### 7.2 技能库 (Skill Library)

- 新增 `memory/skills.py`
- 从成功执行中提炼可复用的"技能模板"
- 下次遇到类似任务时直接复用已验证的执行方案

---

## 建议的实施优先级

### P1 - 核心能力（立即实施）

| 编号 | 任务 | 涉及文件 | 预估工作量 |
|------|------|----------|-----------|
| 1.1 | Shell 命令执行工具 | 新增 `tools/shell.py` | 中 |
| 1.4 | 增强文件操作 | 修改 `tools/file_ops.py` | 中 |
| 1.2 | 真实 Web 搜索 | 修改 `tools/web_search.py` | 小 |
| 1.3 | 网页浏览工具 | 新增 `tools/web_browser.py` | 中 |

### P2 - 交互体验（紧随其后）

| 编号 | 任务 | 涉及文件 | 预估工作量 |
|------|------|----------|-----------|
| 3.1 | LLM 流式输出 | 修改 `llm/client.py`、`agents/executor.py` | 中 |
| 3.3 | 实时 UI | 修改 `main.py` | 中 |
| ~~2.1~~ | ~~动态重规划~~ | ~~修改 `agents/planner.py`、`dag/executor.py`~~ | ~~大~~ ✅ 已完成 |
| 3.2 | Human-in-the-Loop | 修改 `schema.py`、`dag/executor.py` | 中 |

### P3 - 高级特性（按需实施）

| 编号 | 任务 | 涉及文件 | 预估工作量 |
|------|------|----------|-----------|
| 5.1 | 多模型路由 | 新增 `llm/router.py`、修改 `config.py` | 中 |
| 6.1 | 结构化 Trace | 新增 `observability/tracer.py` | 中 |
| 6.2 | 成本统计 | 修改 `llm/client.py` | 小 |
| 7.1 | 向量记忆 | 修改 `memory/long_term.py` | 中 |
| 4.1 | Docker 沙箱 | 新增 `sandbox/docker_sandbox.py` | 大 |
| 1.5 | MCP 协议 | 新增 `tools/mcp_client.py` | 大 |

---

## 与主流 Agent 的对标分析

| 能力维度 | 当前 Demo (v2) | Manus | Claude Code | OpenClaw | V3 目标 |
|----------|---------------|-------|-------------|----------|---------|
| Shell 执行 | 仅 Python subprocess | VM 沙箱 | 原生 shell | 容器化 | sandbox shell |
| 文件操作 | 基础读写 | 完整文件系统 | str_replace + 搜索 | 完整文件系统 | 增强版 + diff 编辑 |
| Web 搜索 | Mock | 真实搜索 | 真实搜索 | 真实搜索 | Tavily/SerpAPI |
| 网页浏览 | 无 | Playwright | 内容抓取 | httpx | httpx + markdownify |
| 流式输出 | 无 | 有 | 有 | 有 | chat_stream() |
| 动态规划 | ~~静态 DAG + 局部重规划~~ **超步级自适应 ✅** | 动态调整 | 隐式规划 | 动态规划 | 超步级自适应 |
| Human-in-Loop | 无 | 审批机制 | 确认提示 | 配置化 | 审批门 |
| 多模型 | 单模型 | 多模型 | 单模型 | 多模型 | 路由器 |
| 可观测性 | Rich UI 日志 | 详细日志 | Trace | 完整追踪 | Trace + 成本 |
| 记忆 | 关键词匹配 | 长期记忆 | 会话级 | 向量记忆 | 向量化 + 技能库 |

---

## 新增依赖（预计）

```
# P1 新增
httpx                # 网页抓取
beautifulsoup4       # HTML 解析
markdownify          # HTML → Markdown
tavily-python        # Web 搜索 API（可选）

# P3 新增
numpy                # 向量计算（向量记忆）
docker               # Docker SDK（可选）
mcp                  # MCP 协议（可选）
```
