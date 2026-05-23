# v14 Phase 1（Reasoning Token Bucketing）代码评审

> **评审日期**：2026-05-22
> **评审范围**：当前工作树未提交的 7 个文件改动 + 1 个新增测试，对照 `iteration-roadmap-v14-v19.md` §五
> **结论**：**这是 v13.x 维护批次第 3 项（推理 token 分桶），而非完整的 v14**。基础设施做得扎实，但只完成了 v14 §五 6 个子项中的 1 个（且 1 个尚未真正生效）。

---

## 一、实施清单（当前工作树未提交）

### 改动文件

| 文件 | 改动行数 | 实际做了什么 |
|---|---|---|
| `schema.py` | +4 / -1 | `TokenUsage` 与 `LLMCallRecord` 新增 `reasoning_tokens: int = 0` 字段；`total_tokens` 注释说明跨提供商语义差异 |
| `config.py` | +4 | 新增 `REASONING_TOKEN_TRACKING=true` 开关（标注 `v14.0`） |
| `llm/client.py` | +44 / -2 | 新增 `_extract_thinking_content()` 解析 `<think>` 标签；`_record_call` 从 `completion_tokens_details.reasoning_tokens` 或 `usage.reasoning_tokens` 提取；`_extract_response_data` 返回 `thinking_content`；OTel span 新增 `gen_ai.usage.reasoning_tokens` + `gen_ai.response.thinking_content` 两个属性 |
| `agents/orchestrator.py` | +3 | `_finalize_token_usage` 在 `by_engine` / `by_caller` / `total` 三处聚合 `reasoning_tokens` |
| `main.py` | +33 / -10 | Rich 表格按需新增 "Reasoning" 列（数据为空时不渲染，避免污染普通模型输出）；Grand total Panel 加入 reasoning 行 |
| `context/manager.py` | +5 | `estimate_messages_tokens` 把 `msg["thinking_content"]` 计入 token 估算（含 `+4` overhead） |
| `react/engine.py` | +3 | **仅添加一条 TODO 注释**——说明"Phase 4 需要在此剥离 `<think>` 内容"，未做任何代码改动 |

### 新增文件

| 文件 | 行数 | 内容 |
|---|---|---|
| `tests/test_v14_reasoning_tokens.py` | 159 | 4 个 TestClass、12+ 测试用例：Schema 默认值、`<think>` 解析、聚合、`_record_call` mock |

### 未改动文件

| 应当被改 | 实际状态 |
|---|---|
| `react/reasoning_engine.py` | **不存在**——roadmap 要求"新增，与 ReActEngine 并行"未做 |
| `agents/prompt_utils.py` | **未改**——roadmap 要求"抽离为 Harness 配置层"未做 |
| `react/tool_call_helpers.py` | **未改**——roadmap 要求"strategies 可配置"未做 |
| `tools/router.py` | **未改**——roadmap 要求"reasoning_effort × ToolRouter 联动"未做 |
| `agents/subagent.py` | 未改（与本期 token 分桶无关，OK） |

---

## 二、对照 roadmap §五 的差距矩阵

| roadmap 子项 | 要求 | 实际状态 | 完成度 |
|---|---|---|---|
| **1. 双协议支持** | 识别 `<think>` 标签 + 读取 `reasoning_tokens` 字段 + **按 model 类型自动选择** | `<think>` 解析有（fallback 正确，primary 死代码）；`reasoning_tokens` 读取有（两路 try）；**未按 model 类型选择**——是兜底式串行尝试 | 🟡 60% |
| **2. ReAct 迭代计数修正** | thinking tokens 不计入 `MAX_REACT_ITERATIONS` + 新增 `MAX_THINKING_TOKENS` | **完全没做**。`react/engine.py` 仅一条 TODO，未引入新 config，未改迭代逻辑 | 🔴 0% |
| **3. Interleaved Thinking (Claude Opus 4.5)** | 支持思考-工具-思考-工具多轮模式，要求 `react/engine.py` 范式重写 | **完全没做** | 🔴 0% |
| **4. reasoning_effort × ToolRouter 联动** | 简单任务低推理，复杂任务高推理 | **完全没做**，`tools/router.py` 未改 | 🔴 0% |
| **5. 任务持久化与恢复 (resume)** | `OrchestratorAgent.resume(task_id)` + checkpoint | **完全没做**，OrchestratorAgent 仅加 3 行聚合 reasoning_tokens | 🔴 0% |
| **Harness-a**：prompt_utils 抽 Harness 配置层 | 按模型类型切换 prompt 风格 | **完全没做** | 🔴 0% |
| **Harness-b**：tool_call_helpers 策略可配置 | truncate / classify 按工具按场景 | **完全没做** | 🔴 0% |
| **Harness-c**：context/manager.py thinking-aware split | 避免在 `<think>` 块中间切开 | **仅做了 token 估算，未改 `_find_safe_split` 切分逻辑** | 🟡 30% |

