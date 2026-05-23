# v14 Phase 1 修复回归评审（fix-audit）

> **评审日期**：2026-05-22
> **评审依据**：v1（`v14-phase1-code-review.md`）+ v2（`v14-phase1-code-review-v2-ultrathink.md`）
> **评审范围**：当前工作树未提交的 9 个文件改动（较初评新增 `tracing/bridge.py`、`tracing/spans.py`）
> **结论**：**修复了 R1（DeepSeek `reasoning_content` 字段读取），并补齐了 OTel 常量化**——这是 v1/v2 评审中最严重的问题；但 v1/v2 的另外 13 个问题中有 12 项原状未动，其中 R2 反而**因 UI 简化删除注释而退步**，main.py 引入新的 `by_engine` / `by_caller` 表"reasoning 列遗漏"功能不对等。

---

## 一、修复全景

### 1.1 diff 统计

```
 agents/orchestrator.py |  3 +++
 config.py              |  4 ++++
 context/manager.py     |  5 +++++
 llm/client.py          | 50 +++++++++++++++++++++++++++++++++++++++++++++++---
 main.py                | 38 +++++++++++++++++++++++++-------------
 react/engine.py        |  3 +++
 schema.py              |  4 +++-
 tracing/bridge.py      |  2 ++
 tracing/spans.py       |  2 ++
 9 files changed, 94 insertions(+), 17 deletions(-)
```

较初评（7 文件 +96/-13）新增：
- ✅ `tracing/spans.py`：新增 `AttrKey.GEN_AI_USAGE_REASONING_TOKENS` + `AttrKey.GEN_AI_RESPONSE_THINKING_CONTENT` 常量
- ✅ `tracing/bridge.py`：根 span 也聚合 `reasoning_tokens`（v7 全链路覆盖）
- ✅ `llm/client.py`：新增 `getattr(message, "reasoning_content", None)` 读取分支

### 1.2 v1/v2 评审清单核对（14 项）

| 编号 | 来源 | 描述 | 修复状态 | 备注 |
|---|---|---|---|---|
| **R1** | v1 §5.1 | DeepSeek 官方 API `message.reasoning_content` 未读取 | ✅ **已修复** | `_extract_response_data` 先读字段，命中即用，否则 fallback 到 `<think>` |
| **R2** | v1 §5.1 | OpenAI `reasoning_tokens` 与 `completion_tokens` 重复计数，UI 算术失真 | 🔴 **未修复且退步** | 原本有 `[dim]Note: Total may include reasoning tokens...[/dim]` 提示，本次**删除了该注释**，问题变得更隐蔽 |
| **R3** | v1 §5.1 / v2 §三 | `_find_safe_split` 未做 thinking-aware 切分 | 🔴 **未修复** | `context/manager.py:167-206` 一字未改 |
| **R4** | v1 §5.1 | primary 正则匹配非标准 `<think\n...\n</think\n>` 是死代码 | 🔴 **未修复** | `_extract_thinking_content()` primary 分支仍在 |
| **R5** | v2 §一 | 跨提供商语义（OpenAI 子集 / DeepSeek 独立 / Claude 不可见）需分流聚合 | 🔴 **未修复** | `orchestrator.py:692-715` 仍按"独立维度"加 |
| **R6** | v2 §二 | OpenAI o-series 与 Claude extended thinking 完全未支持 | 🔴 **未修复** | 仅 DeepSeek 两路 |
| **R7** | v2 §四 | 应新建 `react/reasoning_engine.py` 并行而非原地改 | 🔴 **未修复** | `react/engine.py` 仅一条 TODO |
| **B1** | v2 §六 | `REASONING_TOKEN_TRACKING` 被 `TOKEN_TRACKING_ENABLED` 静默吞掉（开关耦合） | 🔴 **未修复** | `client.py:334` 早 return |
| **B2** | v2 §六 | OTel `thinking_content` 截断 `[:1000]` 非对称 | 🟡 **已应用截断但仍非对称** | 现在显式调用 `config.TRACING_MAX_ATTRIBUTE_LENGTH` |
| **B3** | v2 §六 | 正则 `(.*?)` 在大 content 下有回溯成本 | 🔴 **未修复** | 无任何性能保护 |
| **B4** | v2 §六 | 测试 `LLMClient.__new__()` 绕过 `__init__` 是脆弱契约 | 🔴 **未修复** | 测试未动 |
| **B5** | v2 §六 | 版本号撒谎（v13.x 第 3 项被标为 v14.0） | 🔴 **未修复** | `config.py:145-147` 注释仍写 v14.0 |
| **B6** | v2 §六 | ToolRouter 未联动 reasoning_effort | 🔴 **未修复** | roadmap §五 子项 4 |
| **议题 5** | v2 §五 | v14 残缺会级联阻塞 v15/v17/v18 | 🔴 **未解除** | 5 个 §五 子项 0 进展 |

