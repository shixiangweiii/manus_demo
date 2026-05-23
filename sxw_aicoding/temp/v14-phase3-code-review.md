# v14 Phase 3 深度代码评审

> 评审日期：2026-05-22
> 评审范围：基于 commit `bd84cd5` 之后的 uncommitted 改动（12 文件，+170/-34）
> 对应 roadmap：`sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md` §五（v14 子项 3 + Harness 优化 + Phase 2 遗留 P6）
> 前序评审：
> - `v14-phase1-code-review.md`（v1）
> - `v14-phase1-code-review-v2-ultrathink.md`（v2）
> - `v14-phase1-code-review-v3-fix-audit.md`（v3）
> - `v14-phase2-code-review.md`（Phase 2）
> 本文是 Phase 3 的**单轮深度评审**

---

## 一、Phase 3 全景

### 1.1 改动清单（vs Phase 2 已评审基线）

```
 agents/executor.py     | 30 +++++++++++++++++++++---------    [Phase 2 旧改动复出现]
 agents/orchestrator.py |  3 +++                                [reasoning_tokens 聚合]
 agents/prompt_utils.py |  2 +-                                 [收敛倍数可配置]  ★ Phase 3 NEW
 agents/reflector.py    |  4 +++-                               [Wave-2 prompt 对齐] ★ Phase 3 NEW
 config.py              | 18 ++++++++++++++++++                 [Harness 6 项配置] ★ Phase 3 NEW
 context/manager.py     | 22 ++++++++++++++++++++--             [thinking-aware split + tokens] ★ Phase 3 NEW
 llm/client.py          | 50 +++++++++++++++++++++++++++++++++++++++++++++++---  [Phase 1 内容复出现]
 main.py                | 49 ++++++++++++++++++++++++++++++++++++-------------   [Phase 2 内容复出现]
 react/engine.py        | 17 +++++++++++++----                  [thinking 剥离接入 ReActEngine] ★ Phase 3 NEW
 schema.py              |  4 +++-                               [Phase 1 内容复出现]
 tracing/bridge.py      |  3 +++                                [Phase 2 内容复出现]
 tracing/spans.py       |  2 ++                                 [Phase 2 内容复出现]
 12 files changed, 170 insertions(+), 34 deletions(-)
```

> 注：本次 git diff 仍以 `bd84cd5` 为基线，Phase 1+2+3 的改动尚未提交，叠加在同一未提交工作树上。本评审通过对照前序评审文档识别"Phase 3 净增量"。

### 1.2 Phase 3 净增量识别

剔除前序 Phase 1/2 已评审内容，**Phase 3 真正新增**：

| # | 文件 | 净增量 | 对应 roadmap §五 子项 |
|---|------|--------|----------------------|
| ★1 | `context/manager.py` | thinking-aware `_find_safe_split` + `_messages_to_text` + `estimate_messages_tokens` | Phase 3 主交付（直接消除 Phase 2 P6） |
| ★2 | `react/engine.py` | ReActEngine 接入 thinking 剥离（thinking_content 写入 + content 清洗） | Phase 3 主交付（thinking-aware 全 Engine 化） |
| ★3 | `config.py` | 6 个 Harness 配置：`REACT/REASONING/PLANNER/REFLECTOR_TEMPERATURE` + `CONVERGENCE_ESCALATION_MULTIPLIER` + `THINKING_AWARE_CONTEXT` | §五 Harness 优化 |
| ★4 | `agents/prompt_utils.py` | `build_convergence_hint` 升级阈值倍数从硬编码 `2` 改为 `config.CONVERGENCE_ESCALATION_MULTIPLIER` | §五 Harness 优化 |
| ★5 | `agents/reflector.py` | `build_system_prompt(REFLECTOR_SYSTEM_PROMPT)` 替换裸 prompt 字符串（与 Executor Wave-2 对齐） | 修复 Phase 2 R5（HITL/日期注入路径漏接 Reflector） |
| ★6 | `tests/test_v14_reasoning_tokens.py` | 新增 12 个 Phase 3 测试（`TestReActEngineThinkingStripping`、`TestContextManagerThinkingAware`、`TestHarnessConfig`） | 测试 |

### 1.3 vs roadmap §五 完成度刷新

> Phase 2 评审基线：30%

| 子项 | 描述 | Phase 2 完成度 | Phase 3 完成度 | 增量 |
|------|------|----------------|----------------|------|
| 1 | 双协议支持（DeepSeek R1 / OpenAI o 系列） | 50% | 70% | +20pp（ReActEngine 也接入了，统一了） |
| 2 | ReAct 迭代计数修正 | 80% | 80% | 0（未改 ReasoningEngine） |
| 3 | Interleaved Thinking | 0% | **20%** | **+20pp（thinking_content 已贯穿消息生命周期，但未实现思考-工具-思考多轮）** |
| 4 | reasoning_effort × ToolRouter 联动 | 0% | 0% | 0 |
| 5 | 任务持久化与恢复 | 0% | 0% | 0 |
| Harness a | prompt_utils 配置层抽离 | 0% | **40%**（部分配置可调，但未抽出独立模块） | +40pp |
| Harness b | tool_call_helpers 策略可配置 | 0% | 0% | 0 |
| Harness c | ContextManager thinking-aware split | 0% | **100%** | **+100pp** ✅ |
| **整体** | | **30%** | **44%** | **+14pp** |

**结论**：Phase 3 主交付 = thinking 全面贯穿（ContextManager + ReActEngine） + Harness 配置层雏形。

---

## 二、主交付逐项审查

