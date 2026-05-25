# v14 Phase 3 深度代码评审

> 日期：2026-05-23  
> 范围：截止 `v14 Phase 1-3 + Phase 3 bugfix` 后的当前代码。  
> 参考：`sxw_aicoding/记忆/v14-phase1-work-progress-20260522.md`、`sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md`。  
> 评审方式：静态代码审查 + v14/归因定向测试 + 离线回归尝试。未修改业务代码。

## 总体结论

Phase 1-3 的主线方向是成立的：`reasoning_tokens` 已进入 schema / LLMClient / UI / tracing 聚合链路；`ReasoningEngine` 已作为灰度路径接入 `ExecutorAgent`；`ReActEngine` 和 `ReasoningEngine` 都已经能剥离 `<think/>` / `reasoning_content`；`ContextManager` 也开始感知 `thinking_content`。这说明 v14 的底座已经从“记录 token”推进到了“运行时能携带 reasoning 语义”。

但当前代码还不能视为 v14 可收口状态。最主要的问题是：内部消息格式和 OpenAI-compatible API 消息格式混在一起，`thinking_content` 会被原样发给 `chat.completions.create()`；`ReasoningEngine` 对“只思考、不行动”的循环缺少独立 LLM-call 上限；Phase 4 计划中的 DRY 重构和 `reasoning_effort` 联动仍未做。这些问题会直接影响真实模型运行，而现有 mock 测试没有覆盖。

建议下一步先修 P0/P1，再进入 Phase 4：先做 API message sanitizer / internal transcript 分离，再补 ReasoningEngine 循环熔断，随后抽 `engine_helpers.py`。

## 正向观察

- `LLMCallRecord` / `TokenUsage` 已加入 `reasoning_tokens`，并保持默认值兼容旧数据：`schema.py:311-335`。
- `LLMClient._record_call()` 同时支持 OpenAI `completion_tokens_details.reasoning_tokens` 和 DeepSeek 风格 `usage.reasoning_tokens`：`llm/client.py:371-380`。
- `caller_tag` 已贯穿 `chat` / `chat_with_tools` / `chat_json`，并在 `Orchestrator._finalize_token_usage()` 中形成 `by_caller` 视图：`llm/client.py:113-211`、`agents/orchestrator.py:679-716`。
- `ReActEngine` 和 `ReasoningEngine` 已统一使用 `tool_call_helpers` 的三态分类与截断策略，减少了 v12/v13 的分叉风险：`react/engine.py:44-48`、`react/reasoning_engine.py:208-211`。
- HITL 双门控已落到 Orchestrator：非交互单任务模式不会注册 `ask_user`，也不会注入 HITL guidance：`agents/orchestrator.py:100-162`。
- 定向测试通过：  
  `.venv/bin/python -m pytest tests/test_v14_reasoning_tokens.py tests/test_v14_reasoning_engine.py tests/test_tool_call_helpers.py tests/test_token_attribution.py -q -o asyncio_mode=auto`  
  结果：`87 passed in 2.63s`。

## Findings

### P0 - `thinking_content` 会作为非标准字段发送给 OpenAI-compatible API

`ReActEngine` 在 assistant message 中写入内部字段：

- `react/engine.py:213-219`
- `react/engine.py:231`

随后下一轮直接把同一个 `messages` 列表传给 `LLMClient.chat_with_tools()`：

- `react/engine.py:196-205`
- `llm/client.py:201-208`

`ReasoningEngine` 同样写入并复用该字段：

- `react/reasoning_engine.py:128-147`
- `react/reasoning_engine.py:103-115`

问题是 `thinking_content` 不是 OpenAI Chat Completions message schema 的合法字段。很多 OpenAI-compatible server 会严格校验 message 对象，真实运行时可能返回 400，导致 v14 Phase 3 在 mock 测试中通过，但真实 provider 下失败。当前测试只验证输出没有包含 thinking，未验证传入 API 的 messages 不含非标准键。

建议：

- 在 `LLMClient` 增加 `_sanitize_messages_for_api(messages)`，发送前移除内部字段，如 `thinking_content`。
- 保留内部 transcript 与 API payload 两套视图：内部用于 tracing/context/replay，API payload 只包含 provider 接受的字段。
- 增加回归测试：构造两轮 ReAct，第一轮产生 `thinking_content`，第二轮 `chat_with_tools` 捕获入参，断言所有 message keys 都在允许集合内。

