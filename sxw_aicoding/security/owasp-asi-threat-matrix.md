# OWASP Agentic (ASI) Threat Matrix — manus_demo (v19.0)

> 生成日期：2026-05-30 · 适用：v19 Guardrails · taxonomy：OWASP Agentic Applications Top 10 (ASI01–ASI10)

本矩阵明确 manus_demo 的安全暴露面、对应 guardrail 层、覆盖状态。**护栏默认关**（`GUARDRAILS_ENABLED=false`），开启后分层可配（block / neutralize / redact / observe）。这是一个个人学习 demo 的**最小可用**安全体系，不宣称完整覆盖。

| ASI | 风险 | 本项目暴露面 | 防护（v19 层） | 状态 |
|---|---|---|---|---|
| ASI01 | Prompt Injection（直接/间接） | `web_search`/`fetch_url`/`mcp_*`/`remote_subagent` 返回内容、检索到的记忆中携带注入指令 | 19.2 InputGuardrail：不可信输出包裹 UNTRUSTED 边界 + 剥离注入指令行；记忆同款扫描 | **covered（规则版）** |
| ASI02 | Tool Misuse / 危险工具调用 | `execute_shell`（反弹 shell、`curl\|sh`、凭证读取）、`execute_python`（`os.system`/`subprocess`/`socket`/`eval`） | 19.1 ToolGuardrail（叠加 ShellTool 黑名单纵深）+ block/observe 可配 | **covered** |
| ASI03 | 越权 / 沙箱逃逸 | `file_ops` 路径 `../` 穿越出 `SANDBOX_DIR` | 19.1 路径规范化校验，越界 → BLOCK | **covered** |
| ASI04 | 资源耗尽 / 失控循环 | ReAct 迭代、SubAgent token 预算、handoff/remote 限次 | 既有 `MAX_REACT_ITERATIONS` / SubAgent 熔断 / 调用限次（非 v19 新增） | partial（既有） |
| ASI05 | 敏感数据泄露（凭证/PII） | 最终答案或工具结果含 `sk-…`/`AKIA…`/私钥/`/etc/passwd`/email/信用卡 | 19.3 OutputGuardrail：redact（复用 `tracing.SENSITIVE_KEYS`）；19.1 通用 exfil 标记拦截 | **covered（规则版）** |
| ASI06 | 记忆投毒（Memory Poisoning） | v15 Agentic Memory 写入/检索被注入污染 | 19.2 `scan_memory` 注入检测 + v15 `revoke` 回滚（互补） | partial |
| ASI07 | 写操作 / 副作用未受控 | `file_ops write`、shell 写 | 19.1 写操作门控 `block\|confirm\|allow`，confirm 经 HITL ask_user | **covered** |
| ASI08 | 不安全的输出处理 | 下游消费 agent 输出 | 19.3 输出脱敏（部分） | partial |
| ASI09 | 供应链 / 远端 agent 信任 | v18.3 Remote SubAgent / MCP server | A2A `auth="local"`、远端工具集剔除递归工具；本地可信假设 | partial（本地可信） |
| ASI10 | 可观测性缺失 | 安全事件不可见 | guardrail 事件经 `_emit` 多播 → UI/Tracing/EvaluationProbe | covered |

## 不在 v19 范围（明确 out-of-scope）

- 开放网络的 agent 发现 / 跨信任域 A2A 鉴权。
- 多租户隔离、网络出站策略（egress firewall）、密钥保管库集成。
- LLM-based guardrail 分类器（v19 用确定性规则/正则；LLM 检测后置）。
- 形式化策略引擎 / 完整 OWASP ASI 合规。

## 验收（v19.4 Red-Team 评测脚手架已落地，待运行验收）

`evaluation`：`red_team` suite（safety_001–004 + redteam_* 攻击 + 2 benign 控制）+ `guardrails_on` 变体 vs `react_auto_baseline`，报告 `attack_success_rate`、`blocked_benign_rate`、`guardrail_block_count`，与正常任务成功率并列（避免安全打穿可用性）。指标/探针/suite/variant 已实现（A/B 经 matrix 变体控制 guardrails on/off）；**实际运行 + baseline 落盘待用户整体评测验收。没有基准不宣称安全完成。**