### 2.1 ★1 ContextManager thinking-aware（context/manager.py）✅ 修复 Phase 2 P6

**Phase 2 评审 P6 原文**：
> "`_find_safe_split()` 和 `_messages_to_text()` thinking-aware split 仍缺失，Phase 2 已激活 thinking_content 写入，此问题升级为**确定性 bug**。"

**Phase 3 修复**（`context/manager.py:75-92, 199-212, 220-232`）：

```python
# (1) Token 估算包含 thinking
def estimate_messages_tokens(self, messages):
    for msg in messages:
        ...
        thinking = msg.get("thinking_content", "") or ""
        if thinking:
            total += self.estimate_tokens(thinking) + 4

# (2) 安全切分点保护 thinking 块
if config.THINKING_AWARE_CONTEXT and prev_role == "assistant" and prev_msg.get("thinking_content"):
    split_idx = split_idx - 1
    continue

# (3) 摘要化包含 thinking 行
if config.THINKING_AWARE_CONTEXT:
    thinking = msg.get("thinking_content", "") or ""
    if thinking:
        lines.append(f"[{role} thinking]: {thinking}")
```

**评审结论**：
- ✅ **三处一致改动**：估算、切分、摘要全部覆盖，Phase 2 P6 完全消除
- ✅ **feature flag 保护**：`THINKING_AWARE_CONTEXT` 默认 `true`，但允许实验性关闭（`THINKING_AWARE_CONTEXT=false`）
- ✅ **测试齐备**：3 个单测覆盖（`test_messages_to_text_includes_thinking` / `test_messages_to_text_omits_thinking_when_disabled` / `test_find_safe_split_preserves_thinking_group`）
- ⚠️ **注释笔误**：`context/manager.py:209` 注释 "user, assistant without tool_calls/thinking, etc." 与上面只有 `THINKING_AWARE_CONTEXT=true` 时才检查 thinking 不一致——若 flag 关闭，原始 break 是 "without tool_calls" 即足够。建议改为 "user, assistant without tool_calls (and without thinking when feature on), etc."（小问题）

### 2.2 ★2 ReActEngine 接入 thinking 剥离（react/engine.py:208-220, 246）

**改动核心**：
```python
# 原 ReActEngine 直接把 response_msg.content 整体作为 assistant 消息
# Phase 3 改为先抽出 thinking、再剥离
thinking = getattr(response_msg, "reasoning_content", None) or ""
if not thinking:
    thinking = _extract_thinking_content(response_msg.content or "")
from react.reasoning_engine import _strip_thinking_from_content
response_text = _strip_thinking_from_content(response_msg.content or "", thinking)
assistant_msg = {"role": "assistant", "content": response_text}
if thinking:
    assistant_msg["thinking_content"] = thinking
```

**积极方面**：
- ✅ 现在两个 Engine（ReActEngine + ReasoningEngine）写入消息字典的格式一致——下游 ContextManager / Tracing 拿到统一形态
- ✅ 即使非推理模型偶发包含 `<think/>` 标签（如 R1 蒸馏到 Qwen），也能被 ReActEngine 正确剥离

**🔴 严重问题（详见第三节 P2、P3）**：
- P2：reasoning_only round 在 ReActEngine 路径下被静默吞掉（与 ReasoningEngine 行为不一致）
- P3：从 `react.reasoning_engine` 反向 import `_strip_thinking_from_content`——拓扑倒置，循环依赖隐患

### 2.3 ★3 Harness 配置层（config.py:145-160）

```python
# --- v14 Phase 3: Harness Configuration ---
REACT_TEMPERATURE = float(os.getenv("REACT_TEMPERATURE", "0.5"))
REASONING_TEMPERATURE = float(os.getenv("REASONING_TEMPERATURE", "0.5"))
PLANNER_TEMPERATURE = float(os.getenv("PLANNER_TEMPERATURE", "0.3"))
REFLECTOR_TEMPERATURE = float(os.getenv("REFLECTOR_TEMPERATURE", "0.1"))
CONVERGENCE_ESCALATION_MULTIPLIER = int(os.getenv("CONVERGENCE_ESCALATION_MULTIPLIER", "2"))
THINKING_AWARE_CONTEXT = os.getenv("THINKING_AWARE_CONTEXT", "true").lower() == "true"
```

**接入情况调研**：
| 配置项 | 接入位置 | 状态 |
|--------|----------|------|
| `REACT_TEMPERATURE` | `react/engine.py:204` | ✅ 已接入 |
| `REASONING_TEMPERATURE` | `react/reasoning_engine.py:135`（来自 Phase 2） | ✅ 已接入 |
| `PLANNER_TEMPERATURE` | **无任何接入** | 🔴 **死配置（P1）** |
| `REFLECTOR_TEMPERATURE` | **无任何接入** | 🔴 **死配置（P1）** |
| `CONVERGENCE_ESCALATION_MULTIPLIER` | `agents/prompt_utils.py:250` | ✅ 已接入 |
| `THINKING_AWARE_CONTEXT` | `context/manager.py:82, 205, 226` | ✅ 已接入 |

**评审结论**：6 项配置中有 **2 项纯死代码**——roadmap "Harness 配置层"被宣传为"按模型类型切换 prompt 风格"，本期只达成 67%（4/6），且 Planner/Reflector 这两个对温度最敏感的 Agent 反而没接入。

### 2.4 ★4 收敛倍数可配置（agents/prompt_utils.py:250）

```python
# 原代码
if search_count >= threshold * 2:  # 硬编码 2
# Phase 3
if search_count >= threshold * config.CONVERGENCE_ESCALATION_MULTIPLIER:  # 默认 2
```