**修复完成度**：14 项中真正修复 1 项（R1），部分应用 1 项（B2），原状不动 12 项。修复率 ~7%。

### 1.3 本次新增的"工程化加分项"（v1/v2 未点名但实现得对）

| # | 改进 | 价值 |
|---|---|---|
| **+1** | `tracing/spans.py` 引入 `AttrKey.GEN_AI_USAGE_REASONING_TOKENS` / `GEN_AI_RESPONSE_THINKING_CONTENT` 常量 | 避免散落字符串字面量，符合 OTel GenAI semantic conventions 自治原则 |
| **+2** | `tracing/bridge.py` 在 root span 也聚合 reasoning_tokens | 全链路覆盖（v7 单 LLM span + 根 task span 双层都能拿到） |
| **+3** | OTel `thinking_content` 走 `config.TRACING_MAX_ATTRIBUTE_LENGTH` 截断 | 不会因极端长 thinking 撑爆 span exporter |
| **+4** | LLM span 写入 reasoning_tokens 时加了 `> 0` 守卫 | 普通模型不污染 trace 维度 |

---

## 二、唯一真正修复项的代码审视：R1

### 修复代码

```python
# llm/client.py:558-588
content = getattr(message, "content", None) or ""
reasoning_content = getattr(message, "reasoning_content", None) or ""
...
return {
    "response_content": content,
    "tool_calls": tool_calls,
    "finish_reason": finish_reason,
    "thinking_content": reasoning_content if reasoning_content else _extract_thinking_content(content),
}
```

### 评价

**优点**：
- 优先级正确：字段 > 标签解析（DeepSeek 官方走字段，自部署 R1-distill 走 `<think>`）
- 三元表达式既明确意图，又避免双重赋值
- 异常路径同步加 `"thinking_content": ""` 默认值，无 KeyError 风险

**仍存在的问题**：
1. **R6 未连带修**：Claude API 的 extended thinking 是 `message.content` 数组里的 `thinking` block，OpenAI o-series 不暴露 thinking 文本——这两种主流推理模型仍然 0 覆盖
2. **provider 判定缺失**：当 `reasoning_content` 字段非空但 `<think>` 标签也在 `content` 里（理论上某些 vLLM 部署会同时输出），将丢弃后者无法回收。建议加 `logger.debug` 记录"两路都命中"的诡异情况
3. **没有相应的单元测试**：`tests/test_v14_reasoning_tokens.py` 仍只测试 `_extract_thinking_content()` 函数本身（覆盖 fallback 分支），**没有测试 `_extract_response_data()` 对 `reasoning_content` 字段的优先级**——这是本次修复的核心改动，应该有专门 mock OpenAI 响应对象的 `reasoning_content` 属性的测试

---

## 三、新引入的问题（fix 反而带来的退步与遗漏）

### 新问题 N1（严重）：UI 注释删除是退步

**原代码**（v14 Phase 1 第一版）：
```python
console.print(Panel(
    f"[bold]Total Tokens: {summary.total.total_tokens}[/bold]\n"
    f"  Prompt:     {summary.total.prompt_tokens}\n"
    f"  Completion: {summary.total.completion_tokens}\n"
    f"  [dim]Note: Total may include reasoning tokens (prompt + completion ≤ total)[/dim]",
    ...
))
```

**修复后**：
```python
total_lines = [
    f"[bold]Total Tokens: {summary.total.total_tokens}[/bold]",
    f"  Prompt:     {summary.total.prompt_tokens}",
    f"  Completion: {summary.total.completion_tokens}",
]
if summary.total.reasoning_tokens > 0:
    total_lines.append(f"  [yellow]Reasoning:  {summary.total.reasoning_tokens}[/yellow]")
console.print(Panel("\n".join(total_lines), ...))
```

