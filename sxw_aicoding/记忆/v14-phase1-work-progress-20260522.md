# v14 工作进展记录

> **最后更新**：2026-05-22
> **当前状态**：Phase 1 + Phase 2 + Phase 3 已完成，Phase 3 评审 bugfix 已完成，Phase 4-5 待实施

---

## 一、已完成工作

### 1. Phase 1 主实现（Reasoning Token 分桶）

已合入主线，16 条单元测试全部通过，505 条全量回归通过。

**改动文件**：
- `schema.py` — TokenUsage + LLMCallRecord 加 `reasoning_tokens: int = 0`
- `llm/client.py` — `_record_call()` 提取 reasoning_tokens；`_extract_thinking_content()` 解析 `<think/>` 标签；`_extract_response_data()` 返回 thinking_content
- `config.py` — 新增 `REASONING_TOKEN_TRACKING` 配置
- `main.py` — Per-call 表动态显示 Reasoning 列；Grand total 条件显示 Reasoning 行 + provider 感知 dim 注释
- `context/manager.py` — `estimate_messages_tokens()` 加 thinking_content 估算
- `agents/orchestrator.py` — `_finalize_token_usage()` 聚合 reasoning_tokens 到 by_engine/by_caller/total
- `tests/test_v14_reasoning_tokens.py` — 初始 13 条 → 后续扩展

### 2. Phase 1 补丁（Fix-1~Fix-8，基于 v1 代码评审）

| Fix | 严重性 | 文件 | 内容 |
|---|---|---|---|
| Fix-1 | P0 | llm/client.py | `_end_llm_span()` 加 reasoning_tokens + thinking_content span 属性 |
| Fix-2 | P0 | react/engine.py | 添加 TODO 注释（DeepSeek R1 thinking 剥离） |
| Fix-3 | P1 | context/manager.py | 添加 NOTE 注释（thinking_content 死代码说明） |
| Fix-4 | P1 | schema.py | 更新 total_tokens 注释（区分 provider 语义） |
| Fix-5 | P1 | main.py | by_engine + by_caller 表加 Reasoning 列（条件显示） |
| Fix-6 | P2 | llm/client.py | `import re` 移至模块顶层 |
| Fix-7 | P2 | tests | 新增 3 个 `_record_call()` mock 测试 |
| Fix-8 | P2 | llm/client.py | `_extract_thinking_content` 签名修正 |

### 3. Phase 1 S1-S7 修复（基于 v3 代码评审）

| # | 缺陷 | 修复内容 |
|---|---|---|
| S1 | DeepSeek `reasoning_content` 字段未读 | `_extract_response_data()` 先读 `message.reasoning_content`，无则 fallback |
| S4 | root span 无 reasoning_tokens | `tracing/bridge.py:_on_token_usage()` 加 reasoning_tokens |
| S5 | spans.py 缺属性常量 | 加 `GEN_AI_USAGE_REASONING_TOKENS` + `GEN_AI_RESPONSE_THINKING_CONTENT` |
| S6 | parse_json 局部 import 遗漏 | 删除内层 `import re` |
| S7 | 回退 Fix-5 | 后续由 N2 撤销（见 fix-audit） |

### 4. fix-audit 回归修复（N1/N2/N3/B5）

| # | 修复 | 文件 | 内容 |
|---|---|---|---|
| N1 | grand total dim 注释 | main.py | 恢复 provider 感知 dim 注释 |
| N2 | 恢复 Reasoning 列 | main.py | 撤销 S7 回退，by_engine/by_caller 加 has_reasoning 条件列 |
| N3 | root span `> 0` 守卫 | tracing/bridge.py | reasoning_tokens 只在 > 0 时 set_attribute |
| B5 | 版本号修正 | config.py | v14.0 → v13.x (v14 Phase 1 in progress) |

### 5. Phase 2: ReasoningEngine

| 改动 | 文件 | 内容 |
|---|---|---|
| 新建 ReasoningEngine | `react/reasoning_engine.py` | 继承 ReActEngine，override execute()：thinking 不计迭代、thinking budget 独立控制、assistant_msg 分离 thinking |
| Feature flag | `config.py` | `ENABLE_REASONING_ENGINE` (默认 false) + `MAX_THINKING_TOKENS` (默认 10000) |
| 引擎选择 | `agents/executor.py` | 根据 `ENABLE_REASONING_ENGINE` 选择 ReasoningEngine 或 ReActEngine |
| 测试 | `tests/test_v14_reasoning_engine.py` | 10 条测试 |

### 6. Phase 2 评审 bugfix（P2/P3/P4/P5/P8）

| # | 严重性 | 文件 | 内容 |
|---|---|---|---|
| P2 | 严重 | reasoning_engine.py | iteration 0 边界 bug：`non_system_msgs + tool_calls_log` 判断替代 `iteration==0` |
| P3 | 严重 | reasoning_engine.py | budget 差分法：`records_before/after` 替代 `call_records[-1]` |
| P4 | 中-严重 | reasoning_engine.py | budget 超限输出含 tool summary + partial response |
| P5 | 中 | reasoning_engine.py | `import re` 移至模块顶层 |
| P8 | 中 | test_v14_reasoning_engine.py | +3 回归测试 + 2 断言补充 |

### 7. Phase 3: Thinking 剥离 + Context thinking-aware + Harness 配置层

