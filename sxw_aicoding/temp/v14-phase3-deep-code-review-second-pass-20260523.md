# v14 Phase 3 深度代码评审二次复查

> 日期：2026-05-23  
> 范围：截止 v14 Phase 3 后的当前源码，基于上一份 `v14-phase3-deep-code-review-20260523.md` 做二次复核。  
> 约束：本次只做评审和记录，不修改业务代码。  
> 参考：`sxw_aicoding/记忆/v14-phase1-work-progress-20260522.md`、`sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md`、当前源码。

## 二次复查结论

上一轮评审的主结论成立：Phase 3 的方向是对的，但现在还不能进入“稳定收口”状态。最关键的阻塞仍是两个 P0：

1. 内部消息字段 `thinking_content` 和 OpenAI-compatible API payload 没有隔离。
2. `ReasoningEngine` 在 provider 不返回 usage 或 token tracking 关闭时缺少独立 LLM-call 熔断。

这两个问题不是风格问题，也不是单纯测试缺失，而是会影响真实模型运行的控制面问题。Phase 4 如果继续做 `reasoning_effort × ToolRouter`、Task Resume、DRY 重构，建议先把这两个控制面问题处理掉，否则后续 resume/tracing/router 都会把不稳定的内部 transcript 语义继续放大。

本次复查对上一轮做了三个校准：

- `thinking_content` sanitizer 不能只是简单删除字段，否则 `ReasoningEngine` 的 pure-thinking loop 里“Based on your previous thinking”会失去事实依据。这里需要明确“思考内容是否回灌模型”的产品策略。
- `_current_log` 不是当前 SubAgent 私有 engine 路径的 immediate bug；但 DAG 并行共享 engine 时，它只能代表最后一次绑定的活动日志。若 Phase 4 Task Resume 或观测链路依赖它，需要先改成 run-local handle。
- Planner/Reflector 的 prompt guidance 问题不只是 Reflector：Planner 也不应被注入“你可以调用工具”的措辞，Planner 更准确的角色是“为 Executor 规划可调用工具的步骤”。

## 已复核的上一轮 Findings

### 1. P0 - `thinking_content` 发送给 API：结论维持

证据链仍然成立：

- `react/engine.py:201-206` 直接调用 `llm_client.chat_with_tools(messages, ...)`。
- `react/engine.py:213-219` 把 `thinking_content` 写进 assistant message。
- 下一轮循环复用同一个 `messages` 列表，所以该内部字段会进入下一次 API 请求。
- `react/reasoning_engine.py:110-115`、`react/reasoning_engine.py:128-147` 同样存在该路径。
- `llm/client.py:140-145`、`llm/client.py:201-208` 原样把 `messages` 传给 OpenAI SDK，没有 schema sanitizer。

二次复查补充：这不是简单“删字段”能解决的。当前 `ReasoningEngine` 的续写提示在没有工具调用时会说：

- `react/reasoning_engine.py:96-99`：`Based on your previous thinking, continue with the task or call a tool.`

如果发送 API 前移除 `thinking_content`，模型其实看不到 previous thinking；如果不移除，严格 provider 可能 400。因此这里需要先定策略：

- 策略 A：内部 thinking 永远不回灌模型。那就必须改掉 “previous thinking” 类提示，避免虚假上下文。
- 策略 B：允许回灌，但必须把 thinking 转成合法 message content，例如压缩为一条 system/developer/user-visible summary。
- 策略 C：保留内部 transcript，用独立的 short reasoning summary 回灌，而不是原始 thinking 全量回灌。

建议优先选 C。它最符合 demo 项目的 observability 目标，也能避免把长 thinking 内容无限推高 prompt token。

缺失测试：

- 两轮 ReAct/Reasoning 执行后，捕获第二次 `chat_with_tools` 入参，断言 message 不含 `thinking_content` 等内部 key。
- 同时断言如果系统选择不回灌 thinking，续写提示不能声称基于 previous thinking。

### 2. P0 - `ReasoningEngine` 无限 LLM 调用风险：结论维持

证据链仍然成立：

- `react/reasoning_engine.py:78` 的 loop 条件只看 `iteration < self.max_iterations`。
- pure-thinking round 在 `react/reasoning_engine.py:180-206` 不增加 `iteration`。
- thinking budget 来自 `llm_client.get_call_records()` diff：`react/reasoning_engine.py:108-126`。
- `llm/client.py:351-365` 在 token tracking disabled 或 usage missing 时不会追加可用 record。

因此当模型连续返回：