**评审结论**：
- ✅ 默认值保持 `2`，行为完全等价
- ⚠️ 缺测试：`tests/test_v14_*.py` 仅有 `test_convergence_escalation_multiplier_default`（默认值），未测试 "倍数=3 时阈值正确升级"
- 🟡 **设计观察**：仅升级了乘数，门限本身（`SEARCH_CONVERGENCE_THRESHOLD`，默认 3）已经在 v9 配置过；倍数与门限的乘法语义其实是一个隐式契约（`threshold` 触发 mild、`threshold * multiplier` 触发 critical）。建议在 docstring 中明确写出这个关系（小问题）

### 2.5 ★5 Reflector 系统提示 Wave-2 对齐（agents/reflector.py:29, 95）

**改动**：
```python
# 原代码：直接把 REFLECTOR_SYSTEM_PROMPT 字符串传入
super().__init__(name="Reflector", system_prompt=REFLECTOR_SYSTEM_PROMPT, ...)
# Phase 3
from agents.prompt_utils import build_system_prompt
system_prompt = build_system_prompt(REFLECTOR_SYSTEM_PROMPT)
super().__init__(name="Reflector", system_prompt=system_prompt, ...)
```

**评审结论**：
- ✅ **关键修复**：与 Executor Wave-2 改造（`agents/executor.py:90`）形成对齐——`build_system_prompt` 注入今日日期 + HITL guidance + 其他横切提示
- ✅ **修复 Phase 2 R5 隐患**：Phase 2 评审曾指出 "Reflector 不走 build_system_prompt，HITL 双门控对 Reflector 不生效"。Phase 3 修复了这一点
- ✅ **保持运行时构建**：在 `__init__` 内构建（与 CLAUDE.md 第 137 行 "System prompts built per-instance" 约束一致），不会在 import 时烧录陈旧日期
- 🟡 **遗留风险**：还有哪些 Agent 漏接？
  - `BaseAgent` 基类：检查 `agents/base.py` 看 `system_prompt` 是否所有继承者都用了 `build_system_prompt`
  - `EmergentPlannerAgent` / `GoalDrivenPlannerAgent` / `SubAgent`：未在本次评审范围，但建议下批 PR 横扫一次

### 2.6 ★6 测试覆盖（tests/test_v14_reasoning_tokens.py:293-498）

**Phase 3 新增测试**：
- `TestReActEngineThinkingStripping`（3 个测试）：验证 `<think/>` 剥离 + `reasoning_content` 字段分离
- `TestContextManagerThinkingAware`（4 个测试）：验证 token 计数 + safe split + summarize 包含 thinking
- `TestHarnessConfig`（4 个测试）：验证 6 个配置默认值

**测试结果**：
```
============================== 46 passed in 0.45s ==============================
```

**评审结论**：
- ✅ **全部通过**——46 个 v14 测试无一红色
- ✅ **覆盖面合理**：新增 12 个测试基本覆盖了主交付路径
- 🟡 **缺失场景**：
  1. **没有 ReActEngine 路径下 reasoning-only 响应的测试**（详见 P2）—— 即"reasoning_content 非空、content 为空、tool_calls 为空"在 ReActEngine 下的行为
  2. **没有跨 ReActEngine + ReasoningEngine 的对照测试**——同样输入下两者输出应等价（除了 iteration 计数差异）
  3. **没有 PLANNER_TEMPERATURE / REFLECTOR_TEMPERATURE 实际生效的测试**——也无法测，因为根本没接入（P1 的副作用）
  4. **缺 `test_thinking_content_in_tracing_span`**——P2 已有 `GEN_AI_RESPONSE_THINKING_CONTENT` 属性写入，但无端到端测试
  5. **`test_messages_to_text_omits_thinking_when_disabled` 测试质量较差**——重新加载 module 是非常脆弱的测试模式（线程不安全 + 副作用），建议直接 `monkeypatch.setattr(config, "THINKING_AWARE_CONTEXT", False)`

---

## 三、严重问题 P1-P3

### 🔴 P1 — Harness 配置层死代码：PLANNER_TEMPERATURE / REFLECTOR_TEMPERATURE 未接入任何调用点

**位置**：`config.py:158-159` + 缺失：`agents/planner.py`、`agents/reflector.py`

**事实复核**：
```bash
$ grep -n "PLANNER_TEMPERATURE\|REFLECTOR_TEMPERATURE" --include="*.py" -r .
config.py:158:PLANNER_TEMPERATURE = float(os.getenv("PLANNER_TEMPERATURE", "0.3"))
config.py:159:REFLECTOR_TEMPERATURE = float(os.getenv("REFLECTOR_TEMPERATURE", "0.1"))
# 0 个 import / 0 个使用点
```

而 `agents/planner.py` 和 `agents/reflector.py` 中至今仍是硬编码：
- `planner.py:468`: `temperature=0.0`
- `planner.py:500, 549, 621, 686, 761`: `temperature=0.3`
- `reflector.py:134`: `temperature=0.1`
- `reflector.py:200, 273`: `temperature=0.2`

**严重程度**：🔴 **HIGH**（功能性死代码，配置承诺与现实严重背离）

**用户感知**：
- 用户在 `.env` 设置 `PLANNER_TEMPERATURE=0.0`，期待 Planner 完全确定性输出
- 实际行为：完全无效，Planner 仍按原硬编码（0.0/0.3 混合）运行
- 静默失败，无警告

**roadmap 承诺**：
> "agents/prompt_utils.py 的 build_system_prompt 抽离为'Harness 配置层'，**支持按模型类型切换 prompt 风格**"