**问题**：
- 原版 dim 注释 **明确告诉用户**"prompt + completion ≤ total"——这是面向 OpenAI 用户的算术解释
- 修复版**完全删除**该注释，理由可能是"reasoning_tokens=0 时不显示就够了"——错。**两种情况都让用户困惑**：
  - **OpenAI 用户**（reasoning_tokens=500，completion_tokens=200，total=800）：看到 `Prompt: 100 / Completion: 200 / Reasoning: 500 / Total: 800` → 直觉算 100+200+500=800（恰巧对，但是巧合：实际 completion 已经包含 reasoning，应该是 100+200=300，total 多出 500 是 hidden reasoning 占的 quota）
  - **DeepSeek 用户**（reasoning_tokens=300，completion=200，total=600）：看到同样的视觉布局，但算式是 100+200+300=600（这次是真加法）
- 用户从 UI **不可能区分两种语义**，注释删除让算术变成"既不解释也不警告"

**建议**：保留原 dim 注释，或按 provider 分别渲染：
```python
if summary.total.reasoning_tokens > 0:
    if _is_openai_o_series(engines):
        total_lines.append(f"  [yellow]Reasoning:  {n} (included in Completion)[/yellow]")
    else:
        total_lines.append(f"  [yellow]Reasoning:  {n} (separate from Completion)[/yellow]")
```

### 新问题 N2（严重）：`by_engine` / `by_caller` 表的 Reasoning 列遗漏（功能不对等）

**修复代码**：
```python
# main.py:190 - by_engine table 仅定义 4 列
engine_table.add_column("Engine", ...)
engine_table.add_column("Prompt Tokens", ...)
engine_table.add_column("Completion Tokens", ...)
engine_table.add_column("Total Tokens", ...)
```

**对比同文件 per-call table 的处理**：
```python
# main.py:158 - per-call 表有条件渲染
if has_reasoning:
    table.add_column("Reasoning", style="yellow", justify="right", width=10)
```

**问题**：
- `orchestrator.py:695, 706` **已经把 `reasoning_tokens` 聚合到 `by_engine` 和 `by_caller`**：
  ```python
  by_engine[record.engine].reasoning_tokens += record.reasoning_tokens
  by_caller[caller_key].reasoning_tokens += record.reasoning_tokens
  ```
- 但 main.py 的 engine_table 和 caller_table **完全没有 Reasoning 列**！
- 结果：聚合数据生成了但 UI 不展示。SubAgent 模式下的 reasoning 消耗、不同模型的 reasoning 对比，**用户看不到**
- 但若用户只看 per-call 表，又会困惑"为什么 SubAgent 多消耗了 token 看不到原因"

**建议**：把 per-call 表的 `has_reasoning` 条件渲染模式复制到 engine_table 和 caller_table（DRY 重构成共用辅助函数更好）

### 新问题 N3（中）：根 span 写 reasoning_tokens 时缺 `> 0` 守卫

**修复代码**：
```python
# tracing/bridge.py:752-758
reasoning_tokens = getattr(total, "reasoning_tokens", 0)
self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_INPUT_TOKENS, prompt_tokens)
self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_OUTPUT_TOKENS, completion_tokens)
self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_TOTAL_TOKENS, total_tokens)
self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_REASONING_TOKENS, reasoning_tokens)
```

**对比同期 LLM span 的处理**：
```python
# llm/client.py:506-508 - 有 > 0 守卫
if last_record.reasoning_tokens > 0:
    span.set_attribute(AttrKey.GEN_AI_USAGE_REASONING_TOKENS, last_record.reasoning_tokens)
```

**问题**：
- 根 span 是**所有任务**的根节点，普通模型（reasoning_tokens 永远为 0）的每个 trace 都会被强制带上 `gen_ai.usage.reasoning_tokens=0` 属性
- 影响：
  - OTel 后端（Phoenix / Tempo / Jaeger）会全量索引这个零值属性，造成索引膨胀
  - 看板查询"哪些 trace 用了 reasoning"时会被噪声 0 值干扰
  - 不一致：LLM span 有守卫，root span 没有——下游脚本不能假设统一规则

