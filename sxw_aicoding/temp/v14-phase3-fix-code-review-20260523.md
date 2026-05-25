# v14 Phase 3 修复后代码评审

> 日期：2026-05-23  
> 范围：对照上一轮 `v14-phase3-deep-code-review-second-pass-20260523.md` 后的最新代码改动。  
> 方式：阅读当前 git diff + 关键源码复核 + 定向测试 + 离线全量回归尝试。  
> 约束：本次只做代码评审记录，不修改业务代码。

## 总体结论

这轮修复覆盖了上一轮评审中的多数核心问题：

- `LLMClient` 增加 `_sanitize_messages_for_api()`，`chat` / `chat_with_tools` / `chat_json` 发送前会剥离 `thinking_content`。
- `ReasoningEngine` 增加 `MAX_THINKING_ROUNDS`，避免 usage 缺失或 token tracking 关闭时无限 pure-thinking 调用。
- thinking budget 已前移为全分支 guard，tool-call 分支超预算会阻止继续执行工具。
- `ReActEngine` reasoning-only 分支改为 `needs_explicit_answer`，避免连续追加两条 user message。
- `ShellTool` 改为使用实例级 `_workdir`。
- Planner/Reflector 关闭了不属于自身能力边界的工具 guidance。
- Context compression 已传递 `caller_tag`。
- 并发测试 `12 vs 13` 的错误断言已修正。

但还不能视为完全收口。最需要继续处理的是：sanitizer 修掉了 API schema 问题，但没有同步解决“thinking 是否回灌模型”的语义问题；`MAX_THINKING_ROUNDS` 名义上是“连续纯思考轮次”，当前实现却是累计计数；全量离线回归仍会因为真实 DDGS worker thread abort。

## Findings

### P1 - sanitizer 修掉 schema 泄漏，但 reasoning-only 续写仍引用模型看不到的 thinking

当前 `_sanitize_messages_for_api()` 会在 LLMClient 边界删除 `thinking_content`：

- `llm/client.py:33-49`
- `llm/client.py:155-164`
- `llm/client.py:217-228`
- `llm/client.py:280-290`

这修复了“非标准字段进入 OpenAI-compatible API payload”的 P0。但 `ReActEngine` 和 `ReasoningEngine` 的续写提示仍然假设模型能看到上一轮 reasoning：

- `react/engine.py:192-194`：`Please provide your final answer based on the reasoning above.`
- `react/reasoning_engine.py:94-99`：`Based on your previous thinking, continue with the task or call a tool.`

问题是 sanitized API payload 中，上一轮 assistant message 只剩：

```python
{"role": "assistant", "content": ""}
```

`thinking_content` 还留在内部 transcript，但 provider 看不到。于是下一轮 user prompt 说“based on previous thinking”，实际模型上下文里并没有 previous thinking。这会让 reasoning-only 模型的连续思考链断裂，尤其在 `ReasoningEngine` 的 pure-thinking loop 中更明显。

建议：

- 明确策略：raw thinking 不回灌模型，还是以 summary 形式回灌。
- 如果不回灌，去掉 “based on previous thinking / reasoning above” 类措辞。
- 如果要回灌，新增一个合法的 short thinking summary message，而不是发送内部字段。
- 增加测试：两轮 reasoning-only 后，捕获第二次 API payload，断言 prompt 中要么不引用 previous thinking，要么确实包含合法 thinking summary。

### P1 - `MAX_THINKING_ROUNDS` 标称“连续”但实现为累计计数

配置注释写的是“连续纯思考轮次硬上限”：

- `config.py:153-155`

实现中 `thinking_rounds` 只初始化一次：

- `react/reasoning_engine.py:70-72`

pure-thinking 分支递增：

- `react/reasoning_engine.py:201-208`

但 tool-call 分支没有重置：

- `react/reasoning_engine.py:171-186`