虽然 roadmap 强调的是"prompt 风格"（系统提示），但 commit 中加了 Planner/Reflector 温度配置——既然加了就该接入。

**修复方案**：
```python
# planner.py:468
data = await self.think_json(prompt, temperature=config.PLANNER_TEMPERATURE_CLASSIFIER or 0.0)
# planner.py:500
result = await self.think_json(prompt, temperature=config.PLANNER_TEMPERATURE)
# reflector.py:134
data = await self.think_json(prompt, temperature=config.REFLECTOR_TEMPERATURE)
```

**复杂度**：~15 行改动 × 7 个调用点 = 1 小时工作量

**或者**：删除这两个配置（如果本期不接入），避免误导性 API。

### 🔴 P2 — ReActEngine 路径下 reasoning-only 响应被静默吞掉为"Task completed (no output)"

**位置**：`react/engine.py:208-256`

**触发场景**：
1. 用户没有开启 `ENABLE_REASONING_ENGINE`（默认）
2. 但底层模型是推理模型（DeepSeek R1 / o-series），返回了 `reasoning_content="..."` + `content=""` + `tool_calls=None`
3. 这是 reasoning model 的"中间纯思考轮"——ReasoningEngine 知道怎么处理（continue），ReActEngine 不知道

**当前 ReActEngine 行为**（`react/engine.py:208-256`）：
```python
thinking = getattr(response_msg, "reasoning_content", None) or ""  # = "..."
response_text = _strip_thinking_from_content("", thinking)         # = ""

assistant_msg = {"role": "assistant", "content": ""}               # content 空
if thinking:
    assistant_msg["thinking_content"] = thinking
messages.append(assistant_msg)

if not response_msg.tool_calls:    # True（无工具调用）
    final_output = response_text if response_text.strip() else (response_msg.content or "Task completed (no output).")
    # response_text = "" → 落到右侧分支
    # response_msg.content = "" 或 None → final_output = "Task completed (no output)."
    return StepResult(success=True, output="Task completed (no output).", ...)
```

**结果**：
- LLM 还在思考、还没给出真正答案，引擎就提前返回 success=True 了
- 用户拿到的是 `"Task completed (no output)."`——一个完全是引擎硬编码的兜底字符串
- **这是 silently swallow 用户实际任务的回归**

**与 ReasoningEngine 行为对比**：
| 场景 | ReasoningEngine | ReActEngine（Phase 3 后） |
|------|-----------------|--------------------------|
| `reasoning_content="..."`, `content=""`, `tool_calls=None` | continue（不计 iteration），等下一轮 | **return success=True with "Task completed"** ❌ |
| `reasoning_content="..."`, `content="answer"`, `tool_calls=None` | return success=True | return success=True ✅ |

**严重程度**：🔴 **HIGH**（行为不一致 + 静默用户答案丢失）

**为什么 Phase 2 review 没发现这个**：
- Phase 2 review 关注 ReasoningEngine 内的 P2/P3/P4——彼时 ReActEngine 还没引入 thinking 分离逻辑
- Phase 3 把 thinking 分离也搬进 ReActEngine 了，但 **没有同步搬入对应的"中间思考轮"处理**——这是关键的不对称

**修复方案**：
```python
# react/engine.py:245
if not response_msg.tool_calls:
    # 区分"无 tool_calls 因为模型输出了答案" vs "无 tool_calls 因为还在思考"
    if not response_text.strip() and not thinking:
        # 真正的空响应（异常或模型故障），保持原行为
        final_output = response_msg.content or "Task completed (no output)."
        return StepResult(success=True, output=final_output, ...)
    if response_text.strip():
        # 有真实响应（thinking 已剥离）
        final_output = response_text
        return StepResult(success=True, output=final_output, ...)
    # 只有 thinking 没有 response：这是 reasoning-only round
    # 选项 A（推荐）：警告并要求模型给出答案
    logger.warning("[ReActEngine] reasoning-only response, prompting for explicit answer")
    messages.append({
        "role": "user",
        "content": "Please provide the final answer based on your reasoning."
    })
    continue  # 不计 iteration（也可计，更保守）
    # 选项 B：抛错，要求用户切换到 ReasoningEngine
    # raise RuntimeError("Reasoning-only response in ReActEngine; enable ENABLE_REASONING_ENGINE")
```

**测试补充**：
```python
async def test_reasoning_only_response_in_reactengine():
    """ReActEngine 接到 reasoning_only 响应时不应返回 'Task completed' 兜底。"""
    response = SimpleNamespace(content="", reasoning_content="thinking...", tool_calls=None)
    # 期望：要么继续（option A），要么抛错（option B），但绝不是 success=True with "Task completed"
```

### 🔴 P3 — 反向循环依赖：react/engine 反向 import react/reasoning_engine

**位置**：`react/engine.py:213`

```python
# Lazy import to avoid circular dep (same pattern as build_convergence_hint)
from react.reasoning_engine import _strip_thinking_from_content
```

**依赖图分析**：
```
react/reasoning_engine.py:
  Line 27: from react.engine import ReActEngine     [TOP-LEVEL]
  Line 33: def _strip_thinking_from_content ...     [私有 helper]

react/engine.py:
  Line 213: from react.reasoning_engine import _strip_thinking_from_content  [LAZY]
```

**这违反了 ReActEngine ↔ ReasoningEngine 的"基类不依赖派生类"原则**：
- 派生类（ReasoningEngine）依赖基类（ReActEngine）— 合理
- 基类（ReActEngine）依赖派生类（ReasoningEngine）— 拓扑倒置 ❌

