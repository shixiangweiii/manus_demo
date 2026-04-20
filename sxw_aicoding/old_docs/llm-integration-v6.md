# Manus Demo v6.0 LLM Integration Manual
# Manus Demo v6.0 LLM 集成手册

> **版本**: v6.0
> **更新日期**: 2026-04-02
> **API 提供商**: DeepSeek (OpenAI-compatible)

---

## 目录

1. [LLM 集成概述](#1-llm-集成概述)
2. [环境配置](#2-环境配置)
3. [OpenAI SDK 使用模式](#3-openai-sdk-使用模式)
4. [LLM Client API 参考](#4-llm-client-api-参考)
5. [推理引擎升级 (v6.0)](#5-推理引擎升级-v60)
6. [测试验证](#6-测试验证)
7. [故障排查](#7-故障排查)
8. [执行命令参考](#8-执行命令参考)

---

## 1. LLM 集成概述

### 1.1 架构设计

Manus Demo 使用统一的 `LLMClient` 封装类，通过 OpenAI SDK 与各种 OpenAI-compatible API 提供商通信。

```
┌─────────────────────────────────────────────────────────┐
│                      OrchestratorAgent                   │
│                  (任务编排 + 路由决策)                   │
└─────────────────────────────┬───────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│ PlannerAgent  │    │ ExecutorAgent │    │EmergentPlanner│
│   (DAG)       │    │ (ReAct)      │    │  (v5)        │
└───────┬───────┘    └───────┬───────┘    └───────┬───────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │    LLMClient         │
                  │  (OpenAI SDK 封装)    │
                  └───────────┬───────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │  DeepSeek API         │
                  │  (OpenAI-compatible)  │
                  └───────────────────────┘
```

### 1.2 支持的 API 提供商

| 提供商 | Base URL | 状态 | 备注 |
|--------|----------|------|------|
| DeepSeek | `https://api.deepseek.com/v1` | ✅ 已配置 | 默认使用 |
| OpenAI | `https://api.openai.com/v1` | ✅ 兼容 | 需修改 .env |
| Ollama | `http://localhost:11434/v1` | ✅ 兼容 | 本地模型 |
| vLLM | 自定义 | ✅ 兼容 | 需配置 |

---

## 2. 环境配置

### 2.1 .env 文件配置

在项目根目录创建或编辑 `.env` 文件：

```bash
# LLM API 配置
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=deepseek-chat

# v6.0 Feature Flags (可选)
ENABLE_REACT_ENGINE_V2=false
LLM_RETRY_ENABLED=false
LLM_RETRY_MAX_ATTEMPTS=3
LLM_RETRY_BACKOFF_FACTOR=2.0
```

### 2.2 配置参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | API 端点地址 |
| `LLM_API_KEY` | - | API 密钥（必填） |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `ENABLE_REACT_ENGINE_V2` | `false` | 启用统一 ReActEngine |
| `LLM_RETRY_ENABLED` | `false` | 启用重试机制 |
| `LLM_RETRY_MAX_ATTEMPTS` | `3` | 最大重试次数 |
| `LLM_RETRY_BACKOFF_FACTOR` | `2.0` | 指数退避因子 |

### 2.3 验证配置

```bash
# 验证环境变量加载
cd /path/to/manus_demo
source .venv/bin/activate
python -c "import config; print(config.LLM_BASE_URL, config.LLM_MODEL)"
```

---

## 3. OpenAI SDK 使用模式

### 3.1 SDK 初始化

```python
from openai import AsyncOpenAI
from llm.client import LLMClient

# 方式 1: 使用 LLMClient (推荐)
client = LLMClient()

# 方式 2: 直接使用 AsyncOpenAI
client = AsyncOpenAI(
    base_url="https://api.deepseek.com/v1",
    api_key="sk-your-key",
)
```

### 3.2 核心 API 调用

#### 3.2.1 基础聊天补全

```python
resp = await client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "user", "content": "Hello!"}
    ],
    temperature=0.7,
    max_tokens=4096,
)
content = resp.choices[0].message.content
```

#### 3.2.2 带工具调用的聊天

```python
resp = await client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Use the calculator to add 2+2"}],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Performs basic arithmetic",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"]
                }
            }
        }
    ],
    tool_choice="auto",
    temperature=0.5,
)
# 访问工具调用
tool_calls = resp.choices[0].message.tool_calls
```

#### 3.2.3 JSON 模式响应

```python
resp = await client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Return JSON data"}],
    response_format={"type": "json_object"},
    temperature=0.3,
)
data = json.loads(resp.choices[0].message.content)
```

### 3.3 LLMClient 封装方法

| 方法 | 用途 | 返回类型 |
|------|------|----------|
| `chat(messages, temperature, max_tokens)` | 基础文本对话 | `str` |
| `chat_with_tools(messages, tools, ...)` | 带工具调用的对话 | `Message` 对象 |
| `chat_json(messages, ...)` | 结构化 JSON 输出 | `dict` |

---

## 4. LLM Client API 参考

### 4.1 类定义

```python
class LLMClient:
    """OpenAI-compatible API wrapper with optional retry support."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        retry_enabled: bool | None = None,
        max_retries: int | None = None,
        backoff_factor: float | None = None,
    )
```

### 4.2 方法签名

#### `async chat()`

```python
async def chat(
    self,
    messages: list[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> str
```

**参数**:
- `messages`: 消息列表 `[{"role": "user", "content": "..."}]`
- `temperature`: 随机性控制 (0=确定, 1=随机)
- `max_tokens`: 最大生成长度
- `**kwargs`: 传递给 API 的其他参数

**返回**: 助手回复的文本字符串

**示例**:
```python
response = await client.chat([
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is 2+2?"}
])
print(response)  # "2+2 equals 4."
```

#### `async chat_with_tools()`

```python
async def chat_with_tools(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> Any  # 返回 ChatCompletionMessage 对象
```

**参数**:
- `messages`: 消息历史
- `tools`: OpenAI 格式的工具定义列表
- `temperature`: 随机性控制
- `max_tokens`: 最大生成长度

**返回**: 包含 `tool_calls` 属性的消息对象

**示例**:
```python
tools = [{"type": "function", "function": {"name": "echo", "description": "...", "parameters": {...}}}]
response = await client.chat_with_tools(messages, tools)

if response.tool_calls:
    for call in response.tool_calls:
        print(f"Tool: {call.function.name}, Args: {call.function.arguments}")
```

#### `async chat_json()`

```python
async def chat_json(
    self,
    messages: list[dict[str, Any]],
    temperature: float = 0.3,
    max_tokens: int = 4096,
    **kwargs: Any,
) -> Any  # 解析后的 Python 对象
```

**参数**:
- `messages`: 消息列表
- `temperature`: 低随机性 (默认 0.3)
- `max_tokens`: 最大生成长度

**返回**: 解析后的 Python 对象 (dict/list)

**示例**:
```python
result = await client.chat_json([
    {"role": "user", "content": 'Return {"status": "ok", "count": 5}'}
])
print(result)  # {'status': 'ok', 'count': 5}
```

### 4.3 可重试的错误类型

```python
RETRYABLE_ERRORS = (
    RateLimitError,    # 速率限制
    APITimeoutError,   # 请求超时
    APIError,          # 通用 API 错误
)
```

### 4.4 v6.0 重试机制

```python
# 配置重试参数
client = LLMClient(
    retry_enabled=True,       # 启用重试
    max_retries=3,           # 最多重试 3 次
    backoff_factor=2.0,      # 指数退避: 1s, 2s, 4s
)

# 重试逻辑
# attempt=0: 立即执行
# attempt=1: 等待 2^0 * factor = 1s
# attempt=2: 等待 2^1 * factor = 2s
# attempt=3: 等待 2^2 * factor = 4s
```

---

## 5. 推理引擎升级 (v6.0)

### 5.1 ReAct Engine 架构

```
┌─────────────────────────────────────────────────────────┐
│                    ReActEngine                          │
│  (v6.0 统一 ReAct 执行引擎)                              │
├─────────────────────────────────────────────────────────┤
│  Core Loop:                                              │
│    while not done:                                      │
│      1. LLM generates response + potential tool_calls    │
│      2. If tool_calls:                                   │
│         - Execute each tool                             │
│         - Record observation                            │
│         - Add tool results to messages                  │
│      3. Else:                                           │
│         - Return final answer                          │
└─────────────────────────────────────────────────────────┘
```

### 5.2 ReActEngine API

```python
from react.engine import ReActEngine

engine = ReActEngine(
    llm_client=llm_client,
    tools=[tool1, tool2, ...],
    max_iterations=10,
    tool_router=tool_router,
)

result = await engine.execute(
    prompt="Task description",
    context="Previous context",
    node_id="unique_node_id",
    system_hint="System-level instructions",
)
```

### 5.3 返回值

```python
class StepResult:
    step_id: str
    success: bool
    output: str           # 最终输出文本
    tool_calls_log: list[ToolCallRecord]  # 工具调用记录
```

---

## 6. 测试验证

### 6.1 测试套件

| 测试文件 | 覆盖范围 | 测试数量 |
|----------|----------|----------|
| `tests/test_llm_integration.py` | LLM 集成完整测试 | 27 |
| `tests/test_emergent_planning.py` | 隐式规划功能 | 16 |
| `tests/test_dag_capabilities.py` | DAG 执行能力 | 31 |
| `tests/test_cycle_detection.py` | 循环检测 | 2 |

### 6.2 运行测试

```bash
# 激活虚拟环境
source .venv/bin/activate

# 运行所有 LLM 集成测试
pytest tests/test_llm_integration.py -v

# 运行特定类别测试
pytest tests/test_llm_integration.py -k "chat" -v      # 聊天功能
pytest tests/test_llm_integration.py -k "tools" -v    # 工具调用
pytest tests/test_llm_integration.py -k "retry" -v     # 重试机制
pytest tests/test_llm_integration.py -k "react" -v     # ReAct 引擎

# 运行核心功能测试
pytest tests/test_emergent_planning.py tests/test_dag_capabilities.py -v

# 生成测试覆盖率报告
pytest tests/test_llm_integration.py --cov=llm --cov=react -v
```

### 6.3 验证清单

- [ ] LLM Client 初始化成功
- [ ] 环境变量正确加载
- [ ] 基础聊天功能正常
- [ ] 工具调用功能正常
- [ ] JSON 输出功能正常
- [ ] ReAct Engine 功能正常
- [ ] Feature Flags 默认值正确

---

## 7. 故障排查

### 7.1 常见问题

#### 问题 1: API Key 无效

```
AuthenticationError: Incorrect API key provided
```

**解决**:
1. 检查 `.env` 文件中的 `LLM_API_KEY`
2. 确认 API Key 在 DeepSeek 控制台有效
3. 验证格式: `sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

#### 问题 2: 速率限制

```
RateLimitError: Rate limit reached
```

**解决**:
1. 启用重试机制: `LLM_RETRY_ENABLED=true`
2. 增加延迟: `LLM_RETRY_BACKOFF_FACTOR=3.0`
3. 减少请求频率

#### 问题 3: 模型不支持

```
BadRequestError: model not found
```

**解决**:
1. 确认 `LLM_MODEL` 名称正确
2. DeepSeek 支持: `deepseek-chat`, `deepseek-coder`

#### 问题 4: 连接超时

```
APITimeoutError: Request timed out
```

**解决**:
1. 检查网络连接
2. 增加超时时间
3. 启用重试机制

### 7.2 日志调试

```bash
# 启用调试日志
python main.py --verbose

# 查看 LLM 调用日志
export LOG_LEVEL=DEBUG
python -c "from llm.client import LLMClient; import logging; logging.basicConfig(level=logging.DEBUG); ..."
```

---

## 8. 执行命令参考

### 8.1 环境设置

```bash
# 1. 创建虚拟环境 (如不存在)
python -m venv .venv

# 2. 激活虚拟环境
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 添加 API Key

# 5. 验证配置
python -c "import config; print(config.LLM_API_KEY[:10] + '...')"
```

### 8.2 测试执行

```bash
# 运行完整测试套件
pytest tests/test_llm_integration.py tests/test_emergent_planning.py tests/test_dag_capabilities.py -v

# 运行 LLM 集成验证测试
pytest tests/test_llm_integration.py -v

# 运行单个测试
pytest tests/test_llm_integration.py::TestLLMClientChat::test_chat_basic -v

# 运行带详细输出的测试
pytest tests/test_llm_integration.py -v --tb=long -s
```

### 8.3 应用程序运行

```bash
# 交互模式
python main.py

# 单任务模式
python main.py "分析这段代码的复杂度"

# 强制使用特定规划模式
PLAN_MODE=simple python main.py "简单任务"
PLAN_MODE=complex python main.py "复杂任务"
PLAN_MODE=emergent python main.py "探索性任务"

# 启用 v6.0 新特性
ENABLE_REACT_ENGINE_V2=true python main.py
LLM_RETRY_ENABLED=true python main.py

# 组合使用
ENABLE_REACT_ENGINE_V2=true LLM_RETRY_ENABLED=true python main.py "使用增强引擎的任务"

# 调试模式
python main.py --verbose "调试任务"
```

### 8.4 快速验证脚本

```bash
# 创建验证脚本
cat > validate_llm.sh << 'EOF'
#!/bin/bash
set -e

echo "=== LLM Integration Validation ==="
echo

echo "1. Checking virtual environment..."
source .venv/bin/activate
python --version

echo
echo "2. Checking environment variables..."
python -c "
import config
print(f'  BASE_URL: {config.LLM_BASE_URL}')
print(f'  MODEL: {config.LLM_MODEL}')
print(f'  API_KEY: {config.LLM_API_KEY[:10]}...')
"

echo
echo "3. Testing LLM Client initialization..."
python -c "from llm.client import LLMClient; c = LLMClient(); print('  LLMClient initialized successfully')"

echo
echo "4. Testing ReAct Engine import..."
python -c "from react.engine import ReActEngine; print('  ReActEngine imported successfully')"

echo
echo "5. Running quick API test..."
python -c "
import asyncio
from llm.client import LLMClient

async def test():
    c = LLMClient()
    r = await c.chat([{'role': 'user', 'content': 'Say OK'}])
    print(f'  Response: {r}')

asyncio.run(test())
"

echo
echo "=== Validation Complete ==="
EOF

chmod +x validate_llm.sh
./validate_llm.sh
```

---

## 附录 A: 测试输出示例

```
============================= test session starts ==============================
platform darwin -- Python 3.12.10, pytest-9.0.2
tests/test_llm_integration.py::TestLLMClientInitialization::test_default_initialization PASSED
tests/test_llm_integration.py::TestLLMClientInitialization::test_custom_initialization PASSED
tests/test_llm_integration.py::TestLLMClientInitialization::test_environment_variable_loading PASSED
tests/test_llm_integration.py::TestLLMClientChat::test_chat_basic PASSED
tests/test_llm_integration.py::TestLLMClientChat::test_chat_with_system_prompt PASSED
tests/test_llm_integration.py::TestLLMClientFunctionCalling::test_chat_with_tools_single_tool PASSED
tests/test_llm_integration.py::TestReActEngine::test_react_engine_simple_task PASSED
...
======================== 27 passed in 155.57s ========================
```

---

## 附录 B: API 响应示例

### Chat Response
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Hello! How can I help you today?"
    }
  }]
}
```

### Tool Call Response
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_xxx",
        "type": "function",
        "function": {
          "name": "echo",
          "arguments": "{\"text\": \"Hello\"}"
        }
      }]
    }
  }]
}
```

---

**手册版本**: v6.0
**最后更新**: 2026-04-02
**维护者**: Manus Demo Team
