# v14 Phase 2（ReasoningEngine + 迭代计数修正）代码评审

> **评审日期**：2026-05-22
> **评审范围**：当前工作树未提交的 10 个文件改动 + 1 个新增文件（`react/reasoning_engine.py`）+ 1 个新增测试（`tests/test_v14_reasoning_engine.py`）
> **评审依据**：`iteration-roadmap-v14-v19.md` §五 v14 路线图 + Phase 1 三轮历史评审（v1 / v2-ultrathink / v3-fix-audit）
> **结论**：**Phase 2 落地了 v2 ultrathink 评审里最关键的架构建议（新建 `reasoning_engine.py` 并行）+ 实现了迭代计数修正——这是 roadmap §五 子项 2 的核心**，同时修复了 Phase 1 v3-fix-audit 评审里的 N1/N2/N3 三个新引入问题。但 ReasoningEngine 与 ReActEngine 大量代码重复（DRY 违反）、含 3 个潜在执行 bug（P2-P4）、thinking-aware split 仍未实施。

---

## 一、Phase 2 全景

### 1.1 diff 统计

```
 agents/executor.py     | 30 +++++++++++++++++++++---------    ← dispatch 切换
 agents/orchestrator.py |  3 +++                               ← reasoning 聚合（同 Phase 1）
 config.py              |  9 +++++++++                          ← + 2 个新配置
 context/manager.py     |  5 +++++                              ← thinking_content 估算（同 Phase 1）
 llm/client.py          | 50 +++++++++++++++++++++++++++++++++  ← R1 字段读取（同 Phase 1）
 main.py                | 49 ++++++++++++++++++++++++++++++++  ← UI 加 N1/N2 修复
 react/engine.py        |  3 +++                                ← 仅 TODO 注释（同 Phase 1）
 schema.py              |  4 +++-                               ← reasoning_tokens 字段（同 Phase 1）
 tracing/bridge.py      |  3 +++                                ← root span + > 0 guard（修复 N3）
 tracing/spans.py       |  2 ++                                 ← AttrKey 常量（同 Phase 1）
 10 files changed, 132 insertions(+), 26 deletions(-)

 react/reasoning_engine.py            | 298 行（新增） ← Phase 2 主交付
 tests/test_v14_reasoning_engine.py   | 226 行（新增） ← Phase 2 测试
```

### 1.2 Phase 2 vs Phase 1 v3-fix-audit 增量识别

| 改动 | Phase 1 v3 时已存在 | Phase 2 新增 |
|---|---|---|
| `schema.py` reasoning_tokens 字段 | ✅ | — |
| `config.py` REASONING_TOKEN_TRACKING | ✅ | — |
| `config.py` ENABLE_REASONING_ENGINE / MAX_THINKING_TOKENS | — | ✅ **Phase 2** |
| `llm/client.py` R1 字段读取 + AttrKey + `_extract_thinking_content` | ✅ | — |
| `tracing/spans.py` 两个 AttrKey 常量 | ✅ | — |
| `tracing/bridge.py` root span 加 `> 0` guard | ❌ (Phase 1 N3) | ✅ **Phase 2 修复 N3** |
| `main.py` by_engine / by_caller 的 has_reasoning 条件渲染 | ❌ (Phase 1 N2) | ✅ **Phase 2 修复 N2** |
| `main.py` provider 算术语义注释 | ❌ (Phase 1 N1) | ✅ **Phase 2 修复 N1** |
| `agents/executor.py` ReasoningEngine 灰度 dispatch | — | ✅ **Phase 2** |
| `react/reasoning_engine.py` ReasoningEngine 类 + `_strip_thinking_from_content` | — | ✅ **Phase 2** |
| `tests/test_v14_reasoning_engine.py` 8 个测试 | — | ✅ **Phase 2** |
| `context/manager.py` thinking_content 估算 | ✅（但为死代码） | ⚠️ **现在不再是死代码**（ReasoningEngine 真写入了） |
| `react/engine.py` TODO 注释 | ✅ | ⚠️ **TODO 已过期未更新**（详见 P13） |

### 1.3 roadmap §五 v14 完成度刷新