**实际后果**：
1. **隐藏循环**：当下因 lazy import + sys.modules 缓存正常工作，但任何对 reasoning_engine 的早期 reload / monkey-patch / 测试隔离都可能炸
2. **概念污染**：基类引擎"知道"了它的派生类的实现细节
3. **维护性受损**：未来想抽 ReasoningEngine 到独立包，需要先把这个依赖反转

**严重程度**：🔴 **HIGH**（架构缺陷，但暂时不暴露）

**修复方案**：把 `_strip_thinking_from_content` 从 `react/reasoning_engine.py` 迁移到下列位置之一：
- **首选**：`llm/client.py`（已经是 `_extract_thinking_content` 的家），新增对称函数 `_strip_thinking_from_content`，二者一对儿
- 备选：新建 `react/thinking_helpers.py`（与 `tool_call_helpers.py` 并列）
- 反对：保持原位但加 `__all__` 导出标识（不解决拓扑问题）

**修复后的拓扑**：
```
llm/client.py:
  _extract_thinking_content
  _strip_thinking_from_content        [迁移过来]

react/engine.py:
  from llm.client import _extract_thinking_content, _strip_thinking_from_content   [TOP-LEVEL，无循环]

react/reasoning_engine.py:
  from llm.client import _extract_thinking_content, _strip_thinking_from_content   [TOP-LEVEL，无循环]
  from react.engine import ReActEngine                                              [TOP-LEVEL]
```

**复杂度**：~10 行迁移 + 3 处 import 更新 = 30 分钟

---

## 四、中度问题 P4-P6（Phase 2 遗留状态）

### 🟡 P4 — Phase 2 P1 DRY 违反**仍未解决**

**Phase 2 P1 原文**：
> "ReasoningEngine.execute() 与 ReActEngine.execute() 工具执行块约 80 行完全重复（`asyncio.gather` + `_exec_one` + `tool_messages` 构建）。"

**Phase 3 状态检查**：
```bash
$ wc -l react/engine.py react/reasoning_engine.py
     349 react/engine.py
     310 react/reasoning_engine.py
```

`react/reasoning_engine.py:230-302` 的工具执行块**完全没动**——Phase 3 把更多代码搬进了 ReActEngine（thinking 剥离），但 **没有抽出共享 helper**。

**净效应**：DRY 问题反而**加剧**——现在两个 Engine 共有：
- 工具执行块（~80 行，Phase 1 已有）
- thinking 剥离 + assistant_msg 构建（~15 行，Phase 3 新增）
- 总计 ~95 行重复

**严重程度**：🟡 **MEDIUM**（不是 bug，但每次维护都要双倍工作量；且引发 P2 那种"在一个引擎里改但忘记在另一个改"的真实问题）

**推荐方案**（Phase 4 必做）：
```python
# react/engine_helpers.py（新增）
async def execute_tool_calls(tool_calls, tools, tool_router, agent_name, step_id, truncation_limit):
    """两个引擎共享的工具调用执行块。"""
    ...
    return tool_messages, log_records

def build_assistant_msg(response_msg) -> tuple[dict, str, str]:
    """从 LLM 响应构建 assistant 消息字典。返回 (msg, thinking, response_text)。"""
    ...

# react/engine.py
tool_messages, new_records = await execute_tool_calls(...)
assistant_msg, thinking, response_text = build_assistant_msg(response_msg)

# react/reasoning_engine.py
tool_messages, new_records = await execute_tool_calls(...)  # 同一份代码
assistant_msg, thinking, response_text = build_assistant_msg(response_msg)
```

### 🟡 P5 — Phase 2 P2 iteration==0 边界 bug 仍存在

**Phase 2 P2 原文**：
> "ReasoningEngine 的 'iteration 0 时给 system 占位用户消息' 逻辑边界判断有误..."

**Phase 3 状态检查**：`react/reasoning_engine.py:115-121` **未改动**：
```python
non_system_msgs = [m for m in messages if m.get("role") != "system"]
if not non_system_msgs:
    user_input = prompt
elif not tool_calls_log:
    user_input = "Based on your previous thinking, continue with the task or call a tool."
else:
    user_input = continue_msg
```

**这个分支判断仍有 Phase 2 评审中提到的问题**：
- 第 1 次 LLM 调用：`non_system_msgs` 空 → `prompt`（OK）
- 第 2 次 LLM 调用（thinking-only round 后）：`non_system_msgs` 非空 + `tool_calls_log` 空 → "Based on your previous thinking..."（OK）
- 第 N 次：`tool_calls_log` 非空 → continue_msg（OK）

实际上看了一遍 Phase 2 P2 的本质，逻辑是对的。**但 Phase 2 review 把这个标为问题** 是因为更微妙的情况：
- 第 1 次调用是 thinking-only → 第 2 次调用应该用 "based on your previous thinking..."
- 但若第 1 次直接给最终答案（无 tool_calls）就 return 了，不会有第 2 次调用
- 真正的边界是：**`messages` 长度 > 1（包含 system_hint）但 `non_system_msgs` 长度 == 0** 这种状态不存在

回看 Phase 2 review 完整内容（位置：tests 中已有 `TestP2IterationBoundaryRegression`），实际是已经修复了的。**误报**——Phase 3 review 撤回此项。

> 修订：P5 撤回，Phase 2 P2 已通过测试 `test_thinking_then_tool_call_sequence` 隐式覆盖。

### 🟡 P6 — Phase 2 P3 budget accumulation 状态

