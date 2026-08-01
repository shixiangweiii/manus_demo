# Agent 推理框架评审 Bugfix 记录

> 日期：2026-08-01
>
> 依据：`sxw_aicoding/review/2026-8-1-评审Agent推理框架整体重构-01.md`
>
> 边界：不新增单元测，不运行真实 LLM、联网任务或正式评测。

## 一、二次复核结论

本次采用“完整正确性批次”：采纳已确认的 P1、P2，以及会影响安全、取消、资源、隔离、隐私和结果真实性的 P3。评审报告本身保留为原始证据，未改写。

核心调整有三项：

1. Shell 不再叠加黑名单正则，而是收紧默认能力；同源 Python 执行风险一并治理。
2. DAG 不把所有 `SKIPPED` 等价处理，而是区分条件未命中和执行失败级联。
3. Runtime 正确性不只看主调用，而是覆盖 CLI、WebUI、评测、EventBus、Checkpoint 和 Tracing 的完整生命周期。

## 二、采纳与调整采纳

- **P1 Shell 绕过：采纳并扩大治理。** 新增共享 Shell 策略。`restricted` 用 `shlex` 解析并直接执行 argv，拒绝管道、重定向、展开、多命令、绝对路径、父目录和符号链接越界；ShellTool 与 Tool Guardrail 复用同一判定。`trusted` 保留完整 bash，但必须显式开启。
- **Python 执行风险：补充采纳。** `python_mode` 默认 `disabled`；只有 `trusted` 才注册工具，直接构造也必须传 `trusted=True` 和完整配置。
- **P2 EventBus：采纳。** 新增 `emit_async()`/`drain()`，跟踪异步 handler；无 loop 时明确告警，不再偷偷 `asyncio.run`；每个订阅者获得可变容器的递归副本。
- **P2 DAG：调整采纳。** 条件分支跳过允许成功；任何已执行失败、回滚或失败级联都不允许被算作成功。结果 metadata 分开记录失败节点与条件跳过节点。
- **P2 Workflow：调整采纳。** 统一为 `${steps.<id>}`，`$${steps.<id>}` 输出字面量；旧 `${id}` 及 shell `${HOME}` 不再被解释。
- **P2 Checkpoint/取消：采纳。** 新增 `CANCELLED`；任务和 Workflow 取消时先保存状态、发布 `task_cancelled`，再重新抛出。列表接口跳过畸形文件名和损坏内容。
- **P2 WebUI：采纳。** REST 请求使用明确模型，形状错误返回 422；配置错误为 422，可预期依赖初始化失败为结构化 503，内部异常为 500。会话替换、关闭与服务退出都关闭 Runtime。
- **P2 评测资源/后台任务：采纳。** 每个矩阵单元在删除 sandbox 前关闭 Runtime；服务端持有后台 task，记录逃逸异常并在退出时收尾；CLI 逐单元保存进度。
- **P3 正确性与隐私：采纳。** 修复 LLM usage 读到上一次记录、RunSettings 非法 capabilities、评测损坏 JSON/空白 fuzzy/失败实际选择伪装、HITL 参数解析与终端迟到输入等问题。
- **脱敏与配置隔离：采纳。** 统一敏感键及文本清洗，覆盖 `api-key`/`apikey`/`apiKey`/`passwd`/Token/Authorization；`LLMClient`、TracingBridge、AgentBay 和 Evolution 显式接收配置快照。`log_prompts=false` 时不记录异常正文或原始 exception；开启正文时仍先递归脱敏。

## 三、不采纳或暂缓

- Guardrail `observe` 保持“只观察、不阻断”语义，不将其改成强制模式。
- Workflow 保持确定性，不读取记忆、知识库或自演化提示。
- `settings.toml` 中 Tracing 的现有开关本轮不改；只修复 `log_prompts=false` 的完整保证与开启时的脱敏。
- Selector 启发式需要真实评测数据，本轮不凭静态猜测调整。
- O(n²) 小规模优化、Trace TOCTOU、纯样式和不影响逻辑的注释整理暂缓。

## 四、接口与配置变化

- `ToolSettings.shell_mode = disabled | restricted | trusted`，默认 `restricted`。
- `ToolSettings.python_mode = disabled | trusted`，默认 `disabled`。
- `CheckpointStatus.CANCELLED`。
- `EventBus.emit_async()` 与 `EventBus.drain()`。
- `LLMClient.aclose()` 与 `AgentRuntime.aclose()`，两者均幂等。
- `RuntimeInitializationError`，用于可预期的 Runtime 依赖初始化失败。
- Workflow 模板 `${steps.<step_id>}` 与字面量 `$${steps.<step_id>}`。
- 评测失败结果的 `actual_engine`/`actual_executor`/`actual_effort` 改为可空。

## 五、验证边界

使用项目指定虚拟环境执行编译、导入、CLI/WebUI/评测 `--help`、纯配置与纯解析探针，以及 `git diff --check`。未执行真实 Shell/Python 载荷、真实 LLM、联网任务、WebUI 浏览器交互或正式评测。

交付口径为：**Bugfix 完成，编译及导入通过；真实引擎行为、评测质量、WebUI 交互和外部服务集成待用户后续本地验证。**