因此当前行为是“本次 execute 生命周期内累计 pure-thinking rounds”，不是“连续 pure-thinking rounds”。如果模型先思考 3 轮、调用一次工具、再思考 3 轮，在默认 `MAX_THINKING_ROUNDS=5` 下会失败；按“连续”语义则不该失败。

新增测试的名字也暗示应当 reset：

- `tests/test_v14_reasoning_engine.py:444-448`

但测试数据只在工具调用后追加 1 轮 thinking：

- `tests/test_v14_reasoning_engine.py:455-465`

这不会暴露未 reset 的问题。

建议二选一：

- 如果想限制连续空转：在 `has_tool_calls` 和 `has_final_answer` 分支重置 `thinking_rounds = 0`，并补 3 thinking + tool + 3 thinking 的测试。
- 如果想限制总 pure-thinking 轮次：改名为 `MAX_TOTAL_THINKING_ROUNDS`，修改注释、日志和测试名，避免语义误导。

### P2 - 全量离线回归仍会触发真实 DDGS 并 abort

本轮新增了 `conftest.py` 注册 `integration` marker，但没有实际隔离触网路径。离线全量回归仍失败：

```bash
.venv/bin/python -m pytest tests/ -q -o asyncio_mode=auto --ignore=tests/test_llm_integration.py
```

结果：

```text
Fatal Python error: Aborted
...
tools/web_search.py", line 238 in _sync
.venv/lib/python3.12/site-packages/ddgs/http_client.py
```

直接触发真实 WebSearchTool 的测试仍然存在，例如：

- `tests/test_tracing.py:721-724`
- `tests/test_tracing.py:736-740`
- `tools/web_search.py:234-240`

这说明上一轮 P2 “全量测试没有隔离真实 DDGS 路径”尚未修复。注册 marker 只能消除 unknown marker warning，不能阻止测试触网。

建议：

- 对 tracing 里的 `WebSearchTool.traced_execute()` 使用 fake tool 或 monkeypatch `WebSearchTool._ddgs_search`。
- 或将真实 DDGS 测试标记为 `@pytest.mark.integration`，默认回归命令使用 `-m "not integration"`。
- 更稳的做法是在 root `conftest.py` 为非 integration 测试全局 monkeypatch DDGS fallback。

### P2 - tracing prompt 视图仍未与 API payload 明确对齐

API 调用现在使用 sanitized messages，但 tracing 仍从原始 `messages` 构造 prompt attribute：

- `llm/client.py:151-155` 先 `_start_llm_span(..., messages, ...)`，再 sanitize。
- `llm/client.py:213-217` 同样先记录原始 messages，后 sanitize。
- `llm/client.py:480-509` prompt trace 只记录 `content`、`tool_calls`、`tool_call_id`、`name`，没有显式说明这是 internal transcript 还是 API payload。

当前因为 tracing 没记录 `thinking_content`，trace 与 API payload 大体都看不到 thinking；但代码语义上仍是“trace 输入来自 internal messages，API 输入来自 sanitized messages”。后续如果加入 thinking summary 回灌或更多内部字段，这里容易再次漂移。

建议：

- `_start_llm_span()` 接收或内部生成 `api_messages`，让 `gen_ai.prompt.content` 表示真实 API payload。
- 如果需要观测内部 thinking，使用单独 attribute，例如 `manus.internal.thinking_summary`。

## 已修复项复核

### API message sanitizer

实现位置：

- `llm/client.py:33-49`
- `llm/client.py:155-164`
- `llm/client.py:217-228`
- `llm/client.py:280-290`

新增测试：

- `tests/test_api_sanitizer.py:20-62`
- `tests/test_api_sanitizer.py:112-168`

评审结论：schema 泄漏本身已修复，测试覆盖了 `chat` / `chat_with_tools` / `chat_json` 三个入口。

### chat_json fallback scope

实现位置：

- `llm/client.py:297-311`

新增测试：

- `tests/test_api_sanitizer.py:175-258`

评审结论：比上一版更安全，只对 `BadRequestError` 且错误文本包含 `response_format` 时 fallback。后续可再兼容部分 OpenAI-compatible provider 的不同 unsupported 错误类型，但当前修改方向正确。

