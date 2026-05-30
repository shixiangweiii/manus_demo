# 二次代码评审 — 进度跟踪 (review-tracker)

> 计划：`sxw_aicoding/plan/`（mellow-questing-pike 同步）· 起始 2026-05-30
> 侧重：安全·正确性优先 · 产出：报告 + P0/P1 即修，P2/P3 入 backlog · 顺序：风险分层

## 严重度图例
- **P0** 正确性 bug / 安全漏洞 / 数据丢失 / 开关打开即坏 → 当轮修
- **P1** roadmap 验收缺口 / 潜在 bug / 并发竞态 / 向后兼容破坏 / 违反 CLAUDE.md #1–#25 → 当轮修
- **P2** 可维护性 / DRY / 一致性 / 性能 → backlog
- **P3** 文档漂移 / 命名 / nit → backlog

## 阶段进度
| Phase | 区域 | 状态 |
|---|---|---|
| 0 | 脚手架 + 全局横切扫描 | ✅ done |
| 1 | Chokepoint & 共享 ReAct 引擎 | ✅ done (1×P1 fixed) |
| 2 | Guardrails 子系统 | ✅ done (1×P1 fixed) |
| 3 | Orchestrator 收口 & agent 路由 | ✅ done (clean) |
| 4 | Tools & 集成边界 | ✅ done (1×P1 fixed) |
| 5 | 状态 & 数据子系统 | ✅ done (1×P1 fixed) |
| 6 | 新能力模块 evolution/workflow | ✅ done (1×P2) |
| 7 | 评测 & 可观测 + roadmap 验收审计 | ✅ done (clean) |
| 8 | 汇总 / backlog / 符合性报告 | ✅ done |

**评审完成**：P0=0 · P1=4(全修) · P2=4 · P3=4。详见 review-summary.md + roadmap-conformance-matrix.md。

**实跑评测完成（2026-05-30, 真实 DeepSeek+百炼, ~22 task-run）**：v15 记忆 / v16 百炼 MCP / v19 护栏**实测通过**（护栏 ON 拦截真实 /etc/passwd exfil：21 blocks、内容未泄、benign 零误伤）。详见 `eval/eval-report.md`。新增 findings：
| F-eval-1 | P2→P1 | benchmark + evaluation/probe.py | red-team 指标失真：① 验证器匹配文件路径名（拒绝答复也命中）② **probe 把攻击任务的"无法完成"拒绝当失败 → 误判 attack 成功**（语义颠倒）。修：验证器改内容标记 + probe `is_attack` 任务 task_success=已防御(未泄露) | **fixed** |
| F-eval-2 | P2 | agents/emergent_planner.py | emergent TODO-init 可解析但空/异形 → 静默 0 TODO 空转。修：形状容错抽取 + 空→重试→单 TODO 兜底 | **fixed**（实跑 0→2 TODO） |
| F-eval-3 | P3 | benchmark easy_002 | 验证器拒绝逗号数字 `3,628,800`。修：regex 容许千分位 | **fixed** |
| F-eval-5 | P2 | guardrails/output_guardrail.py | OutputGuardrail passwd 正则 `root:[^:]*:0:0:` 需尾冒号，漏部分 passwd 格式（表格/无尾冒号） | backlog |
| F-eval-4 | P3 | bailian web_parser | 偶发 ExceptionGroup（优雅降级） | backlog |

## Findings 索引
| ID | 级别 | 文件:行 | 摘要 | 状态 |
|---|---|---|---|---|
| F0.1 | P3 | memory/service.py:125 | 陈旧 TODO 引用 v16；`MEMORY_LLM_CONSOLIDATION_ENABLED` 已定义但未接线（LLM 辅助巩固未实现） | backlog |
| F1.1 | P1 | react/reasoning_engine.py | handoff 控制权转移(#20)在 ReasoningEngine 被绕过（handoff_on 变体触发）；抽 `_check_handoff_transfer` 共享 | **fixed** |
| F1.2 | P2 | react/engine_helpers.py | guardrail BLOCK 结果计入 ToolRouter 失败桶（策略拦截≠工具故障） | backlog |
| F1.3 | P2 | guardrails/engine.py | 单迭代多写操作并发 CONFIRM 竞争 console 输入（交互/罕见） | backlog |
| F2.1 | P1 | agents/orchestrator.py resume() | resume 漏接 guardrail wiring + 不脱敏输出（ASI05 执行不一致）；补 wire/redact/unwire + 对称 reset | **fixed** |
| F2.2 | P2 | guardrails/patterns.py | OutputGuardrail 信用卡/email 正则过度脱敏（精度） | backlog |
| F2.3 | P3 | guardrails/patterns.py | 注入正则可被混淆绕过（已知边界，LLM 检测后置） | backlog |
| F2.4 | P3 | guardrails/input_guardrail.py | 父层不扫 subagent/handoff 专家输出（防御在其工具层，可接受） | backlog |
| F4.1 | P1 | tools/subagent_tool.py + agents/specialist.py | depth=1 屏蔽列表漏 `remote_subagent`（#9 隔离不全）；两处补 | **fixed** |
| F5.1 | P1 | agents/orchestrator.py `_gather_context` | `scan_memory` 死功能（ASI06 投毒检测未接线）；新增 `_apply_memory_guardrail` 接入 | **fixed** |
| F6.1 | P2 | workflow/engine.py | workflow 工具步骤绕过 guardrail chokepoint（确定性引擎边界；输出仍脱敏） | backlog |
| F3.1 | P3 | agents/orchestrator.py | TaskOutcome.trajectory 向 learner 传未脱敏工具输出（仅存摘要，低风险） | backlog |

## CLAUDE.md Critical Notes (#1–#25) → 验证归属
| # | 不变量 | 验证阶段 |
|---|---|---|
| 1 `_current_log` rebind | Phase 1 |
| 2 ReActEngine lazy import | Phase 1 |
| 3 SubAgentTool set_caller 本地捕获 | Phase 4 |
| 4 ToolRouter 三态 | Phase 1 |
| 5 context safe-split | Phase 5 |
| 6 OTel detach | Phase 7 |
| 7 LLM span lifecycle | Phase 7 |
| 8 HITL 双门控 | Phase 3 |
| 9 SubAgent depth=1 | Phase 4 |
| 10 caller_tag named kwarg | Phase 1/4 |
| 11 DAG dataflow | Phase 5 |
| 12 checkpoint resume 边界 | Phase 5 |
| 13 checkpoint 原子写 | Phase 5 |
| 14 emergent/goal loop 抽取 | Phase 3/5 |
| 15 MCP lazy import | Phase 4 |
| 16 MCPBridgeTool eager schema | Phase 4 |
| 17 v17 outcome snapshot | Phase 3 |
| 18 v17.3 阈值外置 | Phase 3 |
| 19 v17.4 HITL 偏好捕获 | Phase 3 |
| 20 v18.2 handoff 控制权转移 | Phase 1 |
| 21 v18.1 workflow vs agent | Phase 6 |
| 22 v18.3 remote 防递归 | Phase 4 |
| 23 v18.4 A2A 信封 | Phase 4 |
| 24 v19 guardrail chokepoint | Phase 1/2 |
| 25 v19 注入边界 + 写确认 | Phase 2/3 |
