# Agent 推理框架整体重构记录

> 日期：2026-08-01
>
> 状态：代码重构完成，编译及导入通过；真实引擎行为与评测质量待本地验证。

## 一、重构背景

本项目是用于自学 Agent 开发、比较不同推理与编排方式的本地练习项目，不面向生产环境。随着功能持续增加，原有实现逐渐出现以下问题：

- 推理引擎使用 `V1/V2/...` 版本号命名，版本差异与实际行为不对应。
- “评测”与“评测平台”并存，模型、入口、存储和报告逻辑重复。
- 环境变量同时承担秘密、普通配置和功能开关，数量过多且优先级不清晰。
- CLI、WebUI、Tracing 和评测分别理解运行阶段，存在通过日志文本推断引擎状态的逻辑。
- `schema.py`、旧编排分支、历史结果及文档长期累积，代码语义与当前实现发生偏离。
- A2A、远程 Agent、Subagent、记忆、知识库、技能、自演化、Guardrails、Checkpoint 等外围能力直接渗入主流程，继续扩展会进一步增加维护成本。

本次重构允许破坏旧配置、旧检查点和旧评测结果的兼容性，目标是获得一套适合阅读、实验和后续扩展的朴素架构。

## 二、范围与设计原则

本轮优先治理“引擎、动作执行、基础工具、Tracing、CLI、WebUI、统一评测”。外围能力保留，通过工具注册、运行上下文和生命周期钩子接入，不深入重写其内部算法。

主要原则如下：

1. 以行为命名，不以演进版本命名。
2. 引擎负责任务编排，执行器负责完成单个动作，两者独立选择。
3. `.env` 只保存秘密，`settings.toml` 保存普通配置，CLI 只覆盖单次运行。
4. 所有宿主订阅同一个结构化事件源，不解析展示文本判断状态。
5. 删除已经被替代的代码和文档，不长期维护两套核心路径。
6. 使用直接、易读的 Python 代码，避免不必要的抽象和语法技巧。
7. 本轮不新增单元测试，不执行真实 LLM、联网任务或正式评测。

## 三、实施方案

### 1. 配置与公共契约

建立 `core/`，集中定义 `TaskRequest`、`Action`、`ActionResult`、`EngineResult`、`ToolInvocation`、`EventBus` 和 dataclass 配置。配置优先级固定为：代码默认值 → `settings.toml` → `.env` 秘密 → CLI 覆盖。保留精简的 `config.py` 作为外围模块的临时只读门面。

### 2. 引擎、执行器与运行时

统一接口为：

```text
TaskEngine.run(TaskRequest) -> EngineResult
ActionExecutor.execute(Action, context, effort) -> ActionResult
AgentRuntime.run(task, overrides=None) -> EngineResult
```

引擎按行为划分为 `sequential`、`dag`、`todo`、`goal`、`workflow`；动作执行器划分为 `react`、`thinking`。自动路由依次判断显式选择、目标与完成条件、探索性任务、依赖或并行任务，最后回退到 Sequential。Workflow 只允许显式调用。

### 3. 工具、事件及宿主

通过 `ToolRegistry` 和 `build_default_tools()` 集中装配工具。`EventBus` 统一发布任务、引擎、动作、工具、错误与完成事件；终端、Tracing、WebUI 和评测从相同事件流获取状态。`main.py` 仅保留入口，具体命令迁入 `cli.py`。

### 4. 统一评测平台

将原 `evalplatform/` 的文档生成、存储、服务端和静态页面迁入 `evaluation/`。统一入口提供 `run`、`serve`、`upload`、`generate`、`report`、`analyze` 和 `list` 子命令。每个矩阵单元复制独立配置并使用独立沙箱，不修改模块级配置。

### 5. 清理与验收

删除旧版本引擎分支、重复模型、失效评测模块、已提交历史结果、旧基线、pytest 配置和过期文档。最后只进行编译、导入、帮助入口、dry-run、静态引用及 Git whitespace 检查。

