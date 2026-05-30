# Phase 2 — Guardrails 子系统 findings

日期：2026-05-30 · 文件：`guardrails/{patterns,models,tool_guardrail,input_guardrail,output_guardrail,engine}.py` + orchestrator 接线

## Findings

### F2.1 — P1（已修）resume() 漏接 guardrail + 不脱敏输出
- **现象**：`run()` / `run_workflow()` 都 `_wire_guardrail_runtime()` + 返回前 `_apply_output_guardrail()`，但 `resume()`（orchestrator.py:1364）两者都没有。
- **影响**：① 输出脱敏（ASI05）在恢复任务上**失效**——同一任务经 `run()` 会脱敏 PII/凭证，经 `resume()` 则原样泄露，护栏执行不一致（安全漏洞）。② guardrail 事件在 resume 期间无 sink → UI/probe/tracing 看不到。③ 写-CONFIRM 在 resume 退化为 block。（工具级 BLOCK/NEUTRALIZE 仍生效，因 `current_guardrail()` 由 config 直接门控，与接线无关。）
- **修复**：resume() 在 task_start 后 `_wire_guardrail_runtime()`；把收尾移入 try 并加 `_apply_output_guardrail` + `finally: _unwire_guardrail_runtime()`。顺带补 handoff/remote 的 `reset_task_state()`（与 run() 对称）。
- **验证**：`py_compile` + 反射断言 resume 含 wire/redact/unwire + ≥2 reset。

## 已核验正确（无问题）
- **路径穿越** `tool_guardrail._within_sandbox`：`realpath` 解析符号链接，边界判 `target == sandbox or target.startswith(sandbox+os.sep)`，防 `../` 与 `/sandbox_evil` 前缀攻击。✓
- **CONFIRM 裁决 / observe 降级**：engine `check_tool_input` 按 `GUARDRAIL_WRITE_CONFIRM`(block/confirm/allow) + 无回调 fail-safe block；observe 模式 BLOCK→ALLOW+事件。✓（Phase v19 已冒烟）
- **sink/confirm 跨实例泄漏**：run/workflow/resume 三入口现均 wire + `finally` unwire → 运行结束清空，无残留指向死 orchestrator。父 run 内的 SubAgent/Specialist 经模块 sink 复用父 `_emit`（归因正确）。✓
- **NEUTRALIZE**：不可信工具(web_search/fetch_url/mcp_*/remote_subagent)注入命中 → 剥离指令行 + UNTRUSTED 边界包裹；空内容回退占位。✓
- **current_guardrail() 实时 config**：每次新建轻量 engine 读实时 flag；patterns 模块级常量不重复编译。✓

## Backlog（P2/P3）
- **F2.2 (P2)** OutputGuardrail 过度脱敏精度：信用卡正则 `\b(?:\d[ -]?){13,16}\b` 会命中任意 13–16 位数字串（时间戳/哈希/计算结果）；email 正则脱敏一切邮箱。opt-in 且不破坏正确性，但 redact 模式下可能污染正常答案。建议收紧（卡号分组校验 / email 仅 observe）。
- **F2.3 (P3)** 注入检测为正则版，可被混淆绕过（大小写/分词/编码/base64）。threat-matrix 已声明"规则版、LLM 检测后置"，属已知边界。
- **F2.4 (P3)** 父层不对 subagent/handoff 专家输出做注入扫描——防御在其自身工具层（fetch/search 结果已中和），可接受。

## 结论
Phase 2：1 个 P1（resume 护栏漏洞，已修）+ 3 项 backlog。护栏核心机制（路径/裁决/生命周期）正确，主要遗漏是 resume 路径的执行一致性，已补齐。