**总完成度**：8 个子项中实际完成 1 个（token 分桶基础设施），半完成 2 个（双协议读取、估算），剩余 5 个 0%。

---

## 三、对照 roadmap §四 v13.x 维护批次

v14 §五 显式标注"**前置依赖**：v13.x 维护批次第 3 项（token 分桶）必须先完成"。本次工作树状态对照：

| v13.x 工作项 | 状态 |
|---|---|
| 1. codemap.md / CHANGELOG.md 回填到 v13 | ❌ 未做（文件 modtime 未变） |
| 2. Wave-5 沙箱修复（`shell_tool.py:130`） | ❌ 未做（不在改动列表） |
| 3. LLMClient 推理 token 分桶 | ✅ **本次实际做的就是这个** |
| 4. DAG_SERIAL_EXECUTION 默认值复盘 | ❌ 未做 |
| 5. evaluation 扩样到 30+ 任务 | ❌ 未做 |
| 6. 清理 v12 占位 | ❌ 未做 |

**结论**：本次工作树**实质上是 v13.x 第 3 项**，但被自标为 `v14.0`（config.py 注释、测试文件名、模块注释皆如此）。建议在合入前把版本号修正为 v13.x，避免后续 changelog 混乱。

---

## 四、代码逐处评审

### 4.1 `llm/client.py` — `_extract_thinking_content()` 解析器

```python
def _extract_thinking_content(content: str | None) -> str:
    if not content or "<think" not in content:
        return ""
    # DeepSeek R1: <think\n...content...\n</think\n>
    match = re.search(r"<think\n(.*?)\n</think\n>", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: standard <think ...>...</think ...> with > closing
    match = re.search(r"<think[^>]*>(.*?)</think[^>]*>", content, re.DOTALL)
    return match.group(1).strip() if match else ""
```

**问题 1（严重）：primary 正则匹配的是非标准格式**。
真实 DeepSeek R1 API 输出标签是 `<think>...</think>`（标准 XML 风格，有 `>` 闭合），不是 `<think\n...\n</think\n>`（无 `>` 闭合）。primary 分支永远不会命中，实际工作的是 fallback。建议直接删除 primary，保留 fallback；或写明这是为某个 R1-distill 衍生模型预留。

**问题 2（严重）：完全没处理 DeepSeek 官方 API 的 `reasoning_content` 字段**。
DeepSeek-R1（official）通过 OpenAI-compatible API 返回时，**推理内容在 `message.reasoning_content` 独立字段**，不在 `message.content` 内。当前 `_extract_response_data` 只读 `content`，导致：
- 官方 DeepSeek R1 的 thinking 永远不会被捕获
- 只有 R1-distill 衍生模型（Ollama / vLLM 自部署）的 `<think>` 标签会被捕获
- OTel span 里的 `thinking_content` 对生产环境的官方 API 是空的

建议在 `_extract_response_data` 中先 `getattr(message, "reasoning_content", "")`，如果有就直接用，没有再 fallback 到 `<think>` 解析。

**问题 3（中）：Claude extended thinking 与 OpenAI o-series 完全未支持**。
- Claude API 的 extended thinking 是独立的 `thinking` block（在 `message.content` 数组里），不是文本标签
- OpenAI o-series 不暴露 thinking 文本（只暴露 token 数）
- 当前规则只覆盖 `<think>` 文本标签这一种情况

**问题 4（轻）：`<think` 字符串前缀匹配可能误判**。
如果 content 里某段引用代码包含 `<think>` 字样（例如评测时讨论本项目代码），会触发解析。可考虑收紧到 `<think>` 或 `<think\n` 完整匹配。