**Phase 2 P3 原文**（`react/reasoning_engine.py:141-143`）：
> "budget 累加用了 `call_records[-1]`（只取最后一条），如果一次 chat_with_tools 内部触发了多次 LLM 调用（重试），会漏算。"

**Phase 3 状态检查**：`react/reasoning_engine.py:130, 146-148`：
```python
records_before = len(self.llm_client.get_call_records())
response_msg = await self.llm_client.chat_with_tools(...)
new_records = self.llm_client.get_call_records()[records_before:]
for rec in new_records:
    total_thinking_tokens += rec.reasoning_tokens
```

**这是 Phase 2 已经修过的（differential method）**，从 `[-1]` 改为 `records_before:` 切片。Phase 2 review v3 已记录此项已修。

> 修订：P6 撤回，已在 Phase 2 修复。

---

## 五、轻度问题 P7-P10

### 🟢 P7 — `_strip_thinking_from_content` 应迁移到共享模块（与 P3 同源）

见 P3 修复方案。这是 P3 的副作用。

### 🟢 P8 — `_messages_to_text` 与 `estimate_messages_tokens` 的 thinking 处理不对称

**位置**：`context/manager.py:82-85` vs `context/manager.py:226-229`

```python
# estimate_messages_tokens：无 flag 检查，永远统计 thinking
thinking = msg.get("thinking_content", "") or ""
if thinking:
    total += self.estimate_tokens(thinking) + 4

# _messages_to_text：有 flag 检查
if config.THINKING_AWARE_CONTEXT:
    thinking = msg.get("thinking_content", "") or ""
    if thinking:
        lines.append(...)
```

**不一致后果**：
- `THINKING_AWARE_CONTEXT=false` 时：
  - 估算仍然把 thinking 算进 token 数
  - 但摘要时不包含 thinking
- 结果：判定为"超限"后，压缩前后 token 估算会不一致（压缩前包含 thinking 估算，压缩后 summary 不含 thinking 部分）

**严重程度**：🟢 **LOW**（仅在 flag 关闭时偶发，且后果只是 token 数估算偏差）

**修复**：
```python
# 选项 A：estimate 也加 flag 检查
if config.THINKING_AWARE_CONTEXT:
    thinking = msg.get("thinking_content", "") or ""
    if thinking:
        total += self.estimate_tokens(thinking) + 4

# 选项 B（推荐）：去掉 _messages_to_text 的 flag 检查
# thinking 是消息真实成分，永远应当被估算和压缩
# THINKING_AWARE_CONTEXT 应只控制 _find_safe_split（split 行为），而非估算/摘要
```

### 🟢 P9 — `gen_ai.response.thinking_content` 共享 `TRACING_MAX_ATTRIBUTE_LENGTH`

**位置**：`llm/client.py:523`：
```python
span.set_attribute(AttrKey.GEN_AI_RESPONSE_THINKING_CONTENT, thinking[:config.TRACING_MAX_ATTRIBUTE_LENGTH])
```

**问题**：thinking 通常远长于 response（5-10x），共享同一截断长度时往往被切到只剩开头几句。OTel GenAI 半官方建议是**专门给 thinking 一个独立 limit**（默认更大）。

**严重程度**：🟢 **LOW**（功能性正常，只是 trace 信息保真度有损）

**修复**：
```python
# config.py
TRACING_MAX_THINKING_LENGTH = int(os.getenv("TRACING_MAX_THINKING_LENGTH", "8000"))
# llm/client.py:523
span.set_attribute(AttrKey.GEN_AI_RESPONSE_THINKING_CONTENT, thinking[:config.TRACING_MAX_THINKING_LENGTH])
```

### 🟢 P10 — `react/engine.py:208-210` Phase 2 P13 stale TODO **已自动失效**

Phase 2 review 标记的 stale TODO：
```python
# TODO(v14-Phase4): DeepSeek R1 的 <think/> 内容应在此剥离，...
```

**Phase 3 实际行为**：检查 `react/engine.py` 第 208-220 行——**TODO 已被实际代码替换**（thinking 已经在剥离），但 TODO 注释**已删除**。

> 实测：`grep -n "TODO(v14-Phase4)" react/engine.py` 返回 0 行——已清理。

**评审结论**：✅ **已修复**（Phase 3 在写实现的同时清理了 TODO）

---

## 六、Phase 1+2 backlog 状态汇总

| 编号 | 来源 | 描述 | Phase 2 状态 | Phase 3 状态 |
|------|------|------|-------------|-------------|
| R3 | Phase 1 v3 | reasoning_tokens 字段被 0 覆盖 | 修复 | ✅ 持续 |
| R7 | Phase 1 v3 | reasoning_engine 应该是平行类 | 修复 | ✅ 持续 |
| N1 | Phase 1 v3 | 总览面板缺 reasoning 行 | 修复 | ✅ 持续 |
| N2 | Phase 1 v3 | by_engine/by_caller 表缺 reasoning 列 | 修复 | ✅ 持续 |
| N3 | Phase 1 v3 | tracing 根 span 漏 reasoning_tokens | 修复 | ✅ 持续 |
| N4 | Phase 1 v3 | thinking_content 写入是死代码 | 修复（ReasoningEngine 写入） | ✅ **Phase 3 扩展到 ReActEngine** |
| N5 | Phase 1 v3 | DeepSeek R1 字段双协议未识别 | 修复 | ✅ 持续 |
| R5 | Phase 1 v3 | Reflector 漏 build_system_prompt | 未修 | ✅ **Phase 3 修复** |
| **P1** | Phase 2 | DRY 违反（80 行复制） | 未修 | 🔴 **未修，且加剧** |
| P2 | Phase 2 | iteration==0 边界 | 未修但实测无伤 | 🟢 撤回（误报） |
| P3 | Phase 2 | budget accumulation `[-1]` | 已修 | ✅ 持续 |
| P4 | Phase 2 | budget exceeded 工作丢失 | 未修 | 🟡 持续 |
| P5 | Phase 2 | 局部 import re | 未修 | 🟡 持续 |
| **P6** | Phase 2 | thinking-aware split 缺失 | 未修 | ✅ **Phase 3 完整修复** |
| P7 | Phase 2 | 全局 ENABLE_REASONING_ENGINE 粒度太粗 | 未修 | 🟡 持续 |
| P8 | Phase 2 | 测试断言较弱 | 未修 | 🟡 持续 |
| P13 | Phase 2 | engine.py:208 stale TODO | 未修 | ✅ **Phase 3 自动失效** |