| 子项 | Phase 1 (v3) | Phase 2 后 | Δ |
|---|---|---|---|
| **1. 双协议支持（含 reasoning_content 字段）** | 🟢 90% | 🟢 **95%** (ReasoningEngine 内 thinking 分离更完整) | +5% |
| **2. ReAct 迭代计数 + MAX_THINKING_TOKENS** | 🔴 0% | 🟢 **80%** (核心已实现，含 budget enforcement) | **+80%** |
| 3. Interleaved Thinking | 🔴 0% | 🔴 0% | 0 |
| 4. reasoning_effort × ToolRouter | 🔴 0% | 🔴 0% | 0 |
| 5. 任务持久化与恢复 (resume) | 🔴 0% | 🔴 0% | 0 |
| Harness-a：prompt_utils 抽 Harness 配置层 | 🔴 0% | 🔴 0% | 0 |
| Harness-b：tool_call_helpers strategies 可配置 | 🔴 0% | 🔴 0% | 0 |
| Harness-c：thinking-aware split | 🟡 30% | 🟡 30% | 0 |

**v14 §五 加权完成度**：16% → **30%**（+14 个百分点）

---

## 二、Phase 2 主交付物深度评审

### 2.1 ReasoningEngine 架构定位（优点）

**1. 正确落地 v2 ultrathink R7 / D1 关键架构建议**
- Phase 1 v2 评审反复强调："应新建 `react/reasoning_engine.py` 并行而非原地改 `react/engine.py`"
- roadmap §五 第 87 行也明确："`react/reasoning_engine.py`（新增，与 ReActEngine 并行）"
- Phase 2 真正落地了——继承 `ReActEngine`，灰度开关切换，旧引擎完全不动

**2. 沿用 `ENABLE_REACT_ENGINE_V2` 成功范式**
- `ENABLE_REASONING_ENGINE` 默认 `false`，需用户主动启用
- `executor.py:103-123` 的二选一 dispatch 与旧 `ENABLE_REACT_ENGINE_V2` 模式一致
- 出问题可一键回退到 `ReActEngine`

**3. 修正了核心迭代语义**
```python
# reasoning_engine.py:180-199
if has_tool_calls:
    iteration += 1              # 有工具调用 → 计为迭代
elif has_final_answer:
    iteration += 1              # 有最终答案 → 计为迭代并返回
    return StepResult(...)
else:
    # Pure thinking round — don't increment iteration
    logger.debug(...)
    if total_thinking_tokens > self.max_thinking_tokens:
        return StepResult(success=False, ...)
    continue
```
这是 roadmap §五 子项 2 的核心语义，**实现正确**。

**4. thinking budget 防御失控**
- `MAX_THINKING_TOKENS` 默认 10000，防止"光思考不行动"无限循环
- 超限明确报错并返回 `success=False`

**5. 修复了 Phase 1 N4 死代码**
```python
# reasoning_engine.py:150-151
if thinking:
    assistant_msg["thinking_content"] = thinking
```
Phase 1 v3 评审 N4 指出 `context/manager.py:83` 的 `msg.get("thinking_content", "")` 永远拿不到值——**Phase 2 真正写入了**这个键，死代码激活。

### 2.2 测试覆盖（优点）

- **4 个测试类、8 个测试用例**：覆盖 thinking-only 跳过、tool_calls 计数、budget 超限、`_strip_thinking_from_content` 三种场景、feature flag 默认值
- 测试用 `AsyncMock(side_effect=[...])` 模拟多轮 LLM 响应——**比 Phase 1 的 `LLMClient.__new__()` 模式更安全**（用 `MagicMock(spec=[...])` 限制可调用方法）

---

## 三、严重问题（合入前必修）

### P1（严重）：ReasoningEngine 与 ReActEngine 大量代码重复（DRY 违反）

**对比**：
- `ReActEngine.execute()` ~150 行（engine.py:116-340）
- `ReasoningEngine.execute()` 230 行（reasoning_engine.py:69-297）
- **其中约 80 行（tool 执行 + truncate_for_llm + tool_router 记录 + tool_messages 构建）几乎一字不差复制**

具体重复段落：
```python
# 两个文件几乎完全相同的逻辑：
async def _exec_one(tc: Any) -> tuple[...]:  # reasoning_engine.py:222-242 ≈ engine.py:259-281
    ...
executions = await asyncio.gather(*(_exec_one(tc) for tc in response_msg.tool_calls))
# 后续的 truncate / tool_router.record_xxx / tool_messages 也都重复
```

**后果**：
- 任何 ReActEngine 的 bug fix（例如 v1 评审中的 `_current_log` 并发安全修复、Wave-3 M2 的 budget cancel 兜底）**必须同时改两处**
- 代码 reviewer 与新成员极易遗漏其中一处，导致两个引擎行为漂移
- 项目内 git blame 历史会越来越乱（同样的 bug 在两个文件里被重新修复多次）