### 4.2 `llm/client.py` — `_record_call` 读取 reasoning_tokens

```python
if config.REASONING_TOKEN_TRACKING:
    details = getattr(usage, 'completion_tokens_details', None)
    if details:
        reasoning_tokens = getattr(details, 'reasoning_tokens', 0) or 0
    if not reasoning_tokens:
        reasoning_tokens = getattr(usage, 'reasoning_tokens', 0) or 0
```

**问题 5（中）：跨提供商语义不一致导致 total 可能不可加**。
- **OpenAI o-series**：`total_tokens = prompt + completion`；`completion_tokens` 内已包含 `reasoning_tokens`（即 reasoning 是 completion 的一个子集，重复计数）
- **DeepSeek R1**：`total_tokens = prompt + completion + reasoning`（三者独立计费）
- **Anthropic Claude**：thinking 计入 `output_tokens` 但单独标价

当前 `agents/orchestrator.py:692-715` 把 `reasoning_tokens` 当作独立维度累加，**对 OpenAI 模型会让"prompt+completion+reasoning"超过 total**，让"by_engine / by_caller / total 三个表的算术关系"对 OpenAI 用户失真。schema 注释提到了这点，但聚合代码并未做提供商感知。

**建议**：要么按 model 名分流，要么明确文档说明"reasoning_tokens 仅为信息列，不参与算术核对"，并在 UI 加注脚。

**问题 6（轻）：兜底逻辑会掩盖错误**。
`getattr(usage, 'reasoning_tokens', 0) or 0` 在 OpenAI usage 对象上其实不存在 `reasoning_tokens` 字段，但兜底安静返回 0，导致问题 5 无法被快速发现。

### 4.3 `react/engine.py` — 只有一条 TODO

```python
# TODO(v14-Phase4): DeepSeek R1 的 <think/> 内容应在此剥离，
# 仅将 response 部分追加到 messages，thinking 部分记录到 tracing/StepResult。
# 当前行为：thinking+response 混在一起传入下一轮 context（token 浪费）。
```

**问题 7（中）**：TODO 没有被任何 issue / 任务追踪系统挂钩，"Phase 4" 是什么、何时做、谁做都没定义。建议要么：
- 在 roadmap §五 中显式拆出 v14.1 / v14.2 / v14.3 / v14.4 子阶段并写明每个做什么
- 或在项目 issue 追踪里挂 v14-Phase4 标签

**问题 8（设计层面）**：roadmap §五 要求**新建 `react/reasoning_engine.py` 与 ReActEngine 并行**，但当前做法是在 `react/engine.py` 上原地改。这违反了 roadmap "新建并行实现"的设计意图。并行实现的好处是：
- 推理模型走新引擎，传统模型走旧引擎，**灰度切换**
- 出问题可以一键回退到 ReActEngine v1
- 新引擎可以为推理模型做更激进的优化（如 interleaved thinking 需要完全重写循环结构）

当前的"在 ReActEngine 上加 if reasoning_model 分支"路径会把 ReActEngine 逐步推向"两套逻辑混杂、谁也不敢动"的状态。**强烈建议下一个 Phase 启动前先评审"在 engine.py 上改 vs 新建 reasoning_engine.py"这个分叉点**。

### 4.4 `context/manager.py` — 估算但不切分

```python
thinking = msg.get("thinking_content", "") or ""
if thinking:
    total += self.estimate_tokens(thinking) + 4
```

**问题 9（严重）：永远不会触发**。
- `msg["thinking_content"]` 这个键当前**没有任何写入方**——LLM 返回的 `content` 没人会拆成 `{"content": ..., "thinking_content": ...}` 这种 message dict 结构
- 注释里也承认了："NOTE: thinking_content 键当前无写入方——待 Phase 4 ReActEngine 剥离 thinking 后生效"
- 等于先放了一个"伪 Phase 4 钩子"，但没确保 Phase 4 一定会满足这个契约

**问题 10（严重）**：`_find_safe_split` 完全没改！roadmap 明确要求"避免在 `<think>` 块中间切开"，但当前压缩切分逻辑仍按 message 边界切，**对包含 `<think>` 的 message**完全可能被切到块中间，导致下一轮 LLM 看到只有 `<think>` 开头没有结尾的残缺消息——会让推理模型陷入混乱或拒答。