### P0 - `ReasoningEngine` 在 usage 缺失或 token tracking 关闭时可能无限 LLM 调用

`ReasoningEngine` 对 pure-thinking round 不递增 `iteration`：

- `react/reasoning_engine.py:180-206`

thinking budget 又依赖 `LLMClient.get_call_records()` 的 `reasoning_tokens` 差分：

- `react/reasoning_engine.py:108-126`

但 `_record_call()` 在两种常见情况下不会产生可用记录：

- `TOKEN_TRACKING_ENABLED=false` 时直接返回：`llm/client.py:351-352`
- provider 不返回 `usage` 时直接返回：`llm/client.py:363-365`

于是如果模型连续返回 `content="" + reasoning_content != "" + tool_calls=None`，同时 usage 缺失或 reasoning_tokens 为 0，`iteration` 不增长，`total_thinking_tokens` 也不增长，`while iteration < self.max_iterations` 永远为真。这是长任务中最危险的失控路径之一。

建议：

- 引入 `MAX_REASONING_LLM_CALLS` 或 `MAX_THINKING_ROUNDS`，独立于 `MAX_REACT_ITERATIONS`。
- pure-thinking round 至少要增加一个 `llm_call_count`，并以该计数兜底退出。
- 当 usage 缺失时，用 `len(thinking)` 做粗估预算，不能完全依赖 provider usage。
- 预算逻辑不要被 `TOKEN_TRACKING_ENABLED` 关闭影响；运行时熔断和 UI token 统计应解耦。

### P1 - `ReActEngine` 的 reasoning-only 分支会插入重复 user messages，且仍消耗迭代

`ReActEngine` 每轮开始先 `iteration += 1`：

- `react/engine.py:167-169`

遇到 reasoning-only 响应后，它追加一条用户消息：

- `react/engine.py:247-254`

然后 `continue` 回到循环顶部。下一轮又会根据 `iteration != 1` 再追加一条 `continue_msg`：

- `react/engine.py:189-194`

最终消息序列会出现两个连续 user messages：一个是“Please provide your final answer...”，另一个是“Continue executing based on the tool results above.”。这会弱化前者的指令，而且在还没有工具结果时提示“based on the tool results”也不准确。当前测试只验证最终输出，不验证第二轮 API 入参，因此没有覆盖这个语义问题。

建议：

- reasoning-only 分支不要直接 append user message；改为设置 `next_user_input_override`，下一轮只追加这一条。
- 或者让 ReActEngine 遇到 reasoning-only 时直接失败并提示开启 `ENABLE_REASONING_ENGINE`，避免在默认引擎里半支持 reasoning。
- 增加测试捕获多轮 `messages`，断言不会出现连续 user messages，且无工具结果时不出现 “tool results” 文案。

### P1 - `ReasoningEngine` 的 thinking budget 只在 pure-thinking 分支检查

`ReasoningEngine` 每轮都会累计 `reasoning_tokens`：

- `react/reasoning_engine.py:123-126`

但 budget 检查只发生在 `else` pure-thinking 分支：

- `react/reasoning_engine.py:180-206`

如果某一次响应同时包含大量 reasoning tokens 和最终答案，代码会直接成功返回：

- `react/reasoning_engine.py:167-179`

如果响应包含大量 reasoning tokens 和 tool_calls，代码会继续执行工具：

- `react/reasoning_engine.py:163-166`
- `react/reasoning_engine.py:208-276`

这与 `MAX_THINKING_TOKENS` 作为“推理预算上限”的语义不一致。预算应控制所有 reasoning 消耗，而不只是“空转思考”。

建议：

- 在累计 `total_thinking_tokens` 后立即检查 budget。
- 对 final-answer 超预算可以返回成功但标记 `budget_exceeded=True`，或按配置降级为失败。
- 对 tool-call 超预算应阻止继续执行高成本工具，并让模型汇总当前结果。

### P1 - `ShellTool` 仍绕过实例级 sandbox，影响 SubAgent 隔离

`ShellTool.__init__()` 保存了实例级工作目录：

