# AgentBay 集成变更记录

日期：2026-05-30

## 背景

本次变更基于 `agentbay_research` 目录中的官方文档调研，以及三个已实际验证通过的 Python SDK 示例：

- `00_verify_session.py`：默认 Session 创建与删除成功。
- `01_codespace_run_python.py`：CodeSpace 远程执行 Python 成功。
- `02_browser_playwright_cdp.py`：BrowserUse 通过 Playwright CDP 打开阿里云官网成功。

验证后将 AgentBay 作为可选云端执行运行时接入当前 `manus_demo` 工具体系，默认关闭，按需通过环境变量启用。

## 新增能力

### AgentBay 原生工具子包

新增目录：`tools/agentbay/`

包含：

- `runtime.py`
  - 懒加载 `wuying-agentbay-sdk`，避免未启用时引入 SDK 副作用。
  - 在导入 SDK 前设置 `AGENTBAY_LOG_LEVEL=WARNING`，降低默认日志噪声和敏感链接泄露风险。
  - 统一创建 `CreateSessionParams`，附加 labels：
    - `project=manus_demo`
    - `owner=agentbay_tool`
    - `tool=<tool_name>`
  - 使用 `LifecyclePolicy` 设置 Session 自动释放兜底。
  - SDK 同步调用通过 `asyncio.to_thread()` 包装，避免阻塞 ReAct 事件循环。
  - 使用全局 Semaphore 限制 AgentBay 并发 Session。

- `code_tool.py`
  - 新增 BaseTool：`agentbay_code`
  - 使用 AgentBay CodeSpace 远程执行代码。
  - 支持参数：
    - `code`
    - `language`: `python` / `javascript`
    - `timeout_s`: 强制限制在 `1..60`
  - 保证创建 Session 后无论成功失败都尝试删除。
  - 返回结果中包含 `session_deleted: true/false`，便于确认是否残留云端资源。

- `browser_tool.py`
  - 新增 BaseTool：`agentbay_browser`
  - 使用 AgentBay BrowserUse + Playwright CDP 操作云端浏览器。
  - 支持参数：
    - `url`
    - `operation`: `title` / `text` / `screenshot`
    - `selector`
    - `full_page`
    - `timeout_ms`
    - `max_chars`
  - 阻止访问本地、内网、回环、保留地址。
  - 截图保存到 `SANDBOX_DIR/agentbay_screenshots/`。
  - 不把 `resource_url`、CDP endpoint 或 authcode 返回给 LLM。

- `cleanup_sessions.py`
  - 新增运维脚本：
    - 默认只列出 `project=manus_demo, owner=agentbay_tool` 的匹配 Session。
    - 只有传入 `--delete` 才执行删除。
  - 用于调试阶段确认是否存在残留运行中的 AgentBay Session。

### 工具注册

修改 `main.py` 的 `_build_tools()`：

- 默认不注册 AgentBay 工具。
- 只有满足以下条件才注册：
  - `AGENTBAY_ENABLED=true`
  - `AGENTBAY_API_KEY` 非空
- 可单独控制：
  - `AGENTBAY_CODE_TOOL_ENABLED`
  - `AGENTBAY_BROWSER_TOOL_ENABLED`

这样可以避免 LLM 看到一个没有 API Key、调用必失败的工具。

### 配置项

修改 `config.py`，新增：

```text
AGENTBAY_ENABLED
AGENTBAY_API_KEY
AGENTBAY_CODE_TOOL_ENABLED
AGENTBAY_BROWSER_TOOL_ENABLED
AGENTBAY_LOG_LEVEL
AGENTBAY_MAX_CONCURRENT_SESSIONS
AGENTBAY_CODE_IMAGE
AGENTBAY_BROWSER_IMAGE
AGENTBAY_SESSION_IDLE_RELEASE_MINUTES
AGENTBAY_SESSION_MAX_RUNTIME_MINUTES
AGENTBAY_CODE_TIMEOUT_SECONDS
AGENTBAY_BROWSER_TIMEOUT_MS
```