**这是一个真实的、当前就存在的 bug，触发条件：使用推理模型 + 上下文超过 `MAX_CONTEXT_TOKENS` 触发压缩**。Phase 1 没修。

### 4.5 `main.py` 渲染 + `agents/orchestrator.py` 聚合

**优点**：
- `has_reasoning = any(r.reasoning_tokens > 0 for r in ...)` 条件渲染，普通模型不会污染表头——好设计
- `by_engine` / `by_caller` / `total` 三处对称加 `reasoning_tokens`——一致性好

**问题 11（轻）**：grand total Panel 把 reasoning 行加在 completion 后面：
```
Total Tokens: 800
  Prompt:     100
  Completion: 200
  Reasoning:  500
```
对 OpenAI 模型会让用户疑惑（100+200+500=800，看起来对得上；但实际 completion 200 已经包含 reasoning 500，应该是 100+200=300 ≠ 800）。建议加注解说明算术关系，或按 provider 类别分别渲染。

### 4.6 `tests/test_v14_reasoning_tokens.py`

**优点（明显）**：
- 覆盖了 Schema 默认值、向后兼容反序列化、`<think>` 解析多种格式、聚合算术、`_record_call` 两条提取路径——**对已实现部分的单元测试是充分的**
- 用 `SimpleNamespace` mock OpenAI usage 对象，避免依赖真实 API

**问题 12（中）**：`test_deepseek_r1_thinking` 测的是 `<think\n...\n</think\n>` 格式——这是 primary 正则匹配的非标准格式，实际 DeepSeek R1 API 不会输出这种格式。测试通过 ≠ 真实场景能用。**建议补一个用 `<think>...</think>` 标准格式的测试**，并加一个 `reasoning_content` 独立字段的测试（即使该字段当前未被读取，也提醒这是缺口）。

**问题 13（中）**：**没有 OTel span attribute 的测试**。`gen_ai.usage.reasoning_tokens` 和 `gen_ai.response.thinking_content` 是 v14 引入的可观测性契约，没测试保证未来不被破坏。

**问题 14（轻）**：**没有集成测试**。所有测试都是 mock，没有跑过真实 ReActEngine + 推理模型的端到端测试。建议至少写一个 `pytest.mark.integration` 测试，用 `LLM_MODEL=deepseek-reasoner`（或 mock LLM 返回带 `<think>` 的响应）跑一次完整 ReAct 循环，验证 reasoning_tokens 真的被记录到 token summary 里。

---

## 五、风险与建议

### 5.1 必须在合并前修

| # | 风险 | 修复建议 |
|---|---|---|
| R1 | DeepSeek R1 官方 API 的 `message.reasoning_content` 字段完全未捕获 → 生产环境 thinking content 永远为空 | `_extract_response_data` 先读 `getattr(message, "reasoning_content", "")`，命中即返回；否则 fallback 到 `<think>` 解析 |
| R2 | OpenAI 模型的 `reasoning_tokens` 与 `completion_tokens` 重复计数，渲染时让用户算术对不上 | 至少在 UI Panel 加 footnote；理想做法是按 provider 区分聚合策略 |
| R3 | `context/manager.py` `_find_safe_split` 未改，**推理模型 + 压缩** 路径会切断 `<think>` 块导致残缺 message | 在 `_find_safe_split` 里增加"块完整性"检查：包含 `<think>` 但未闭合的 message 不允许作为切点边界 |
| R4 | primary 正则是死代码 | 直接删除，仅保留 fallback；或注释说明它为某衍生模型预留并补对应测试 |

### 5.2 建议下一个 Phase 启动前先决定

| # | 决策点 | 选项 |
|---|---|---|
| D1 | **新建 `react/reasoning_engine.py` vs 在 `react/engine.py` 原地改？** | roadmap 倾向并行新建（灰度切换 + 不污染旧引擎）；当前做法是原地改。建议保留 roadmap 设计意图。 |
| D2 | **本次工作树该标 v13.x 还是 v14？** | 严格按 roadmap 应标 v13.x 第 3 项；当前自标 v14.0 会让 CHANGELOG 与 roadmap 错位 |
| D3 | **Phase 2/3/4 怎么排期？** | roadmap §五 没拆 sub-phase；既然实际按 phase 推进，建议在 roadmap 里显式补 v14.1/v14.2/.../v14.6 子阶段 |
| D4 | **TODO 追踪机制** | 当前 TODO 散落在代码注释里，无 issue/任务挂钩，会被遗忘 |