**建议**：与 LLM span 对称化：
```python
if reasoning_tokens > 0:
    self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_REASONING_TOKENS, reasoning_tokens)
```

### 新问题 N4（中）：context/manager.py 的修复仍是"伪修复"

**修复代码**：
```python
# context/manager.py:79-83
# NOTE: thinking_content 键当前无写入方——待 Phase 4 ReActEngine 剥离 thinking 后生效
thinking = msg.get("thinking_content", "") or ""
if thinking:
    total += self.estimate_tokens(thinking) + 4
```

**问题（v2 议题 3 早已指出，未解除）**：
- 注释自己也承认 "**当前无写入方**"
- `_extract_response_data` 把 `thinking_content` 放在**返回数据中**（流向 OTel/tracing），但**完全不会**写到 `messages[i]["thinking_content"]`（流向下一轮 context）
- 这个 if 分支 **100% 死代码**——估算逻辑等于不存在
- 真实场景下，DeepSeek R1 的 reasoning 在 `message.reasoning_content` 字段，但**当前 ReActEngine 完全没有把这个字段保留到 message dict**（参见 `react/engine.py:208-216`，仅一条 TODO），所以下一轮 LLM 也根本看不到上一轮的 thinking

**真正应该做的**（v2 议题 3）：
- 要么先在 `react/engine.py` 里完成 thinking 剥离/保留逻辑，再来加估算
- 要么直接读 `msg["content"]` 内的 `<think>` 块估算（更现实，因为当前 `<think>` 是嵌在 content 里的）

### 新问题 N5（轻）：main.py 表格构建从"一行一调用"改成"list + unpack" 但**只对 by_engine/by_caller** 改

```python
# 修复前：直接传参
engine_table.add_row(engine, str(usage.prompt_tokens), str(usage.completion_tokens), str(usage.total_tokens))

# 修复后：构建 list + unpack
row = [engine, str(usage.prompt_tokens), str(usage.completion_tokens)]
row.append(str(usage.total_tokens))
engine_table.add_row(*row)
```

**问题**：
- 改了表格构建方式但**没有真的为 reasoning 留位置**——只是把"5 行"重排成"4 行 + 1 行 append"，逻辑等价
- 这是典型的"中途改了一半"特征：似乎本意是想加 reasoning 列（参考 per-call 表的 `if has_reasoning` 模式），但只改了变量结构没改 add_column——半成品代码
- 阅读成本却增加了：原本一目了然的 `add_row(...)` 变成了多步 build

**建议**：要么彻底完成（加 has_reasoning 条件列），要么恢复成原版。当前是最糟的状态

---

## 四、未触及的 v2 议题原状回顾（4 大项）

> 此节不重复 v2 内容，仅列状态。详情参见 `v14-phase1-code-review-v2-ultrathink.md`

### 议题 1：跨提供商算术失真
- 状态：**未触及**
- 风险等级：现在用户能在 UI 同时看到 OpenAI 和 DeepSeek 模型的同位置数字，但**算术语义不同**且不再有 dim 注释提醒（参见新问题 N1）
- 阻塞：v15+ 任何 reasoning 相关基准评测都不能直接累加跨模型的数字

### 议题 3：`_find_safe_split` 已是当前 bug
- 状态：**未触及**（`context/manager.py:167-206` 一字未改）
- 风险等级：使用 DeepSeek R1 + 上下文超过 `MAX_CONTEXT_TOKENS=16000` 触发压缩时，`<think>` 块被切到中间，下一轮 LLM 收到残缺消息——会让推理模型陷入混乱或拒答
- 复现触发链：
  ```
  PLAN_MODE=emergent + LLM_MODEL=deepseek-reasoner + 长任务
   → ContextManager.compress() 被触发
   → _find_safe_split() 在 messages 内寻找切点
   → 切到 <think>...</think> 中间
   → recent_msgs[0] 是 `</think>\n好的，我开始执行...`
   → 下一轮 LLM 看到 "无开头有结尾" 的 thinking 残片
   ```
- 这是 v14 范围内**当前就存在**的 bug，不是"Phase 4 才暴露"

