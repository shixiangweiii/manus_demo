# HITL 核心流程详解

> 版本：v13.0 | 日期：2026-05-16

## 1. 端到端流程（天气查询示例）

以用户提问"今天天气怎么样"为例，完整追踪 HITL 在 simple flat 路径中的执行流程：

```
┌─────────────────────────────────────────────────────────────────────┐
│  用户输入: "今天天气怎么样"                                           │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  OrchestratorAgent.run()                                            │
│  ├─ reset_task_state()  ← 重置 _prompt_count=0                     │
│  ├─ classify_task() → "simple"                                      │
│  ├─ create_plan() → [Step1: 查位置, Step2: 查天气, Step3: 汇总]      │
│  └─ _execute_and_reflect_simple()                                   │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Step 1: "查询用户位置"                                              │
│  ReActEngine.execute(prompt, context, ...)                          │
│  ├─ Iteration 1: LLM 决定调用 get_user_location                     │
│  │   ├─ UserLocationTool.execute() → "上海 (APPROXIMATE, via IP)"  │
│  │   └─ tool message: "上海 (APPROXIMATE, via IP)" → append        │
│  ├─ Iteration 2: LLM 看到 APPROXIMATE，决定调用 ask_user             │
│  │   ┌──────────────────────────────────────────────────────────┐  │
│  │   │  ask_user(question="我通过IP定位发现您在上海，           │  │
│  │   │  这是正确的吗？如果不是，请告诉我您想查询的城市。")     │  │
│  │   │                                                        │  │
│  │   │  AskUserTool.execute():                                │  │
│  │   │  1. _prompt_count=1 (未超限) ✓                         │  │
│  │   │  2. _interactive_mode=True ✓                           │  │
│  │   │  3. Semaphore(1) 获取 ✓                                │  │
│  │   │  4. 创建 asyncio.Future[str]                           │  │
│  │   │  5. _on_user_prompt(question, id, future)              │  │
│  │   │     → orchestrator._handle_user_prompt()               │  │
│  │   │     → _emit("ask_user_prompt", {question, future})     │  │
│  │   │  6. await asyncio.wait_for(future, timeout=120)        │  │
│  │   │     ← ReAct 循环在此暂停，事件循环继续运行              │  │
│  │   └──────────────────────────────────────────────────────────┘  │
│  │                           │                                       │
│  │                           ▼                                       │
│  │   ┌──────────────────────────────────────────────────────────┐  │
│  │   │  main.py on_event("ask_user_prompt"):                    │  │
│  │   │  1. Rich Panel 展示问题                                  │  │
│  │   │  2. asyncio.create_task(_collect_and_resolve())          │  │
│  │   │     → asyncio.to_thread(console.input, "You > ")         │  │
│  │   │     ← 在线程池中等待用户输入，事件循环不阻塞             │  │
│  │   │                                                          │  │
│  │   │  用户输入: "北京"                                        │  │
│  │   │  3. future.set_result("北京")                            │  │
│  │   └──────────────────────────────────────────────────────────┘  │
│  │                           │                                       │
│  │                           ▼                                       │
│  │   AskUserTool.execute() 恢复:                                     │
│  │   7. user_response = "北京"                                       │
│  │   8. _emit("ask_user_response", {response: "北京", count: 1})    │
│  │   9. 返回 "User response: 北京"                                   │
│  │  10. Semaphore(1) 释放                                            │
│  │                                                                    │
│  │   tool message: "User response: 北京" → append to messages        │
│  ├─ Iteration 3: LLM 看到用户确认"北京"，决定调用 web_search          │
│  │   ├─ WebSearchTool.execute("北京今天天气") → 天气结果              │
│  │   └─ tool message: 天气结果 → append                              │
│  ├─ Iteration 4: LLM 有了完整信息，不再调用工具                       │
│  │   └─ 返回 StepResult(success=True, output="北京天气...")           │
│  └─ Step 1 完成 ✓                                                    │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Step 2: "查询天气详情" (可能已在上一步中完成)                        │
│  → StepResult(success=True)                                         │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Reflector.reflect() → passed=True                                  │
│  _synthesize_final_answer() → "北京今天天气：小雨，22-29°C..."       │
│  （使用用户确认的"北京"，而非 IP 推断的"上海"）                       │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  task_complete: "北京今天天气：小雨，22-29°C..."                      │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. asyncio.Future 桥接机制详解

### 2.1 为什么需要 Future

现有 `_emit(event, data)` 是 **fire-and-forget**：事件从 Agent 发出，UI 接收并渲染，没有返回通道。但 ask_user 需要**请求-响应**模式：Agent 提出问题，等待用户回答。

`asyncio.Future` 完美解决了这个问题：

```
AskUserTool (在 asyncio 事件循环中)      UI 层 (也在同一个事件循环中)
    │                                          │
    ├─ 创建 Future ←──────────────────────────┐│
    ├─ _emit("ask_user_prompt", {future}) ────┤│
    ├─ await future  ← 暂停，不阻塞事件循环    ││
    │                                          ││
    │         asyncio.create_task()             ││
    │              │                            ││
    │              ▼                            ││
    │         asyncio.to_thread(console.input)  ││
    │              │                            ││
    │              ▼                            ││
    │         用户输入 ──────────────────────→ future.set_result()
    │                                          ││
    ├─ future 已 resolve ←─────────────────────┘│
    ├─ 继续执行                                 │
    └─ 返回工具结果                              │
