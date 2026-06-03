# Manus Demo 操作手册

> **版本**: v20.0-dev | **更新日期**: 2026-06-02
> **定位**: 基于最新源码和路线图（v14-v20）的完整操作手册，涵盖环境变量配置、命令行用法、各功能的实际运行与验证方法。

---

## 目录

1. [快速开始](#1-快速开始)
2. [命令行用法](#2-命令行用法)
3. [环境变量完整参考](#3-环境变量完整参考)
4. [功能模块运行指南](#4-功能模块运行指南)
   - 4.1 [规划路由（simple / complex / emergent）](#41-规划路由)
   - 4.2 [子智能体 SubAgent（v9）](#42-子智能体-subagent)
   - 4.3 [人机交互 HITL（v13）](#43-人机交互-hitl)
   - 4.4 [记忆系统（v15 Agentic Memory）](#44-记忆系统)
   - 4.5 [自演化 Self-Evolution（v17）](#45-自演化-self-evolution)
   - 4.6 [确定性工作流 Workflow（v18.1）](#46-确定性工作流-workflow)
   - 4.7 [专家委派 Handoff（v18.2）](#47-专家委派-handoff)
   - 4.8 [远端子智能体 + A2A（v18.3/18.4）](#48-远端子智能体--a2a)
   - 4.9 [安全护栏 Guardrails（v19）](#49-安全护栏-guardrails)
   - 4.10 [智能体技能 Skills（v20）](#410-智能体技能-skills)
   - 4.11 [全链路追踪 Tracing（v7）](#411-全链路追踪-tracing)
   - 4.12 [任务恢复 Task Resume（v14.5）](#412-任务恢复-task-resume)
   - 4.13 [MCP 桥接（v16）](#413-mcp-桥接)
   - 4.14 [推理引擎 ReasoningEngine（v14）](#414-推理引擎-reasoningengine)
   - 4.15 [AgentBay 云端工具](#415-agentbay-云端工具)
5. [评测系统](#5-评测系统)
6. [测试](#6-测试)
7. [目录结构速查](#7-目录结构速查)

---

## 1. 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，至少设置 LLM_API_KEY
```

最小 `.env`（使用 DeepSeek）：

```env
LLM_API_KEY=sk-your-key-here
```

### 运行

```bash
# 交互模式（多轮对话）
python main.py

# 单任务模式
python main.py "搜索2026年最新的Python Web框架并做对比"

# 详细日志模式
python main.py -v "你的任务"
```

### 退出交互模式

输入 `quit`、`exit` 或 `q`。

---

## 2. 命令行用法

### 基本语法

```
python main.py [-v|--verbose] [--list-tasks] [--resume <task_id>] [--workflow <spec.json>] ["任务描述"]
```

| 命令 | 说明 |
|------|------|
| `python main.py` | 交互模式（多轮对话，记忆跨任务积累） |
| `python main.py "任务"` | 单任务模式（执行完退出） |
| `python main.py -v "任务"` | 详细日志（DEBUG 级别） |
| `python main.py --list-tasks` | 列出所有 checkpoint 任务 |
| `python main.py --resume <task_id>` | 恢复指定任务 |
| `python main.py --workflow <spec.json>` | 运行确定性工作流（v18.1） |

### 交互模式内的特殊命令

| 命令 | 说明 |
|------|------|
| `/resume` | 列出所有 checkpoint 任务 |
| `/resume <task_id>` | 恢复指定任务 |
| `quit` / `exit` / `q` | 退出 |

---

## 3. 环境变量完整参考

所有配置通过 `.env` 文件或系统环境变量设置。`.env` 优先级低于系统环境变量。

### 3.1 LLM API 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | （空） | **必填**。API 密钥 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容 API 地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |

**支持的 LLM 后端**：

```env
# DeepSeek（默认）
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-your-key
LLM_MODEL=deepseek-chat

# Ollama（本地）
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3

# Qwen（DashScope）
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-your-key
LLM_MODEL=qwen-turbo
```

### 3.2 智能体执行限制

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MAX_CONTEXT_TOKENS` | `16000` | 上下文 Token 上限，超出触发摘要压缩 |
| `MAX_REACT_ITERATIONS` | `10` | 每个 Action 节点 ReAct 循环最大迭代次数 |
| `MAX_REPLAN_ATTEMPTS` | `3` | 反思失败后最大重规划次数 |

### 3.3 知识库

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KNOWLEDGE_CHUNK_SIZE` | `500` | 文档切片大小（字符数） |
| `KNOWLEDGE_TOP_K` | `3` | 知识检索返回的最大条数 |

> `KNOWLEDGE_DOCS_DIR` 为硬编码路径（`{config.py所在目录}/knowledge/docs`），不可通过环境变量修改。知识检索基于 TF-IDF + 余弦相似度。

### 3.4 规划路由

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PLAN_MODE` | `auto` | `auto`=自动分类 \| `simple`=强制v1 \| `complex`=强制v2 DAG \| `emergent`=强制v5 |

### 3.5 DAG 执行

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MAX_PARALLEL_NODES` | `3` | 每个 Super-step 最大并行节点数 |
| `DAG_SERIAL_EXECUTION` | `true` | 串行执行（默认，修复并发串话；设 `false` 恢复并行） |
| `NODE_EXECUTION_TIMEOUT` | `300` | 单节点超时（秒） |
| `MAX_CHECKPOINTS` | `10` | 内存中最大 Checkpoint 数 |

### 3.6 自适应规划（v3）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADAPTIVE_PLANNING_ENABLED` | `true` | 启用超步间自适应规划 |
| `ADAPT_PLAN_INTERVAL` | `1` | 每隔几个超步检查一次 |
| `ADAPT_PLAN_MIN_COMPLETED` | `1` | 至少完成几个 ACTION 后启动 |

### 3.7 工具路由

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TOOL_FAILURE_THRESHOLD` | `2` | 连续失败 N 次后建议切换工具 |

### 3.8 Emergent 规划（v5）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EMERGENT_PLANNING_ENABLED` | `true` | 启用隐式规划模式 |
| `MAX_TODO_ITEMS` | `20` | TODO 列表最大项数 |
| `MAX_TODO_RETRIES` | `3` | 单个 TODO 最大重试次数 |
| `TODO_COMPRESSION_THRESHOLD` | `0.8` | 上下文窗口使用率 80% 时压缩 |
| `MAX_EMERGENT_OUTER_ITERATIONS` | `60` | Emergent 主循环最大迭代数 |

### 3.9 目标驱动规划（v8）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_GOAL_DRIVEN_PLANNER` | `false` | 启用 v8 目标驱动规划引擎 |
| `GOAL_REANCHOR_INTERVAL` | `5` | 每隔 N 次迭代重新锚定目标 |
| `GOAL_REFLECTION_INTERVAL` | `1` | 每隔 N 次迭代执行目标反思 |
| `MAX_GOAL_DRIVEN_ITERATIONS` | `60` | 主循环最大迭代数 |
| `GOAL_DRIVEN_STAGNATION_WINDOW` | `3` | 连续 N 轮无进度则提前终止 |

### 3.10 子智能体 SubAgent（v9）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SUBAGENT_ENABLED` | `false` | **主开关** |
| `SUBAGENT_MAX_ITERATIONS` | `10` | 内部 ReAct 最大迭代 |
| `SUBAGENT_TIMEOUT` | `300` | 执行超时（秒） |
| `SUBAGENT_MAX_CONCURRENT` | `2` | 最大并发数（削峰缓解 QPS 限流） |
| `SUBAGENT_SUMMARY_MAX_LENGTH` | `2000` | 摘要最大字符数 |
| `SUBAGENT_MAX_CALLS_PER_TASK` | `3` | 单任务调用上限 |
| `SUBAGENT_MAX_TOKENS_PER_CALL` | `120000` | 单次 Token 预算上限（深度联网调研 50000 偏小，已上调） |
| `SUBAGENT_DEFAULT_TOOL_WHITELIST` | （空） | 默认工具白名单（逗号分隔，空=全量） |
| `SUBAGENT_MAX_TASK_DESCRIPTION_LENGTH` | `2000` | task_description 最大字符数 |
| `SUBAGENT_ITERATION_EVENT_VERBOSITY` | `summary` | UI 粒度：`summary` / `full` / `silent` |
| `SUBAGENT_ITERATION_EVENT_EVERY_N` | `2` | summary 模式下每 N 轮渲染一次 |
| `EMERGENT_PARALLEL_TODOS` | `false` | emergent 路径：无依赖 TODO 并发委派给隔离 SubAgent（需 SUBAGENT_ENABLED=true） |

### 3.11 人机交互 HITL（v13）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HITL_ENABLED` | `false` | **主开关** |
| `HITL_MAX_PROMPTS_PER_TASK` | `5` | 单任务最大 ask_user 次数 |
| `HITL_USER_INPUT_TIMEOUT` | `120` | 等待用户输入超时（秒） |

### 3.12 推理引擎（v14）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ENABLE_REASONING_ENGINE` | `false` | 启用 ReasoningEngine |
| `MAX_THINKING_TOKENS` | `10000` | thinking token 预算上限 |
| `MAX_THINKING_ROUNDS` | `5` | 连续纯思考轮次硬上限 |
| `REASONING_EFFORT` | `auto` | 推理力度：`auto` / `low` / `medium` / `high` |
| `REASONING_TOKEN_TRACKING` | `true` | 追踪 reasoning tokens |

### 3.13 任务恢复（v14.5）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TASK_RESUME_ENABLED` | `true` | 启用 Task Resume checkpoint |
| `CHECKPOINT_DIR` | `~/.manus_demo/checkpoints` | checkpoint 文件目录 |
| `CHECKPOINT_MAX_PER_TASK` | `5` | 每任务最大 checkpoint 数 |
| `CHECKPOINT_RETENTION_DAYS` | `7` | 已完成 checkpoint 保留天数 |

### 3.14 记忆系统（v15）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENTIC_MEMORY_ENABLED` | `false` | **主开关** |
| `MEMORY_TOOLS_ENABLED` | `false` | 注册 memory tools 到 ReAct |
| `MEMORY_MIN_CONFIDENCE` | `0.35` | 检索最低置信度 |
| `MEMORY_SEARCH_TOP_K` | `3` | 检索返回最大条数 |
| `MEMORY_LLM_CONSOLIDATION_ENABLED` | `false` | LLM 辅助记忆巩固 |
| `MEMORY_DIR` | `~/.manus_demo` | 长期记忆存储目录 |
| `SHORT_TERM_WINDOW` | `20` | 短期记忆窗口大小 |

### 3.15 自演化（v17）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SELF_EVOLUTION_ENABLED` | `false` | **主开关**（需 AGENTIC_MEMORY_ENABLED=true） |
| `SELF_EVOLUTION_LLM_EXTRACTION` | `false` | 用 LLM 提炼经验（关=确定性提炼） |
| `SELF_EVOLUTION_MAX_HINTS` | `3` | 单次注入的失败避坑提示上限 |
| `SELF_EVOLUTION_CONFIDENCE_CAP` | `0.6` | 自动学习记忆 confidence 上限 |
| `SELF_EVOLUTION_PREFERENCE_ENABLED` | `true` | 从 HITL 交互学习用户偏好 |

### 3.16 分类器校准（v17.3）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CLASSIFIER_SIMPLE_THRESHOLD` | `-1` | 规则评分 ≤ 此值 → simple |
| `CLASSIFIER_COMPLEX_THRESHOLD` | `2` | 规则评分 ≥ 此值 → complex |

### 3.17 确定性工作流（v18.1）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WORKFLOW_ENABLED` | `true` | Workflow 引擎开关 |

### 3.18 专家委派 Handoff（v18.2）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HANDOFF_ENABLED` | `false` | **主开关** |
| `HANDOFF_ALLOW_ASK_USER` | `false` | 专家 agent 是否可 ask_user |
| `HANDOFF_MAX_CALLS_PER_TASK` | `2` | 单任务 handoff 上限 |
| `HANDOFF_TIMEOUT` | `300` | 专家执行超时（秒） |
| `HANDOFF_MAX_ITERATIONS` | `10` | 专家 ReAct 迭代上限 |

### 3.19 MCP 桥接（v16 客户端）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_BRIDGE_ENABLED` | `false` | **主开关** |
| `MCP_BRIDGE_CONFIG_PATH` | （空） | JSON 配置文件路径 |
| `MCP_BRIDGE_SERVERS_JSON` | （空） | 内联 JSON 服务器配置 |
| `MCP_BRIDGE_TOOL_PREFIX` | `mcp` | 工具名前缀：`{prefix}_{server}_{tool}` |
| `MCP_BRIDGE_SCHEMA_MODE` | `loose` | Schema 转换：`loose` / `strict` |
| `MCP_BRIDGE_DISCOVERY_TTL` | `300` | 工具重新发现间隔（秒） |
| `MCP_BRIDGE_CALL_TIMEOUT` | `30` | 单次 MCP 调用超时（秒） |

### 3.20 MCP 服务端（v16 服务端）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MCP_SERVER_ENABLED` | `false` | **主开关** |
| `MCP_SERVER_TRANSPORT` | `streamable_http` | 传输模式：`streamable_http` / `stdio` |
| `MCP_SERVER_HOST` | `127.0.0.1` | 监听地址 |
| `MCP_SERVER_PORT` | `8080` | 监听端口 |
| `MCP_SERVER_EXPOSE_AGENT` | `false` | 暴露 AgentCard + A2A 接口 |

### 3.21 远端子智能体 + A2A（v18.3/18.4）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REMOTE_SUBAGENT_ENABLED` | `false` | 客户端 remote_subagent 工具开关 |
| `REMOTE_AGENT_SERVER_JSON` | （空） | 远端 agent 的 MCP 配置 JSON |
| `REMOTE_SUBAGENT_MAX_CALLS_PER_TASK` | `2` | 单任务远端调用上限 |
| `REMOTE_SUBAGENT_TIMEOUT` | `300` | 远端任务超时（秒） |
| `REMOTE_AGENT_FETCH_CARD` | `true` | 调用前是否先拉取 AgentCard |

### 3.22 安全护栏 Guardrails（v19）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GUARDRAILS_ENABLED` | `false` | **主开关**（关=零开销） |
| `GUARDRAIL_TOOL_ENABLED` | `true` | 19.1 工具输入层 |
| `GUARDRAIL_INPUT_ENABLED` | `true` | 19.2 工具输出/上下文层 |
| `GUARDRAIL_OUTPUT_ENABLED` | `true` | 19.3 输出层 |
| `GUARDRAIL_TOOL_MODE` | `block` | `block` / `observe` |
| `GUARDRAIL_INPUT_MODE` | `neutralize` | `neutralize` / `annotate` / `observe` |
| `GUARDRAIL_OUTPUT_MODE` | `redact` | `redact` / `observe` |
| `GUARDRAIL_WRITE_CONFIRM` | `block` | `block` / `confirm` / `allow` |

### 3.23 智能体技能 Skills（v20）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SKILLS_ENABLED` | `false` | **主开关** |
| `SKILLS_PROJECT_DIR` | `{config.py所在目录}/.agents/skills` | 项目级技能目录（可信，**硬编码**，不可通过环境变量修改） |
| `SKILLS_USER_DIR` | `~/.manus_demo/skills` | 用户级技能目录（半可信，可通过环境变量覆盖） |
| `SKILLS_DIRS` | （空） | 额外技能目录（逗号分隔） |
| `SKILLS_MAX_ACTIVATIONS_PER_TASK` | `3` | 单任务最大激活次数 |
| `SKILLS_MAX_CONTENT_TOKENS` | `5000` | 单技能内容最大 token |
| `SKILL_AUTO_DISTILL_ENABLED` | `false` | v20.5 自动蒸馏主开关 |
| `SKILL_AUTO_DISTILL_MIN_SUCCESSES` | `3` | 同类任务成功 N 次后触发蒸馏 |
| `SKILL_AUTO_DISTILL_CONFIDENCE_CAP` | `0.55` | 蒸馏记忆 confidence 上限 |
| `SKILL_OPTIMIZE_LLM_ENABLED` | `false` | v20.6 LLM 辅助修订 |
| `SKILL_OPTIMIZE_VALIDATION_RATIO` | `0.2` | train/validation split |
| `SKILL_OPTIMIZE_MAX_TOKENS` | `1200` | LLM 修订最大输出 token |

### 3.24 全链路追踪 Tracing（v7）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRACING_ENABLED` | `false` | **主开关** |
| `TRACING_BACKEND` | `console` | `console` / `file` / `rich` / `otlp` / `phoenix` |
| `TRACING_ENDPOINT` | `http://localhost:4318` | OTLP HTTP 端点 |
| `TRACING_SERVICE_NAME` | `manus-demo` | 服务标识 |
| `TRACING_SAMPLE_RATE` | `1.0` | 采样率 (0.0-1.0) |
| `TRACING_LOG_PROMPTS` | `false` | **当前源码未实际门控 LLM prompt/response 记录**；Tracing 开启后 LLM span 会记录明文 prompt/response |
| `TRACING_MAX_ATTR_LENGTH` | `1000` | 属性值最大字符数 |

### 3.25 网络搜索 & Bailian MCP

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_SEARCH_MAX_RESULTS` | `5` | 单次搜索最大结果数 |
| `WEB_SEARCH_TIMEOUT` | `15` | 搜索超时（秒） |
| `DASHSCOPE_API_KEY` | （空） | 百炼 MCP Key（空=回退 DDGS） |
| `BAILIAN_WEBSEARCH_MCP_URL` | `https://dashscope.aliyuncs.com/.../mcp` | 搜索 MCP 端点 |
| `BAILIAN_WEBPARSER_MCP_URL` | `https://dashscope.aliyuncs.com/.../sse` | 网页解析 MCP 端点（**SSE 传输**，streamable HTTP 会 405） |
| `BAILIAN_MCP_MAX_RETRIES` | `3` | 429/瞬时错误最大重试次数（0=不重试） |
| `BAILIAN_MCP_RETRY_BASE_DELAY` | `2.0` | 指数退避基础延迟（秒）：delay = base × 2^attempt |
| `SEARCH_CONVERGENCE_THRESHOLD` | `3` | 同工具调用 N 次后注入收敛提示 |
| `FETCH_URL_MAX_CONTENT_LENGTH` | `10000` | fetch_url 最大返回字符数 |
| `TOOL_RESULT_TRUNCATION_LIMIT` | `2000` | 工具结果截断长度 |

### 3.26 工具执行

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SANDBOX_DIR` | `~/.manus_demo/sandbox` | 沙箱目录 |
| `CODE_EXEC_TIMEOUT` | `30` | Python 代码执行超时（秒） |
| `SHELL_EXEC_TIMEOUT` | `30` | Shell 命令超时（秒） |
| `SUBPROCESS_MAX_OUTPUT_BYTES` | `524288` | 子进程最大输出字节数 (512KB) |
| `SHELL_MAX_CONCURRENT` | `3` | 最大并发 Shell 数 |
| `CODE_MAX_CONCURRENT` | `3` | 最大并发代码执行数 |

### 3.27 用户位置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `USER_LOCATION` | （空） | 显式城市名（最高优先级） |
| `LOCATION_IP_LOOKUP_ENABLED` | `true` | 是否允许公网 IP 定位 |
| `LOCATION_SSL_VERIFY` | `true` | IP 定位 HTTPS 证书校验 |

### 3.28 Harness 配置（v14 Phase 3）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REACT_TEMPERATURE` | `0.5` | ReActEngine 温度 |
| `REASONING_TEMPERATURE` | `0.5` | ReasoningEngine 温度 |
| `PLANNER_TEMPERATURE` | `0.3` | PlannerAgent 温度 |
| `REFLECTOR_TEMPERATURE` | `0.1` | ReflectorAgent 温度 |
| `CONVERGENCE_ESCALATION_MULTIPLIER` | `2` | 收敛提示升级倍数 |
| `THINKING_AWARE_CONTEXT` | `true` | ContextManager 感知 thinking_content |
| `TOKEN_TRACKING_ENABLED` | `true` | 是否启用 Token 消耗追踪 |
| `ENABLE_REACT_ENGINE_V2` | `false` | **已弃用（v12）**：ReActEngine 现在是唯一实现；保留仅为向后兼容，不影响行为 |

### 3.29 LLM 重试

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_RETRY_ENABLED` | `false` | LLM 调用重试 |
| `LLM_RETRY_MAX_ATTEMPTS` | `3` | 最大重试次数 |
| `LLM_RETRY_BACKOFF_FACTOR` | `2.0` | 退避因子 |

### 3.30 AgentBay 云端工具

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENTBAY_ENABLED` | `false` | 总开关 |
| `AGENTBAY_API_KEY` | （空） | AgentBay API Key |
| `AGENTBAY_CODE_TOOL_ENABLED` | `true` | 注册 agentbay_code |
| `AGENTBAY_BROWSER_TOOL_ENABLED` | `true` | 注册 agentbay_browser |
| `AGENTBAY_LOG_LEVEL` | `WARNING` | SDK 日志级别（WARNING 避免输出 resource_url/authcode） |
| `AGENTBAY_MAX_CONCURRENT_SESSIONS` | `1` | Session 并发上限 |
| `AGENTBAY_CODE_IMAGE` | `code_latest` | CodeSpace 镜像 alias |
| `AGENTBAY_BROWSER_IMAGE` | `browser_latest` | BrowserUse 镜像 alias |
| `AGENTBAY_SESSION_IDLE_RELEASE_MINUTES` | `5` | 空闲自动释放兜底 |
| `AGENTBAY_SESSION_MAX_RUNTIME_MINUTES` | `10` | 单 Session 最大运行时间兜底 |
| `AGENTBAY_CODE_TIMEOUT_SECONDS` | `60` | CodeSpace 单次超时 |
| `AGENTBAY_BROWSER_TIMEOUT_MS` | `30000` | BrowserUse 超时 |

---

## 4. 功能模块运行指南

### 4.1 规划路由

系统自动根据任务复杂度选择执行路径，也可通过 `PLAN_MODE` 强制指定。

**三种路径**：
- **simple (v1)**: 扁平 2-6 步计划，顺序执行
- **complex (v2)**: 层级 DAG（Goal → SubGoals → Actions），支持并行 super-step
- **emergent (v5/v8)**: TODO 驱动的自底向上规划

```bash
# 自动分类（默认）
python main.py "写一个斐波那契函数"

# 强制简单路径
PLAN_MODE=simple python main.py "搜索今天的新闻"

# 强制复杂路径（DAG 并行）
PLAN_MODE=complex python main.py "调研三个Python Web框架并做对比分析"

# 强制 emergent 路径
PLAN_MODE=emergent python main.py "研究量子计算的最新进展并生成报告"

# emergent + 目标驱动（v8）
PLAN_MODE=emergent ENABLE_GOAL_DRIVEN_PLANNER=true python main.py "多步骤研究任务"
```

### 4.2 子智能体 SubAgent

SubAgent 遵循 depth=1 隔离模式：独立上下文，仅返回结构化摘要。

```bash
# 启用 SubAgent
SUBAGENT_ENABLED=true PLAN_MODE=emergent python main.py \
  "调研Python异步编程、Django和FastAPI三个主题，分别总结优缺点"

# 自定义 Token 预算和调用上限
SUBAGENT_ENABLED=true \
SUBAGENT_MAX_TOKENS_PER_CALL=30000 \
SUBAGENT_MAX_CALLS_PER_TASK=5 \
SUBAGENT_TIMEOUT=600 \
python main.py "多主题独立调研任务"

# 限制工具白名单（SubAgent 只能使用指定工具）
SUBAGENT_ENABLED=true \
SUBAGENT_DEFAULT_TOOL_WHITELIST="web_search,fetch_url" \
python main.py "搜索任务"

# 控制 UI 输出粒度（减少并行 SubAgent 时的日志噪声）
SUBAGENT_ENABLED=true \
SUBAGENT_ITERATION_EVENT_VERBOSITY=silent \
python main.py "多任务调研"
```

**验证要点**：
- UI 中应出现 `[SubAgent] xxx spawned` 和 `[SubAgent] xxx completed`
- SubAgent 无法调用 `subagent` 工具（depth=1 结构化限制）
- Token 消耗表应显示 SubAgent 独立的 caller 行

### 4.3 人机交互 HITL

HITL 仅在交互模式下激活。单任务模式自动失活。LLM 通过 `ask_user` 工具主动向用户提问。

```bash
# 启用 HITL（必须在交互模式下运行）
HITL_ENABLED=true python main.py
# 然后在交互中输入一个需要澄清信息的任务
# 例如: "帮我订一个餐厅" → agent 可能会问 "在哪个城市？"

# 调整最大提问次数和超时
HITL_ENABLED=true \
HITL_MAX_PROMPTS_PER_TASK=10 \
HITL_USER_INPUT_TIMEOUT=300 \
python main.py
```

**`ask_user` 工具行为**：
- 参数：`question`（向用户提出的具体问题）
- 使用 `asyncio.Future` 桥接异步 ReAct 循环与同步用户输入
- 达到 `HITL_MAX_PROMPTS_PER_TASK` 上限后返回 Error，LLM 自主继续
- 超时后返回 Error，LLM 自主继续
- 用户 Ctrl+C 取消后返回 Error，LLM 自主继续
- SubAgent 不可调用 `ask_user`（depth=1 限制）

**验证要点**：
- UI 中出现 `Agent Asks` 面板，等待用户输入
- 超时后 agent 自主继续执行
- Ctrl+C 取消提问，agent 自主继续

### 4.4 记忆系统

系统有两层记忆：
- **短期记忆**: 滑动窗口（`SHORT_TERM_WINDOW`），多轮对话间保持
- **长期记忆**: JSON 文件持久化（`MEMORY_DIR`），双语关键词检索（英文 word + 中文 bigram，中文任务可正常召回）
- **Agentic Memory (v15)**: 结构化记忆，支持多类型、置信度、撤销

```bash
# 基本长期记忆（默认开启，自动存储/召回）
python main.py
# 多轮对话中，前面的结果会自动存入长期记忆

# 启用 Agentic Memory (v15)
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
python main.py

# 调整检索参数
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
MEMORY_SEARCH_TOP_K=5 \
MEMORY_MIN_CONFIDENCE=0.5 \
python main.py

# 启用 LLM 辅助记忆巩固
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
MEMORY_LLM_CONSOLIDATION_ENABLED=true \
python main.py
```

**记忆工具**（当 `MEMORY_TOOLS_ENABLED=true` 时注册到 ReAct）：
- `memory_search` — 搜索记忆（支持按 kind、tags 过滤）
- `memory_store` — 存储记忆（Agent 发起的写入置信度上限 0.6）
- `memory_consolidate` — 将任务经验巩固为长期记忆
- `memory_revoke` — 撤销记忆（软删除，不物理移除）

> **`AGENTIC_MEMORY_ENABLED` vs `MEMORY_TOOLS_ENABLED` 的区别**：
> - `AGENTIC_MEMORY_ENABLED=true`：启用 Agentic Memory 内部系统（Orchestrator 自动在任务开始时搜索、结束时存储）
> - `MEMORY_TOOLS_ENABLED=true`：额外注册 4 个 memory 工具到 LLM 的 ReAct 循环，让 LLM **主动**管理记忆
> - 通常需要两者同时开启；仅开启 `AGENTIC_MEMORY_ENABLED` 时，记忆是被动自动管理的

**验证要点**：
- UI 中出现 `Searching agentic memory...` 和 `Agentic memory: N results found`
- 任务完成后出现 `(Result stored in agentic memory)`

### 4.5 自演化 Self-Evolution

从任务结果中提炼经验，失败时保存避坑提示，成功时沉淀流程知识。

```bash
# 启用自演化（硬依赖是 AGENTIC_MEMORY_ENABLED=true；MEMORY_TOOLS_ENABLED 是可选的）
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
python main.py

# 可选：若希望 LLM 在 ReAct 循环中主动调用 memory_search/store/... 工具，额外加 MEMORY_TOOLS_ENABLED
# （自演化的经验学习/失败反思/避坑提示注入核心路径不需要它）
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
python main.py

# 启用 LLM 辅助经验提炼
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_LLM_EXTRACTION=true \
python main.py

# 调整避坑提示注入上限
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_MAX_HINTS=5 \
python main.py

# 启用用户偏好学习（需同时启用 HITL，且偏好学习仅在【交互模式】下生效）
AGENTIC_MEMORY_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SELF_EVOLUTION_PREFERENCE_ENABLED=true \
HITL_ENABLED=true \
python main.py   # 不带任务参数进入交互模式后再输入任务；单任务命令行模式下 HITL 被抑制，偏好无法学习
```

> ⚠️ 自演化硬门控在 `AGENTIC_MEMORY_ENABLED=true`：缺少它时仅打印
> `WARNING [Orchestrator] SELF_EVOLUTION_ENABLED but AGENTIC_MEMORY_ENABLED is off — self-evolution disabled`，
> 自演化静默失效，UI 无任何相关输出。上面所有示例均已显式带上该变量。

**验证要点**：
- 失败任务后出现 `🧠 Failure lesson stored: ...`
- 成功任务后出现 `🧠 Experience learned: ...`
- 下次类似任务出现 `🧭 Past-failure avoidance hints injected`
- 偏好学习仅在交互模式中生效，交互问答后出现 `🧠 User preference learned: ...`
- 已知用户偏好注入时出现 `🧭 Known user preferences injected`

#### 分类器校准（v17.3）

离线分析分类器阈值，不修改运行配置：

```bash
# 运行分类器校准（不需要 API Key）
python -m evolution.calibrate --show-per-task

# 指定搜索范围（注意：负数范围必须用 = 形式，否则 argparse/shell 会把 -3:1 误判为选项）
python -m evolution.calibrate \
  --simple-range="-3:1" \
  --complex-range="1:5" \
  -o calibration_result.json
```

**验证要点**：
- 输出 accuracy / ambiguous 表格（current vs suggested）
- `--show-per-task` 额外打印每个 task 的 rule score / expected / emergent 明细
- 建议写入 `${MEMORY_DIR}/classifier_thresholds.suggested.json`
- 若有改进，打印 APPLY 提示（`export CLASSIFIER_SIMPLE_THRESHOLD=... CLASSIFIER_COMPLEX_THRESHOLD=...`）；
  校准为建议性质，绝不自动改 live config。

### 4.6 确定性工作流 Workflow

无 LLM 参与的工具编排，按 JSON 声明式执行。

```bash
# 从 JSON spec 运行工作流
python main.py --workflow workflow_spec.json
```

**WorkflowSpec JSON 格式示例**：

> Workflow 的 `${step_id}` 只做纯字符串替换，不会从上一步输出中自动抽取 URL 或字段。若需要 `fetch_url`，请传入明确 URL，或先增加一个解析步骤把搜索结果转成单个 URL。

```json
{
  "name": "fetch-known-url",
  "description": "抓取一个明确 URL 的页面内容",
  "steps": [
    {
      "id": "fetch_python_docs",
      "tool": "fetch_url",
      "params": {"url": "https://docs.python.org/3/whatsnew/3.12.html"}
    }
  ],
  "final_step": "fetch_python_docs"
}
```

**验证要点**：
- UI 出现 `Workflow 'name' (N steps, deterministic)`
- 每个步骤显示 `step 'id' (tool)`
- 完成后显示 `Workflow complete`

### 4.7 专家委派 Handoff

上下文传递 + 控制权转移的专家委派，与 SubAgent 的隔离式互补。Handoff 成功后专家的完整输出成为当前 ReAct 循环的最终答案（控制权转移）。

**内置专家注册表**：

| 专家名 | 描述 | 默认工具 |
|--------|------|---------|
| `researcher` | 信息调研专家：web 搜索 + 页面抓取 + 综合分析 | `web_search`, `fetch_url`, `get_user_location` |
| `coder` | 编码专家：实现/运行/验证代码 | `execute_python`, `file_ops`, `execute_shell` |
| `writer` | 写作/综合专家：基于上下文组织最终答案 | （无工具，纯文本合成） |

> **Handoff vs SubAgent 区别**：
> - **SubAgent**：隔离式，独立上下文，只返回结构化摘要，父循环继续
> - **Handoff**：控制权转移式，带上下文简报，专家完整输出即最终答案，父循环结束

```bash
# 启用 Handoff
HANDOFF_ENABLED=true python main.py "需要专家分析的复杂任务"

# 允许专家调用 ask_user（需同时启用 HITL）
HANDOFF_ENABLED=true \
HANDOFF_ALLOW_ASK_USER=true \
HITL_ENABLED=true \
python main.py

# 调整调用上限和超时
HANDOFF_ENABLED=true \
HANDOFF_MAX_CALLS_PER_TASK=5 \
HANDOFF_TIMEOUT=600 \
python main.py
```

**验证要点**：
- UI 出现 `Handoff → specialist 'xxx' (control transfer)`
- 完成后出现 `Handoff 'xxx' complete`
- 专家不可调用 `handoff`、`subagent`、`remote_subagent`、`memory_store`、`memory_revoke`（depth=1 限制）

### 4.8 远端子智能体 + A2A

通过 MCP 调用远端 agent，支持 AgentCard 能力发现。

```bash
# 启用远端子智能体
REMOTE_SUBAGENT_ENABLED=true \
REMOTE_AGENT_SERVER_JSON='{"name":"remote-agent","transport":"streamable_http","url":"http://localhost:8080/mcp"}' \
python main.py "远端调研任务"

# 启用 MCP 服务端 + 暴露 A2A 接口
MCP_SERVER_ENABLED=true \
MCP_SERVER_PORT=8080 \
MCP_SERVER_EXPOSE_AGENT=true \
python main.py
```

**验证要点**：
- 客户端出现 `Remote SubAgent → 'server' (cross-process via MCP/A2A)`
- 服务端日志显示 `MCP Server started (streamable_http, 127.0.0.1:8080)`
- AgentCard 可被拉取：`AgentCard 'name' — skills: ...`

### 4.9 安全护栏 Guardrails

三层安全：工具输入（19.1）、上下文注入（19.2）、输出脱敏（19.3）。

```bash
# 启用全部护栏
GUARDRAILS_ENABLED=true python main.py

# 仅观察不阻断（调试模式）
GUARDRAILS_ENABLED=true \
GUARDRAIL_TOOL_MODE=observe \
GUARDRAIL_INPUT_MODE=observe \
GUARDRAIL_OUTPUT_MODE=observe \
python main.py

# 写操作需用户确认
GUARDRAILS_ENABLED=true \
GUARDRAIL_WRITE_CONFIRM=confirm \
python main.py

# 关闭特定层
GUARDRAILS_ENABLED=true \
GUARDRAIL_OUTPUT_ENABLED=false \
python main.py
```

**三层防护说明**：

| 层 | 功能 | 默认行为 |
|----|------|----------|
| 19.1 工具输入 | 路径遍历、危险命令、写操作拦截 | `block` |
| 19.2 上下文注入 | web_search/fetch_url 返回内容中的间接注入 | `neutralize` |
| 19.3 输出脱敏 | API Key、密码、PII 等红action | `redact` |

**验证要点**：
- 工具输入被阻断时出现 `Guardrail BLOCKED tool (risk): reason`
- 注入被中和时出现 `Injection neutralized in tool output`
- 输出被脱敏时出现 `Output redacted: reason`

### 4.10 智能体技能 Skills

Agent Skills 遵循 agentskills.io V1.0 规范，支持渐进式披露（Discovery → Activation → Execution）。

```bash
# 启用 Skills
SKILLS_ENABLED=true python main.py

# 指定额外 skill 目录
SKILLS_ENABLED=true \
SKILLS_DIRS="/path/to/my-skills,/another/dir" \
python main.py

# 调整激活限制
SKILLS_ENABLED=true \
SKILLS_MAX_ACTIVATIONS_PER_TASK=5 \
SKILLS_MAX_CONTENT_TOKENS=8000 \
python main.py
```

**内置技能（`.agents/skills/`）**：

| 技能 | 描述 | 预授权工具 |
|------|------|-----------|
| `hello-world` | 演示 skill 激活机制 | 无 |
| `web-research` | 使用 web 搜索和 URL 抓取调研主题 | `web_search`, `fetch_url` |
| `data-analysis` | 分析数据文件（CSV/JSON） | `execute_python`, `file_ops` |
| `malicious-skill` | 安全测试种子（注入攻击模拟） | `execute_shell`, `file_ops` |

**自定义 Skill 创建**：

在 `.agents/skills/my-skill/` 下创建 `SKILL.md`：

```markdown
---
name: my-skill
description: >
  做什么 + 何时用的描述（这是 LLM 判断是否激活的唯一依据）。
  必须极其明确。
metadata:
  author: your-name
  version: "1.0"
allowed-tools: web_search fetch_url
---

# Skill 指令正文

## 工作流程
1. 第一步
2. 第二步
...
```

**自动蒸馏（v20.5）**：

```bash
# 从高频成功模式自动蒸馏 SKILL.md
SKILLS_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
AGENTIC_MEMORY_ENABLED=true \
SKILL_AUTO_DISTILL_ENABLED=true \
SKILL_AUTO_DISTILL_MIN_SUCCESSES=3 \
python main.py
# 同类任务成功 3 次后，自动生成 .agents/skills/auto-{name}/SKILL.md
# 蒸馏的 skill 为半可信级别，需用户确认后移至项目级目录
```

**技能优化闭环（v20.6）**：

```bash
# 从评测结果优化 skill description
python -m skills.optimize --skill .agents/skills/web-research --results eval_results.json

# 应用优化（默认只生成 diff，不自动写入）
python -m skills.optimize --skill .agents/skills/web-research --results eval_results.json --apply
```

**验证要点**：
- 启动时出现 `Skills discovered: N (skill-a, skill-b, ...)`
- 激活时出现 `Skill activated: name`
- 安全护栏对恶意 skill 生效：`Skill content guarded (trust): name → action`

### 4.11 全链路追踪 Tracing

OpenTelemetry 全生命周期可观测。

```bash
# 启用 console 追踪
TRACING_ENABLED=true TRACING_BACKEND=console python main.py "任务"

# Rich 控制台输出
TRACING_ENABLED=true TRACING_BACKEND=rich python main.py "任务"

# 输出到文件
TRACING_ENABLED=true TRACING_BACKEND=file python main.py "任务"

# 发送到 OTLP 端点（Jaeger/Zipkin 等）
TRACING_ENABLED=true \
TRACING_BACKEND=otlp \
TRACING_ENDPOINT=http://localhost:4318 \
python main.py "任务"

# 注意：当前源码中 TRACING_LOG_PROMPTS 未门控 LLM prompt/response 明文记录。
# 隐私敏感任务不要开启 tracing，尤其不要使用 file/rich/console 后端记录完整链路。
```

**追踪 Web Viewer**：

```bash
# 启动 FastAPI 服务器（默认端口 8600，自动打开浏览器）
python -m tracing

# 自定义端口
python -m tracing --port 9000

# 指定 traces 目录
python -m tracing --dir ./my_traces

# 自定义绑定地址
python -m tracing --host 0.0.0.0

# 不自动打开浏览器
python -m tracing --no-open

# 访问地址：http://127.0.0.1:8600/traces
```

| Web Viewer 参数 | 默认值 | 说明 |
|----------------|--------|------|
| `--port` / `-p` | `8600` | Web 服务端口 |
| `--dir` / `-d` | `traces` | trace JSON 文件目录 |
| `--host` | `127.0.0.1` | 绑定地址 |
| `--no-open` | （不启用） | 不自动打开浏览器 |

**验证要点**：
- console 后端直接在终端输出 span 树
- rich 后端用 Rich 面板展示
- Web viewer 提供可视化界面

### 4.12 任务恢复 Task Resume

长任务中断后可从 checkpoint 恢复。

```bash
# Task Resume 默认开启
python main.py "一个长时间运行的任务"
# 到达执行边界后会保存 checkpoint；Ctrl+C 不保证立即生成新的中断点

# 列出所有 checkpoint 任务
python main.py --list-tasks

# 在交互模式中恢复
python main.py
# 然后输入：
# /resume
# 或
# /resume <task_id>

# 命令行直接恢复
python main.py --resume <task_id>
```

**验证要点**：
- 任务执行中出现 `(Checkpoint saved: task_id=xxx, state=...)`
- `--list-tasks` 显示任务列表和状态
- 恢复时已完成步骤不重跑，从失败/运行中的步骤继续

### 4.13 MCP 桥接

连接外部 MCP 服务器，将远程工具桥接为本地 BaseTool。

```bash
# 方式一：内联 JSON 配置（快速测试）
MCP_BRIDGE_ENABLED=true \
MCP_BRIDGE_SERVERS_JSON='{"servers":{"my-server":{"transport":"streamable_http","url":"http://localhost:9090/mcp"}}}' \
python main.py

# 方式二：配置文件
MCP_BRIDGE_ENABLED=true \
MCP_BRIDGE_CONFIG_PATH=/path/to/mcp_config.json \
python main.py

# stdio 传输模式
MCP_BRIDGE_ENABLED=true \
MCP_BRIDGE_SERVERS_JSON='{"servers":{"local-tool":{"transport":"stdio","command":"node","args":["server.js"]}}}' \
python main.py
```

**MCP 配置文件格式**（JSON）：

```json
{
  "servers": {
    "my-server": {
      "name": "my-server",
      "transport": "streamable_http",
      "url": "http://localhost:9090/mcp",
      "headers": {"Authorization": "Bearer xxx"},
      "timeout": 30,
      "enabled": true
    }
  },
  "schema_mode": "loose",
  "tool_prefix": "mcp",
  "call_timeout_seconds": 30
}
```

### 4.14 推理引擎 ReasoningEngine

支持 DeepSeek R1 / OpenAI o 系列推理模型。

```bash
# 启用 ReasoningEngine
ENABLE_REASONING_ENGINE=true python main.py "需要深度推理的任务"

# 指定推理力度
ENABLE_REASONING_ENGINE=true \
REASONING_EFFORT=high \
MAX_THINKING_TOKENS=20000 \
python main.py

# 自动推理力度（由分类器动态决定）
ENABLE_REASONING_ENGINE=true \
REASONING_EFFORT=auto \
python main.py
```

### 4.15 AgentBay 云端工具

云端 CodeSpace 和 BrowserUse 运行时。

```bash
# 启用 AgentBay 工具
AGENTBAY_ENABLED=true \
AGENTBAY_API_KEY=your-key \
python main.py

# 仅启用代码执行
AGENTBAY_ENABLED=true \
AGENTBAY_API_KEY=your-key \
AGENTBAY_BROWSER_TOOL_ENABLED=false \
python main.py

# 自定义超时和并发
AGENTBAY_ENABLED=true \
AGENTBAY_API_KEY=your-key \
AGENTBAY_MAX_CONCURRENT_SESSIONS=2 \
AGENTBAY_CODE_TIMEOUT_SECONDS=60 \
python main.py
```

> `agentbay_code` 的执行超时会在工具内限制到 1..60 秒；即使环境变量设置更大，实际单次代码执行也不会超过 60 秒。

---

## 5. 评测系统

### 基本用法

```bash
# 查看 benchmark 任务清单（不需要 API Key）
python -m evaluation.eval_cli --dry-run

# 快速 smoke 测试
python -m evaluation.eval_cli --difficulty easy --modes simple

# 全量评测
python -m evaluation.eval_cli

# 指定模式和难度
python -m evaluation.eval_cli --modes simple complex --difficulty medium hard

# 指定具体任务
python -m evaluation.eval_cli --tasks easy_002 easy_003

# 导出 JSON 结果
python -m evaluation.eval_cli --output results.json

# pass@k 可靠性测试（重复 k 次）
python -m evaluation.eval_cli --repeat 3

# 详细日志
python -m evaluation.eval_cli -v
```

### 基线对比

```bash
# 保存当前结果为基线
python -m evaluation.eval_cli --save-baseline baselines/my_baseline.json

# 与已有基线对比
python -m evaluation.eval_cli --baseline evaluation/baselines/v14_6_initial.json

# 对比失败时返回非零退出码（CI 集成）
python -m evaluation.eval_cli \
  --baseline evaluation/baselines/v14_6_initial.json \
  --fail-on-regression
```

### 推理矩阵

```bash
# 跨 suite × variant 矩阵运行
python -m evaluation.reasoning_matrix \
  --suite smoke_reasoning \
  --variants react_auto_baseline reasoning_auto reasoning_high \
  --repeat 2

# 矩阵 CLI 完整参数
python -m evaluation.reasoning_matrix \
  --suite smoke_reasoning \                    # 评测套件（默认 smoke_reasoning）
  --variants reasoning_auto reasoning_high \   # 配置变体列表
  --modes simple emergent \                    # 覆盖模式（默认由 suite/variant 决定）
  --tasks easy_001 easy_002 \                  # 覆盖 suite 的任务 ID
  --repeat 3 \                                 # 覆盖重复次数
  --output-dir evaluation/results \            # 输出目录（默认 evaluation/results）
  --run-id my-run \                            # 固定 run ID（可复现路径）
  --baseline-variant react_auto_baseline \     # 基线变体名
  --dry-run \                                  # 只打印配置不执行
  -v                                           # 详细日志

# 生成对比报告（需要 --run-dir 指向矩阵运行输出目录）
python -m evaluation.compare_variants \
  --run-dir evaluation/results/20260602-120000-smoke_reasoning \
  --baseline-variant react_auto_baseline

# 报告输出为 Markdown 文件 variant_comparison.md
```

---

## 6. 测试

```bash
# 离线运行大多数测试（跳过真实 LLM API 集成测试）
python -m pytest tests/ -v -o asyncio_mode=auto --ignore=tests/test_llm_integration.py

# 运行单个测试
python -m pytest tests/test_dag_capabilities.py::test_topological_sort -v -o asyncio_mode=auto

# 运行完整测试套件（需要 .env 或环境变量中配置 LLM_API_KEY）
python -m pytest tests/ -v -o asyncio_mode=auto

# 仅运行非集成标记的测试（同时显式跳过未加 marker 的真实 LLM 集成测试）
python -m pytest tests/ -o asyncio_mode=auto -m "not integration" --ignore=tests/test_llm_integration.py

# 语法检查修改的文件
python3 -m py_compile config.py schema.py llm/client.py agents/orchestrator.py react/engine.py
```

**测试基础设施**：
- `conftest.py`（根目录）提供 session 级 `_block_real_ddgs` fixture，自动阻止非集成测试中真实的 DDGS 网络调用
- `tests/test_llm_integration.py` 会调用真实 LLM API；离线开发时应显式 `--ignore=tests/test_llm_integration.py`
- 标记 `@pytest.mark.integration` 用于需要外部服务（网络、API Key）的测试，但并非所有真实 API 测试都已用该 marker 覆盖
- `asyncio_mode=auto` 参数必须显式传入（项目无 pytest.ini 设置此默认值）

---

## 7. 目录结构速查

```
manus_demo/
├── main.py                 # 入口（交互式/单任务/工作流/恢复）
├── config.py               # 所有环境变量
├── schema.py               # 核心数据模型
├── conftest.py             # pytest 配置（DDGS mock、integration 标记）
├── .env.example            # 环境变量参考模板
├── .agents/skills/         # 项目级 Agent Skills
├── agents/                 # 智能体（Orchestrator, Planner, Executor, Reflector, ...）
│   ├── orchestrator.py     # 中央协调者
│   ├── specialist.py       # 专家注册表 + SpecialistAgent（v18.2）
│   └── subagent.py         # SubAgent（v9）
├── a2a/                    # Agent-to-Agent 原型（v18.4）
├── checkpoint/             # 任务恢复（v14.5）
├── context/                # 上下文管理 & 压缩
├── dag/                    # DAG 图 + 执行器 + 状态机
├── evaluation/             # 评测系统
│   ├── eval_cli.py         # 评测 CLI
│   ├── reasoning_matrix.py # 推理矩阵 CLI
│   ├── compare_variants.py # 矩阵对比报告 CLI
│   ├── benchmark.py        # 任务定义
│   ├── baselines/          # 基线文件
│   └── results/            # 评测结果
├── evolution/              # 自演化（v17）+ 技能蒸馏（v20.5）
│   ├── learner.py          # 经验学习
│   ├── skill_distiller.py  # SKILL.md 自动蒸馏
│   ├── calibrate.py        # 分类器校准 CLI
│   └── calibration.py      # 校准引擎
├── guardrails/             # 安全护栏（v19）
├── knowledge/              # 知识库检索
├── llm/                    # LLM 客户端
├── memory/                 # 记忆系统
│   ├── short_term.py       # 短期记忆
│   ├── long_term.py        # 长期记忆
│   ├── agentic_store.py    # Agentic Memory（v15）
│   └── service.py          # 记忆服务
├── react/                  # ReAct 引擎
├── skills/                 # 技能发现/注册/激活/优化（v20）
│   ├── loader.py           # Skill 发现
│   ├── registry.py         # Skill 注册表
│   ├── activation.py       # SkillActivationTool
│   ├── optimizer.py        # 技能优化器（v20.6）
│   └── optimize.py         # 优化 CLI
├── tools/                  # 工具集
│   ├── web_search.py       # 网络搜索（Bailian MCP + DDGS）
│   ├── fetch_url.py        # URL 页面抓取
│   ├── code_executor.py    # Python 代码执行
│   ├── file_ops.py         # 文件读写
│   ├── shell_tool.py       # Shell 命令
│   ├── user_location.py    # 用户位置
│   ├── ask_user.py         # HITL 人机交互工具（v13）
│   ├── subagent_tool.py    # SubAgent 元工具（v9）
│   ├── handoff_tool.py     # Handoff 专家委派工具（v18.2）
│   ├── remote_subagent_tool.py # 远端子智能体工具（v18.3）
│   ├── memory_tools.py     # 记忆工具 4 个（v15）
│   └── mcp/                # MCP 桥接 + 服务端（v16）
├── tracing/                # OpenTelemetry 追踪（v7）
├── workflow/               # 确定性工作流引擎（v18.1）
├── tests/                  # 测试套件
└── sxw_aicoding/           # 文档 & 路线图
    ├── docs/               # 设计文档
    └── roadmap/            # 迭代路线图
```

---

## 附录 A：功能组合示例

### 全功能开启

```bash
AGENTIC_MEMORY_ENABLED=true \
MEMORY_TOOLS_ENABLED=true \
SELF_EVOLUTION_ENABLED=true \
SUBAGENT_ENABLED=true \
HITL_ENABLED=true \
SKILLS_ENABLED=true \
GUARDRAILS_ENABLED=true \
TRACING_ENABLED=true \
TRACING_BACKEND=rich \
TASK_RESUME_ENABLED=true \
PLAN_MODE=emergent \
python main.py
```

### 安全研究模式

```bash
GUARDRAILS_ENABLED=true \
GUARDRAIL_TOOL_MODE=block \
GUARDRAIL_INPUT_MODE=neutralize \
GUARDRAIL_OUTPUT_MODE=redact \
GUARDRAIL_WRITE_CONFIRM=confirm \
python main.py "安全测试任务"
```

> 安全/隐私敏感测试默认不要开启 tracing；当前 LLM span 会记录明文 prompt/response。

### 评测 + 基线对比（CI 模式）

```bash
python -m evaluation.eval_cli \
  --modes simple complex emergent \
  --baseline evaluation/baselines/v14_6_initial.json \
  --fail-on-regression \
  --output ci_results.json
```

### 离线开发（无 API Key）

```bash
# 查看 benchmark 任务
python -m evaluation.eval_cli --dry-run

# 运行单元测试
python -m pytest tests/ -v -o asyncio_mode=auto

# 分类器校准
python -m evolution.calibrate --show-per-task

# 语法检查
python3 -m py_compile config.py schema.py
```
