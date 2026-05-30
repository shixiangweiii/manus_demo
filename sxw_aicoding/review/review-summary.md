# 二次代码评审 — 终版汇总

日期：2026-05-30 · 范围：全源码（105 .py）对照 roadmap v14.6–v19 · 侧重：安全·正确性优先 · 产出：报告 + P0/P1 即修

## 总览
- 阶段 0–8 全部完成。
- **P0：0**；**P1：4（全部已修并验证）**；P2：4（backlog）；P3：4（backlog）。
- 4 个 P1 共性：v17–v19 连续多轮快速实现引入的**"旁路 / 双循环漂移 / 死功能 / 路径不一致"**——代码"看起来都在"，但运行时被绕过或不一致。正是二次评审的价值所在。

## 已修复的 P1
| ID | 问题 | 修复 |
|---|---|---|
| **F1.1** | handoff 控制权转移(#20)在 `ReasoningEngine` 被绕过（`handoff_on` 评测变体同开 reasoning+handoff，恰中） | 抽 `ReActEngine._check_handoff_transfer` 共享方法，两引擎均调用 |
| **F2.1** | `resume()` 漏接 guardrail + 不脱敏输出（ASI05 执行不一致：同任务 run 脱敏、resume 泄露） | resume 补 wire/redact/unwire(finally) + 对称 reset |
| **F4.1** | depth=1 屏蔽列表漏 `remote_subagent`（SubAgent/Specialist 可再起远端 agent，违反 #9） | 两屏蔽列表补 `remote_subagent` |
| **F5.1** | `scan_memory` 死功能（记忆投毒检测 ASI06 从未被调用） | 新增 `_apply_memory_guardrail`，接入 `_gather_context` |

## Backlog（P2/P3，未改）
- F1.2 (P2) guardrail BLOCK 计入 ToolRouter 失败桶（策略拦截≠工具故障）。
- F1.3 (P2) 单迭代多写操作并发 CONFIRM 竞争 console 输入。
- F2.2 (P2) OutputGuardrail 信用卡/email 正则过度脱敏（精度）。
- F6.1 (P2) workflow 工具步骤绕过 guardrail chokepoint（确定性引擎边界；输出仍脱敏）。
- F0.1 (P3) memory/service.py:125 陈旧 TODO（MEMORY_LLM_CONSOLIDATION_ENABLED 未接线）。
- F2.3 (P3) 注入正则可混淆绕过（规则版已知边界）。
- F2.4 (P3) 父层不扫子代理/专家输出（防御在其工具层）。
- F3-note (P3) TaskOutcome.trajectory 向 learner 传未脱敏工具输出（learner 仅存摘要）。

## 健康度（正面确认）
- **配置无漂移**：105 文件扫描，除已修 main.py 外无 `import config` 类缺陷。
- **特性开关默认安全**：全部新能力主开关默认关；关闭路径零副作用（已验证）。
- **关键不变量 #1–#25**：逐项核验通过（#20 经 F1.1 修复后成立）。
- **编译**：`compileall` 全包 0 错误。
- **roadmap 符合性**：v14.6–v19 代码层全部 met（4 项经评审修复后达成）；评测类为 partial（待实跑）。详见 `roadmap-conformance-matrix.md`。

## 回归验证
- 每个 P1 修复后定向冒烟通过（handoff 双引擎转移 / resume 含 redact / 两屏蔽列表含 remote / scan_memory 中和）。
- 终验：`compileall` 全绿；默认关零副作用复测通过；修复文件 config 导入齐全。

## 后续建议（非本评审范围）
1. 实跑评测 + baseline 落盘（验证 partial → met 的实测收益，尤其 v18.5/v19.4 A/B）。
2. 补单元测试（guardrails / evolution / workflow / handoff / a2a / 三引擎 handoff 一致性回归）。
3. 清理 P2/P3 backlog（择优）。