```

### 2.2 与现有 on_event 的兼容性

- `_emit` 仍然是同步的、fire-and-forget 的
- Future 作为 data dict 的一个字段传递
- UI 层从 data 中取出 Future 并 resolve 它
- **不需要修改 `_emit` 的签名或行为**

### 2.3 并发场景

```
ReActEngine 的 asyncio.gather 中:
  Task A: web_search("北京天气") → 正常执行，1-2秒完成
  Task B: ask_user("这是正确的城市吗？") → await Future，等待用户

两个 Task 并发运行。Task A 完成后结果已在 messages 中，
Task B 等待用户输入时，事件循环继续处理 Task A 的结果。

Semaphore(1) 确保：如果 LLM 同时调用两个 ask_user，
第二个会等第一个完成后再弹出提问面板。
```

## 3. 防护机制流程

### 3.1 最大提问次数

```
AskUserTool.execute():
  _prompt_count >= _max_prompts (5)?
     ├─ Yes → 返回 "Error: Maximum user prompts reached..."
     │         → ReActEngine 检测 Error: 前缀
     │         → ToolRouter.record_failure()
     │         → LLM 看到错误，改为自主推理
     │
     └─ No → _prompt_count += 1
              → 继续执行提问
```

### 3.2 超时处理

```
AskUserTool.execute():
  await asyncio.wait_for(future, timeout=120)
     ├─ Future 在 120s 内 resolve → 返回 "User response: ..."
     │
     └─ TimeoutError → _emit("ask_user_timeout", ...)
                       → 返回 "Error: User did not respond within 120s..."
                       → ToolRouter.record_failure()
                       → LLM 看到错误，改为自主推理
```

### 3.3 用户取消

```
_collect_and_resolve() in main.py:
  console.input() raises KeyboardInterrupt/EOFError
     └─ future.set_result("(user cancelled)")
        → AskUserTool 返回 "User response: (user cancelled)"
        → LLM 看到用户取消，改为自主推理
```

### 3.4 非交互模式

```
AskUserTool.execute():
  _interactive_mode == False?
     └─ Yes → 返回 "Error: ask_user is not available in non-interactive mode..."
              → ToolRouter.record_failure()
              → LLM 看到错误，改为自主推理