**正确做法**：
- 提取 `ReActEngine._execute_tool_calls(response_msg, tool_calls_log, step_id)` 方法
- 提取 `ReActEngine._build_continue_message(tool_calls_log)` 方法
- ReasoningEngine 仅 override `execute()` 中与 thinking 相关的部分（iteration 计数逻辑、thinking 分离、budget 检查）
- 工具执行、消息构建、tool_router 记录全部走父类共享方法

**修复复杂度**：中（30-50 行重构，但对 Phase 3+ 极其重要）

---

### P2（严重）：iteration 0 边界 bug — 多轮 thinking 后会重复发送原始 prompt

**Bug 位置**：`reasoning_engine.py:115-118`
```python
if iteration == 0 and len(messages) == (1 if system_hint else 0):
    user_input = prompt
else:
    user_input = continue_msg
```

**触发路径**：
1. **第 1 轮**：`iteration=0`, `len(messages)=0` → `user_input = prompt`
2. 第 1 轮返回 pure thinking（无 tool_calls 无 content）→ `continue`（iteration 仍为 0）
3. **第 2 轮**：`iteration=0`, `len(messages)=2`（user + assistant{thinking}）→ 条件 `len(messages) == 0` **不成立** → `user_input = continue_msg`

OK，这一步暂时没出问题。但看下一个场景：

**触发路径 B（更危险）**：
1. **第 1 轮**：`iteration=0`, `system_hint=""`, `len(messages)=0` → `user_input = prompt`
2. 第 1 轮 thinking-only → `continue`
3. **第 2 轮**：`iteration=0`, `len(messages)=2` ≠ 0 → `user_input = continue_msg = "Continue executing based on the tool results above."`

**严重问题**：第 2 轮的 `continue_msg` 内容是"基于上面工具结果继续执行"——**但此时根本没有任何工具调用过！** LLM 会困惑：你说"基于工具结果"，但消息里没有 tool message。

**对比 ReActEngine（engine.py:189-192）**：
```python
if iteration == 1:           # ReActEngine 用 iteration == 1（因为先 iteration += 1）
    user_input = prompt
else:
    user_input = continue_msg
```
ReActEngine 的逻辑是"第一轮发 prompt，后续发 continue"——但 ReActEngine 没有 pure thinking 跳过逻辑，所以"后续轮"一定意味着已经有工具调用。

**ReasoningEngine 的 thinking-only 跳过破坏了"iteration > 0 ⟹ 已有工具调用"这个不变量**。

**测试盲点**：`test_thinking_only_round_skipped` 用的 LLM mock 响应序列是 `[thinking, thinking, final_answer]`，第 3 轮直接返回 final answer。**测试没有覆盖"第 2 轮收到 continue_msg 之后 LLM 还是返回 thinking 或 tool_calls"的场景**——所以这个 bug 测试通过了。

**修复建议**：
```python
# 改为：以"是否已有工具调用记录"为判据，而非 iteration / messages 长度
if not tool_calls_log and len([m for m in messages if m["role"] != "system"]) == 0:
    user_input = prompt
elif not tool_calls_log:
    user_input = "Continue thinking about the task above."  # 专为 thinking-only 后续
else:
    user_input = continue_msg
```

**修复复杂度**：低（3-5 行）

---

### P3（严重）：MAX_THINKING_TOKENS budget 累加错误

**Bug 位置**：`reasoning_engine.py:141-143`
```python
call_records = self.llm_client.get_call_records()
if call_records:
    total_thinking_tokens += call_records[-1].reasoning_tokens
```

**问题**：
- `call_records` 是 `LLMClient._call_records` 的副本（`get_call_records()` 返回 `list(self._call_records)`）——**LLMClient 实例维度的累计列表**
- `call_records[-1]` 是 **全局** 最后一条 LLM 调用记录，**不一定** 是本次 ReasoningEngine `chat_with_tools` 产生的
- 触发路径：
  1. DAG/Emergent 模式下，OrchestratorAgent 在调用 Executor 前可能先调 PlannerAgent 或 ReflectorAgent
  2. 这些调用同样会写入 `_call_records`
  3. ReasoningEngine 看到的 `call_records[-1]` 可能是 PlannerAgent 的记录，被错误地累加为本任务的 thinking budget

