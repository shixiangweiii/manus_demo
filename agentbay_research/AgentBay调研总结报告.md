# 无影 AgentBay 调研与验证总结

日期：2026-05-30

## 结论概览

本次基于 `agentbay_research` 目录中的官方文档摘要，完成了 AgentBay Python SDK 的基础接入验证。三个基础 demo 均已跑通：

- 默认 Session 创建与删除验证成功。
- CodeSpace 远程执行 Python 代码成功。
- BrowserUse 远程浏览器通过 Playwright CDP 连接成功，并能访问阿里云官网。

当前 demo 已具备作为后续集成到 `manus_demo` 工具体系的最小可用样例价值。下一步重点不是能力验证，而是工程化整理：日志脱敏、依赖固定、异常清理兜底、以及将 CodeSpace / BrowserUse 封装成 ReAct 可调用工具。

## 已验证环境

- 本地虚拟环境：项目根目录 `.venv`
- Python：3.12.10
- AgentBay SDK：`wuying-agentbay-sdk==0.21.0`
- Playwright：`playwright==1.60.0`
- API Key 来源：环境变量 `AGENTBAY_API_KEY`
- AgentBay 实际区域：验证日志显示为 `cn-hangzhou`

注意：AgentBay SDK 会自动加载项目根目录 `.env` 文件。运行日志中出现了 `Loaded .env file from: /Users/shixiangweii/PycharmProjects/manus_demo/.env`，说明除了 shell export，也可以通过 `.env` 提供 `AGENTBAY_API_KEY`。

## Demo 验证结果

### 1. 默认 Session 验证

脚本：`agentbay_research/00_verify_session.py`

验证结果：

- `agent_bay.create()` 创建 Session 成功。
- 默认镜像为 `computer-use-ubuntu-2204`。
- 镜像类型为 `ComputerUse`。
- 删除由 API 调用触发，`EndReason=API_CALL`。
- 状态流转正常：`DELETING` -> `FINISH`。
- 最终 `IsDeleted=1`，确认 Session 已删除。

结论：基础 SDK 认证、创建 Session、删除 Session 均正常。

### 2. CodeSpace 执行 Python

脚本：`agentbay_research/01_codespace_run_python.py`

验证结果：

- 使用 `CreateSessionParams(image_id="code_latest")` 创建 CodeSpace 成功。
- 实际底层镜像为 `code-space-debian-12`。
- 镜像类型为 `CodeSpace`。
- 远程 MCP 工具调用为 `run_code`，服务端为 `wuying_codespace`。
- 远程代码输出符合预期：

```text
Factorial of 10: 3628800
Fibonacci of 10: 55
Squares: [1, 4, 9, 16, 25, 36, 49, 64, 81, 100]
```

- 删除状态正常结束：`DELETING` -> `FINISH`，最终 `IsDeleted=1`。

结论：CodeSpace 可作为后续远程代码执行工具的底座。

### 3. BrowserUse + Playwright CDP

脚本：`agentbay_research/02_browser_playwright_cdp.py`

验证结果：

- 使用 `CreateSessionParams(image_id="browser_latest")` 创建 BrowserUse 成功。
- 实际底层镜像为 `browser-use-debian-12`。
- 镜像类型为 `BrowserUse`。
- `session.browser.initialize(BrowserOption())` 初始化成功。
- 浏览器 CDP 端口为 `9333`。
- Playwright 成功通过 CDP 连接远端浏览器。
- 打开 `https://www.aliyun.com` 后获取标题成功：

```text
Title: 阿里云-计算，为了无法计算的价值
```

- 删除状态正常结束：`DELETING` -> `FINISH`，最终 `IsDeleted=1`。

结论：BrowserUse + Playwright CDP 路径可用，适合作为网页自动化、远程浏览器检索、页面截图和表单操作的底座。

## 与官方文档的差异

官方示例中 BrowserOption 的导入路径是：

```python
from agentbay.browser.browser import BrowserOption
```

但当前实际安装的 `wuying-agentbay-sdk==0.21.0` 中不存在 `agentbay.browser` 模块。SDK 实际将 `BrowserOption` 从顶层 `agentbay` 导出，因此 demo 已调整为：

```python
from agentbay import AgentBay, BrowserOption, CreateSessionParams
```

这个差异说明官方文档与当前 SDK 包结构存在轻微漂移。为了避免后续复现失败，`requirements.txt` 已固定本次验证版本：

```text
wuying-agentbay-sdk==0.21.0
playwright==1.60.0
```