- `content=""`
- `reasoning_content` 非空
- `tool_calls=None`
- usage 缺失或 reasoning_tokens 为 0

循环既不会增加 `iteration`，也不会增加 `total_thinking_tokens`，会持续发起 LLM 调用。

二次复查补充：这个风险不能只靠 `MAX_THINKING_TOKENS` 修。运行时熔断必须和 UI token 统计解耦，因为关闭 token tracking 不应该关闭安全熔断。

建议的最小修复方向：

- 增加 `thinking_rounds` 或 `llm_calls` 计数。
- 配置项可叫 `MAX_REASONING_LLM_CALLS` 或 `MAX_THINKING_ROUNDS`。
- usage 缺失时用 `len(thinking)` 做粗估预算。
- 所有 LLM 响应类型都先做预算检查，再决定 final/tool/pure-thinking 分支。

缺失测试：

- mock `get_call_records()` 始终返回空列表，mock LLM 连续返回 reasoning-only，断言在有限调用次数内失败退出。
- mock `TOKEN_TRACKING_ENABLED=false`，断言熔断仍生效。

### 3. P1 - ReActEngine reasoning-only 分支：结论维持，但建议更聚焦

证据链：

- `react/engine.py:167-169` 每轮开始就增加 iteration。
- reasoning-only 时 `react/engine.py:247-254` 追加一条 user message 并 `continue`。
- 下一轮 `react/engine.py:189-194` 又会追加通用 `continue_msg`。

结果是连续 user messages，并且其中一条会说 `tool results above`，即使之前没有工具结果。这个问题对默认 ReActEngine 的影响是语义污染，不一定立即导致 API 报错，所以优先级低于两个 P0。

二次复查建议：如果 `ENABLE_REASONING_ENGINE=false`，默认 ReActEngine 可以只做轻量兼容，不必半支持 reasoning-only loop。更稳的策略是：

- 发现 reasoning-only 响应时，下一轮只追加一条覆盖提示，不追加通用 continue_msg。
- 或直接返回明确错误：当前模型产生了 reasoning-only response，请开启 `ENABLE_REASONING_ENGINE`。

### 4. P1 - thinking budget 只检查 pure-thinking 分支：结论维持

证据链：

- `react/reasoning_engine.py:123-126` 每轮累计 reasoning tokens。
- `react/reasoning_engine.py:184-205` 只在 pure-thinking 分支检查预算。
- final answer 分支 `react/reasoning_engine.py:167-179` 不检查。
- tool-call 分支 `react/reasoning_engine.py:163-166`、`react/reasoning_engine.py:208-276` 不检查。

二次复查补充：如果预算语义是“限制思考成本”，那么工具调用前尤其应该检查，因为工具调用可能继续放大成本。如果预算语义只是“限制空转思考”，变量名和配置名就应该更明确，比如 `MAX_PURE_THINKING_TOKENS`。当前命名更像全局 thinking budget。

### 5. P1 - ShellTool 实例级 sandbox 绕过：结论维持

证据链：

- `tools/shell_tool.py:59-61` 初始化了实例级 `_workdir`。
- `tools/shell_tool.py:129-135` 的 `_run_shell()` 是 `@staticmethod`，执行 cwd 使用全局 `config.SANDBOX_DIR`。
- `tools/shell_tool.py:150` 输出 working directory 也使用全局值。

二次复查补充：这对当前主路径不一定立即出错，因为默认 ShellTool 本身就是全局 sandbox。但它与 SubAgent sandbox isolation / future per-agent workspace 的设计目标冲突。Phase 4 如果要做 Task Resume 或更强隔离，这里必须先清掉。

缺失测试：

- 手动把 `tool._workdir` 改成临时目录，执行 `pwd`，断言结果使用实例目录。

## 二次复查新增 Findings

### P1 - API sanitizer 与 tracing/context 之间需要统一消息视图

当前 tracing 的 prompt 记录来自原始 `messages`：

- `llm/client.py:456-485`

它记录 `content`、`tool_calls`、`tool_call_id`、`name`，但不记录 `thinking_content`。与此同时，API 当前收到的却是包含 `thinking_content` 的原始 `messages`。这会形成一个调试错觉：trace 里看不到的字段，真实 API payload 里却存在。

如果后续增加 `_sanitize_messages_for_api()`，还需要同时定义 tracing 记录的是哪一种视图：