**更严重的场景**：
- 同一个 ReasoningEngine 实例被 `execute()` 多次（如 `executor.create_for_node()` 共享 `_react_engine`）
- `total_thinking_tokens` 是函数局部变量（每次 execute() 重置为 0，这点 OK）
- 但 `call_records[-1]` 在第 2 次 execute 的第 1 轮 LLM 调用前，仍然是第 1 次 execute 的最后一条记录
- 该 record 会被错误地"加给"第 2 次 execute 的 budget

**正确做法**（任选其一）：
```python
# 方案 A：差分法
records_before = len(self.llm_client.get_call_records())
response_msg = await self.llm_client.chat_with_tools(...)
records_after = self.llm_client.get_call_records()
if records_after and len(records_after) > records_before:
    total_thinking_tokens += records_after[-1].reasoning_tokens

# 方案 B（推荐）：直接读 response_msg.usage（如果 SDK 暴露）
# 或读 LLMClient 公开 last-call-only 的接口
```

**修复复杂度**：低（4-5 行）

---

### P4（中-严重）：budget 超限时返回 `success=False` 但已完成的工具调用结果丢失

**Bug 位置**：`reasoning_engine.py:202-213`
```python
if total_thinking_tokens > self.max_thinking_tokens:
    return StepResult(
        step_id=step_id,
        success=False,
        output=f"Thinking budget exceeded ({total_thinking_tokens} > {self.max_thinking_tokens} tokens) without producing an answer.",
        tool_calls_log=tool_calls_log,     # ← log 还在，但 output 没体现
        iterations_completed=iteration,
    )
```

**问题**：
- 这条返回路径出现在"thinking-only round 且 budget 超限"——**意味着本轮没有 tool_calls 也没有 final answer**
- 但前面的迭代里可能已经成功执行了多个 tool_calls（如 web_search 拿到了结果），只是这一轮 LLM 卡在思考无法收敛
- `output` 字段只说"budget exceeded"，**用户看不到前面工具调用的成果**
- 调用方（OrchestratorAgent / DAGExecutor）拿到 `success=False` 会把整个 step 标记失败，所有 partial work 被丢弃

**建议**：
```python
# 把已完成的工具调用摘要加到 output
tool_summary = ", ".join(f"{tc.tool_name}" for tc in tool_calls_log) if tool_calls_log else "no tools called"
output = (
    f"Thinking budget exceeded ({total_thinking_tokens} > {self.max_thinking_tokens} tokens). "
    f"Completed tools: [{tool_summary}]. "
    f"Last response (partial): {response_text[:200] if response_text else '(empty)'}"
)
```

**修复复杂度**：低（5 行）

---

## 四、中等问题（强烈建议合入前修）

### P5（中）：`_strip_thinking_from_content` 局部 `import re`

**位置**：`reasoning_engine.py:43`
```python
def _strip_thinking_from_content(content: str, thinking: str) -> str:
    ...
    if "<think" in content:
        import re                          # ← 局部 import
        stripped = re.sub(r"<think\n.*?\n</think\n>", "", content, count=1, flags=re.DOTALL)
```

**问题**：
- Phase 1 评审已指出 `llm/client.py` 的 `import re` 应放在模块顶层（已在 Phase 1/2 修复，见 client.py:20）
- ReasoningEngine 重复了"局部 import"反模式
- `tests/test_v14_reasoning_engine.py:186-211` 多次直接调用 `_strip_thinking_from_content`，每次都会触发局部 import（虽然 Python import 缓存可摊薄成本，但风格不一致）

**修复**：把 `import re` 移到 `reasoning_engine.py:21` 区域。

**修复复杂度**：无（1 行）

---

### P6（中）：thinking-aware split 仍未实施 + thinking_content 会在压缩中丢失

**位置**：`context/manager.py:167-206` `_find_safe_split()` 与 `_messages_to_text()` 一字未改

**Phase 2 真正激活了 thinking_content 写入**，反而暴露了 Phase 1 v3 评审 R3/N4 的潜在问题：

**触发路径**：
1. ReasoningEngine 把 `thinking_content` 写入 assistant message
2. 上下文超过 `MAX_CONTEXT_TOKENS=16000` 触发 ContextManager 压缩
3. `_find_safe_split()` 仅保护 tool_calls 完整性，**不保护 assistant 消息中的 thinking_content**
4. assistant 消息进入 `old_msgs` → 被 LLM 摘要
5. `_messages_to_text()`（context/manager.py:208-219）**只读 `content`，不读 `thinking_content`**
6. 摘要后的消息块完全没有 thinking 信息
7. 重要的推理路径被永久丢失

