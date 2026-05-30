# Phase 3–7 findings（合并：orchestrator / tools / state / 新模块 / 评测·观测）

日期：2026-05-30

## Phase 3 — Orchestrator 收口 & agent 路由 —— ✅ 无 P0/P1
- `_record_outcome` 覆盖全部 14 个路径返回点（simple/dag/emergent/goal + resume emergent/goal + 重置），self-evolution 成功信号穿透完整。✓
- 输出 guardrail 在 run()/run_workflow()/resume() 三入口均对 `final_answer` 套用 → **失败合成答案**(`_synthesize_failure_answer`)/best-effort/`无法完成` 字符串也经 redact（预判 #4 经核验**非 bug**）。✓
- HITL 双门控(#8)、classify 阈值外置(#18)读 config、`_guardrail_confirm` 非交互/超时 fail-safe(False)。✓
- 注：`TaskOutcome.trajectory` 向 learner 传未脱敏工具输出（learner 仅存摘要，低风险）→ P3 backlog。

## Phase 4 — Tools & 集成边界
### F4.1 — P1（已修）depth=1 屏蔽列表漏 `remote_subagent`
- SubAgent `_BLOCKED_TOOLS` 与 Specialist `_BLOCKED_SPECIALIST_TOOLS` 均缺 `remote_subagent`（二者早于 remote 工具编写，漂移）。depth=1 委派可被授予 remote_subagent → 再起远端 agent，违反 #9 隔离意图。
- 修复：两处屏蔽列表补 `"remote_subagent"`。验证通过。
- 已核验：`set_caller` 反并发(#3) 在 subagent/handoff/remote 齐备；MCP server `_REMOTE_BLOCKED`(#22) 正确；shell/python/file_ops 原生防护 + guardrail 分层为纵深叠加（无回归）。✓

## Phase 5 — 状态 & 数据子系统
### F5.1 — P1（已修）`scan_memory` 死功能（记忆投毒检测未接线）
- `GuardrailEngine.scan_memory` / `InputGuardrail.scan_memory` 已定义但**无任何调用**；`_gather_context` 注入 agentic/legacy 记忆时未扫描 → v19.2 记忆投毒防护(ASI06)为死代码（threat-matrix 却声称已覆盖）。
- 修复：新增 `_apply_memory_guardrail()`，在 `_gather_context` 注入前对 agentic + legacy 记忆文本各扫一次（neutralize）。验证：投毒文本→UNTRUSTED 包裹；干净文本不变；关闭→透传。
- 已核验：checkpoint 原子写 `.tmp`+`os.replace`(#13) + 版本检查(#12)；context `_find_safe_split` 不切 tool_calls 组(#5)；memory store/migration/revoke 稳定。✓

## Phase 6 — 新能力模块 evolution / workflow
### F6.1 — P2（backlog）workflow 工具步骤绕过 guardrail chokepoint
- `workflow/engine.py:86` 直接 `tool.traced_execute`，**不经** `execute_tool_calls` → 工具级 guardrail(BLOCK/NEUTRALIZE) **不作用于 workflow 步骤**；`run_workflow` 虽 wire 了 sink 但 engine 不触发 guardrail（sink 实际仅供输出脱敏用）。
- 评估：workflow 为**确定性、作者显式编排**（非 LLM 驱动攻击面），且**输出仍脱敏**(line 483)；`${step_id}` 把前序（可能不可信）输出注入后续工具参数是小众注入向量。判 **P2 边界**，不改码；在 threat-matrix 标注"workflow 工具步骤不受工具级护栏门控、仅输出脱敏"。
- 已核验：evolution learner dedup/confidence cap/LLM 回退/偏好 done-callback；calibration **suggestion-only 不变量成立**（仅读 config，`write_suggestion` 原子写建议文件，无 `setattr(config)`）。✓

## Phase 7 — 评测 & 可观测 + roadmap 验收审计 —— ✅ 无 P0/P1
- probe 覆盖全部新事件：handoff_*、remote_subagent_*、guardrail_*（blocks/neutralized/redactions）。✓
- runner tag 激活恢复对称（10 个 original_* 含 handoff/handoff_ask_user）。✓
- 指标数学（aggregate 按 is_attack 分区、`_success_score` 复用、delegation/guardrail 计数）经 Phase v18.5/v19.4 冒烟验证。✓
- tracing：脱敏 `SENSITIVE_KEYS` 与 guardrail 同源理念；OTel detach(#6)/span lifecycle(#7) 稳定。✓
- 详见 `roadmap-conformance-matrix.md`。
