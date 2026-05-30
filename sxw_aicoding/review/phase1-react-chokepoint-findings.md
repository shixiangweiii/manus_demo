# Phase 1 — Chokepoint & 共享 ReAct 引擎 findings

日期：2026-05-30 · 文件：`react/{engine_helpers,engine,reasoning_engine,tool_call_helpers}.py`

## Findings

### F1.1 — P1（已修）handoff 控制权转移在 ReasoningEngine 被绕过
- **现象**：`#20` handoff 控制权转移逻辑只写在 `ReActEngine.execute`，而 `ReasoningEngine.execute`（`react/reasoning_engine.py`）是**完整 override**，未复制该逻辑（`_handoff_tool_names` 仅在 `engine.py:325` 使用）。
- **触发**：`agents/executor.py:103` 在 `ENABLE_REASONING_ENGINE=true` 时切换到 ReasoningEngine；而 `evaluation/variants.py` 的 `handoff_on` 变体**同时**设 `ENABLE_REASONING_ENGINE=True` + `HANDOFF_ENABLED=True` → v18.5 handoff 评测恰好走被绕过的路径。
- **影响**：reasoning 模式下 handoff 退化为普通工具调用——专家执行了，但不终止父循环、不以专家完整输出为最终答案；父级拿到的是被 `truncate_for_llm` 截断的工具结果。破坏 v18.2 设计语义，且使 handoff 评测失真。属"双循环漂移"（`tool_call_helpers` 文档明确警示的反模式）。
- **修复**：把 handoff 终止检查抽成 `ReActEngine._check_handoff_transfer(...)`（共享方法），`ReActEngine.execute` 与 `ReasoningEngine.execute` 在 `execute_tool_calls` 后均调用。读 `_last_output`（未截断）保持控制权转移完整语义。
- **验证**：两引擎冒烟均 `success=True, output='SPECIALIST FULL ANSWER', iters=1`；`py_compile` 通过。

## 已核验正确（无问题）
- **guardrail 关闭零开销**：`execute_tool_calls` 先判 `config.GUARDRAILS_ENABLED` 才 lazy import + `current_guardrail()`；关闭→None→跳过。✓
- **guardrail 异常隔离**：`check_tool_input` / `scan_tool_output` 均包 try/except（前者放行、后者透传），护栏异常不打断工具执行。✓
- **BLOCK 短路**：BLOCK → 返回 `Error: [GUARDRAIL BLOCKED]` 且不调 `traced_execute`（工具不执行）。✓
- **NEUTRALIZE 顺序**：仅对非 error 结果中和；中和文本（UNTRUSTED 头在前）再过 `truncate_for_llm`，头部信息保留。✓
- **ReasoningEngine guardrail 覆盖**：其 `execute()` 走同一 `execute_tool_calls`（line 269），故 guardrail tool-input/output **已覆盖**（仅 handoff 漂移，已修）。✓
- **#1 `_current_log` rebind**：两引擎每次 execute 都 `self._current_log = tool_calls_log`（新列表）。✓
- **#2 lazy import**：`build_convergence_hint` 两引擎均在 execute 内 lazy import；guardrail 亦 lazy。✓
- **#4 ToolRouter 三态**：`classify_result` → rate_limited/failure/success 三态记账保留。✓

## Backlog（P2/P3，本轮不修）
- **F1.2 (P2)** guardrail BLOCK 的结果以 `Error:` 前缀返回 → `classify_result` 记为 tool failure → 累计达 `TOOL_FAILURE_THRESHOLD` 触发"换工具"提示。行为可接受（促使 LLM 改方案），但把"策略拦截"与"工具故障"混入同一失败桶，统计语义不纯。可考虑加 guardrail marker 区分。
- **F1.3 (P2)** 单次迭代内多个写操作工具并发 `gather` 时，guardrail CONFIRM 会各自 `await _confirm_cb` → 多个 `ask_user_prompt` 事件竞争同一 console 输入（仅交互模式、罕见）。可串行化写确认。

## 结论
Phase 1：1 个 P1（已修复并验证）+ 2 个 P2（backlog）。chokepoint 集成总体正确，最大风险（双引擎 handoff 漂移）已消除。