**严重性升级理由**：
- Phase 1 v3 评审里 R3 评为"潜在 bug"——因为 thinking_content 是死代码
- **Phase 2 激活后变为"必然 bug"**——只要用 ReasoningEngine + 上下文长任务，必然丢失 thinking

**修复建议**：
- `_messages_to_text()` 加 thinking_content 拼接（摘要 LLM 能看到推理）
- `_find_safe_split()` 优先保护包含 thinking_content 的 assistant 消息

**修复复杂度**：中（20-30 行）

---

### P7（中）：feature flag 粒度太粗，缺少 per-model 自动判断

**位置**：`executor.py:103`
```python
if config_module.ENABLE_REASONING_ENGINE:
    self._react_engine = ReasoningEngine(...)
else:
    self._react_engine = ReActEngine(...)
```

**问题**：
- roadmap §五 子项 1 明确："通过 model 类型自动选择协议"
- 当前 dispatch 完全按全局 env flag，**用户混合用 deepseek-chat 和 deepseek-reasoner 时只能整体开/关**
- 后果：
  - 启用 ReasoningEngine 后，普通 `deepseek-chat` 也走 ReasoningEngine 的 thinking budget 逻辑——多余开销 + 可能误判
  - 不启用时，`deepseek-reasoner` 走 ReActEngine，thinking 不剥离，仍有 Phase 1 评审里的所有问题

**期望**：
```python
def _is_reasoning_model(model: str) -> bool:
    return any(token in model.lower() for token in ["reasoner", "o1", "o3", "r1", "thinking"])

# 自动判断 + 全局开关二选一
use_reasoning = config_module.ENABLE_REASONING_ENGINE or _is_reasoning_model(llm_client.model)
```

**修复复杂度**：低（5-10 行）

---

### P8（中）：测试覆盖不严密

**`test_thinking_only_round_skipped`** 仅断言 `iterations_completed == 1`，**没有验证**：
1. **messages 中是否真的有 thinking_content 键**（验证 Phase 1 N4 死代码已激活的关键证据）
2. **多轮 thinking 后 user message 是否重复 prompt**（与 P2 bug 相关，若加此断言会直接暴露 bug）
3. **LLMClient.chat_with_tools 被调用次数 == 3**（侧面验证 thinking-only round 不计 iteration 但仍发 LLM 请求）

**`test_thinking_budget_exceeded`** 没有验证 `tool_calls_log` 的状态（P4 bug 相关）。

**`TestThinkingSeparatedInMessages.test_reasoning_content_field_separated`** 测试体只是简单运行了 execute，没有任何关于"messages 内容分离"的断言（见 line 181-183 的注释 "We can't directly access messages"——这恰好说明测试可观测性不够）。

**修复建议**：
- ReasoningEngine 提供 `_last_messages` 调试属性（仅 test_mode 启用）
- 或者把 messages 构建拆为可单独测试的纯函数

**修复复杂度**：中（重构 ReasoningEngine 为可测设计 ~20 行）

---

## 五、轻量问题（下一个 PR 处理）

### P9（轻）：测试 `LLMClient` mock 模式从 `__new__` 进化到 `MagicMock(spec=[...])`——好但仍有缺口

`_make_reasoning_engine`（test:14-30）用 `MagicMock(spec=["chat_with_tools", "get_call_records"])`，比 Phase 1 的 `LLMClient.__new__()` 安全得多。**但**：

- `chat_with_tools` 内部行为不被 spec 限制——如果它依赖 `self.model` / `self.retry_enabled` 等属性，mock 会静默失败
- 未给 ReasoningEngine 设 `agent_name`，所以 `caller_tag=self.agent_name or "ReasoningEngine"` 走 fallback（这是测试无意暴露的 caller_tag 默认行为）

**建议**：未来加 `LLMClientStub` 测试夹具，集中管理 mock 行为契约。

---

### P10（轻）：ReasoningEngine 与 ReActEngine 的 `caller_tag` 默认值不一致

- ReActEngine：`caller_tag=self.agent_name or "ReActEngine"`（engine.py:205）
- ReasoningEngine：`caller_tag=self.agent_name or "ReasoningEngine"`（reasoning_engine.py:131）

**后果**：
- 用户切换 `ENABLE_REASONING_ENGINE` 后，`by_caller` 表里会同时出现 "ReActEngine" 和 "ReasoningEngine" 两条 caller（来自不同任务）
- 历史 trace 对比变得困难——同一个 Executor 在两次任务里 caller 不一样