### 5.3 v13.x 其他 5 项的拖延风险

本次 PR 只做了 v13.x 第 3 项就跳到自标 v14，意味着：
- **Wave-5 沙箱 bug**（`shell_tool.py:130`）继续 pending——v19 Guardrails 安全章节的前置阻塞项
- **evaluation 扩样**没做——v17 自演化 / v18 协作模式 / v19 安全基准全部依赖它，**这是 roadmap 标注的横切阻塞依赖**
- **codemap / CHANGELOG 回填**没做——新成员入项目读到的还是 v9.1 错误现状

建议把"v13.x 第 1/2/4/5/6 项"作为单独的 cleanup PR，与 v14.2+ 并行推进。

---

## 六、整体评价

### 评分

| 维度 | 评分 | 说明 |
|---|---|---|
| **基础设施质量** | ⭐⭐⭐⭐ | Schema / config / 聚合 / 渲染 / 单元测试一条线落地完整 |
| **roadmap §五 完成度** | ⭐ | 8 子项 1 完成、2 半完成、5 未做（≈ 12% 加权完成度） |
| **代码工艺** | ⭐⭐⭐ | 渲染条件化、向后兼容、注释充分；但有死代码、跨提供商语义未处理、TODO 钩子不闭环 |
| **roadmap 对齐** | ⭐⭐ | 把 v13.x 第 3 项标为 v14.0；偏离了"新建 `reasoning_engine.py` 并行"的设计 |
| **测试覆盖** | ⭐⭐⭐ | 单元测试扎实，缺集成测试与 OTel 契约测试，且核心解析测试用了非标准格式 |

### 一句话总结

**这次合入解决了 token 观测性的"前置基建"，但只做完了 roadmap §五 8 子项中的 1 个；现有改动里 3 个严重 bug（DeepSeek 官方 reasoning_content 未读、跨提供商算术失真、`_find_safe_split` 未做 thinking-aware）应在合入前修复或显式记录为已知问题**。建议把本次 PR 重新定位为"**v13.x 第 3 项 + v14 Phase 1 基建**"，并在合入说明里明确列出剩余 5 子项的排期。

---

## 七、附录：建议的修订版 v14 子阶段拆分

如果接受"v14 多阶段推进"的现实，建议把 roadmap §五 重写为：

```
v14.1 ✅ Token 分桶基础设施（本次工作，调整为 v13.x 第 3 项 + v14 基建）
  ├── Schema reasoning_tokens 字段
  ├── LLMClient 提取（含 DeepSeek reasoning_content 字段读取）
  ├── 聚合 / 渲染 / OTel
  └── 单元测试（含 OTel 契约测试 + 真实格式测试）

v14.2 ⏳ Thinking 内容剥离 + 跨提供商语义统一（1 周）
  ├── react/engine.py 实际剥离 <think> / reasoning_content
  ├── context/manager._find_safe_split thinking-aware
  ├── 按 provider 分流聚合策略
  └── 集成测试

v14.3 ⏳ ReAct 迭代计数修正 + MAX_THINKING_TOKENS（半周）

v14.4 ⏳ 任务持久化与恢复 (resume)（1 周）
  ├── OrchestratorAgent.resume(task_id)
  ├── ReAct / DAG / Emergent 三引擎的 checkpoint
  └── HITL × resume 边界设计

v14.5 ⏳ Interleaved Thinking 范式重写（2 周）
  ├── 新建 react/reasoning_engine.py（与 engine.py 并行）
  ├── 思考-工具-思考-工具多轮支持
  └── ContextManager 与新引擎集成

v14.6 ⏳ reasoning_effort × ToolRouter 联动（半周）

v14.7 ⏳ Harness 配置层抽离（1 周）
  ├── prompt_utils → harness 配置层
  ├── tool_call_helpers strategies 可配置
  └── 按模型类型切换
```

总计 5-6 周，与 roadmap §五 的 "2-3 周" 相比仍偏紧，建议把工期估计修正为 5-6 周。
