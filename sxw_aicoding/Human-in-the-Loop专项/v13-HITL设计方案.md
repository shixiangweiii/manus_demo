# v13 Human-in-the-Loop (HITL) 实施设计方案

> 版本：v13.0 | 日期：2026-05-16 | 状态：已实现

## 1. 背景与动机

当前系统的 ReAct 引擎是完全自主的：一旦用户输入任务，引擎自动推理、调用工具、生成结果，中途无法与用户交互。这导致两类问题：

1. **信息模糊时只能猜测**：例如 `get_user_location` 通过 IP 返回 APPROXIMATE 位置，LLM 无法确认，只能假设正确。Reflector 的"禁止未授权假设"规则也无法补救——它只能标记问题，不能提供替代路径。
2. **用户偏好无法获取**：当任务有多种合理路径时（如天气查询的城市选择），LLM 只能选一条，无法询问用户。

**典型场景**：用户问"今天天气怎么样" → 系统查 IP 得到城市 A → 查 A 的天气 → 反问用户"这是你想查的城市吗？" → 用户输入城市 B → 查 B 的天气并返回。

## 2. 架构设计：Human-as-Tool + asyncio.Future Bridge

### 2.1 核心模式

采用 **"Human-as-Tool"** 模式：将人类注册为 ReAct 引擎的一个工具 `ask_user`。LLM 在需要时自主调用此工具，工具通过 `asyncio.Future` 暂停 ReAct 循环，UI 层收集用户输入后 resolve Future，引擎继续。

```
LLM → tool_call: ask_user(question="这是你想查的城市吗?")
  → AskUserTool.execute()
    → 创建 asyncio.Future
    → 通过 orchestrator._emit("ask_user_prompt", {question, future}) 通知 UI
    → await Future (ReAct 循环暂停，事件循环不阻塞)
  → main.py on_event 处理
    → asyncio.to_thread(console.input, ...) 在线程池中等待用户输入
    → Future.set_result(user_response) 回传
  → AskUserTool.execute() 恢复
    → 返回 "User response: <用户输入>" 作为工具结果
  → ReAct 循环继续，用户输入已在上下文中
```

### 2.2 关键技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 用户输入桥接 | `asyncio.Future` + `asyncio.to_thread` | 不阻塞事件循环；与现有 fire-and-forget `_emit` 兼容 |
| HITL 触发方式 | LLM 自主决策调用 `ask_user` 工具 | 遵循 ReAct 模式，无需硬编码暂停点 |
| Future 传递路径 | `_emit("ask_user_prompt", {future})` | 复用现有事件系统，Future 提供返回通道 |
| 并发安全 | `asyncio.Semaphore(1)` 序列化提问 | 避免同时弹出多个问题 |
| 超时处理 | `asyncio.wait_for(timeout=120s)` → 返回 Error 字符串 | 与现有 Error: 前缀检测模式一致 |
| 非交互模式 | 工具仍注册但返回 Error | LLM 看到工具可用，调用后获知不可用，改为自主推理 |
| 调用计数层级 | 每任务计数（默认 5 次/任务） | 与 SubAgentTool.reset_task_state 一致，多步计划中每步可能需要 1 次确认 |
| SubAgent 隔离 | ask_user 排除在白名单外 | SubAgent 不应直接与用户交互，歧义应通过 summary 上报 |

### 2.3 为什么不用其他方案

| 替代方案 | 问题 |
|----------|------|
| **LangGraph `interrupt_on`**（图级中断） | 需要图状态机架构，本项目 ReAct 循环不是图结构；改动量大 |
| **硬编码暂停点**（在 orchestrator 的特定位置插入 `input()`） | 破坏 ReAct 自主性，无法覆盖所有歧义场景；不够灵活 |
| **修改 `_emit` 为 async** | 需要改动整个事件回调链，影响 30+ 事件类型，风险大 |
| **`console.input()` 直接阻塞** | 阻塞 asyncio 事件循环，其他协程（如超时检测、并发工具执行）无法运行 |

## 3. 修改文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `config.py` | 修改 | +3 配置项 (HITL_ENABLED, HITL_MAX_PROMPTS_PER_TASK, HITL_USER_INPUT_TIMEOUT) |
| `tools/ask_user.py` | **新建** | AskUserTool 核心实现 |
| `tools/__init__.py` | 修改 | 导出 AskUserTool |
| `agents/prompt_utils.py` | 修改 | +HITL 引导文本 + build_system_prompt 新参数 |
| `agents/orchestrator.py` | 修改 | +asyncio import, 注册工具, _handle_user_prompt 桥接, reset_task_state, 答案合成规则 |
| `tools/subagent_tool.py` | 修改 | 白名单排除 ask_user (3 处过滤) |
| `main.py` | 修改 | ask_user_prompt/response/timeout 事件处理 + interactive 模式设置 |
| `agents/reflector.py` | 修改 | 软引导提示：ask_user 可用但未使用时建议 |

**不需要修改的文件**：`react/engine.py`（ask_user 作为普通工具被调用，无需结构性变更）、`schema.py`（无需新数据模型）、`agents/base.py`、`agents/executor.py`、`agents/planner.py`

## 4. 各模块详细设计

### 4.1 AskUserTool (`tools/ask_user.py`)

```python
class AskUserTool(BaseTool):
    def __init__(
        self,
        on_user_prompt: Callable[[str, str, asyncio.Future[str]], None] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        max_prompts_per_task: int | None = None,
        timeout: int | None = None,
    ): ...
```