**Phase 3 净效应**：
- ✅ 修复 5 项（R5, N4 扩展, P6, P13, 加上隐含修正 P2 的误判）
- 🔴 引入 3 项新严重问题（P1 死配置, P2 reasoning-only 静默, P3 反向循环）
- 🟡 1 项加剧（DRY 加剧到 95 行重复）

**净评分**：+5 修复 − 3 新引入严重问题 − 1 加剧 = **+1 净改进**（比 Phase 2 的 −0.5 略好，但仍有结构性问题）

---

## 七、综合结论 + 合并建议

### 7.1 Phase 3 的真正贡献

✅ **重大架构修复**：消除 Phase 2 P6（context 压缩与 thinking 不兼容的确定性 bug）
✅ **格式一致性**：两个 Engine 写入 message 字典格式现已统一，下游可不区分对待
✅ **配置层雏形**：4 个温度 + 收敛倍数 + thinking 开关——为 Harness 配置层奠基
✅ **Reflector 对齐**：补上 Wave-2 漏掉的最后一个 Agent

### 7.2 Phase 3 的真正代价

🔴 **死配置（P1）**：PLANNER/REFLECTOR_TEMPERATURE 是 `.env` 假承诺
🔴 **静默吞答案（P2）**：reasoning model + ReActEngine 路径下用户答案被引擎兜底字符串吞掉
🔴 **反向循环（P3）**：基类反向依赖派生类
🟡 **DRY 加剧**：从 80 行复制变 95 行

### 7.3 评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 完成度 | 7/10 | 主交付完整，但 Harness 配置层 4/6 接入 |
| 正确性 | 6/10 | 主路径正确，但 P2 静默吞答案是回归 |
| 架构 | 6/10 | P3 反向循环 + P1 加剧 DRY |
| 测试 | 8/10 | 46 测试全过，但缺 P2 关键场景 |
| 文档/注释 | 7/10 | bilingual 注释到位，但 P10 stale TODO 已清干净是亮点 |
| **综合** | **6.8/10** | **可合并但需 follow-up** |

### 7.4 合并建议（两步法）

**第 1 步：本 PR 必合修复（~1 小时）**：
- ✅ **P2 修复**：在 `react/engine.py:245` 加入 reasoning-only 分支（绝不可静默返回 "Task completed"）
- ✅ **P3 修复**：把 `_strip_thinking_from_content` 从 `reasoning_engine.py` 迁移到 `llm/client.py`，去掉 lazy import
- ✅ **P1 二选一**：要么接入 PLANNER_TEMPERATURE / REFLECTOR_TEMPERATURE，要么从 config 删除（推荐删除，保留 README 中"由用户调用 think_json 时显式指定"的状态）

**第 2 步：合并后立即跟进 PR（~1 周）**：
- P4 DRY 重构：抽 `react/engine_helpers.py` 共享模块
- P8 估算/摘要的 thinking flag 统一
- P9 thinking trace 独立 limit
- 补 P2 测试：`test_reasoning_only_response_in_reactengine`
- 补 PLANNER/REFLECTOR temperature 接入测试

**第 3 步：roadmap §五 子项 3（Interleaved Thinking）继续推进**：
- 真正的 interleaved thinking 范式重写还远（仅完成 20%）
- 当前阻塞：P3 修复必须先做（否则 thinking_helpers 的位置反复横跳）

### 7.5 红线

🚫 **本 PR 不应在 P2 未修复的情况下合并**——这是用户答案丢失的回归
🚫 **本 PR 不应在 P3 未修复的情况下合并**——架构反向依赖一旦存在就难逆转

---

## 八、修补伪代码

### 8.1 P1 修复（去除死配置 + 接入或删除）

**方案 A — 接入**（推荐）：
```python
# agents/planner.py
from agents import config as _config  # 或 import config
# Line 468:
data = await self.think_json(prompt, temperature=0.0)  # 分类器仍 0.0 强约束
# Line 500:
result = await self.think_json(prompt, temperature=config.PLANNER_TEMPERATURE)
# Line 549, 621, 686, 761: 同上替换为 config.PLANNER_TEMPERATURE

# agents/reflector.py
# Line 134:
data = await self.think_json(prompt, temperature=config.REFLECTOR_TEMPERATURE)
# Line 200, 273:
data = await self.think_json(prompt, temperature=config.REFLECTOR_TEMPERATURE)
```

**方案 B — 删除死配置**：
```python
# config.py — 删除 PLANNER_TEMPERATURE / REFLECTOR_TEMPERATURE
# 或加注释 "TODO Phase 4: actually wire these in"
```

### 8.2 P2 修复（reasoning-only round 处理）

