
# Manus Demo - 全链路 Tracing 模块配置与使用指南

> **版本**: v7.0  
> **更新日期**: 2026-05-11  
> **目的**: 介绍全链路 Tracing 模块的配置方法、使用流程、操作手册和故障排查，帮助开发人员快速启用和利用运行时可观察性能力

---

## 目录

- [1. 概述与价值](#1-概述与价值)
- [2. 快速开始](#2-快速开始)
- [3. 配置参考](#3-配置参考)
- [4. 导出后端详解](#4-导出后端详解)
- [5. 模块结构与文件说明](#5-模块结构与文件说明)
- [6. Span 层级与数据模型](#6-span-层级与数据模型)
- [7. 自定义埋点（装饰器）](#7-自定义埋点装饰器)
- [8. 集成原理](#8-集成原理)
- [9. 隐私与安全](#9-隐私与安全)
- [10. 性能影响](#10-性能影响)
- [11. 常见操作手册](#11-常见操作手册)
- [12. 故障排查](#12-故障排查)
- [13. 与评测模块的关系](#13-与评测模块的关系)
- [14. 扩展指南](#14-扩展指南)

---

## 1. 概述与价值

### 1.1 什么是全链路 Tracing

Tracing 模块基于 [OpenTelemetry](https://opentelemetry.io/) 标准，为 Manus Demo 的任务执行全生命周期提供结构化的可观察性能力。

一次完整的任务执行会被记录为一棵 **Span 树**，从任务接收到最终响应，覆盖：

```
分类 → 规划 → 执行（含 LLM 调用 + 工具调用）→ 反思 → 持久化
```

### 1.2 解决的问题

| 之前（仅事件回调） | 现在（Tracing 模块） |
|---|---|
| 事件仅在内存中流转，进程结束后丢失 | Span 持久化到文件 / OTLP 后端 |
| 扁平事件流，无层级关系 | 结构化的 Span 父子树 + 时间线 |
| 无法进行 LLM 调用粒度的性能分析 | 每次 LLM 调用记录 model、tokens、latency |
| 跨组件因果关系不可追踪 | 统一 Trace ID 串联全链路 |

### 1.3 设计原则

- **零侵入**：通过事件桥接 + 装饰器集成，核心 Agent 业务逻辑零改动
- **零开销**：`TRACING_ENABLED=false` 时不创建 Span、不加载 OpenTelemetry 依赖
- **Feature Flag 控制**：环境变量一键开关，向后完全兼容
- **多后端支持**：开发时用 Console / File，生产时切换到 OTLP / Phoenix

---

## 2. 快速开始

### 2.1 安装依赖

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp
```

或直接使用项目的 `requirements.txt`：

```bash
pip install -r requirements.txt
```

### 2.2 最简启用（控制台输出）

在 `.env` 文件中添加：

```bash
TRACING_ENABLED=true
TRACING_BACKEND=console
```

然后正常运行项目：

```bash
python main.py
```

控制台将输出 OpenTelemetry 标准格式的 Span 信息。

### 2.3 文件输出（推荐开发使用）

```bash
TRACING_ENABLED=true
TRACING_BACKEND=file
```

Trace 数据将以 JSON 格式写入 `traces/` 目录，每个 Trace 一个文件，文件名以 Trace ID 命名：

```
traces/
└── 5b4de5adb669fe05923ecf33e41e2263.json
```

> **设计说明**：文件名使用稳定的 `{trace_id}.json` 命名，而非带时间戳后缀。这样 `BatchSpanProcessor` 多次导出同一 Trace 的 Span 时，后续批次会自动**追加合并**到同一文件中，确保一次完整任务执行的所有 Span 始终聚合在同一个 JSON 文件内，便于离线分析。

### 2.4 Rich 格式输出（最佳开发体验）

需要额外安装 `rich` 包：

```bash
pip install rich
```

```bash
TRACING_ENABLED=true
TRACING_BACKEND=rich
```

终端将以带图标和颜色的树形结构渲染 Span：

```
🔍 task_execution (1.2s) ✅
  🎯 orchestrator.gather_context (50ms) ✅
  📋 planner.classify_task (200ms) ✅ [complexity=simple]
  ⚡ execution.simple (800ms) ✅
    🤖 llm.chat_with_tools (350ms) ✅ [model=deepseek-chat, total_tokens=1024]
    🔧 tool.execute.web_search (150ms) ✅
  🪞 reflector.reflect (100ms) ✅ [passed=True]
```

### 2.5 OTLP 后端（Jaeger / Phoenix）

```bash
TRACING_ENABLED=true
TRACING_BACKEND=otlp
TRACING_ENDPOINT=http://localhost:4318
```

如果使用 [Arize Phoenix](https://phoenix.arize.com/)（推荐的 LLM 可观察性工具）：

```bash
TRACING_ENABLED=true
TRACING_BACKEND=phoenix
TRACING_ENDPOINT=http://localhost:6006
```

> Phoenix 后端会自动在 endpoint 后追加 `/v1/traces`。

### 2.7 Web 可视化查看器（推荐查看历史 Trace）

当你使用 `file` 后端生成了 trace 文件后，可以通过内置的 Web Viewer 在浏览器中以树形结构查看：

```bash
# 启动 Trace Web Viewer（默认端口 8600，自动打开浏览器）
python -m tracing

# 自定义端口和目录
python -m tracing --port 9000 --dir ./my_traces

# 不自动打开浏览器
python -m tracing --no-open
```

浏览器打开后：
- **列表页** (`/traces`)：展示所有已保存的 Trace 文件（状态、根 Span 名称、Span 数量、耗时）
- **详情页** (`/traces/{trace_id}`)：以可折叠树形结构展示 Span 层级，点击节点查看详细属性

```
http://localhost:8600/traces
├── Trace 列表（按时间倒序）
│   └── 点击某行 → 进入详情页
└── Trace 详情
    ├── 🔍 task_execution (1.2s) ✅
    │   ├── 📋 planner.classify_task (200ms) ✅
    │   │   └── 🤖 llm.chat (140ms) ✅
    │   ├── ⚡ execution.simple (640ms) ✅
    │   │   ├── 🤖 llm.chat_with_tools (330ms) ✅
    │   │   └── 🔧 tool.execute.execute_python (270ms) ✅
    │   └── 🪞 reflector.reflect (190ms) ✅
    └── 属性面板（点击 Span 展开）
```

> **依赖**：`pip install fastapi uvicorn jinja2`（已包含在 requirements.txt 中）

### 2.8 关闭 Tracing

```bash
TRACING_ENABLED=false
```

或直接从 `.env` 中删除 `TRACING_ENABLED`（默认为 `false`）。

---

## 3. 配置参考

所有配置通过环境变量或 `.env` 文件设置：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `TRACING_ENABLED` | `false` | 总开关。`true` 启用，`false` 关闭（零开销） |
| `TRACING_BACKEND` | `console` | 导出后端：`console` / `file` / `rich` / `otlp` / `phoenix` |
| `TRACING_ENDPOINT` | `http://localhost:4318` | OTLP HTTP 端点地址 |
| `TRACING_SERVICE_NAME` | `manus-demo` | 服务标识名称（在后端 UI 中区分不同服务） |
| `TRACING_SAMPLE_RATE` | `1.0` | 采样率，范围 `0.0` - `1.0`（超出范围自动 clamp）。`1.0` = 全量采集 |
| `TRACING_LOG_PROMPTS` | `false` | 是否记录完整的 prompt / response 内容 |
| `TRACING_MAX_ATTR_LENGTH` | `1000` | 属性值最大字符长度（超出部分截断） |

### 3.1 推荐配置

**开发环境**：

```bash
TRACING_ENABLED=true
TRACING_BACKEND=file
TRACING_LOG_PROMPTS=true       # 开发时记录 prompt 便于调试
TRACING_SAMPLE_RATE=1.0        # 全量采集
```

**生产环境**：

```bash
TRACING_ENABLED=true
TRACING_BACKEND=otlp
TRACING_ENDPOINT=http://your-otel-collector:4318
TRACING_LOG_PROMPTS=false      # 生产环境禁止记录 prompt（隐私保护）
TRACING_SAMPLE_RATE=0.1        # 10% 采样，降低开销
TRACING_MAX_ATTR_LENGTH=500
```

---

## 4. 导出后端详解

### 4.1 console — 标准控制台

使用 OpenTelemetry SDK 自带的 `ConsoleSpanExporter`，输出原始 Span 数据到 stdout。

- **适用场景**：快速验证 tracing 是否正常工作
- **处理器**：`SimpleSpanProcessor`（同步即时输出）
- **无额外依赖**

### 4.2 file — JSON 文件

使用自定义的 `FileSpanExporter`，将每个 Trace 输出为独立的 JSON 文件。

- **适用场景**：开发调试、离线分析、CI/CD 中采集 Trace 数据
- **处理器**：`BatchSpanProcessor`（异步批量导出）
- **输出目录**：`traces/`（相对于项目根目录，可通过 `TRACING_FILE_OUTPUT_DIR` 配置）
- **文件命名**：`{trace_id}.json`（稳定命名，多 batch 自动合并到同一文件）
- **合并策略**：首次写入创建文件并写入 spans 数组；后续 batch 读取已有内容并追加新 span，去重后重新写入

**JSON 文件结构**：

```json
{
  "trace_id": "5b4de5adb669fe05923ecf33e41e2263",
  "exported_at": "2026-05-11T10:30:01.200Z",
  "spans": [
    {
      "span_id": "a1b2c3d4e5f60718",
      "parent_span_id": null,
      "name": "task_execution",
      "start_time": "2026-05-11T10:30:00.000Z",
      "end_time": "2026-05-11T10:30:01.200Z",
      "duration_ms": 1200.0,
      "attributes": {
        "task.input": "Write a Python function that returns hello world",
        "task.complexity": "simple",
        "task.success": true
      },
      "events": [],
      "status": "OK"
    },
    {
      "span_id": "b2c3d4e5f6071829",
      "parent_span_id": "a1b2c3d4e5f60718",
      "name": "planner.classify_task",
      "start_time": "2026-05-11T10:30:00.050Z",
      "end_time": "2026-05-11T10:30:00.250Z",
      "duration_ms": 200.0,
      "attributes": {
        "task.complexity": "simple",
        "latency_ms": 200.0
      },
      "events": [],
      "status": "OK"
    }
  ]
}
```

> **注意**：由于使用 `BatchSpanProcessor` 异步批量导出，短时任务可能在程序退出前尚未触发导出。请确保程序正常退出（会自动调用 `shutdown_tracing()` 刷新缓冲区）。如手动终止，可在退出前调用 `shutdown_tracing()`。

### 4.3 rich — Rich 控制台

使用自定义的 `RichConsoleExporter`，以带图标和颜色的格式渲染 Span。

- **适用场景**：开发调试时的最佳视觉体验
- **处理器**：`SimpleSpanProcessor`（同步即时输出）
- **额外依赖**：`pip install rich`

**图标映射**：

| Span 类型 | 图标 | 示例 |
|---|---|---|
| task_execution | 🔍 | 根 Span |
| orchestrator | 🎯 | 上下文收集 |
| planner | 📋 | 任务分类、规划 |
| execution | ⚡ | DAG/简单/涌现执行 |
| llm | 🤖 | LLM 调用 |
| tool | 🔧 | 工具调用 |
| reflector | 🪞 | 反思 |
| memory | 🧠 | 记忆操作 |
| react | 💭 | ReAct 循环 |
| todo | 📝 | 涌现规划任务项 |

### 4.4 otlp — OTLP 标准协议

使用 `OTLPSpanExporter`，通过 HTTP 协议将 Span 发送到任何兼容 OTLP 的后端。

- **适用场景**：生产环境、与 Jaeger / Grafana Tempo / Datadog 等集成
- **处理器**：`BatchSpanProcessor`（异步批量导出）
- **额外依赖**：`pip install opentelemetry-exporter-otlp`

### 4.5 phoenix — Arize Phoenix

与 `otlp` 相同的导出方式，但自动追加 `/v1/traces` 路径后缀。

- **适用场景**：LLM 应用的专业可观察性工具
- **推荐使用**：Arize Phoenix 对 GenAI Span 有原生的可视化支持

**启动 Phoenix**：

```bash
pip install arize-phoenix
python -m phoenix.server.main serve
# Phoenix UI: http://localhost:6006
```

### 4.6 web — 内置 Web Viewer

使用自定义的 FastAPI 应用，提供浏览器端的树形 Trace 可视化界面。

- **适用场景**：查看本地 `file` 后端保存的历史 Trace，无需安装外部可观测平台
- **启动方式**：`python -m tracing`（独立 CLI 命令，不依赖 TRACING_BACKEND 配置）
- **额外依赖**：`pip install fastapi uvicorn jinja2`

**功能特性**：

| 功能 | 说明 |
|---|---|
| Trace 列表页 | 表格展示所有 trace 文件，含状态、根 Span 名称、Span 数量、耗时、文件大小 |
| Trace 详情页 | 可折叠树形 Span 层级，与 RichConsoleExporter 图标一致 |
| 属性面板 | 点击 Span 展开完整 attributes 和 events |
| 暗色主题 | DevTools 风格的暗色界面 |
| JSON API | `GET /api/traces` 和 `GET /api/traces/{trace_id}` 供程序化访问 |

**CLI 参数**：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--port` / `-p` | `8600` | Web 服务端口 |
| `--dir` / `-d` | `./traces` | trace JSON 文件目录 |
| `--host` | `127.0.0.1` | 绑定地址 |
| `--no-open` | — | 不自动打开浏览器 |

**使用示例**：

```bash
# 1. 先用 file 后端运行任务生成 trace 文件
TRACING_ENABLED=true TRACING_BACKEND=file python main.py

# 2. 启动 Web Viewer 查看
python -m tracing

# 3. 浏览器打开 http://localhost:8600/traces
```

> **注意**：Web Viewer 是只读的，它直接读取 `traces/` 目录中的 JSON 文件。你可以在 agent 运行的同时启动 Viewer，新产生的 trace 文件刷新页面即可看到。

---

## 5. 模块结构与文件说明

```
tracing/
├── __init__.py      # 模块入口：条件导入 + No-op Stubs
├── __main__.py      # Web Viewer CLI 入口（python -m tracing）
├── config.py        # 配置集中管理（从 root config.py 读取）
├── spans.py         # Span 名称 / Attribute 键名 / Event 名称常量 / SPAN_ICONS 图标映射
├── provider.py      # TracerProvider 工厂（Resource + Exporter + Sampler）
├── exporters.py     # 自定义导出器（FileSpanExporter + RichConsoleExporter）
├── decorators.py    # 声明式装饰器（@traced）+ 共享工具函数
├── bridge.py        # 事件桥接器（_emit 事件 → OTel Span）
├── server.py        # Web Viewer FastAPI 应用
└── templates/       # Web Viewer HTML 模板
    ├── base.html          # 基础模板（暗色主题）
    ├── trace_list.html    # Trace 列表页
    └── trace_detail.html  # Trace 详情页（树形视图）
```

| 文件 | 职责 | 关键类/函数 |
|---|---|---|
| `__init__.py` | 模块门面，`TRACING_ENABLED=false` 时提供 no-op 桩 | `init_tracing()`, `TracingBridge`, `traced()`（仅 `traced` 装饰器） |
| `__main__.py` | Web Viewer CLI 入口 | `main()` — argparse + uvicorn 启动 |
| `config.py` | 集中管理所有配置常量 | `ENABLED`, `BACKEND`, `ENDPOINT`, `SAMPLE_RATE` |
| `spans.py` | 语义常量 + 图标映射，遵循 OTel GenAI SemConv | `SpanName`, `AttrKey`, `EventName`, `SPAN_ICONS` |
| `provider.py` | 初始化 OTel SDK | `init_tracing()`, `get_tracer()`, `shutdown_tracing()` |
| `exporters.py` | File + Rich 导出器 | `FileSpanExporter`, `RichConsoleExporter` |
| `decorators.py` | 方法级声明式埋点 + 共享工具函数 | `@traced()`, `_truncate`, `_is_sensitive_key`, `_safe_set_attribute` |
| `bridge.py` | 事件流转 Span 桥接 | `TracingBridge.on_event()` |
| `server.py` | Web Viewer 服务 | `app` (FastAPI), `_build_span_tree()` |
| `templates/` | Jinja2 模板 | 列表页 + 详情页（树形展示） |

---

## 6. Span 层级与数据模型

### 6.1 Span 树结构

一次完整的任务执行生成如下 Span 层级树：

```
Trace: task_execution/{task_id}
├── orchestrator.gather_context
│   ├── memory.search
│   └── knowledge.retrieve
├── planner.classify_task
│   └── llm.chat (classification)
├── planner.create_dag / planner.create_plan / planner.create_todo_list
│   └── llm.chat_json (plan generation)
├── execution.dag / execution.simple / execution.emergent
│   ├── dag.super_step.{n}
│   │   ├── node.execute.{node_id}
│   │   │   ├── react.iteration.{i}
│   │   │   │   ├── llm.chat_with_tools
│   │   │   │   └── tool.execute.{tool_name}
│   │   │   └── react.iteration.{i+1}
│   │   └── node.execute.{node_id_2}  (并行)
│   └── dag.super_step.{n+1}
├── reflector.reflect
│   └── llm.chat_json (reflection)
└── memory.store
```

### 6.2 关键 Attributes

**任务级别**：

| Attribute | 类型 | 示例 |
|---|---|---|
| `task.id` | string | `"abc123"` |
| `task.input` | string | `"Write a Python function that returns hello world"` |
| `task.complexity` | string | `"simple"` / `"complex"` |
| `task.success` | bool | `true` |

**LLM 调用**（遵循 OTel GenAI SemConv）：

| Attribute | 类型 | 示例 |
|---|---|---|
| `gen_ai.system` | string | `"openai"` |
| `gen_ai.request.model` | string | `"deepseek-chat"` |
| `gen_ai.request.temperature` | float | `0.7` |
| `gen_ai.caller` | string | `"GoalDrivenPlanner"` / `"SubAgent-1"` / `"ExecutorAgent"`（**Wave-6 新增**） |
| `gen_ai.usage.input_tokens` | int | `512` |
| `gen_ai.usage.output_tokens` | int | `256` |
| `gen_ai.usage.total_tokens` | int | `768` |
| `latency_ms` | float | `350.0` |

**SubAgent 调用**（v9 新增）：

| Attribute | 类型 | 示例 |
|---|---|---|
| `subagent.id` | string | `"SubAgent-1"` |
| `subagent.task_description` | string | `"Search the codebase for X"` |
| `subagent.parent_agent` | string | `"GoalDrivenPlanner"` / `"ExecutorAgent"`（**Wave-1 H1 修复**：之前因 GoalDrivenPlanner 不调 set_caller 而恒为 `OrchestratorAgent`） |
| `subagent.tool_whitelist` | string | `"file_ops, web_search"` |
| `subagent.status` | string | `"completed"` / `"failed"` / `"timed_out"` |
| `subagent.iterations_used` | int | `3` |
| `subagent.tokens_used` | int | `4818` |
| `subagent.duration_ms` | float | `9249.37` |

**工具调用**：

| Attribute | 类型 | 示例 |
|---|---|---|
| `tool.name` | string | `"web_search"` |
| `tool.parameters` | string(JSON) | `'{"query": "python tutorial"}'` |
| `tool.success` | bool | `true` |
| `tool.result_size` | int | `1024` |
| `latency_ms` | float | `150.0` |

**反思**：

| Attribute | 类型 | 示例 |
|---|---|---|
| `reflection.passed` | bool | `true` |
| `reflection.score` | float | `0.85` |
| `reflection.feedback` | string | `"Code is correct"` |

### 6.3 Event 名称

Span 内部的关键事件：

| Event | 触发场景 |
|---|---|
| `llm.request.start` / `llm.request.end` | LLM 请求开始/结束 |
| `llm.retry` | LLM 请求重试 |
| `tool.call.start` / `tool.call.end` | 工具调用开始/结束 |
| `plan.generated` | 计划生成完成 |
| `reflection.complete` | 反思完成 |
| `replan.triggered` | 触发重新规划 |
| `node.state_transition` | DAG 节点状态转换 |

---

## 7. 自定义埋点（装饰器）

除了 Bridge 自动采集的事件外，你还可以使用装饰器为自定义代码添加埋点。

### 7.1 `@traced` — 通用方法追踪

```python
from tracing import traced

@traced("my_module.process_data")
async def process_data(input_data: str) -> str:
    """This method will be automatically traced."""
    result = do_something(input_data)
    return result

@traced("my_module.sync_operation")
def sync_operation(x: int) -> int:
    """同步方法同样支持。"""
    return x * 2
```

自动记录：
- Span 名称（自定义或自动推导 `{class_name}.{method_name}`）
- 执行耗时（`latency_ms`）
- 异常信息（`error.type`, `error.message`）
- 自定义静态属性

带自定义属性：

```python
@traced("data.transform", attributes={"data.format": "json", "data.version": "2.0"})
async def transform(data: dict) -> dict:
    return {"normalized": True, "payload": data}
```

### 7.2 `TRACING_ENABLED=false` 时的行为

当 Tracing 关闭时，`@traced` 装饰器退化为 **透传模式**，不创建 Span、不记录属性、不产生任何性能开销：

```python
# TRACING_ENABLED=false 时，traced 等价于：
def traced(span_name="", attributes=None):
    def decorator(func):
        return func  # 原封不动返回原函数
    return decorator
```

同样，`__init__.py` 中的 `init_tracing()`、`get_tracer()`、`shutdown_tracing()`、`TracingBridge` 也都替换为 no-op 桩，OpenTelemetry SDK 不会被加载。

---

## 8. 集成原理

### 8.1 双通道架构

Tracing 通过两个互补的通道收集数据：

```
通道 1: 事件桥接（TracingBridge）
  OrchestratorAgent._emit() → on_event() → TracingBridge.on_event()
  覆盖：任务生命周期事件（task_start, phase, node_running, reflection 等）

通道 2: 直接埋点（traced_execute / _start_llm_span）
  LLMClient.chat() → _start_llm_span() / _end_llm_span()
  BaseTool.traced_execute() → execute() with Span wrapping
  覆盖：LLM 调用细节、工具执行细节
```

### 8.2 TracingBridge 事件映射

Bridge 将现有事件流自动转换为 Span：

| 事件名 | 生成的 Span | 说明 |
|---|---|---|
| `task_start` | `task_execution`（根 Span） | 任务开始 |
| `phase: "Gathering context"` | `orchestrator.gather_context` | 上下文收集阶段 |
| `phase: "Classifying task complexity"` | `planner.classify_task` | 任务分类阶段 |
| `phase: "Planning (v2 hierarchical DAG)"` | `planner.create_dag` | DAG 规划 |
| `phase: "Planning (v1 simple flat plan)"` | `planner.create_plan` | 简单规划 |
| `phase: "Planning (v5 emergent via task list)"` | `planner.create_todo_list` | 涌现规划 |
| `phase: "Executing DAG (attempt 1)"` | `execution.dag` | DAG 执行 |
| `phase: "Executing simple plan"` | `execution.simple` | 简单执行 |
| `phase: "Reflecting on results"` | `reflector.reflect` | 反思阶段 |
| `node_running` | `node.execute.{id}` | 节点开始执行 |
| `node_complete` | — (结束 Span) | 节点执行完成 |
| `reflection` | 事件附加到当前 Span | 反思结果 |
| `task_complete` | — (结束根 Span) | 任务完成 |

### 8.3 多播事件分发

TracingBridge 通过多播模式与现有的 UI 回调、EvaluationProbe 共存：

```python
# agents/orchestrator.py 中的初始化
multicast = OrchestratorAgent._make_multicast(
    original_on_event,          # UI 回调
    tracing_bridge.on_event,    # Tracing 桥接
)
```

多播保证：
- 一个订阅者的异常不影响其他订阅者
- 所有订阅者都能收到完整的事件流

### 8.4 工具调用的 Tracing 入口

`BaseTool` 提供 `traced_execute()` 方法作为带 tracing 的执行入口：

```python
async def run_tool_with_tracing(tool, func_args: dict):
    # 所有执行器（ExecutorAgent / ReActEngine / EmergentPlannerAgent）
    # 使用 traced_execute 而非 execute：
    result = await tool.traced_execute(**func_args)
    return result

# traced_execute 内部逻辑：
# 1. TRACING_ENABLED=false → 直接调用 execute()
# 2. TRACING_ENABLED=true  → 创建 Span → 调用 execute() → 记录属性
```

这种模板方法模式保证了子类无需任何修改即可获得 tracing 能力。

---

## 9. 隐私与安全

### 9.1 默认安全策略

| 策略 | 默认行为 | 配置项 |
|---|---|---|
| Prompt / Response 内容 | **不记录** | `TRACING_LOG_PROMPTS=true` 显式开启 |
| 属性值截断 | 超过 1000 字符自动截断 | `TRACING_MAX_ATTR_LENGTH` |
| 敏感字段脱敏 | 包含 `api_key`/`password`/`token`/`secret`/`credential`/`authorization` 的属性值替换为 `[REDACTED]` | 内置，不可配置 |

### 9.2 敏感字段检测

`tracing/decorators.py` 中的 `_is_sensitive_key()` 函数会检测属性键名是否包含以下模式：

```python
SENSITIVE_KEYS = {
    "api_key", "api_secret", "token", "password",
    "credential", "secret", "authorization",
}
```

匹配时属性值被替换为 `[REDACTED]`，原始值不会被导出。

### 9.3 生产环境建议

```bash
TRACING_LOG_PROMPTS=false         # 绝不记录 prompt 内容
TRACING_MAX_ATTR_LENGTH=500       # 限制属性长度
TRACING_SAMPLE_RATE=0.1           # 10% 采样
```

---

## 10. 性能影响

### 10.1 关闭时（TRACING_ENABLED=false）

**零开销**。原理：

1. `tracing/__init__.py` 在模块加载时检查 `TRACING_ENABLED`
2. 如果为 `false`，导出的都是 no-op 桩（空函数、空类）
3. OpenTelemetry SDK 不会被加载，不会被 import
4. `BaseTool.traced_execute()` 直接委托给 `execute()`，只有一次 `if` 判断开销

### 10.2 开启时的开销

| 组件 | 开销 | 说明 |
|---|---|---|
| Bridge 事件处理 | ~0.01ms/事件 | 字典查找 + Span 创建 |
| Span 创建 | ~0.05ms/Span | OTel SDK 内部开销 |
| BatchSpanProcessor | 异步后台 | 不阻塞主线程 |
| FileSpanExporter | 磁盘 I/O | 批量写入，影响极小 |
| OTLP Exporter | 网络 I/O | 异步发送，不阻塞主流程 |

**典型场景**：一次包含 5 个 LLM 调用 + 3 个工具调用的复杂任务，Tracing 额外耗时 < 1ms。

### 10.3 采样率优化

生产环境建议设置采样率：

```bash
TRACING_SAMPLE_RATE=0.1   # 只追踪 10% 的请求
```

采样基于 `TraceIdRatioBased`，确保被采样的 Trace 包含完整的 Span 树。

---

## 11. 常见操作手册

### 11.1 分析 LLM 调用耗时

**目的**：找出哪些 LLM 调用最慢，识别性能瓶颈。

```bash
# 1. 启用 file 后端
TRACING_ENABLED=true TRACING_BACKEND=file python main.py

# 2. 分析 trace 文件中的 LLM spans
python3 -c "
import json, glob
for f in glob.glob('traces/*.json'):
    data = json.load(open(f))
    llm_spans = [s for s in data['spans'] if s['name'].startswith('llm.')]
    for s in sorted(llm_spans, key=lambda x: x['duration_ms'], reverse=True):
        model = s['attributes'].get('gen_ai.request.model', 'N/A')
        tokens = s['attributes'].get('gen_ai.usage.total_tokens', 'N/A')
        print(f'{s[\"name\"]:30s} {s[\"duration_ms\"]:8.1f}ms  model={model}  tokens={tokens}')
"
```

### 11.2 统计 Token 消耗

```python
import json, glob

total_input_tokens = 0
total_output_tokens = 0

for filepath in glob.glob("traces/*.json"):
    data = json.load(open(filepath))
    for span in data["spans"]:
        attrs = span.get("attributes", {})
        total_input_tokens += attrs.get("gen_ai.usage.input_tokens", 0)
        total_output_tokens += attrs.get("gen_ai.usage.output_tokens", 0)

print(f"Total input tokens:  {total_input_tokens}")
print(f"Total output tokens: {total_output_tokens}")
print(f"Total tokens:        {total_input_tokens + total_output_tokens}")
```

### 11.3 检查工具调用成功率

```python
import json, glob
from collections import Counter

tool_stats = Counter()
tool_errors = Counter()

for filepath in glob.glob("traces/*.json"):
    data = json.load(open(filepath))
    for span in data["spans"]:
        if span["name"].startswith("tool.execute"):
            tool_name = span["attributes"].get("tool.name", "unknown")
            tool_stats[tool_name] += 1
            if not span["attributes"].get("tool.success", True):
                tool_errors[tool_name] += 1

print("Tool call statistics:")
for tool, count in tool_stats.most_common():
    errors = tool_errors.get(tool, 0)
    success_rate = (count - errors) / count * 100
    print(f"  {tool}: {count} calls, {success_rate:.0f}% success")
```

### 11.4 查看完整的 Span 树

```python
import json

data = json.load(open("traces/YOUR_TRACE_FILE.json"))

# Build parent-child tree
children = {}
root = None
for span in data["spans"]:
    parent_id = span.get("parent_span_id")
    if parent_id is None:
        root = span
    else:
        children.setdefault(parent_id, []).append(span)

def print_tree(span, indent=0):
    status = "✅" if span["status"] == "OK" else "❌"
    print(f"{'  ' * indent}{status} {span['name']} ({span['duration_ms']:.1f}ms)")
    for child in children.get(span["span_id"], []):
        print_tree(child, indent + 1)

print_tree(root)
```

### 11.5 对接 Phoenix UI

```bash
# 1. 安装并启动 Phoenix
pip install arize-phoenix
python -m phoenix.server.main serve

# 2. 配置环境变量
TRACING_ENABLED=true
TRACING_BACKEND=phoenix
TRACING_ENDPOINT=http://localhost:6006

# 3. 运行任务
python main.py

# 4. 在浏览器中打开 Phoenix UI
open http://localhost:6006
```

在 Phoenix UI 中你可以：
- 查看 Span 时间线瀑布图
- 查看 LLM 调用的 token 用量统计
- 对比不同 Trace 的性能差异
- 搜索和过滤特定的 Span

---

## 12. 故障排查

### 12.1 Tracing 没有生效

**检查清单**：

1. 确认 `TRACING_ENABLED=true` 已设置（区分大小写，必须小写 `true`）
2. 确认 `.env` 文件在项目根目录下
3. 确认已安装 OpenTelemetry 依赖：
   ```bash
   pip install opentelemetry-api opentelemetry-sdk
   ```
4. 查看日志中是否有 `[Tracing] Initialized` 信息

### 12.2 File 后端没有生成文件

**可能原因**：
- `BatchSpanProcessor` 是异步批量导出（默认每 5 秒或缓冲区满 256 个 span 触发），短任务可能在退出前未触发导出
- 解决：确保程序正常退出（`main.py` 末尾会自动调用 `shutdown_tracing()` 刷新缓冲区）
- 如果手动终止了进程，缓冲区中未导出的 span 会丢失
- 或在代码末尾手动调用：
  ```python
  from tracing import shutdown_tracing
  shutdown_tracing()
  ```

### 12.2.1 File 后端文件内容不完整

**可能原因**：
- 任务执行时间较长，`BatchSpanProcessor` 可能会分多个 batch 导出同一 Trace 的 Span
- 文件命名为 `{trace_id}.json`，后续 batch 会自动读取已有文件并追加新 span
- 如果在导出期间程序异常终止，可能只有部分 span 被写入

**验证方法**：
```bash
# 检查 trace 文件中 span 数量是否与预期一致
python3 -c "
import json
data = json.load(open('traces/YOUR_TRACE_ID.json'))
print(f'Span count: {data[\"span_count\"]}')
print(f'Actual spans: {len(data[\"spans\"])}')
"
```

### 12.3 OTLP 连接失败

```
[Tracing] OTLP exporter not available, falling back to console.
```

**解决**：
```bash
pip install opentelemetry-exporter-otlp
```

如果是连接后端失败，检查 `TRACING_ENDPOINT` 是否正确且后端服务正在运行。

### 12.4 Rich 后端输出为纯文本

```
[RichConsoleExporter] 'rich' package not installed, falling back to print
```

**解决**：
```bash
pip install rich
```

### 12.5 "Overriding of current TracerProvider is not allowed"

多次调用 `init_tracing()` 或测试中多次设置 TracerProvider 会触发此警告。

**解决**：`init_tracing()` 是幂等的，重复调用不会出错（内部有 `_initialized` 守卫）。如果在测试中遇到，参考 `tests/test_tracing.py` 中的 `monkeypatch` 方案。

### 12.6 属性值被截断

```json
{"tool.parameters": "{\"query\": \"very long query content [truncated]"}
```

这是预期行为。调整 `TRACING_MAX_ATTR_LENGTH` 以增大截断长度：

```bash
TRACING_MAX_ATTR_LENGTH=5000
```

---

## 13. 与评测模块的关系

Tracing 和评测（Evaluation）是两个互补的可观察性模块：

| 维度 | Evaluation | Tracing |
|---|---|---|
| **时机** | 离线基准测试 | 运行时实时追踪 |
| **数据** | 指标聚合（正确率、Token 消耗） | 结构化 Span 树 + 时间线 |
| **目的** | 量化对比三种范式 | 诊断单次执行的性能瓶颈 |
| **集成方式** | `EvaluationProbe` 订阅事件 | `TracingBridge` 订阅事件 |
| **共存** | ✅ 可同时启用 | ✅ 可同时启用 |

两者共享同一套事件机制（`_emit`/`on_event` → 多播分发），互不干扰。

---

## 14. 扩展指南

### 14.1 添加新的导出后端

1. 在 `tracing/exporters.py` 中创建新类，继承 `SpanExporter`
2. 实现 `export(spans)`, `shutdown()`, `force_flush()` 方法
3. 在 `tracing/provider.py` 的 `_create_exporter()` 中注册新后端名称

```python
# tracing/exporters.py
class MyCustomExporter(SpanExporter):
    def export(self, spans):
        for span in spans:
            span_name = getattr(span, "name", "unknown")
            print(f"export span: {span_name}")
        return SpanExportResult.SUCCESS

# tracing/provider.py
def _create_exporter(backend):
    if backend == "my_custom":
        from tracing.exporters import MyCustomExporter
        return MyCustomExporter()
    return ConsoleSpanExporter()
```

### 14.2 添加新的事件处理

在 `tracing/bridge.py` 的 `TracingBridge` 类中：

1. 在 `__init__` 方法的 `self._event_handlers` 字典中注册新事件，将事件名映射到处理方法
2. 实现对应的处理方法

```python
# 在 __init__ 中添加条目
self._event_handlers: dict[str, Any] = {
    "task_start": self._on_task_start,
    # ... 已有映射 ...
    "my_new_event": self._on_my_new_event,
}

def _on_my_new_event(self, data):
    if self._phase_span:
        self._phase_span.add_event("my_event", attributes={"key": "value"})
```

### 14.3 添加新的 Span 名称或属性

在 `tracing/spans.py` 中添加常量：

```python
class SpanName:
    TASK_EXECUTION = "task_execution"
    MY_NEW_OPERATION = "my_module.new_operation"

class AttrKey:
    TASK_ID = "task.id"
    MY_NEW_ATTR = "my_module.new_attribute"
```

### 14.4 为新工具添加 Tracing

新工具只需继承 `BaseTool` 并实现 `execute()` 方法，Tracing 能力自动获得：

```python
class MyNewTool(BaseTool):
    @property
    def name(self) -> str:
        return "my_new_tool"

    async def execute(self, **kwargs) -> str:
        value = kwargs.get("param", "default")
        return f"result: {value}"

async def run_new_tool(tool: MyNewTool) -> str:
    # 执行器调用 traced_execute 即可自动追踪
    result = await tool.traced_execute(param="value")
    return result
```

---

## 15. Wave-6：按 caller 的 LLM Token 分账（v9.1）

### 15.1 新属性

每个 `llm.*` span 现在都带 `gen_ai.caller` 属性,标识发起该 LLM 调用的 agent。值由 LLMClient 三入口的命名参数 `caller_tag` 透传：

- `BaseAgent.think_*()` 自动用 `self.name`（PlannerAgent / Reflector / EmergentPlanner / SubAgent 等）
- `ReActEngine.execute()` 用 `self.agent_name`（ExecutorAgent / EmergentPlannerAgent / SubAgent-N）
- `GoalDrivenPlannerAgent._execute_todo_goal_guided` 显式传 `"GoalDrivenPlanner"`
- `SubAgent._summarize_result` 显式传 `self.name`

无 caller_tag 的旧路径会显示为缺失（默认空字符串）—— 任何没透传的位置都是潜在的"漏接"。

### 15.2 用例：按 caller 拆 token 看主-子分账

```bash
# 跑一次带 SubAgent + GoalDriven 的任务,持久化 trace
SUBAGENT_ENABLED=true ENABLE_GOAL_DRIVEN_PLANNER=true PLAN_MODE=emergent \
TRACING_ENABLED=true TRACING_BACKEND=file \
python main.py "复杂任务"
```

完成后用 Python 一行汇总：

```python
import json, glob, os
from collections import Counter

trace = sorted(glob.glob('traces/*.json'), key=os.path.getmtime)[-1]
data = json.load(open(trace))

caller_tokens = Counter()
for span in data.get('spans', []):
    if span.get('name', '').startswith('llm.'):
        caller = span['attributes'].get('gen_ai.caller', '(missing)')
        caller_tokens[caller] += span['attributes'].get('gen_ai.usage.total_tokens', 0)

for c, t in caller_tokens.most_common():
    print(f'{c:30s} {t:>6} tokens')
```

输出：

```
GoalDrivenPlanner               30092 tokens
SubAgent-1                       6377 tokens
SubAgent-2                       5696 tokens
```

加和等于 `task.*` span 上的 `task.token_total`(因为同一 LLMClient,同一 LLMCallRecord 数据源)。

### 15.3 用例：找漏接 caller_tag 的代码路径

如果 `gen_ai.caller` 出现 `(missing)` / 空字符串,说明某条 LLM 调用路径漏了 caller_tag 透传。grep:

```bash
# 找 trace 里的 (missing)
grep -o '"gen_ai.caller":"[^"]*"' traces/<id>.json | sort | uniq -c
```

或在 web viewer 里展开任意 `llm.*` span,看 `gen_ai.caller` 字段是否为空。然后 grep 源码：

```bash
# 找直接调 llm_client 但没传 caller_tag 的调用点
grep -rn "llm_client\.\(chat\|chat_with_tools\|chat_json\)" agents/ react/ tools/ \
  | grep -v "caller_tag"
```

应该只剩 BaseAgent 的内部封装（自动 setdefault `caller_tag=self.name`）。其它直接调用都应显式传值。

### 15.4 为何不放在 `**kwargs`

LLMClient 的 `chat` / `chat_with_tools` / `chat_json` 把 `caller_tag` 设计为**显式命名参数**。原因：内部最终会 `await self._client.chat.completions.create(..., **kwargs)` 把剩余 kwargs 透给 OpenAI SDK。如果 caller_tag 落进 kwargs,会被作为额外参数传给 OpenAI API,触发 `unknown_parameter` 错误。

也即:**新写直接调用 LLMClient 的代码时，必须把 `caller_tag` 作为命名参数传入,不可塞进 dict-style kwargs**。

### 15.5 与 SubAgent 父级归因的协同

每个 `subagent.execute.*` span 还有 `subagent.parent_agent` 属性（v9 既有）。Wave-6 后两个属性配合形成完整端到端归因证据：

- **谁派生了谁**：`subagent.parent_agent` 标识哪个 agent 派生了 SubAgent-N
- **谁花了多少 token**：`gen_ai.caller` 标识每次 LLM 调用归属哪个 agent

下面这个组合拳能在调试时回答「父-子整条链路花了多少 token,哪一段贵」：

```python
# 同一脚本,两个维度都看
for span in data.get('spans', []):
    name = span.get('name', '')
    attrs = span.get('attributes', {})
    if name.startswith('subagent.execute'):
        print(f'{name}: parent={attrs.get("subagent.parent_agent")}, '
              f'tokens={attrs.get("subagent.tokens_used")}')
    elif name.startswith('llm.'):
        print(f'  {name}: caller={attrs.get("gen_ai.caller")}, '
              f'tokens={attrs.get("gen_ai.usage.total_tokens")}')
```

---

> **相关文档**：
> - [Tracing 设计文档](./tracing-design.md) — 详细的架构设计和技术决策
> - [评测模块使用指南](./evaluation-guide.md) — 离线基准测试
> - [代码地图](./codemap.md) — 项目整体结构
> - [多智能体模式快速上手](./多智能体模式快速上手.md) — SubAgent 启用与归因证据
> - [更新日志](./CHANGELOG.md) — 版本变更记录