- `tools/shell_tool.py:59-61`

但 `_run_shell()` 是静态方法，并且执行时使用全局 `config.SANDBOX_DIR`：

- `tools/shell_tool.py:130-134`
- `tools/shell_tool.py:150`

这与 SubAgent sandbox 隔离目标冲突。即使某个 SubAgent / future tool wrapper 修改了 `ShellTool._workdir`，shell 实际仍会跑在全局 sandbox。roadmap 已经把它列为 Wave-5 风险，当前代码中仍存在。

建议：

- 将 `_run_shell()` 改为实例方法，使用 `self._workdir`。
- 输出中的 working directory 也使用实例 workdir。
- 增加测试：构造 `ShellTool`，手动设置 `_workdir` 到临时目录，执行 `pwd`，断言输出目录为临时目录而不是 `config.SANDBOX_DIR`。

### P2 - Reflector 注入了它不能使用的工具指导

`ReflectorAgent` 构造 system prompt 时直接调用默认 `build_system_prompt()`：

- `agents/reflector.py:95-97`

而默认值会注入 location / search / subagent / HITL guidance：

- `agents/prompt_utils.py:188-195`
- `agents/prompt_utils.py:219-230`

Reflector 使用的是 `think_json()`，没有 tools schema，也不会执行工具。给它注入 “You have access to ask_user / web_search / get_user_location” 一类指导，会污染质量评估角色。尤其 HITL guidance 会让 Reflector 建议或假设自己有交互工具，和实际执行能力不一致。

建议：

- Reflector 应使用：
  `build_system_prompt(..., inject_location_guidance=False, inject_search_guidance=False, inject_subagent_guidance=False, inject_hitl_guidance=False)`
- Planner 是否注入 HITL guidance 也应重新审视：Planner 不能直接调用工具，但可以规划“确认用户信息”的步骤；提示词应表达为“executor may use ask_user”，而不是“you have access”。

### P2 - `ReActEngine` 与 `ReasoningEngine` 的工具执行块仍高度重复

`ReActEngine` 工具执行块：

- `react/engine.py:280-355`

`ReasoningEngine` 工具执行块：

- `react/reasoning_engine.py:208-276`

虽然 Phase 3 已抽出 `tool_call_helpers.py`，但 `_exec_one`、`asyncio.gather`、ToolRouter accounting、tool message 组装仍重复。后续任何 tool-call 协议调整、guardrail、tool policy、MCP outputSchema 校验，都需要改两处，容易再次漂移。

建议：

- Phase 4 优先抽 `react/engine_helpers.py`。
- 建议函数边界：
  - `execute_tool_calls(response_tool_calls, tools, agent_name, step_id, tool_router, truncation_limit) -> (tool_records, tool_messages)`
  - `build_tool_error_message(result)`
- 抽出后让 `EmergentPlanner` / `GoalDrivenPlanner` 也复用同一入口，真正形成 Harness 层。

### P2 - 全量测试没有隔离真实 DDGS 路径，离线回归会 abort

尝试运行：

```bash
.venv/bin/python -m pytest tests/ -q -o asyncio_mode=auto --ignore=tests/test_llm_integration.py
```

结果进程 abort，堆栈落在真实 DDGS worker thread：

- `tools/web_search.py:234-240`
- `.venv/lib/python3.12/site-packages/ddgs/http_client.py`

即使排除了 `test_web_search.py` / `test_real_tools.py`，仍有其他测试间接实例化 `WebSearchTool` 并走真实 DDGS。单元测试不应依赖公网搜索，更不应让第三方库 crash 中断整个回归。

建议：

- 所有非集成测试里 monkeypatch `WebSearchTool._ddgs_search`，或默认设置 `DASHSCOPE_API_KEY` / DDGS mock。
- 把真实 DDGS 搜索统一标记为 integration，并默认跳过。
- 增加 pytest marker，例如 `@pytest.mark.integration`，文档命令默认 `-m "not integration"`。

### P2 - 并发测试已有失败，期望值与实际节点数不一致

单独运行：

```bash
.venv/bin/python -m pytest tests/test_concurrent_execution.py -q -o asyncio_mode=auto
```

结果：

```text
FAILED test_medium_parallelism
AssertionError: 期望 12 个节点完成，实际 13
```