**建议**：统一为 `self.agent_name or "Executor"`（与 ExecutorAgent.__init__ 传入的 agent_name="ExecutorAgent" 一致）。

---

### P11（轻）：`assistant_msg["thinking_content"]` 不是 OpenAI 标准字段

**位置**：`reasoning_engine.py:150-151`

```python
if thinking:
    assistant_msg["thinking_content"] = thinking
```

这条 message 被 push 到 `messages` 列表，下一轮 `chat_with_tools` 会把整个 messages 发给 OpenAI 兼容 API。

**风险**：
- OpenAI Python SDK 当前确实会丢弃未知字段（实测）
- 但 **vLLM / Ollama / 部分 OpenAI-compatible API（如硅基流动）行为不一致**——有的会原样转发，有的会拒绝
- 用户切换部署目标时可能出现"突然 400 错误"

**建议**：
- 在 LLMClient 入口处加 `_strip_internal_fields()` 函数，剥离 `thinking_content` 等内部键
- 或在 ReasoningEngine 调用前手动 strip：`api_messages = [{k: v for k, v in m.items() if k != "thinking_content"} for m in messages]`

---

### P12（轻）：版本号体系仍混乱

- `config.py:145-150` 现在的注释：
  - Phase 1 部分：`v13.x Reasoning Model Adaptation (v14 in progress)` ← 比之前清晰
  - Phase 2 部分：`v14 Phase 2: Reasoning Engine` ← OK
- 但 Phase 1 评审 v3 已指出：应改为 v13.3 或重新对齐 roadmap §四 v13.x 维护批次

**建议**：本次 Phase 2 合入时一起把版本号体系明确（写在 CHANGELOG.md 里）。

---

### P13（轻）：`react/engine.py:208-210` 的 TODO 已过期未更新

```python
# TODO(v14-Phase4): DeepSeek R1 的 <think/> 内容应在此剥离，
# 仅将 response 部分追加到 messages，thinking 部分记录到 tracing/StepResult。
# 当前行为：thinking+response 混在一起传入下一轮 context（token 浪费）。
```

**问题**：
- 这条 TODO 写的是 "Phase 4"，但实际 Phase 2 就已经做了——只是在新文件 `reasoning_engine.py` 里做的
- 阅读 `react/engine.py` 的人会误以为"剥离逻辑还没做"
- 实际应该改为："旧 ReActEngine 路径不剥离 thinking；启用 ENABLE_REASONING_ENGINE 走 ReasoningEngine 的剥离逻辑"

**建议**：3 行注释修改，避免后续 contributor 重复加同样的剥离逻辑。

---

## 六、Phase 1 v3 评审遗留问题状态

### v3 评审清单核对（14+5 项）

| 编号 | 来源 | Phase 1 v3 状态 | Phase 2 后状态 | Δ |
|---|---|---|---|---|
| R1 (DeepSeek reasoning_content) | v1 §5.1 | ✅ 已修复 | ✅ 同 | 0 |
| R2 (跨 provider 算术失真) | v1 §5.1 | 🔴 未修复 | 🟡 **部分缓解**（UI 加了 provider 语义注释，但聚合仍混算） | +20% |
| R3 (`_find_safe_split` thinking-aware) | v1 §5.1 / v2 §三 | 🔴 未修复 | 🔴 **未修复且严重性升级**（详见 P6） | -10% |
| R4 (primary regex 死代码) | v1 §5.1 | 🔴 未修复 | 🔴 未修复 | 0 |
| R5 (跨 provider 分流聚合) | v2 §一 | 🔴 未修复 | 🔴 未修复 | 0 |
| R6 (Claude / OpenAI o-series 协议) | v2 §二 | 🔴 未修复 | 🔴 未修复 | 0 |
| **R7 (新建 reasoning_engine.py 并行)** | v2 §四 | 🔴 未修复 | ✅ **已修复**（Phase 2 主交付） | **+100%** |
| B1 (REASONING_TOKEN × TOKEN_TRACKING 耦合) | v2 §六 | 🔴 未修复 | 🔴 未修复 | 0 |
| B2 (OTel thinking 截断不对称) | v2 §六 | 🟡 已应用截断 | 🟡 同 | 0 |
| B3 (正则回溯成本) | v2 §六 | 🔴 未修复 | 🔴 未修复 | 0 |
| B4 (测试 `__new__` 脆弱) | v2 §六 | 🔴 未修复 | 🟡 **部分改善**（Phase 2 用 `MagicMock(spec=[...])`） | +30% |
| B5 (版本号 v14.0 撒谎) | v2 §六 | 🔴 未修复 | 🟡 **部分改善**（Phase 1 部分改为 v13.x） | +20% |
| B6 (ToolRouter 联动 reasoning_effort) | v2 §六 | 🔴 未修复 | 🔴 未修复 | 0 |
| 议题 5 (v15+ 阻塞) | v2 §五 | 🔴 未解除 | 🔴 未解除 | 0 |
| **N1 (UI 注释删除)** | v3 §三 | 🔴 退步 | ✅ **已修复**（main.py:237-238 加了 provider 语义注释） | **+100%** |
| **N2 (by_engine / by_caller 无 Reasoning 列)** | v3 §三 | 🔴 退步 | ✅ **已修复**（main.py:189, 222 条件渲染） | **+100%** |
| **N3 (root span 无 > 0 guard)** | v3 §三 | 🔴 退步 | ✅ **已修复**（bridge.py:759-760） | **+100%** |
| **N4 (context/manager.py 死代码)** | v3 §三 | 🔴 是死代码 | ✅ **激活**（ReasoningEngine 真写入），但带出 P6 新问题 | **+100% & 同时引入 P6** |
| N5 (main.py 半成品代码) | v3 §三 | 🔴 半成品 | ✅ **完成**（条件渲染补齐） | **+100%** |

