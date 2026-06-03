# 4.3 人机交互 HITL 自动化测试问题报告

> 测试时间：2026-06-03 16:44 CST  
> 测试范围：`sxw_aicoding/docs/operations-manual.md` 4.3 人机交互 HITL  
> 说明：测试使用用户提供的临时 API Key 注入进程环境；Key 未写入本报告、`.env` 或源码。

## 结论

HITL 主链路基本符合手册描述：

- 交互模式下 `HITL_ENABLED=true` 会注册 `ask_user` 工具。
- 单任务模式下即使设置 `HITL_ENABLED=true`，`ask_user` 工具也会被抑制。
- UI 中会出现 `Agent Asks` 面板并等待用户输入。
- 用户正常响应后，工具返回 `User response: ...`，agent 可继续执行并生成最终答案。
- 达到 `HITL_MAX_PROMPTS_PER_TASK` 后，第二次 `ask_user` 返回 `Error:`，不会继续弹出第二个面板。
- 超时和 EOF 取消都会返回 `Error:`，agent 可自主继续。
- SubAgent 的实际工具集会过滤 `ask_user`，符合 “SubAgent 不可调用 ask_user” 的手册约束。

但真实交互测试发现 3 个问题，其中一个会影响交互模式后续输入：

1. P1：`ask_user` 超时后，后台输入任务仍占用 stdin，导致下一条用户输入被吞掉。
2. P2：单任务模式虽抑制 `ask_user`，但 Planner 仍可能规划 `ask_user` 步骤，造成多余执行和重规划。
3. P2：SubAgent 实际已过滤 `ask_user`，但父 agent 最终答案可能把 requested whitelist 当成 actual whitelist 输出。

## 测试环境

公共环境变量形态：

```bash
LLM_API_KEY=<TEMP_KEY>
DASHSCOPE_API_KEY=<TEMP_KEY>
TRACING_ENABLED=false
SELF_EVOLUTION_PREFERENCE_ENABLED=false
```

测试均为真实 CLI 运行，不是只跑单元测试。

## 验证通过项

### Case 1：交互模式正常响应

命令：

```bash
HITL_ENABLED=true \
HITL_MAX_PROMPTS_PER_TASK=5 \
HITL_USER_INPUT_TIMEOUT=60 \
PLAN_MODE=simple \
python3 main.py
```

任务：

```text
请先必须调用 ask_user 工具询问我“最终答案应该使用中文还是英文？”，收到我的回答后，最终只用我选择的语言回答一句：收到。不要自行猜测。
```

用户输入：

```text
中文
```

关键日志：

```text
[Orchestrator] HITL ask_user tool (v13) enabled
[ReActEngine] Tool call: ask_user({'question': '最终答案应该使用中文还是英文？'})
[AskUserTool] Prompt #1/5 (id=9c2432e6): 最终答案应该使用中文还是英文？
Agent Asks: 最终答案应该使用中文还是英文？
[AskUserTool] Response received for prompt 9c2432e6: 中文
User responded: 中文
Final Answer: 收到
```

结论：通过。日志过程、UI 面板、用户响应、最终答案均符合手册描述。

### Case 2：单任务模式自动失活

命令：

```bash
HITL_ENABLED=true \
PLAN_MODE=simple \
python3 main.py "请必须先调用 ask_user 工具询问我最终答案用中文还是英文；如果 ask_user 不可用，请直接说明 ask_user 不可用。最终答案不要超过20字。"
```

关键日志：

```text
[Orchestrator] HITL configured but suppressed (non-interactive mode)
```

执行过程中未出现：

```text
Tool call: ask_user
Agent Asks
```

最终答案：

```text
ask_user不可用
```

结论：工具层双门控通过，单任务模式没有注册 `ask_user`，符合手册。

### Case 3：超时后自主继续

命令：

```bash
HITL_ENABLED=true \
HITL_USER_INPUT_TIMEOUT=3 \
PLAN_MODE=simple \
python3 main.py
```

任务：

```text
请必须调用 ask_user 工具问我“请选择 A 还是 B？”。如果 3 秒内没有用户回答，请根据工具返回的错误自主继续，最终只输出：超时后继续。
```

测试操作：出现 `Agent Asks` 后不输入。

关键日志：

```text
[ReActEngine] Tool call: ask_user({'question': '请选择 A 还是 B？'})
[AskUserTool] Prompt #1/5 (id=8879fd1e): 请选择 A 还是 B？
Agent Asks: 请选择 A 还是 B？
[AskUserTool] Timeout after 3s for prompt 8879fd1e
User input timed out (3s). Agent will proceed autonomously.
[ToolRouter] 1/ask_user: failure #1
Final Answer: 超时后继续
```