| 改动 | 文件 | 内容 |
|---|---|---|
| ReActEngine thinking 剥离 | `react/engine.py` | 接入 `_extract_thinking_content` + `_strip_thinking_from_content`，assistant_msg 写入 thinking_content，删除 TODO |
| ContextManager thinking-aware | `context/manager.py` | `_messages_to_text` 包含 thinking；`_find_safe_split` 保护 thinking 块；更新 NOTE 注释 |
| Harness 配置层 | `config.py` | 6 个新配置：REACT/REASONING/PLANNER/REFLECTOR_TEMPERATURE + CONVERGENCE_ESCALATION_MULTIPLIER + THINKING_AWARE_CONTEXT |
| 收敛倍数可配置 | `agents/prompt_utils.py` | `threshold * 2` → `threshold * config.CONVERGENCE_ESCALATION_MULTIPLIER` |
| Reflector Wave-2 对齐 | `agents/reflector.py` | 使用 `build_system_prompt()` 替代裸 prompt（修复日期注入缺失） |
| 温度值接入 | `react/engine.py`, `react/reasoning_engine.py` | `temperature=0.5` → `config.REACT_TEMPERATURE` / `config.REASONING_TEMPERATURE` |
| 测试 | `tests/test_v14_reasoning_tokens.py` | +12 测试：TestReActEngineThinkingStripping + TestContextManagerThinkingAware + TestHarnessConfig |

### 8. Phase 3 评审 bugfix（P1/P2/P3/P8）

| # | 严重性 | 文件 | 内容 |
|---|---|---|---|
| P1 | HIGH | planner.py + reflector.py | 接入 PLANNER_TEMPERATURE（5 处 0.3）和 REFLECTOR_TEMPERATURE（3 处 0.1/0.2） |
| P2 | HIGH | react/engine.py | reasoning-only 响应不再静默返回 "Task completed"，改为 continue 请求显式答案 |
| P3 | HIGH | llm/client.py + engine.py + reasoning_engine.py | `_strip_thinking_from_content` 迁移到 llm/client.py，消除基类反向依赖派生类 |
| P8 | LOW | context/manager.py | 去掉 `_messages_to_text` 的 THINKING_AWARE_CONTEXT flag 检查，thinking 永远包含在摘要中 |

---

## 二、当前状态：Phase 1-3 + 评审 bugfix 全部完成

**测试**：52 条 v14 测试全通过，542 条全量回归通过（1 个预存 flaky test 与 v14 无关）。

### 待修复（中期，不阻塞合并）

| # | 缺陷 | 说明 |
|---|---|---|
| S2 | 跨 provider 算术失真 | OpenAI: reasoning 是 completion 子集；DeepSeek: 独立维度；Claude: 隐形。渲染层需按 provider 区分或加 footnote |
| P4(DRY) | 两个 Engine ~95 行工具执行块重复 | 需抽出 `react/engine_helpers.py` 共享模块（Phase 4 必做） |
| P9 | thinking trace 截断长度与 response 共享 | 建议 `TRACING_MAX_THINKING_LENGTH` 独立配置（默认 8000） |

---

## 三、v14 整体完成度

| 子项 | 描述 | 完成度 |
|------|------|--------|
| 1 | 双协议支持（DeepSeek R1 / OpenAI o 系列） | **90%**（ReActEngine + ReasoningEngine 均已剥离 thinking） |
| 2 | ReAct 迭代计数修正 | **80%**（ReasoningEngine 已完成，ReActEngine 对 reasoning-only 也有了 continue 处理） |
| 3 | Interleaved Thinking | **20%**（thinking_content 贯穿消息生命周期，但未实现思考-工具-思考多轮） |
| 4 | reasoning_effort × ToolRouter 联动 | **0%** |
| 5 | 任务持久化与恢复 | **0%** |
| Harness a | prompt_utils 配置层抽离 | **60%**（6 项配置接入 6/6，但未抽出独立模块） |
| Harness b | tool_call_helpers 策略可配置 | **0%** |
| Harness c | ContextManager thinking-aware split | **100%** ✅ |
| **整体** | | **~50%** |

---

## 四、关键文件索引

| 文件 | 用途 |
|---|---|
| `sxw_aicoding/temp/v14-phase1-code-review.md` | v1 评审 |
| `sxw_aicoding/temp/v14-phase1-code-review-v2-ultrathink.md` | v3 评审（覆盖 v2） |
| `sxw_aicoding/temp/v14-phase2-code-review.md` | Phase 2 评审 |
| `sxw_aicoding/temp/v14-phase3-code-review.md` | Phase 3 评审 |
| `sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md` | v14-v19 完整路线图 |
| `.claude/plans/v14-phase-1-optimized-squirrel.md` | 最新实施计划 |
| `tests/test_v14_reasoning_tokens.py` | 39 条 v14 测试（Phase 1 + 3） |
| `tests/test_v14_reasoning_engine.py` | 13 条 v14 测试（Phase 2） |

---

## 五、下游阻塞关系

```
v14 Phase 1-3 (已完成) ──→ v14 Phase 4 (reasoning_effort + Task Resume + DRY 重构)
                        ──→ v14 Phase 5 (Interleaved Thinking)

v14 全部完成 ──→ v15 Agentic Memory（依赖 thinking-aware split + reasoning_content）
            ──→ v17 Self-Evolution（依赖 reasoning_effort + cost-aware 信号）
            ──→ v18 Handoff（依赖 thinking 归因语义）
```

---

## 六、恢复会话时

下次启动时说"继续 v14 Phase 4"即可。关键上下文：
- Phase 1 + Phase 2 + Phase 3 + 评审 bugfix 全部完成
- Phase 4: reasoning_effort × ToolRouter 联动 + Task Resume + DRY 重构（抽 engine_helpers.py）
- Phase 5: Interleaved Thinking（范式重写）
- 实施计划在 `.claude/plans/v14-phase-1-optimized-squirrel.md`