## 四、实际改动记录

### 统一配置与模型

- 新增 `core/models.py`、`core/settings.py`、`core/events.py`。
- 新增并启用 `settings.toml`；`.env.example` 仅保留 `LLM_API_KEY`、`DASHSCOPE_API_KEY`、`AGENTBAY_API_KEY`。
- 配置采用严格字段和类型校验；未知字段、非法端口、空路径、错误依赖关系、无效 MCP/远程 Agent JSON 均在启动阶段失败。
- 删除无效的 Subagent 事件配置、偏好学习开关及旧版本选择变量。
- 将原 `schema.py` 拆分到 `core/`、`execution/`、`engines/`、`dag/`、`agents/`、`memory/` 等实际归属模块，并删除原文件。

### 引擎与执行器

- 新增 `SequentialPlanEngine`、`DagEngine`、`TodoEngine`、`GoalEngine`、`WorkflowEngine`。
- 新增 `ReactActionExecutor` 与 `ThinkingAwareActionExecutor`。
- TODO 与 Goal 的动作统一委托给 `ActionExecutor`，不再维护独立工具执行循环。
- `EffortPolicy` 与引擎选择解耦；Thinking 自动选择只读取 `llm.supports_reasoning`。
- Workflow 增加空步骤、重复 ID、缺失依赖、依赖环、无效 `final_step`、未知模板引用和无依赖模板引用校验。

### 运行时与外围能力

- 新增 `runtime/context.py`、`runtime/factory.py`、`runtime/app.py`。
- Subagent、Handoff、远程 Agent、MCP、AgentBay、记忆、技能、自演化、Guardrails 和 Checkpoint 通过工厂装配。
- 新 Checkpoint 只保存语义化引擎、执行器、effort 和任务边界；旧格式不迁移。
- 修复准备阶段异常导致 Checkpoint 永久停留在 `running` 的问题。
- Workflow 现在同样经过记忆、自演化、输出 Guardrail 和统一完成事件。

### 工具与安全边界

- 新增 `ToolRegistry.register/get/require/schemas/values/as_dict`。
- 工具调用参数必须是合法 JSON 对象；解析失败时不再用空参数继续执行。
- 工具事件、调用记录和 Workflow 参数统一脱敏 API Key、Token、Password 等字段。
- Guardrail 导入、输入检查、输出检查及技能内容检查失败时改为明确阻断，不再静默放行。
- 文件、Shell、Python、Workflow 等错误输出统一采用可识别的 `Error:` 语义。
- MCP 与远程 Agent 配置增加字段白名单和类型校验；AgentBay 日志级别配置正式接入 SDK logger。

### Tracing、CLI 与 WebUI

- Tracing 装配收口到 `runtime/factory.py`，CLI、WebUI、评测和直接构造 Runtime 使用同一路径。
- `TracingBridge` 使用结构化事件创建任务、引擎、动作和工具 span，不再根据版本文本判断阶段。
- 修复引擎 span 延迟关闭、失败任务标记错误、上下文未释放和 Trace 文件符号链接越界读取问题。
- `tracing.log_prompts=false` 时不写入任务、计划、工具和模型正文，只保留结构、身份、耗时与状态。
- `engine_completed` 与 `task_completed` 只广播必要摘要和经过 Guardrail 的最终答案，不暴露完整内部轨迹。
- CLI 统一为 `chat`、`run`、`workflow`、`mcp-server`、`tasks`、`resume`。
- WebUI 统一通过 `AgentRuntime` 执行任务，并修复取消、关闭会话和失败结果状态处理。

### 统一评测