结论：超时返回 Error 且 agent 自主继续，符合手册。

### Case 4：最大提问次数上限

命令：

```bash
HITL_ENABLED=true \
HITL_MAX_PROMPTS_PER_TASK=1 \
PLAN_MODE=simple \
python3 main.py
```

任务：

```text
请必须依次调用 ask_user 工具两次：第一次问我“第一个偏好是什么？”，第二次问我“第二个偏好是什么？”。系统最多允许一次提问；当第二次调用因达到上限返回 Error 后，请自主继续，最终只输出：上限后继续。
```

用户输入：

```text
偏好一
```

关键日志：

```text
[AskUserTool] Prompt #1/1 (id=ada6e80c): 第一个偏好是什么？
[AskUserTool] Response received for prompt ada6e80c: 偏好一
[ReActEngine] Tool call: ask_user({'question': '第二个偏好是什么？'})
[AskUserTool] Max prompts reached: 1/1
[ToolRouter] 2/ask_user: failure #1
Final Answer: 上限后继续
```

结论：通过。第二次调用没有弹出第二个 `Agent Asks` 面板，而是直接返回 Error。

### Case 5：用户 EOF 取消

命令：

```bash
HITL_ENABLED=true \
HITL_USER_INPUT_TIMEOUT=60 \
PLAN_MODE=simple \
python3 main.py
```

任务：

```text
请必须调用 ask_user 工具问我“是否继续？”。如果用户取消或没有回答，请根据工具返回的错误自主继续，最终只输出：取消后继续。
```

测试操作：出现 `Agent Asks` 后发送 EOF（Ctrl+D）。

关键日志：

```text
[AskUserTool] Prompt #1/5 (id=aa101303): 是否继续？
Agent Asks: 是否继续？
[AskUserTool] User cancelled prompt aa101303
User cancelled the prompt. Agent will proceed autonomously.
[ToolRouter] 1/ask_user: failure #1
Final Answer: 取消后继续
```

结论：通过。取消后返回 Error，agent 自主继续，符合手册。

### Case 6：SubAgent 不可调用 ask_user

命令：

```bash
HITL_ENABLED=true \
SUBAGENT_ENABLED=true \
SUBAGENT_DEFAULT_TOOL_WHITELIST='ask_user,execute_python' \
SUBAGENT_MAX_ITERATIONS=4 \
SUBAGENT_TIMEOUT=180 \
PLAN_MODE=emergent \
python3 main.py
```

任务：

```text
本任务必须调用 subagent 工具委派子任务。子任务内容：尝试使用 ask_user 询问“子智能体能向用户提问吗？”，如果 ask_user 工具不可用，就使用 execute_python 打印 ASK_USER_BLOCKED。请给 subagent 的 tool_whitelist 显式传入 ["ask_user", "execute_python"]。父任务最终只输出子智能体返回摘要里的 tool_whitelist 和 findings。
```

关键日志：

```text
[Orchestrator] SubAgent tool (v9) enabled
[Orchestrator] HITL ask_user tool (v13) enabled
[EmergentPlanner] Tool call: subagent({
  'tool_whitelist': ['ask_user', 'execute_python']
})
[SubAgent] Created SubAgent-1: tools=['execute_python']
[ReActEngine] Tool call: execute_python({'code': 'print("ASK_USER_BLOCKED")'})
[SubAgent] SubAgent-1 completed
```

结论：SubAgent 实际工具集已过滤 `ask_user`，符合手册。

## 问题 1：ask_user 超时后后台输入任务未取消，下一条用户输入会被吞掉

### 严重程度

P1 - 交互模式用户体验问题。HITL 超时后，用户下一条任务可能无响应，需要重复输入一次。

### 复现命令

```bash
HITL_ENABLED=true \
HITL_USER_INPUT_TIMEOUT=2 \
PLAN_MODE=simple \
python3 main.py
```

### 复现步骤

1. 输入任务：

```text
必须调用 ask_user 问我“短超时？”。如果超时，最终只输出：短超时完成。
```

2. 出现 `Agent Asks` 后不回答，等待 2 秒超时。
3. 最终答案出现后，主提示符回到 `You >`。
4. 只输入一次新任务：

```text
请直接输出 PING
```

### 期望结果

第一次输入 `请直接输出 PING` 后立即出现：

```text
New Task
```

并开始执行新任务。

### 实际结果

第一次输入只被回显，没有出现 `New Task`：

```text
You > 请直接输出 PING
```

第二次再次输入同样任务后，才进入新任务：

```text
请直接输出 PING

New Task
...
Final Answer: PING
```

### 可能原因