### ReasoningEngine 熔断和预算

实现位置：

- `config.py:153-155`
- `react/reasoning_engine.py:164-185`
- `react/reasoning_engine.py:201-241`

新增测试：

- `tests/test_v14_reasoning_engine.py:387-442`
- `tests/test_v14_reasoning_engine.py:491-534`

评审结论：usage 缺失/关闭 token tracking 的无限调用风险已被硬上限挡住；budget 覆盖 tool-call/final-answer 分支也已补上。剩余问题是 rounds 的“连续 vs 累计”语义。

### ReActEngine reasoning-only 重复 user message

实现位置：

- `react/engine.py:161`
- `react/engine.py:190-196`
- `react/engine.py:251-255`

评审结论：连续追加 user message 的问题已修正。但如 P1 所述，sanitizer 后 “reasoning above” 对 provider 不可见，仍需处理语义。

### ShellTool 实例级 sandbox

实现位置：

- `tools/shell_tool.py:129-150`

新增测试：

- `tests/test_shell_tool.py:219-254`

评审结论：实例 `_workdir` 已生效，SubAgent/future sandbox 隔离的基础问题已修。

### Planner / Reflector prompt guidance

实现位置：

- `agents/planner.py:113-121`
- `agents/planner.py:255-262`
- `agents/reflector.py:95-102`

评审结论：工具 guidance 边界已收紧。建议后续补一两个 prompt unit test，避免未来改默认参数时回退。

### Context compression caller_tag

实现位置：

- `agents/base.py:99-107`
- `agents/base.py:120-127`
- `agents/base.py:147-154`
- `react/engine.py:200-203`
- `react/reasoning_engine.py:104-107`
- `context/manager.py:99-146`
- `context/manager.py:235-259`

评审结论：主要调用路径已传递 caller_tag。当前默认 `"ContextManager"` 也能覆盖直接调用场景。

## 验证结果

语法检查通过：

```bash
.venv/bin/python -m py_compile \
  llm/client.py react/reasoning_engine.py react/engine.py context/manager.py \
  agents/base.py agents/planner.py agents/reflector.py tools/shell_tool.py \
  config.py main.py tracing/config.py
```

定向测试通过：

```bash
.venv/bin/python -m pytest \
  tests/test_api_sanitizer.py \
  tests/test_v14_reasoning_engine.py \
  tests/test_shell_tool.py \
  tests/test_token_attribution.py \
  tests/test_concurrent_execution.py \
  -q -o asyncio_mode=auto
```

结果：

```text
80 passed in 5.00s
```

离线全量回归未通过，原因是 DDGS 真实路径 abort：

```bash
.venv/bin/python -m pytest tests/ -q -o asyncio_mode=auto --ignore=tests/test_llm_integration.py
```

结果：

```text
Fatal Python error: Aborted
tools/web_search.py", line 238 in _sync
```

## 建议收尾顺序

1. 先决定 thinking 回灌策略，修复 sanitizer 后 reasoning-only prompt 的语义断裂。
2. 明确 `MAX_THINKING_ROUNDS` 是连续还是累计，并让实现、命名、测试一致。
3. 隔离真实 DDGS，使 `tests/ --ignore=test_llm_integration.py` 可以稳定跑完。
4. 补 prompt guidance 回归测试。
5. 再进入 Phase 4 的 DRY 抽取和 `reasoning_effort × ToolRouter`。

## 最终判断

这轮修复质量整体是正向的，之前两个 P0 的最危险部分已经被压住了：API schema 泄漏已挡在 LLMClient 边界，无 usage 无限调用也有了硬熔断。当前剩余问题主要集中在“语义一致性”和“测试隔离”上。

如果下一步要继续 Phase 4，建议先用一个小 patch 处理 P1 的 reasoning-only 语义和 thinking rounds 语义，再处理 DDGS 测试隔离。否则 Phase 4 的 resume / router / tracing 会继续继承这些不稳定边界。