**Phase 2 净修复：5 项（R7 + N1 + N2 + N3 + N5），新引入 4 项（P1 + P2 + P3 + P4），1 项严重性升级（R3/P6）**

---

## 七、综合结论

### 7.1 Phase 2 真实价值

**Phase 2 是 v14 路线图上具有里程碑意义的一步**：

1. **架构正名**：终于落地 v2 ultrathink 评审里反复呼吁的"`reasoning_engine.py` 并行"设计，避免 ReActEngine 被推向"两套逻辑混杂、谁也不敢动"的状态
2. **roadmap §五 子项 2 完成 80%**：迭代计数修正 + MAX_THINKING_TOKENS 是核心交付
3. **修复了 Phase 1 全部 5 个新引入问题**（N1-N5）——v3-fix-audit 的所有立即修复项全部落地
4. **测试模式进化**：从 `LLMClient.__new__()` 升级到 `MagicMock(spec=[...])`，更安全

### 7.2 净改善评分

```
Phase 2 修复/进步：
  +1 R7 架构正名（新建 reasoning_engine.py 并行）
  +1 子项 2 迭代计数修正（roadmap §五 关键交付）
  +1 N1 修复（UI provider 语义注释）
  +1 N2 修复（by_engine / by_caller 表）
  +1 N3 修复（root span > 0 guard）
  +1 N5 修复（main.py 半成品补齐）
  +1 N4 激活（thinking_content 真写入 messages）
  +0.5 B4 改善（mock 模式更安全）
  +0.5 R2 缓解（UI 加了语义注释）
  = +8 净改善

Phase 2 退步/新问题：
  -2 P1 DRY 违反（ReasoningEngine 与父类重复 80 行）
  -1.5 P2 iteration 0 边界 bug（多轮 thinking 后 continue_msg 语义混乱）
  -1.5 P3 budget 累计错误（call_records[-1] 取错记录）
  -1 P4 budget 超限丢失工作（success=False 不带 partial result）
  -1 P6 thinking-aware split 缺失升级为必然 bug（因为 N4 激活）
  -0.5 P5 局部 import re
  -0.5 P7 灰度开关粒度粗
  -0.5 P11 thinking_content 不是 OpenAI 标准字段
  = -8.5 退步

净分：+8 - 8.5 = **-0.5 净分**
```

**虽然净分仍微负**，但 Phase 2 的进步是结构性的（R7 架构正名），退步主要是局部 bug（可在合入前修复）。

### 7.3 合入决策建议

**Step 1（合入前必修）**：
- 🔴 **P2 (iteration 0 边界 bug)**：3-5 行修改 + 1 个新测试用例覆盖"thinking → continue → tool_call"序列
- 🔴 **P3 (budget 累计错误)**：4-5 行修改改为差分法 + 1 个新测试用例覆盖"多 execute() 共享 LLMClient"
- 🔴 **P5 (局部 import re)**：1 行修改

**Step 2（强烈建议合入前修）**：
- 🟡 **P4 (budget 超限丢失工作)**：5 行修改 + 1 个新测试
- 🟡 **P8 (测试不严密)**：补 messages 状态验证 + LLMClient 调用次数验证