**核心方法**：
- `execute(question)` → 创建 Future → 调用 on_user_prompt → await future with timeout → 返回结果
- `reset_task_state()` → 每任务开始时重置 _prompt_count
- `set_interactive_mode(enabled)` → 控制 non-interactive 模式下直接返回 Error

**防护机制**：
- `Semaphore(1)`：序列化提问，避免同时弹出多个问题
- `_prompt_count` 计数器：超过 `HITL_MAX_PROMPTS_PER_TASK` 时返回 Error
- `_interactive_mode` 开关：非交互模式直接返回 Error
- `asyncio.wait_for`：超时后返回 Error，让 LLM 自主继续

**返回格式**（与现有 Error: 检测模式一致）：
- 成功：`"User response: <用户输入>"`
- 失败：`"Error: ..."` 字符串 → ReActEngine 检测为 tool failure → ToolRouter.record_failure()

### 4.2 OrchestratorAgent 集成 (`agents/orchestrator.py`)

**注册时机**：`__init__` 中 SubAgent 注册之后，feature-flag 控制：

```python
self._ask_user_tool = None
if config.HITL_ENABLED:
    self._ask_user_tool = AskUserTool(
        on_user_prompt=self._handle_user_prompt,
        on_event=self._emit,
    )
    tools = list(tools or []) + [self._ask_user_tool]
```

**桥接方法** `_handle_user_prompt`：将 AskUserTool 的 Future 通过事件系统传递给 UI 层：

```python
def _handle_user_prompt(self, question, prompt_id, response_future):
    self._emit("ask_user_prompt", {
        "question": question,
        "prompt_id": prompt_id,
        "response_future": response_future,
    })
```

**任务边界 reset**：`run()` 中调用 `self._ask_user_tool.reset_task_state()`

**答案合成增强**：`_synthesize_final_answer` 中新增规则 #4——如果 ask_user 被使用且用户提供了更正，最终答案应使用用户更正后的信息。

### 4.3 UI 层事件处理 (`main.py`)

新增 3 个事件类型：

| 事件 | 说明 |
|------|------|
| `ask_user_prompt` | LLM 向用户提问，携带 Future；UI 层用 `asyncio.to_thread(console.input, ...)` 收集输入 |
| `ask_user_response` | 用户已回复（info 日志） |
| `ask_user_timeout` | 用户超时未回复（warning 提示） |

**关键实现**：`asyncio.create_task(_collect_and_resolve())` —— 在事件循环中调度输入收集任务，ReAct 循环通过 await Future 暂停，其他协程仍可运行。

**交互模式设置**：
- `run_interactive()`：`set_interactive_mode(True)`
- `run_single()`：`set_interactive_mode(False)`

### 4.4 系统提示词引导 (`agents/prompt_utils.py`)

新增 `_HITL_GUIDANCE` 告诉 LLM 何时使用 ask_user：
- 有模糊/近似信息时（如 IP 定位的 APPROXIMATE 城市）
- 需要用户偏好/确认时
- 任务不清晰且继续假设有风险时
- **不用于**可由其他工具回答的问题
- **不用于**重复提问（每任务上限 5 次）

`build_system_prompt()` 新增 `inject_hitl_guidance=True` 参数，默认启用。

### 4.5 SubAgent 白名单排除 (`tools/subagent_tool.py`)

在所有过滤 `"subagent"` 的位置同时排除 `"ask_user"`（3 处）：
1. 用户显式白名单验证（line ~144）
2. config 默认白名单过滤（line ~158）
3. 全量授权回退过滤（line ~161-163）

理由：SubAgent 运行在隔离环境，不应直接与用户交互。如有歧义，应在 structured summary 中报告，由父 Agent 决定是否 ask_user。

### 4.6 Reflector 软引导 (`agents/reflector.py`)

在 REFLECTOR_SYSTEM_PROMPT 的规则 #5（禁止未授权假设）后添加：

> NOTE: If the "ask_user" tool was available and a step encountered ambiguous data but did NOT use it to clarify with the user, note this as a missed opportunity in suggestions. Do not set passed=false solely for this (the step may have had valid reasons to proceed), but suggest using ask_user in the next replan.

这是**软引导**而非硬性失败——LLM 不使用 ask_user 可能有合理原因（如歧义很小），但 Reflector 应在建议中提醒。

## 5. 并发安全性分析

| 场景 | 安全性 |
|------|--------|
| LLM 同时调用 ask_user 和其他工具 | 安全：每次 execute() 创建独立 Future；Semaphore(1) 序列化提问显示；其他工具正常执行 |
| 多个 ask_user 同时调用 | Semaphore(1) 保证同时只有一个提问，用户不会看到多个问题叠加 |
| 用户超时不回复 | asyncio.wait_for 超时返回 Error，ReAct 循环继续 |
| 用户按 Ctrl+C | asyncio.to_thread 中 console.input 抛出 KeyboardInterrupt，被 _collect_and_resolve 捕获，Future resolve 为 "(user cancelled)" |
| Future 被 GC | asyncio.Future 由 AskUserTool.execute() 持有引用（局部变量），直到 await 完成 |
| _prompt_count 并发递增 | 单线程 asyncio 事件循环中 list.append / int += 1 是原子的 |

## 6. 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HITL_ENABLED` | `false` | 总开关（默认关闭，向后兼容） |
| `HITL_MAX_PROMPTS_PER_TASK` | `5` | 单任务最大 ask_user 调用次数 |
| `HITL_USER_INPUT_TIMEOUT` | `120` | 等待用户输入超时（秒） |