- `api_prompt.content`：真实发送给 provider 的 payload。
- `internal_prompt.content`：内部 transcript，可包含 thinking summary。
- 或者在 trace attributes 中明确区分 `gen_ai.prompt.content` 和 `manus.internal_prompt.thinking_content`。

否则真实 400、token 估算、context compression、trace replay 会出现互相对不上的问题。

建议：

- sanitizer 放在 `LLMClient` 边界。
- `_start_llm_span()` 记录 sanitized payload，另用单独 attribute 记录内部 thinking 摘要。
- `ContextManager` 继续可以估算/压缩内部 transcript，但传给 API 前必须统一 sanitize。

### P2 - Context compression 的 LLM 调用没有 caller_tag，token attribution 会进入 unknown

`BaseAgent.think*()` 已经给调用默认设置 `caller_tag=self.name`：

- `agents/base.py:106-127`

但 `ContextManager._summarize()` 直接调用：

- `context/manager.py:258`：`llm_client.chat(summary_prompt, temperature=0.2, max_tokens=1024)`

这里没有传 `caller_tag`。因此一旦触发 context compression，摘要调用会落入默认空 caller，最终进入 Orchestrator token usage 的 `unknown` bucket。

这不是执行正确性 bug，但会影响 Phase 3 刚建立起来的 token attribution 可信度，尤其长任务中 context compression 可能是重要 token 消耗来源。

建议：

- `compress_if_needed(messages, llm_client, caller_tag: str = "")` 增加可选 caller_tag。
- `BaseAgent.think*()` 和 ReAct/ReasoningEngine 调用 context manager 时传入当前 agent/engine tag。
- 统计层可以把 compression 单独归类为 `ExecutorAgent.context_compression` 或 `ContextManager`.

缺失测试：

- 强制触发 `ContextManager.compress_if_needed()`，mock `llm_client.chat`，断言传入 `caller_tag`。

### P2 - Planner/Reflector guidance 注入边界仍偏宽

上一轮指出 Reflector 注入工具 guidance 不合适。二次复查发现 Planner 也有类似边界问题：

- `agents/reflector.py:95-97` 直接 `build_system_prompt(REFLECTOR_SYSTEM_PROMPT)`，所有 guidance 默认打开。
- `agents/planner.py:113-118` 和 `agents/planner.py:252-257` 只关闭了 `inject_subagent_guidance`，但 location/search/HITL guidance 仍默认打开。
- `agents/prompt_utils.py:188-230` 默认注入 context、location、search、subagent、HITL。

Planner 的 base prompt 本身说“executor agent has access to tools”，这是合理的。但额外 guidance 的措辞如果是 “You have access to ...”，就会把 Planner 的能力边界说错。Planner 不应该自己调用工具，它只能规划 Executor 去获取信息或询问用户。

建议：

- Reflector：除 `inject_context=True` 外，关闭所有工具类 guidance。
- Planner：保留 date context；location/HITL/search 改成 planner-specific wording，强调“plan a step for executor to use ...”，而不是“you have access”。
- Executor/Emergent/GoalDriven/ReAct 才使用真正 tool-user guidance。

缺失测试：

- Reflector system prompt 不包含 `web_search`、`fetch_url`、`ask_user`、`get_user_location` 的“可调用工具”指导。
- Planner system prompt 可以提及 Executor 工具，但不能说 Planner 自己 can call/use/access tools。

### P2 - `LLMClient.chat_json()` fallback 捕获过宽，可能掩盖真实 API 故障

`chat_json()` 的 JSON-mode 调用失败时会捕获所有 Exception：

- `llm/client.py:261-286`

注释说这是为了兼容不支持 `response_format` 的模型，但现在任何异常都会被当成“不支持 JSON mode”：

- 鉴权失败
- 网络失败
- rate limit
- provider 400 且不是 response_format 原因
- payload schema 错误，例如 `thinking_content` 非法

这会造成两个问题：

- 真实故障被重试成普通 `chat()`，排查难度上升。
- 对非 response_format 问题会多消耗一次 LLM 调用。

这个问题不是 v14 Phase 3 引入的核心 bug，但在当前 `thinking_content` schema 风险存在时，它会让症状更混乱。

建议：

- 只对明确的 `response_format` unsupported 错误 fallback。
- 其他异常直接抛出，并保留原始错误类型。
- 测试区分 “JSON mode unsupported” 与 “auth/rate/schema error”。

### P2 - `_current_log` 当前不是 SubAgent bug，但不适合作为通用并发观测源

`ReActEngine` 已经在注释中承认共享 engine 并发时 `_current_log` 只代表最后一次 rebind：

