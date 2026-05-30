# Roadmap 符合性矩阵（v14.6–v19）

日期：2026-05-30 · 基准：`sxw_aicoding/roadmap/iteration-roadmap-v14-v19.md`
状态：met=验收点有对应代码 · partial=代码在但验收待跑评测 · gap=缺口

| 版本 | 验收/评测要求（摘） | 状态 | 证据 / 行动 |
|---|---|---|---|
| v14.5 Task Resume | 4 路径恢复边界、HITL paused checkpoint、原子写 | met | checkpoint/store.py（.tmp+os.replace、版本检查）、orchestrator `_resume_*` |
| v14.6 Eval Harness | dry-run 离线无 LLM 导入、30+ benchmark、verifier、baseline gate、tag matrix | met（代码）| evaluation/{benchmark,verifiers,baseline,suites,variants,probe,compare_variants}.py；dry-run 离线已验证 |
| v15 Agentic Memory | factual/experiential/working + Memory as Tool + memory tag 评测 + memory_hit_rate | met（代码）/ partial（未跑）| memory/*、tools/memory_tools.py、`memory_agentic` suite、metrics memory_* |
| v16 MCP Bridge | stdio+HTTP、schema adapter、MCP server、4+ MCP tag 任务、tool_parameter_error 分布 | met（代码）| tools/mcp/*、`mcp_bridge` suite、schema_adapter metrics |
| v17 Self-Evolution | 经验/失败提炼写 experiential、可回滚、分类器校准（配置化建议）、偏好学习 | met（代码）| evolution/*、orchestrator `_learn_from_task`/`_apply_*hints`；calibration suggestion-only ✓ |
| v18.1 Workflow Engine | 确定性工具 DAG、与 agentic loop 显式区分 | met | workflow/*、`run_workflow()`、`--workflow` |
| v18.2 Handoff | 上下文传递 + 控制权转移、与 SubAgent 互补、ask_user 显式配置 | met（**评审修复后**）| handoff_tool/specialist；**F1.1 修复**：两引擎均生效控制权转移 |
| v18.3 Remote SubAgent | 经 MCP 调远端 agent、跨进程隔离、防递归 | met | remote_subagent_tool、MCP server agent 端点 `_REMOTE_BLOCKED`(#22)；**F4.1 修复**：本地 depth=1 屏蔽 remote |
| v18.4 A2A 原型 | AgentCard + task req/resp、本地可信 | met | a2a/*、server get_agent_card/a2a_run_task |
| v18.5 Multi-Agent Eval | 协作任务集、与 single-agent baseline 对比、delegation/handoff 成功率 | met（代码）/ partial（未跑）| `multi_agent` suite、`handoff_on` variant、delegation-aware score、handoff 指标 |
| v19.0 Threat Model | OWASP ASI 威胁矩阵 | met | sxw_aicoding/security/owasp-asi-threat-matrix.md |
| v19.1 Tool Guardrail | 危险参数/路径越权/写操作门控（联动 ask_user） | met | guardrails/tool_guardrail.py、`_guardrail_confirm` |
| v19.2 Input/Context Guardrail | indirect injection 中和 + memory poisoning | met（**评审修复后**）| input_guardrail；**F5.1 修复**：`scan_memory` 接入 `_gather_context`（此前死功能） |
| v19.3 Output Guardrail | PII/凭证脱敏、与 tracing redaction 一致 | met（**评审修复后**）| output_guardrail；**F2.1 修复**：resume 也脱敏（此前漏） |
| v19.4 Red-Team Benchmarks | AgentDojo 风格用例、attack_success_rate/blocked_benign_rate、接入 baseline/gate | met（代码）/ partial（未跑）| `red_team` suite、`guardrails_on` variant、security 指标 |

## 结论
- **代码层面 v14.6–v19 全部 met**；其中 4 个 met 状态是**评审修复后**才真正达成（F1.1/F2.1/F4.1/F5.1——均为"旁路/漂移/死功能"，单看代码"存在"但运行时不生效或不一致）。
- **partial 项**（v15/v16/v18.5/v19.4 评测）= 代码与指标齐备，仅差**实跑评测 + baseline 落盘**。无 unresolved gap。

## 实测验证更新（2026-05-30, focused 实跑，详见 eval/eval-report.md）
- **v15 Agentic Memory** → **实测通过**：write→recall `hit_count=1`，真实召回。
- **v16 / 百炼 MCP** → **实测通过**：web_search 走 `bailian_web_search`。
- **v19.1–19.3 Guardrails** → **实测通过**：护栏 ON 拦截真实 `/etc/passwd` exfil（21 blocks、内容未泄）、注入中和、benign 零误伤。
- **v19.4 Red-Team 指标** → ⚠ 部分失真：`attack_success_rate` 受 benchmark 验证器缺陷（F-eval-1：子串匹配文件路径名）影响，未反映护栏真实效果；护栏真实效果由 block_count + 答案内容证明。**建议修 F-eval-1 后指标才可信。**
- **v18.2 Handoff（reasoning 路径 F1.1）** → 单测验证；实跑未被模型触发（best-effort）。
- **v5/v8 Emergent**（roadmap 既有）→ ⚠ F-eval-2：DeepSeek 下 emergent TODO-init 返回 0 TODO，需专项排查（pre-existing）。
