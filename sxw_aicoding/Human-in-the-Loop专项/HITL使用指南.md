# HITL (Human-in-the-Loop) 使用指南

> 版本：v13.0 | 日期：2026-05-16

## 1. 快速开始

### 1.1 启用 HITL

HITL 默认关闭（向后兼容），需通过环境变量启用：

```bash
# 交互模式（推荐）
HITL_ENABLED=true python main.py

# 单任务模式（ask_user 在此模式下自动禁用，返回 Error 让 agent 自主继续）
HITL_ENABLED=true python main.py "今天天气怎么样"

# 同时启用 SubAgent + HITL
HITL_ENABLED=true SUBAGENT_ENABLED=true python main.py

# 强制 simple 路径 + HITL（适合调试）
HITL_ENABLED=true PLAN_MODE=simple python main.py
```

### 1.2 典型交互场景

**场景 1：天气查询（城市确认）**

```
You > 今天天气怎么样

[Agent 正在执行...]
  → 调用 get_user_location → 返回 "上海 (APPROXIMATE, via IP)"
  → 调用 web_search("上海今天天气") → 返回上海天气
  → 调用 ask_user("我通过 IP 定位发现您在上海，这是正确的吗？
    如果不是，请告诉我您想查询哪个城市的天气。")

╭─────────── Agent Asks ───────────╮
│ 我通过 IP 定位发现您在上海，      │
│ 这是正确的吗？如果不是，请告诉   │
│ 我您想查询哪个城市的天气。        │
╰───────────────────────────────────╯
You > 北京

  Response sent to agent.
  → 调用 web_search("北京今天天气") → 返回北京天气
  → 综合回答（使用用户确认的城市）

╭────────── Final Answer ──────────╮
│ 北京今天天气：小雨，22-29°C...    │
╰───────────────────────────────────╯
```

**场景 2：任务方向确认**

```
You > 帮我分析一下最近的AI行业新闻

[Agent 执行中...]
  → 调用 ask_user("我找到了很多AI行业新闻，您更关心哪个方向？
    1. 大模型技术进展
    2. AI创业融资
    3. AI监管政策
    请选择或描述您感兴趣的方向。")

You > 3

  → 调用 web_search("AI监管政策 最新新闻") → 返回相关新闻
```

### 1.3 非交互模式行为

在单任务模式（`python main.py "任务"`）中，ask_user 工具虽然注册在工具列表中（LLM 能看到），但调用时立即返回 Error：

```
Error: ask_user is not available in non-interactive mode.
Proceed with your best judgment using available tools.
```

LLM 收到此错误后会改为自主推理，不会卡住。

## 2. 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HITL_ENABLED` | `false` | 总开关。设为 `true` 启用 |
| `HITL_MAX_PROMPTS_PER_TASK` | `5` | 每个任务中 ask_user 的最大调用次数。超过后返回 Error，agent 自主继续 |
| `HITL_USER_INPUT_TIMEOUT` | `120` | 等待用户输入的超时时间（秒）。超时后 agent 自主继续 |

### 2.1 推荐配置

```bash
# .env 文件中的推荐配置
HITL_ENABLED=true
HITL_MAX_PROMPTS_PER_TASK=5
HITL_USER_INPUT_TIMEOUT=120
```

### 2.2 调试配置

```bash
# 限制提问次数，测试 agent 自主推理的兜底能力
HITL_ENABLED=true HITL_MAX_PROMPTS_PER_TASK=2 python main.py

# 缩短超时，测试超时处理
HITL_ENABLED=true HITL_USER_INPUT_TIMEOUT=10 python main.py
```

## 3. 事件类型

HITL 引入了 3 个新事件类型，用于 UI 渲染和追踪：

| 事件 | 数据结构 | 说明 |
|------|----------|------|
| `ask_user_prompt` | `{"question": str, "prompt_id": str, "response_future": Future}` | Agent 向用户提问。UI 展示提问面板并收集输入 |
| `ask_user_response` | `{"prompt_id": str, "response": str, "prompt_count": int}` | 用户已回复（info 日志） |
| `ask_user_timeout` | `{"prompt_id": str, "timeout": int, "prompt_count": int}` | 用户超时未回复（warning 提示） |

## 4. 工具参数

### ask_user 工具

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | 是 | 向用户提问的问题文本。应具体、简洁，包含已知上下文 |

**LLM 生成的提问示例**：

```json
// 好的提问：包含已知上下文
{"question": "我通过 IP 定位发现您在上海，这是正确的吗？如果不是，请告诉我您想查询哪个城市的天气。"}

// 好的提问：提供选项
{"question": "您希望我使用哪种方式分析？1. 技术角度 2. 商业角度 3. 政策角度"}

// 差的提问：过于笼统
{"question": "请问？"}
```

## 5. 与其他功能的交互

### 5.1 与 SubAgent 的关系

- ask_user 工具从 SubAgent 白名单中**结构性排除**（与 subagent 工具并列）
- SubAgent 运行在隔离环境，不应直接与用户交互
- 如果 SubAgent 遇到歧义，应在 structured summary 的 `issues` 字段中报告
- 父 Agent 收到 summary 后，可以决定是否调用 ask_user 向用户确认

### 5.2 与 Reflector 的关系

- Reflector 的"禁止未授权假设"规则（#5）仍然生效
- 新增软引导：当 ask_user 可用但步骤未使用它来消除歧义时，在 suggestions 中提示
- 这不是硬性失败（passed 不会因此变 false），因为 LLM 不使用 ask_user 可能有合理原因

### 5.3 与 ToolRouter 的关系

- ask_user 返回的 `Error:` 前缀字符串被 ReActEngine 检测为工具失败
- ToolRouter 记录 failure，连续失败达到阈值后建议切换
- 对于 ask_user 来说，这意味着如果连续多次超时/不可用，ToolRouter 会提示 LLM 不要再调用

### 5.4 与 Tracing 的关系

- `BaseTool.traced_execute()` 正常包装 ask_user 的执行
- Span 中记录 `tool.name=ask_user`、`tool.success`、`latency_ms` 等
- `response_future` 不会被序列化到 Span 属性中（AskUserPrompt 中 exclude=True）
- 超时返回的 Error 字符串会被标记为 `tool.success=False`

## 6. 用户操作

### 6.1 正常输入

当看到 `Agent Asks` 面板时，在 `You >` 提示符后输入回答并按 Enter。

### 6.2 取消输入

- 按 **Ctrl+C**：Agent 收到 `"(user cancelled)"`，改为自主继续
- 按 **Ctrl+D** (EOF)：同上

### 6.3 超时

- 默认 120 秒内未输入：Agent 收到超时 Error，改为自主继续
- 可通过 `HITL_USER_INPUT_TIMEOUT` 调整超时时间

### 6.4 空输入

- 直接按 Enter（空输入）：Agent 收到 `"(no response)"`

## 7. 故障排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Agent 从不调用 ask_user | `HITL_ENABLED` 未设为 true | 设置环境变量 `HITL_ENABLED=true` |
| Agent 调用 ask_user 但立即返回 Error | 在非交互模式（run_single）下运行 | 使用交互模式 `python main.py` |
| Agent 反复调用 ask_user | 超过最大次数后自动停止 | 检查 `HITL_MAX_PROMPTS_PER_TASK`，默认 5 |
| 等待用户输入时卡住 | 用户未输入且超时未触发 | 检查 `HITL_USER_INPUT_TIMEOUT`，默认 120s |
| 系统提示词中没有 HITL 引导 | `build_system_prompt` 未传 `inject_hitl_guidance=True` | 默认为 True，检查是否有代码覆盖 |