```python
# react/engine.py:245-256
if not response_msg.tool_calls:
    if response_text.strip():
        # Path 1: 真实回答
        final_output = response_text
    elif thinking and not response_text.strip():
        # Path 2: reasoning-only round —— 模型还在思考，没给最终答案
        # 选项 A（推荐）：再问一轮
        logger.info("[ReActEngine] Reasoning-only response, requesting explicit answer")
        messages.append({
            "role": "user",
            "content": "Please provide your final answer based on the reasoning above."
        })
        continue  # 不计 iteration（与 ReasoningEngine 行为一致）
        # 选项 B：抛错引导用户启用 ENABLE_REASONING_ENGINE
        # raise RuntimeError("Reasoning-only response detected. Enable ENABLE_REASONING_ENGINE for proper handling.")
    else:
        # Path 3: 真正空响应（异常路径）
        final_output = response_msg.content or "Task completed (no output)."

    logger.info("[ReActEngine] Completed in %d iterations", iteration)
    if on_iteration:
        on_iteration(iteration, tool_calls_log)
    return StepResult(
        step_id=step_id, success=True, output=final_output,
        tool_calls_log=tool_calls_log, iterations_completed=iteration,
    )
```

**测试**：
```python
@pytest.mark.asyncio
async def test_reasoning_only_response_does_not_swallow_answer():
    """ReActEngine 接到 reasoning-only 响应不应早返回 'Task completed'."""
    engine = _make_react_engine(max_iterations=3)
    responses = [
        SimpleNamespace(content="", reasoning_content="thinking deeply...", tool_calls=None),
        SimpleNamespace(content="The answer is 42", reasoning_content=None, tool_calls=None),
    ]
    engine.llm_client.chat_with_tools = AsyncMock(side_effect=responses)
    result = await engine.execute("test")
    assert result.success is True
    assert "Task completed (no output)" not in result.output
    assert "42" in result.output
```

### 8.3 P3 修复（迁移 _strip_thinking_from_content 到 llm/client.py）

```python
# llm/client.py（在 _extract_thinking_content 旁边）
def _strip_thinking_from_content(content: str, thinking: str) -> str:
    """Remove the thinking portion from content, returning only the response."""
    if not thinking or not content:
        return content
    if "<think" in content:
        stripped = re.sub(r"<think\n.*?\n</think\n>", "", content, count=1, flags=re.DOTALL)
        if stripped == content:
            stripped = re.sub(r"<think[^>]*>.*?</think[^>]*>", "", content, count=1, flags=re.DOTALL)
        return stripped.strip()
    return content

# react/engine.py（顶层 import，移除 lazy import）
from llm.client import LLMClient, _extract_thinking_content, _strip_thinking_from_content
# 删除 line 213 的 lazy import

# react/reasoning_engine.py
# 移除原 _strip_thinking_from_content 定义（line 33-51）
from llm.client import _extract_thinking_content, _strip_thinking_from_content

# tests/test_v14_reasoning_engine.py:192, 202, 212
from llm.client import _strip_thinking_from_content  # 改 import 来源
```

**架构净效应**：
```
Before:  llm/client → react/engine → react/reasoning_engine → react/engine（循环！）
After:   llm/client（含 thinking helpers） → react/engine → react/reasoning_engine（无环）
```

---

## 附录 A — Phase 3 改动行号速查

| 文件 | 关键行 | 内容 |
|------|--------|------|
| `config.py:145-160` | +16 | Harness 6 配置 |
| `context/manager.py:75-92` | +6 | estimate_messages_tokens 含 thinking |
| `context/manager.py:199-212` | +6 | _find_safe_split 保护 thinking 块 |
| `context/manager.py:220-232` | +6 | _messages_to_text 含 thinking |
| `react/engine.py:204` | ~1 | `temperature=config.REACT_TEMPERATURE` |
| `react/engine.py:208-220` | +12 | thinking 抽取 + 剥离 + 写入 |
| `react/engine.py:213` | +1 | lazy import _strip_thinking_from_content（**P3**）|
| `react/engine.py:246` | ~1 | `final_output = response_text if ...` |
| `agents/prompt_utils.py:250` | ~1 | `* config.CONVERGENCE_ESCALATION_MULTIPLIER` |
| `agents/reflector.py:29, 95` | +2 | `build_system_prompt(REFLECTOR_SYSTEM_PROMPT)` |
| `tests/test_v14_reasoning_tokens.py:293-498` | +200 | TestReActEngineThinkingStripping + ContextManager + Harness |

## 附录 B — 测试运行结果

```
$ python3 -m pytest tests/test_v14_reasoning_tokens.py tests/test_v14_reasoning_engine.py -o asyncio_mode=auto
============================== 46 passed in 0.45s ==============================
```

- TestReasoningTokensInSchema：5 passed
- TestExtractThinkingContent：7 passed
- TestTokenAggregation：1 passed
- TestRecordCallReasoningTokens：3 passed
- TestExtractResponseDataReasoningContent：4 passed
- TestOnTokenUsageReasoningTokens：2 passed
- **TestReActEngineThinkingStripping：3 passed（Phase 3）**
- **TestContextManagerThinkingAware：4 passed（Phase 3）**
- **TestHarnessConfig：4 passed（Phase 3）**
- TestThinkingRoundDoesNotIncrementIteration：2 passed
- TestThinkingBudget：2 passed
- TestThinkingSeparatedInMessages：3 passed
- TestFeatureFlag：2 passed
- TestP2IterationBoundaryRegression：1 passed
- TestP3BudgetAccumulationRegression：1 passed
- TestP4BudgetExceededPartialWork：1 passed

总计 46 passed in 0.45s ✅