修改 `.env.example`，新增同名配置说明。

### 依赖固定

修改 `requirements.txt`，固定本次已验证版本：

```text
wuying-agentbay-sdk==0.21.0
playwright==1.60.0
```

固定原因：

- 官方 BrowserUse 示例中的 `BrowserOption` 导入路径与 SDK `0.21.0` 实际结构存在差异。
- 当前已验证写法是从顶层导入：

```python
from agentbay import AgentBay, BrowserOption, CreateSessionParams
```

## 安全与 Guardrails

修改 `guardrails/tool_guardrail.py`：

- `agentbay_code` 复用 `execute_python` 的危险 Python 模式检查。
- `agentbay_browser` 阻止：
  - 非 `http/https` URL
  - `localhost`
  - `0.0.0.0`
  - `.local`
  - private / loopback / link-local / reserved / multicast IP

修改 `guardrails/input_guardrail.py`：

- 将 `agentbay_browser` 输出归类为不可信外部内容。
- 开启 guardrails 时，浏览器返回内容会像 `web_search` / `fetch_url` 一样进入间接提示注入检测与中和流程。

## 测试

新增测试文件：`tests/test_agentbay_tools.py`

覆盖：

- `agentbay_code` 成功执行并删除 Session。
- `agentbay_code` 执行异常仍删除 Session。
- `agentbay_browser` 成功执行并删除 Session。
- `agentbay_browser` URL 校验阻止本地和内网地址。
- guardrails 覆盖 `agentbay_code` 和 `agentbay_browser`。
- `_build_tools()` 在启用且有 API Key 时注册 AgentBay 工具。
- `_build_tools()` 在缺少 API Key 时跳过 AgentBay 工具。

已通过验证命令：

```bash
.venv/bin/python -m py_compile config.py main.py guardrails/tool_guardrail.py guardrails/input_guardrail.py tools/__init__.py tools/agentbay/__init__.py tools/agentbay/runtime.py tools/agentbay/code_tool.py tools/agentbay/browser_tool.py tools/agentbay/cleanup_sessions.py tests/test_agentbay_tools.py

.venv/bin/python -m pytest tests/test_agentbay_tools.py -q -o asyncio_mode=auto
# 7 passed

.venv/bin/python -m pytest tests/test_engine_helpers.py tests/test_workflow_guardrail.py -q -o asyncio_mode=auto
# 29 passed
```

## 使用方式

启用 AgentBay 工具：

```bash
export AGENTBAY_ENABLED=true
export AGENTBAY_API_KEY="your-agentbay-api-key"
export AGENTBAY_LOG_LEVEL=WARNING
```

运行主程序后，LLM 可调用：

- `agentbay_code`
- `agentbay_browser`

检查残留 Session：

```bash
python -m tools.agentbay.cleanup_sessions
```

删除匹配的运行中 Session：

```bash
python -m tools.agentbay.cleanup_sessions --delete
```

## 当前边界

- 默认不启用 AgentBay，避免无意产生云端资源费用。
- 第一版不做 Session 池化，每次工具调用创建短生命周期 Session，并在调用结束后删除。
- 第一版不使用 AgentBay PageUseAgent / ToolUseAgent，避免嵌套 LLM 调度和额外 Credits 成本。
- 第一版不替换本地 `execute_python`，AgentBay 作为云端隔离运行时补充能力存在。

## 后续建议

- 增加 live smoke 测试开关，例如 `AGENTBAY_EVAL_LIVE=true` 时才运行真实云端调用。
- 给 evaluation benchmark 增加 `agentbay` tag 任务，但默认跳过。
- 按需增加更多 AgentBay 能力：
  - 文件上传/下载
  - Shell 命令执行
  - 浏览器表单填写
  - 移动端 MobileUse
  - 云电脑 ComputerUse