- `react/engine.py:145-159`

这对当前 SubAgent 私有 engine 失败快照路径是可以接受的。但 `ExecutorAgent.create_for_node()` 会共享 `_react_engine`：

- `agents/executor.py:125-146`

因此 DAG 并发时，如果未来 Phase 4 的 Task Resume、timeout recovery、debug viewer 或 tracing 想读 `_current_log`，读到的可能是另一个节点的活动日志。

建议：

- 不要把 `_current_log` 扩展为通用 resume 状态源。
- Phase 4 如果要做 resume，应引入 `run_id/node_id -> RunState` 映射，或让 `execute()` 返回/暴露 run-local handle。
- `SubAgent._failure_tool_calls()` 可以继续依赖私有 engine 的 `_current_log`，但注释需要写清楚边界。

## 测试验证

本次二次复查重新跑了两组验证。

通过：

```bash
.venv/bin/python -m pytest tests/test_v14_reasoning_tokens.py tests/test_v14_reasoning_engine.py tests/test_token_attribution.py tests/test_prompt_freshness.py -q -o asyncio_mode=auto
```

结果：

```text
77 passed in 1.93s
```

仍失败：

```bash
.venv/bin/python -m pytest tests/test_concurrent_execution.py -q -o asyncio_mode=auto
```

结果：

```text
FAILED tests/test_concurrent_execution.py::test_medium_parallelism
AssertionError: 期望 12 个节点完成，实际 13
```

复查后判断：这个失败大概率是测试期望写错，而不是 DAGExecutor 行为错。该测试构造了：

- 1 个 goal
- 2 个 subgoal
- 每个 subgoal 下 5 个 action，共 10 个 action

总节点数是 13，断言却写成 12：

- `tests/test_concurrent_execution.py:78-112` 构造 13 个节点。
- `tests/test_concurrent_execution.py:133-134` 断言 completed_count == 12。

这条仍应修，否则全量回归会被无关失败污染。

## Phase 4 前建议的修复顺序

建议不要直接继续 Phase 4 的大功能。更稳的顺序是：

1. 先做 message boundary：内部 transcript、API payload、trace payload 三者分清。
2. 给 `ReasoningEngine` 加 LLM-call/pure-thinking round 熔断，且不依赖 token tracking。
3. 修 ReActEngine reasoning-only 的重复 user message。
4. 修 ShellTool 实例 sandbox。
5. 修 Planner/Reflector guidance 注入边界。
6. 修 token attribution 中 context compression 的 caller_tag。
7. 修并发测试断言和 DDGS 单元测试隔离。
8. 再抽 `react/engine_helpers.py`，进入 Phase 4 的 DRY 和 ToolRouter 联动。

## 二次复查后的优先级表

| 优先级 | 问题 | 处理建议 |
| --- | --- | --- |
| P0 | `thinking_content` 非标准字段进入 API payload | 立即修，作为 Phase 4 前置 |
| P0 | `ReasoningEngine` usage 缺失时可能无限 LLM 调用 | 立即修，作为 Phase 4 前置 |
| P1 | ReActEngine reasoning-only 产生重复 user messages | 修完 P0 后处理 |
| P1 | thinking budget 只覆盖 pure-thinking | 与 ReasoningEngine 熔断一起处理 |
| P1 | ShellTool 绕过实例 sandbox | Phase 4 resume/isolation 前处理 |
| P1 | sanitizer、tracing、context 的消息视图不一致 | 与 API payload 修复一起设计 |
| P2 | Context compression caller_tag 缺失 | Phase 3 attribution 补口 |
| P2 | Planner/Reflector guidance 边界偏宽 | Prompt hygiene 修复 |
| P2 | chat_json fallback 捕获过宽 | 稳定性补强 |
| P2 | `_current_log` 不适合作为 DAG 并发通用观测源 | Phase 4 Task Resume 设计约束 |
| P2 | DDGS 路径未完全隔离、并发测试断言错误 | 回归质量修复 |

## 最终判断

Phase 3 现在更像“功能主线已打通，但边界还没硬化”。核心能力已经能在 mock 测试里跑通，局部 v14 测试也继续通过；但真实 provider、长任务和并发观测三个方向还存在明显风险。

如果目标是学习型 demo，当前代码已经足够展示 v14 的思路；如果目标是继续做 v14 Phase 4，并希望后续 resume/tool-router/reasoning-effort 不是建立在脆弱状态上，建议先用一个小的 hardening iteration 把 P0/P1 清掉，再继续扩展。