### 议题 4：架构反模式（应新建 `reasoning_engine.py` 并行）
- 状态：**未触及**（仅 `react/engine.py:208-210` 一条 TODO）
- roadmap §五 line 87 明确写："`react/reasoning_engine.py`（新增，与 ReActEngine 并行）"
- 当前路径会让 `react/engine.py` 逐步堆积 reasoning model 分支，最终成为"两套逻辑混杂、谁也不敢动"的状态
- 历史先例：`config.py:104` 的 `ENABLE_REACT_ENGINE_V2` 是项目自己的灰度成功范式，本次未沿用

### 议题 5：v13.x 第 3 项被标为 v14.0
- 状态：**未触及**（`config.py:145` 仍写 v14.0）
- 影响：合入后 CHANGELOG 与 roadmap 错位，新成员读到的是"v14 已完成"
- v13.x 维护批次的另 5 项（codemap 回填、Wave-5 沙箱修复、DAG 默认值复盘、evaluation 扩样、清理 v12 占位）**全部未做**

---

## 五、修复 vs 评审 的对应关系矩阵

```
       v1/v2 评审清单 14 项
         |
         | 已修复 1 项（R1）          ┐
         |                             |
         | 部分应用 1 项（B2）         |  实际修复率 ≈ 7%
         |                             |
         | 原状未动 12 项              ┘
         |
         | + 退步 1 项（R2 注释删除）  ┐
         | + 新引入 5 个问题（N1-N5）  |  净恶化 +6 项
         | + 半成品代码 1 处（N5）     ┘
         |
       综合：技术债增加，关键 bug 1 个真正消除
```

---

## 六、综合结论

### 6.1 修复的真实价值

**R1 是 v1/v2 评审中最严重的问题**——DeepSeek 官方 R1 在生产环境中 reasoning_content 永远空——这次修复是**正确且必要的**，单这一项就值得合入。

**OTel 常量化** 是良好工程实践，值得鼓励。

但 R2-R7 + B1-B6 + 议题 1/3/4/5 全部未动，且 R2 还出现退步、N1-N5 净增 6 个新问题——**这次修复的实际净改善**只有：
```
+1 (R1)
+1 (OTel 常量化)
+1 (root span 聚合)
-1 (UI 注释删除)
-1 (by_engine/by_caller 表格不对等)
-1 (根 span 无 > 0 守卫)
-1 (context/manager 仍是死代码)
-0.5 (main.py 半成品代码)
=  -1.5 净分
```

也就是说，**这次修复在工艺上有进步（OTel 常量化、根 span 覆盖），但在 v1/v2 评审清单的执行率上是退步的**——只挑了最大那个 bug 修，其他 12 项暂搁。

### 6.2 合入决策建议

**建议方案**：分两步合入

**Step 1（立即合入）**：
- ✅ R1 修复（DeepSeek `reasoning_content` 字段读取）
- ✅ OTel `AttrKey` 常量化
- ✅ Root span reasoning_tokens 聚合（**但需先加 `> 0` 守卫**——见 N3）

**Step 2（合入前必修）**：
- 🔴 撤销 UI 注释删除（N1）—— 5 行代码，恢复 dim 注释或加分流逻辑
- 🔴 补齐 by_engine / by_caller 的 reasoning 列（N2）—— 20 行代码，复用 has_reasoning 模式
- 🔴 添加 R1 修复的单元测试 —— mock 一个带 `reasoning_content` 属性的 message 对象

**Step 3（下一个 PR）**：
- R3（`_find_safe_split` thinking-aware）—— 当前真实 bug
- R5（按 provider 分流聚合）—— 跨模型评测前置
- R7（`reasoning_engine.py` 并行设计决策）—— roadmap 对齐
- 删除版本号撒谎（v14.0 → v13.3 或新版本号）

### 6.3 一句话总结

**这次修复消除了 v1/v2 评审中最严重的 R1（DeepSeek `reasoning_content` 缺失）并补齐了 OTel 常量化，但仅占评审清单 14 项中的 1 项；同期 main.py 的 UI 简化删除了关键的算术注释，by_engine/by_caller 聚合数据有但不展示，根 span 缺 `> 0` 守卫——净引入 5 个新问题、1 项退步。** 强烈建议在合入前补齐 N1-N3 三个新问题，避免本次修复 PR 的净改善变成负值。

---