**Step 3（下一个 PR）**：
- 🔵 **P1 (DRY 重构)**：提取 `ReActEngine._execute_tool_calls()` + `_build_continue_message()` 共享方法
- 🔵 **P6 (thinking-aware split)**：context/manager.py 改 `_find_safe_split` + `_messages_to_text`
- 🔵 **P7 (per-model 自动判断)**：executor.py 加 `_is_reasoning_model()` 检测
- 🔵 **P10, P11, P13 (轻量)**：caller_tag 统一、thinking_content strip、TODO 更新

### 7.4 一句话总结

> **Phase 2 正确地落地了 v2 ultrathink 评审里最关键的架构建议（`reasoning_engine.py` 并行）与 roadmap §五 子项 2（迭代计数修正），并修复了 Phase 1 v3 评审的全部 N1-N5 问题；但 ReasoningEngine 与 ReActEngine 大量代码重复（P1）、含 3 个潜在执行 bug（P2 重复 prompt / P3 budget 累计错 / P4 budget 超限丢失工作）、thinking-aware split 缺失升级为必然 bug（P6）。强烈建议合入前修复 P2/P3/P5，下一个 PR 处理 P1/P6/P7，避免 Phase 3 启动时带病前行。**

---

## 八、附：合入前必修修复脚本（伪代码）

### 修复 P2（iteration 0 边界 bug）
```python
# reasoning_engine.py:115-118 改为：
# 以"是否已有工具调用 + 是否已有 user/assistant 交互"为判据
non_system_msgs = [m for m in messages if m.get("role") != "system"]
if not non_system_msgs:
    user_input = prompt
elif not tool_calls_log:
    # 已有 thinking-only 轮但无工具调用 → 提示继续思考或行动
    user_input = "Based on your previous thinking, continue with the task or call a tool."
else:
    user_input = continue_msg
```

### 修复 P3（budget 累计错误）— 差分法
```python
# reasoning_engine.py:140-143 改为：
records_before = len(self.llm_client.get_call_records())
response_msg = await self.llm_client.chat_with_tools(...)  # 已有的调用
new_records = self.llm_client.get_call_records()[records_before:]
for rec in new_records:
    total_thinking_tokens += rec.reasoning_tokens
```

### 修复 P4（budget 超限丢失工作）
```python
# reasoning_engine.py:202-213 改为：
if total_thinking_tokens > self.max_thinking_tokens:
    tool_summary = (
        ", ".join(f"{tc.tool_name}" for tc in tool_calls_log)
        if tool_calls_log else "no tools called"
    )
    partial_response = response_text.strip()[:200] if response_text else "(empty)"
    output = (
        f"Thinking budget exceeded ({total_thinking_tokens} > "
        f"{self.max_thinking_tokens} tokens) without producing an answer. "
        f"Tools executed: [{tool_summary}]. "
        f"Last partial response: {partial_response}"
    )
    iteration += 1
    if on_iteration:
        on_iteration(iteration, tool_calls_log)
    return StepResult(
        step_id=step_id,
        success=False,
        output=output,
        tool_calls_log=tool_calls_log,
        iterations_completed=iteration,
    )
```

### 修复 P5（局部 import re）
```python
# reasoning_engine.py 顶部 imports 加：
import re

# 删除 reasoning_engine.py:43 的局部 import re
```

### 修复 P8（测试不严密）— 加关键断言
```python
# tests/test_v14_reasoning_engine.py
async def test_thinking_only_round_skipped(self):
    # ... 现有 setup ...
    result = await engine.execute("test task")

    # 现有断言
    assert result.success is True
    assert result.iterations_completed == 1

    # 新增断言：LLM 被调用 3 次（2 次 thinking + 1 次 final）
    assert engine.llm_client.chat_with_tools.call_count == 3

    # 新增断言：验证 messages 中含 thinking_content 键（P6 / N4 激活证据）
    last_call_args = engine.llm_client.chat_with_tools.call_args_list[-1]
    messages = last_call_args.args[0] if last_call_args.args else last_call_args.kwargs.get("messages")
    thinking_msgs = [m for m in messages if "thinking_content" in m]
    assert len(thinking_msgs) >= 2  # 至少 2 条 assistant 消息带 thinking
```

按上述 5 处修复合入后，Phase 2 净改善评分将从 **-0.5 提升至 +5.5**（P2/P3/P4/P5/P8 全修），ReasoningEngine 即可放心进入 Phase 3 的 interleaved thinking 范式重写工作。