该测试构造了 13 个节点：`goal + sub1 + sub2 + 10 actions`，但断言期望 12：

- `tests/test_concurrent_execution.py:112-134`

这不是 Phase 3 新增 bug，但它会阻碍后续判断 `DAG_SERIAL_EXECUTION=false` 是否可恢复。当前 v14 Phase 4 要做 resume / 并发状态稳定性时，这类测试需要先修正。

建议：

- 明确该测试是期望 action 节点完成数，还是全部节点完成数。
- 若是全部节点，应断言 13；若是 action，应筛 `node_type == ACTION` 并断言 10。

### P3 - UI / 文档仍有版本叙事过旧

交互欢迎页仍显示 `Manus Demo v6` 和早期 ReActEngine v2 叙述：

- `main.py:622-646`

这不影响运行，但会让使用者误判当前系统状态。Phase 3 后，当前叙事应更新为 v14 in progress / v13 + v14 Phase 1-3。

建议：

- 更新 `main.py` welcome panel。
- 将 `CLAUDE.md` / `AGENTS.md` / codemap 的版本状态统一到“v13 + v14 Phase 1-3 completed”。

## 测试验证记录

### 通过

```bash
.venv/bin/python -m pytest \
  tests/test_v14_reasoning_tokens.py \
  tests/test_v14_reasoning_engine.py \
  tests/test_tool_call_helpers.py \
  tests/test_token_attribution.py \
  -q -o asyncio_mode=auto
```

结果：`87 passed in 2.63s`。

### 未通过 / 未完成

系统 Python 环境不可用：

```bash
python -m pytest ...
```

失败原因：`python` 命令不存在。

```bash
python3 -m pytest ...
```

失败原因：系统 Python 3.14 环境缺少 `openai`，且无 pytest-asyncio 配置。

`.venv` 全量离线回归未完成：

```bash
.venv/bin/python -m pytest tests/ -q -o asyncio_mode=auto --ignore=tests/test_llm_integration.py
```

失败原因：真实 DDGS worker thread abort。

并发测试单独失败：

```bash
.venv/bin/python -m pytest tests/test_concurrent_execution.py -q -o asyncio_mode=auto
```

失败原因：`test_medium_parallelism` 期望完成 12 个节点，实际 13 个节点完成。

## 建议修复顺序

1. **API message sanitizer**
   - 先阻止 `thinking_content` 进入 `chat.completions.create()`。
   - 增加 API payload schema 测试。

2. **ReasoningEngine 熔断**
   - 增加 `MAX_THINKING_ROUNDS` / `MAX_REASONING_LLM_CALLS`。
   - usage 缺失时使用 thinking 字符数估算。
   - budget 检查覆盖 final/tool-call 响应。

3. **修 ReActEngine reasoning-only 消息流**
   - 避免连续 user messages。
   - 无工具结果时不要注入 “based on tool results”。

4. **修 ShellTool sandbox**
   - `_run_shell()` 改实例方法，使用 `self._workdir`。

5. **Phase 4 DRY**
   - 抽 `react/engine_helpers.py`。
   - 统一 ReActEngine / ReasoningEngine / Emergent / GoalDriven 的工具执行语义。

6. **修测试隔离**
   - 修 `test_concurrent_execution.py` 断言。
   - 所有真实 DDGS 路径改为 integration 或 mock。

7. **清理 prompt 注入边界**
   - Reflector 禁用工具类 guidance。
   - Planner 的 HITL wording 改成“executor may ask user”。

## 对 Phase 4 的影响

Phase 4 不建议直接从 `reasoning_effort × ToolRouter` 开始。原因是当前两个 engine 的工具执行块仍重复，且 API payload 尚未隔离内部字段。如果先加 `reasoning_effort`，会把策略判断嵌到两个重复循环里，后续抽 helper 时再迁移一次。

更稳的 Phase 4 拆法：

1. `api_message_sanitizer` + 回归测试。
2. `ReasoningEngine` 熔断补齐。
3. `engine_helpers.py` 抽工具执行。
4. 再做 `reasoning_effort × ToolRouter`。
5. 最后接 `Task Resume`，因为 resume 需要稳定的内部 transcript / API payload 分层。