## 计费与资源释放结论

本次三个 demo 都在执行结束后成功删除 Session，日志中均出现 `FINISH` 和 `IsDeleted=1`。因此这些已完成的 Session 不会继续保持运行态，也不会因残留会话持续扣 CPU/内存费用。

需要注意：

- 运行中 Session 即使没有任务，也可能继续产生资源占用费用。
- BrowserUse 会生成 `resource_url`，其中包含访问授权信息，应视为敏感信息。
- `get_endpoint_url()` / CDP 链接能力可能涉及高级权益或高级功能计费，实际以阿里云控制台权益和账单为准。

## 当前 Demo 建议调整项

### P0：日志脱敏与降噪

SDK 默认 INFO 日志会输出 `resource_url`、`authcode`、`Aliuid`、`ApikeyId`、`AppUserId` 等信息。虽然不一定等同于 API Key 原文，但不适合复制到公开渠道。

建议后续在 demo 或运行文档中加入：

```bash
export AGENTBAY_LOG_LEVEL=WARNING
```

或者在 Python 导入 `agentbay` 前设置：

```python
import os
os.environ.setdefault("AGENTBAY_LOG_LEVEL", "WARNING")
```

当前暂未直接改 demo 默认日志级别，原因是调研阶段保留 SDK 原始日志更利于观察真实 API 行为。进入正式集成阶段应改为默认降噪。

### P1：Session 清理兜底

`01_codespace_run_python.py` 和 `02_browser_playwright_cdp.py` 已使用 `finally` 删除 Session。`00_verify_session.py` 当前流程很短，也成功删除；但为了风格一致，后续可以也改为 `try/finally`。

正式集成到工具层时，应保证：

- Session 创建后所有远程调用都包在 `try/finally` 中。
- 删除失败时要打印 Session ID，方便去控制台手动回收。
- 长任务建议增加超时控制，避免本地进程卡住导致云端 Session 长时间运行。

### P1：依赖版本固定

本次已将 AgentBay 相关依赖固定为已验证版本：

```text
wuying-agentbay-sdk==0.21.0
playwright==1.60.0
```

原因是官方文档导入路径与实际 SDK 包结构已经出现一次漂移，固定版本能减少后续复现成本。

### P2：补充更多最小能力示例

当前 demo 覆盖了 Session、CodeSpace、BrowserUse 三条主路径。后续如果要更完整评估 AgentBay，可继续补：

- 文件上传/下载示例。
- Shell 命令执行示例。
- Browser 截图示例。
- Browser 表单填写示例。
- MobileUse 或 ComputerUse 的基础操作示例。
- List Session / 查询状态 / 清理残留 Session 的运维脚本。

## 对 manus_demo 的集成建议

AgentBay 与当前 `manus_demo` 的 ReAct 工具体系比较匹配，建议按工具封装，而不是直接侵入 Orchestrator。

推荐新增工具方向：

1. `AgentBayCodeTool`
   - 输入：代码、语言、超时时间。
   - 行为：创建 `code_latest` Session，执行 `session.code.run_code()`，返回 stdout/stderr/error。
   - 适用：远程运行不适合在本地沙箱执行的代码。

2. `AgentBayBrowserTool`
   - 输入：URL、操作类型、可选选择器/脚本。
   - 行为：创建 `browser_latest` Session，通过 Playwright CDP 操作页面。
   - 适用：网页访问、页面标题提取、截图、表单自动化、动态页面调研。

3. `AgentBaySessionCleanupTool` 或运维脚本
   - 输入：可选 Session ID。
   - 行为：查询和清理残留运行中的 Session。
   - 适用：调试阶段降低误扣费风险。

设计上建议保持“每次工具调用创建短生命周期 Session”的策略，先追求安全和可控；等稳定后再考虑 Session 复用、上下文持久化和并发池。

## 推荐下一步

1. 给三个 demo 增加统一的运行说明：如何设置 `AGENTBAY_API_KEY`、如何设置 `AGENTBAY_LOG_LEVEL=WARNING`、如何确认 Session 已删除。
2. 将 `00_verify_session.py` 也改成 `try/finally` 清理模式。
3. 新增一个 `03_browser_screenshot.py`，验证远程浏览器截图能力。
4. 新增一个 `04_cleanup_sessions.py`，用于调研阶段手动清理残留 Session。
5. 在 `tools/` 下设计 `AgentBayCodeTool` 和 `AgentBayBrowserTool` 的最小接口，再决定是否接入 `main.py` 的基础工具列表。