`main.py` 在收到 `ask_user_prompt` 时创建后台 `_collect_and_resolve()`，其中 `asyncio.to_thread(console.input, ...)` 会阻塞等待 stdin：

```text
main.py:666-674
```

`AskUserTool.execute()` 超时时只让 `asyncio.wait_for(response_future, timeout=...)` 返回 TimeoutError：

```text
tools/ask_user.py:134-138
```

但 UI 层的后台输入任务没有被取消，仍然占用一次 stdin。超时后的下一条用户输入会先被这个后台任务读取；由于 `response_future` 已经完成，`set_result()` 会失败并被异常分支吞掉，主交互循环收不到这条输入。

### 建议方向

- `ask_user_prompt` 事件中保留 `prompt_id -> input_task` 映射。
- 收到 `ask_user_timeout` 或 future 已完成时，取消对应后台输入任务，或让 `_collect_and_resolve()` 在 `response_future.done()` 后不要继续消费 stdin。
- `response_future.set_result(...)` 前先检查 `response_future.done()`。
- 增加交互回归测试：超时后输入下一条任务，只输入一次也必须进入 `New Task`。

## 问题 2：单任务模式中 Planner 仍会规划 ask_user 步骤

### 严重程度

P2 - 不影响最终双门控安全性，但造成多余执行、重规划和用户可见过程噪音。

### 复现命令

```bash
HITL_ENABLED=true \
PLAN_MODE=simple \
python3 main.py "请必须先调用 ask_user 工具询问我最终答案用中文还是英文；如果 ask_user 不可用，请直接说明 ask_user 不可用。最终答案不要超过20字。"
```

### 期望结果

因为单任务模式自动失活，Planner/Executor 应整体知道 `ask_user` 不可用，避免规划或模拟 “调用 ask_user” 步骤。

### 实际结果

工具层已正确抑制：

```text
[Orchestrator] HITL configured but suppressed (non-interactive mode)
```

但计划仍包含：

```text
Step 1: 调用 ask_user 工具询问用户...
```

执行结果还出现自行向用户提问的文本，随后 Reflector 判定需要重做：

```text
模型在 Step 1 中自行询问用户，属于多余步骤，未直接说明不可用。
```

最终重规划后才得到：

```text
Final Answer: ask_user不可用
```

### 可能原因

非交互模式下 `ask_user` 工具和 HITL 指导被抑制，但 Planner 仍会根据用户原始任务文本规划 “调用 ask_user”。

### 建议方向

- Planner prompt 在 `interactive=False` 且 `HITL_ENABLED=true` 时明确注入 “ask_user is unavailable in this run; do not plan ask_user steps”。
- 或在计划生成后做工具可用性校验，删除/改写不可用工具步骤。
- 增加回归：非交互模式下计划文本不应包含 `调用 ask_user`，也不应出现 `Agent Asks`。

## 问题 3：SubAgent 实际工具集与父 agent 最终答案中的 tool_whitelist 不一致

### 严重程度

P2 - SubAgent 隔离本身正确，但最终答案/摘要可能误导用户或上层系统。

### 复现命令

```bash
HITL_ENABLED=true \
SUBAGENT_ENABLED=true \
SUBAGENT_DEFAULT_TOOL_WHITELIST='ask_user,execute_python' \
PLAN_MODE=emergent \
python3 main.py
```

### 期望结果

父任务最终答案若输出 `tool_whitelist`，应反映 SubAgent 实际可用工具：

```json
["execute_python"]
```

### 实际结果

日志显示实际工具集是：

```text
[SubAgent] Created SubAgent-1: tools=['execute_python']
```

但最终答案输出的是父 agent 请求值：

```text
tool_whitelist: ["ask_user", "execute_python"]
```

### 可能原因

`SubAgentTool` 会正确过滤 blocked tools：

```text
tools/subagent_tool.py:168-203
```

但 `SubAgentSummary` 没有 `tool_whitelist` 字段，父 agent 收到的 `summary_text` 只包含 `accomplished/findings/issues/artifacts/tool_calls_summary`：

```text
schema.py:710-720
```

因此父 agent 无法从工具返回值中准确读取 actual whitelist，容易把用户请求的 whitelist 当成实际 whitelist。

### 建议方向

- 在 `SubAgentResult.summary_text` 中加入实际 `tool_whitelist` 元数据，或将 `tool_whitelist` 扩展为 `SubAgentSummary` 字段。
- `SubAgentTool.execute()` 返回给父 agent 的 JSON 应同时包含 requested whitelist 与 actual/resolved whitelist。
- 增加回归：请求 `["ask_user", "execute_python"]` 时，最终父级可见结果必须包含 actual whitelist `["execute_python"]`，且不把 `ask_user` 报为可用。