```

## 4. 事件传播路径

```
AskUserTool.execute()
  │
  ├─ _on_user_prompt(question, prompt_id, future)
  │    │
  │    └─ OrchestratorAgent._handle_user_prompt()
  │         │
  │         └─ _emit("ask_user_prompt", {question, prompt_id, response_future})
  │              │
  │              ├─→ main.py on_event()     → Rich Panel + asyncio.to_thread(input)
  │              ├─→ TracingBridge (if on)   → 创建 Span
  │              └─→ EvaluationProbe (if on) → 记录指标
  │
  ├─ _on_event("ask_user_response", {prompt_id, response, prompt_count})
  │    │
  │    └─ _emit("ask_user_response", ...)
  │         ├─→ main.py on_event()     → "User responded: ..."
  │         ├─→ TracingBridge (if on)   → 记录事件
  │         └─→ EvaluationProbe (if on) → 记录指标
  │
  └─ _on_event("ask_user_timeout", {prompt_id, timeout, prompt_count})
       │
       └─ _emit("ask_user_timeout", ...)
            ├─→ main.py on_event()     → "User input timed out..."
            ├─→ TracingBridge (if on)   → 记录事件
            └─→ EvaluationProbe (if on) → 记录指标
```

## 5. 数据流

### 5.1 LLM 看到的消息序列

```
messages = [
  {"role": "system", "content": "<system prompt with HITL guidance>"},
  {"role": "user", "content": "Execute the following step: 查询用户位置"},
  {"role": "assistant", "content": null, "tool_calls": [
    {"id": "tc1", "function": {"name": "get_user_location", "arguments": "{}"}}
  ]},
  {"role": "tool", "tool_call_id": "tc1", "content": "上海 (APPROXIMATE, via IP)"},
  {"role": "user", "content": "Continue executing... [convergence hint]"},
  {"role": "assistant", "content": null, "tool_calls": [
    {"id": "tc2", "function": {"name": "ask_user", "arguments": "{\"question\": \"...\"}"}}
  ]},
  {"role": "tool", "tool_call_id": "tc2", "content": "User response: 北京"},
  {"role": "user", "content": "Continue executing..."},
  {"role": "assistant", "content": null, "tool_calls": [
    {"id": "tc3", "function": {"name": "web_search", "arguments": "{\"query\": \"北京今天天气\"}"}}
  ]},
  {"role": "tool", "tool_call_id": "tc3", "content": "北京天气：小雨，22-29°C..."},
  {"role": "user", "content": "Continue executing..."},
  {"role": "assistant", "content": "根据查询结果，北京今天天气为小雨..."}  ← 不再调用工具，循环结束
]
```

### 5.2 ToolCallRecord 记录

```python
[
  ToolCallRecord(tool_name="get_user_location", parameters={}, result="上海 (APPROXIMATE...)"),
  ToolCallRecord(tool_name="ask_user", parameters={"question": "..."}, result="User response: 北京"),
  ToolCallRecord(tool_name="web_search", parameters={"query": "北京今天天气"}, result="北京天气：..."),
]
```

## 6. 与 simple path 各阶段的集成

```
Orchestrator.run("今天天气怎么样")
  │
  ├─ [1] _gather_context()
  │      → 长期记忆 + 知识库 → combined_context
  │      → ask_user 不参与此阶段
  │
  ├─ [2] classify_task()
  │      → "simple"
  │      → ask_user 不参与此阶段
  │
  ├─ [3] create_plan()
  │      → [Step1: 查位置, Step2: 查天气, Step3: 汇总]
  │      → ask_user 不参与此阶段（Planner 不调用工具）
  │
  ├─ [4] _execute_and_reflect_simple()
  │      │
  │      ├─ execute_step(Step1) ← ask_user 在此被调用
  │      │   └─ ReActEngine.execute() → 可能调用 ask_user
  │      │
  │      ├─ execute_step(Step2) ← ask_user 也可能在此被调用
  │      │   └─ ReActEngine.execute() → 可能调用 ask_user
  │      │
  │      └─ ...
  │
  ├─ [5] reflect()
  │      → Reflector 检查是否使用了 ask_user 消除歧义
  │      → 如果未使用但有歧义 → 在 suggestions 中提示
  │
  ├─ [6] _synthesize_final_answer()
  │      → 如果用户通过 ask_user 提供了更正 → 使用更正后的信息
  │
  └─ [7] _store_memory()
         → 长期记忆中保存任务和答案（含 ask_user 交互记录）
```