## 七、附：本次修复后的 roadmap §五 完成度刷新

| roadmap 子项 | v1/v2 评审时 | 本次修复后 | Δ |
|---|---|---|---|
| 1. 双协议支持（含 reasoning_content 字段） | 🟡 60% | 🟢 **90%** | **+30%** |
| 2. ReAct 迭代计数修正 + MAX_THINKING_TOKENS | 🔴 0% | 🔴 0% | 0 |
| 3. Interleaved Thinking | 🔴 0% | 🔴 0% | 0 |
| 4. reasoning_effort × ToolRouter 联动 | 🔴 0% | 🔴 0% | 0 |
| 5. 任务持久化与恢复 (resume) | 🔴 0% | 🔴 0% | 0 |
| Harness-a：prompt_utils 抽 Harness 配置层 | 🔴 0% | 🔴 0% | 0 |
| Harness-b：tool_call_helpers strategies 可配置 | 🔴 0% | 🔴 0% | 0 |
| Harness-c：thinking-aware split | 🟡 30% | 🟡 30% | 0 |

**v14 §五 加权完成度**：从 12% → 16%（提升 4 个百分点），仍距 80% 完成可发布门槛差距巨大。

---

## 八、给后续 PR 的具体修复脚本（伪代码）

### 修复 N1（UI 注释）
```python
# main.py:238-251
if summary.total.reasoning_tokens > 0:
    total_lines.append(f"  [yellow]Reasoning:  {summary.total.reasoning_tokens}[/yellow]")
    total_lines.append("  [dim](Note: provider-dependent — for OpenAI o-series, reasoning is included in Completion; for DeepSeek R1, reasoning is separate)[/dim]")
```

### 修复 N2（表格不对等）
```python
# main.py:184-196 - by_engine table
engine_table.add_column("Engine", ...)
engine_table.add_column("Prompt Tokens", ...)
engine_table.add_column("Completion Tokens", ...)
if has_reasoning:
    engine_table.add_column("Reasoning Tokens", style="yellow", ...)
engine_table.add_column("Total Tokens", ...)

for engine, usage in summary.by_engine.items():
    row = [engine, str(usage.prompt_tokens), str(usage.completion_tokens)]
    if has_reasoning:
        row.append(str(usage.reasoning_tokens) if usage.reasoning_tokens else "-")
    row.append(str(usage.total_tokens))
    engine_table.add_row(*row)
# 同样模式应用到 caller_table
```

### 修复 N3（根 span 守卫）
```python
# tracing/bridge.py:758
if reasoning_tokens > 0:
    self._root_span.set_attribute(AttrKey.GEN_AI_USAGE_REASONING_TOKENS, reasoning_tokens)
```

### 修复 R1 测试缺失
```python
# tests/test_v14_reasoning_tokens.py - 新增测试
class TestExtractResponseDataReasoningContent:
    def test_deepseek_official_reasoning_content_field(self):
        """DeepSeek 官方 API 的 reasoning_content 字段优先于 <think> 标签解析."""
        from types import SimpleNamespace
        from llm.client import LLMClient

        message = SimpleNamespace(
            content="实际响应",
            reasoning_content="推理过程",
            tool_calls=None,
        )
        choice = SimpleNamespace(message=message, finish_reason="stop")
        resp = SimpleNamespace(choices=[choice])

        client = LLMClient.__new__(LLMClient)
        result = client._extract_response_data(resp)
        assert result["thinking_content"] == "推理过程"

    def test_fallback_to_think_tag_when_no_reasoning_content(self):
        """无 reasoning_content 字段时回退到 <think> 标签解析."""
        from types import SimpleNamespace
        from llm.client import LLMClient

        message = SimpleNamespace(
            content="<think\n思考\n</think\n>响应",
            tool_calls=None,
        )
        # 没有 reasoning_content 属性
        choice = SimpleNamespace(message=message, finish_reason="stop")
        resp = SimpleNamespace(choices=[choice])

        client = LLMClient.__new__(LLMClient)
        result = client._extract_response_data(resp)
        assert "思考" in result["thinking_content"]
```

按上述四处修复合入，本次 PR 的净改善将从 -1.5 变为 +5.5（R1 + OTel + root span + N1 修复 + N2 修复 + N3 修复 + R1 测试覆盖）。