- 删除 `evalplatform/`，将其能力迁入 `evaluation/`。
- 新增统一题库模型、文档摄取、题集生成、运行存储、报告、分析、API 和静态页面。
- 结果默认保存到 `~/.manus_demo/evaluation`；仓库不再保存生成结果。
- 修复存储 ID 与 Verifier 文件路径穿越风险。
- 空正则、空 JSON 字段、空组合验证器、非法数值区间等配置现在明确失败，避免假阳性。
- 自动选择准确率只统计 `engine=auto` 的实验。
- `repeat=1` 时稳定性显示为未定义；同一 case 至少重复两次后才计算稳定性。
- 报告分别展示成功率、Verifier、Token、延迟、工具次数、迭代、重规划、稳定性和自动选择准确率，不计算替代这些维度的综合分。

### 删除与文档收敛

- 删除旧 `evalplatform/`、旧评测 CLI/Runner/基线/结果、旧引擎入口和重复模型。
- 删除 `conftest.py`、pytest 文档和 requirements 中的 pytest 依赖；不恢复已删除测试目录。
- 从 Git 移除 `.env`、日志和历史生成结果，并补充 `.gitignore`。
- 当前维护文档收敛为 `README.md`、`AGENTS.md`、`docs/architecture.md`、`docs/engines.md`、`docs/configuration.md`、`docs/evaluation.md` 及本文档。
- 外围能力的历史研究材料暂时保留，留待对应模块后续专项治理。

## 五、代码审查追加修复

整体实现完成后又进行了一轮全仓代码审查，额外修复了以下问题：

- `LLMClient.from_settings()` 仍读取全局配置，破坏评测配置隔离。
- LLM 重试范围包含普通 4xx 与鉴权错误，造成无意义重试。
- 异步事件订阅在无事件循环或后台任务失败时处理不完整。
- 引擎失败仍将 Checkpoint 标记为完成。
- Goal 已满足但仍有剩余 TODO 时被错误判定为失败。
- 并行 Subagent 的动作结果未进入统一 `EngineResult.actions`。
- Workflow 失败步骤、真实参数和工具调用没有完整记录。
- WebUI 关闭期间可能遗留运行任务、Future 或错误的运行状态。
- Trace 查看器对损坏 JSON、目录外符号链接和异常字段缺少防御。
- 单次评测被错误报告为完全稳定，显式引擎实验被计入自动路由准确率。

## 六、验证记录

使用指定虚拟环境完成以下离线验证：

```bash
/Users/shixiangweii/PycharmProjects/manus_demo/.venv/bin/python -m compileall -q .
/Users/shixiangweii/PycharmProjects/manus_demo/.venv/bin/python -B -c "import core, runtime, engines, execution, evaluation, tracing, webui"
/Users/shixiangweii/PycharmProjects/manus_demo/.venv/bin/python -B main.py --help
/Users/shixiangweii/PycharmProjects/manus_demo/.venv/bin/python -B -m evaluation --help
/Users/shixiangweii/PycharmProjects/manus_demo/.venv/bin/python -B -m webui --help
/Users/shixiangweii/PycharmProjects/manus_demo/.venv/bin/python -B -m evaluation run --dry-run --cases factorial
git diff --check
git diff --cached --check
```

另外完成了 153 个保留模块逐一导入，以及 EventBus、引擎选择、Workflow、工具参数解析、Checkpoint、配置校验、Verifier、Trace 路径和评测指标的离线契约冒烟检查。静态搜索确认当前治理范围内不存在旧 `evalplatform` 引用、Vx 引擎名称、废弃选择变量或评测模块级配置写入。

## 七、交付状态与后续验证

当前状态为：**代码重构完成、编译及导入通过**。

以下内容未在本轮宣称通过：

- 真实模型下五种引擎的任务完成质量。
- React 与 Thinking 执行器的模型兼容性和成本差异。
- 正式评测矩阵、稳定性与自动路由准确率。
- WebUI 浏览器交互与 Trace 页面视觉效果。
- MCP、A2A、远程 Agent、AgentBay、知识库、记忆、技能和自演化的外部集成。
- 旧配置、旧 Checkpoint 和旧评测结果的兼容性。

后续应由本地正式评测分别验证“代码可运行”“实验可执行”“质量门槛通过”和“适合成为默认配置”，不要用编译通过或单次无报错替代质量结论。
