# Manus Demo 后续迭代路线图 (v14 ~ v19)

> **生成日期**: 2026-05-21 初版；2026-05-24 v6 调整
> **当前状态**: v14 Phase 4 fix pass 已完成；v14.5 Task Resume 已实施；下一阶段上调 v14.6 Evaluation Harness 为 P0
> **数据来源**: 最新源码复核 + v14.5 实施结果 + 当前 evaluation 模块实现 + 既有论文/官方资料复查
> **定位**: 个人学习 agent 架构设计与工程落地的自学 demo；后续所有架构实验优先通过可复现评测来验证收益。

---

## 一、当前代码事实与路线图调整

截至 2026-05-24，当前主线代码和 roadmap 的关键事实如下：

- v14 Phase 4 已完成 reasoning_effort 端到端接线、ReAct-style 工具执行 helper 抽取、ReasoningEngine/Harness 修复和定向回归。
- v14.5 Task Resume 已完成第一版实施：`TaskCheckpoint` / `TaskStateStore`、`OrchestratorAgent.resume(task_id)`、正常完成/失败 checkpoint 闭环、simple/DAG/emergent/goal-driven 路径恢复边界、HITL paused checkpoint metadata。
- 当前 evaluation 已有较完整骨架：`evaluation/benchmark.py` 定义 18 个任务；`EvaluationProbe` 通过事件流采集 planning/execution/efficiency/reflection 指标；`EvaluationRunner` 支持 forced mode、HITL simulated user、SubAgent/GoalDriven tag 激活、pass@k、LLM-as-Judge fallback、JSON/Rich 报告。
- 当前 benchmark 分布为 18 个任务：easy 7、medium 4、hard 7；覆盖 search/code/file_ops/shell/HITL/SubAgent/GoalDriven，但仍不足以支撑后续 memory、自演化、multi-agent、guardrails 的统计判断。
- 当前评测测试存在工程债：在裸 `python3` 环境下，`tests/test_evaluation.py` collection 会因为 `evaluation.runner -> OrchestratorAgent -> LLMClient` 直接导入 `openai` 而失败；`eval_cli --dry-run` 也因为 CLI 顶层导入 `rich` / `EvaluationRunner`，无法作为真正轻量的任务清单命令使用。说明 evaluation 的模型/指标/任务清单能力和运行时依赖还没有解耦。

因此本版路线图做两项调整：

> **v14.5 从“待实施”更新为“已实施，进入评测回归验证”。**

> **新增 v14.6 Evaluation Harness / Benchmark Expansion，优先级 P0，作为 v15+ 架构实验之前的下一阶段主线。**

这个调整的核心判断是：后续 Memory、Self-Evolution、Multi-Agent、Guardrails 都必须依赖可信评测，否则只能靠主观演示判断“变好了没有”。

---

## 二、版本号与已交付能力

| 已交付版本 | 内容 | 当前判断 |
|---|---|---|
| v7 | OpenTelemetry tracing + web viewer | 已交付，后续与 evaluation / guardrails 做关联分析 |
| v9 | SubAgent depth=1 隔离委派 | 已交付，后续由 v14.6 先补协作收益评测，再进入 v18 扩展 |
| v10 | DDGS 联网搜索 | 已交付，当前测试中已加入离线隔离 |
| v11 | Bailian MCP WebSearch / WebParser 出站集成 | 已交付，但不是通用 MCP Client |
| v12 | ReActEngine 统一化、context 注入、工具结果截断 | 已交付；文档中不再补 v12 路线 |
| v13 | HITL ask_user | 已交付，v14.6 先补 scripted user 评测，v19 再做 guardrail 联动 |
| v14 | Reasoning Model + Harness fixes | Phase 4 fix pass 已完成，Interleaved Thinking 暂缓 |
| v14.5 | Long-Horizon Reliability / Task Resume | 已完成第一版实施，后续由 v14.6 补恢复可靠性评测 |

---

## 三、最新资料复查后的关键方向

| 方向 | 可信来源 | 对本项目的影响 |
|---|---|---|
| Agent 研发必须先有可复现评测闭环 | 当前 `evaluation/` 实现 + AgentEval / SWE-bench / TauBench / LLM-as-Judge 思路 | v14.6 上调为 P0；先让评测可离线、可扩样、可对比，再继续架构扩展 |
| Agent memory 应按 Forms x Functions x Dynamics 建模 | [Memory in the Age of AI Agents](https://arxiv.org/abs/2512.13564) | v15 不再沿用简单 long/short memory 二分；拆成 factual / experiential / working |
| 动态关联记忆适合 agent 经验沉淀 | [A-MEM](https://arxiv.org/abs/2502.12110) | v15 可借鉴 Zettelkasten 风格关联，但第一版仍保持轻量实现 |
| 长任务需要 checkpoint + resume + HITL persistence | [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[LangGraph HITL interrupts](https://docs.langchain.com/oss/python/langgraph/human-in-the-loop) | v14.5 已完成最小闭环；v14.6 应补恢复可靠性任务集 |
| MCP 已形成 stdio + Streamable HTTP 双传输范式 | [MCP overview](https://modelcontextprotocol.io/specification/2025-11-25/basic)、[MCP transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) | v16 应从 Bailian 单点包装升级为通用 MCP bridge |
| Agent SDK 主流抽象是 Agent / Handoff / Guardrail | [OpenAI Agents SDK](https://platform.openai.com/docs/guides/agents-sdk/)、[Guardrails](https://openai.github.io/openai-agents-python/guardrails/) | v18 / v19 分别对齐 handoff 和 guardrail，但先由 v14.6 建评测门槛 |
| Prompt injection / tool-returned untrusted data 是 agent 安全主风险 | [AgentDojo NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html) | v19 的安全评测应覆盖工具返回内容中的间接注入；v14.6 先预留 red-team seed suite |
| Self-evolving agents 需要清楚限定演化对象与反馈闭环 | [Self-Evolving AI Agents Survey](https://arxiv.org/abs/2508.07407)、[Reflexion](https://papers.nips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html) | v17 只做 prompt / memory / tool policy 层面的低风险 Reflexion，并依赖 v14.6 评测样本 |

---

## 四、v14 - Reasoning Model + Harness Fixes（P0，已完成主线）

### 已完成

| 子项 | 状态 | 说明 |
|---|---|---|
| Reasoning token 分桶 | 完成 | `LLMCallRecord` / `TokenUsage` 已支持 `reasoning_tokens` |
| ReasoningEngine | 完成 | thinking 不计普通 ReAct iteration，支持 thinking budget |
| thinking-aware context | 完成 | ContextManager 估算和 safe split 已感知 `thinking_content` |
| Harness 配置层初步接入 | 完成 | REACT / REASONING / PLANNER / REFLECTOR temperature 等配置已接入 |
| reasoning_effort 端到端流转 | 完成 | simple / DAG / emergent / goal-driven 均已接线 |
| 工具执行 DRY helper | 完成 | `execute_tool_calls()` 已覆盖主要 ReAct-style loop |
| 测试隔离 | 基本完成 | 普通离线测试通过；DDGS integration 语义需进一步收窄 |

### 暂缓 / 后置

| 子项 | 优先级 | 调整 |
|---|---|---|
| Interleaved Thinking | P2 | 范式级改造，先不进入下一阶段；等 v14.6 有更可信长任务评测后再判断收益 |
| ToolExecutionPolicy 配置语义文档 | P2 | 可在 v14.6 报告中顺手补充，避免 high effort 截断语义不清 |
| DDGS fixture 收窄 | P2 | 纳入 v14.6 evaluation dependency hygiene |
| pytest warning 清理 | P3 | 纳入 v14.6 test hygiene，不单独开阶段 |

---

## 五、v14.5 - Long-Horizon Reliability / Task Resume（P0，已实施）

### 已完成能力

| 子项 | 状态 | 说明 |
|---|---|---|
| `TaskCheckpoint` / path state models | 已实施 | simple、DAG、emergent、goal-driven 均有路径状态模型 |
| `TaskStateStore` | 已实施 | 本地 JSON checkpoint，支持 save/load/list/mark_completed/mark_failed/delete |
| `OrchestratorAgent.resume(task_id)` | 已实施 | 支持从最新 checkpoint 分发到路径级恢复 |
| 正常完成闭环 | 已实施 | 普通 `run()` 成功后标记 `COMPLETED`，异常后标记 `FAILED` |
| Store 注入 | 已实施 | Orchestrator 可注入 `TaskStateStore`，测试和运行时不再错读不同目录 |
| Emergent / GoalDriven checkpoint | 已实施 | TODO 边界通过 `on_checkpoint` 回调保存路径状态 |
| HITL paused checkpoint | 已实施 | `ask_user_prompt` 前保存 `PAUSED_WAITING_USER` 与 prompt metadata |
| 最小幂等边界 | 已实施 | 以已完成 step/node/todo 作为不重跑边界，失败/运行中状态允许重试 |

### 剩余工作交给 v14.6 验证

- 补 task resume 的 benchmark scenario：中断、resume、HITL paused、TODO completed 不重跑、DAG super-step 后恢复。
- 把 resume 结果纳入 pass@k / reliability 报告，而不是只靠 `tests/test_task_resume.py`。
- 在 evaluation report 中显示 checkpoint count、resume count、paused count、resume_success_rate。

### 当前验证记录

- `tests/test_task_resume.py` 在当前裸 `python3` 环境中通过：`30 passed, 7 skipped`。
- py_compile 覆盖 `schema.py`、`checkpoint/models.py`、`checkpoint/store.py`、`agents/orchestrator.py`、`agents/emergent_planner.py`、`agents/goal_driven_planner.py`。
- 大范围测试在当前环境受缺失依赖影响：`pytest-asyncio`、`openai`、`mcp`、`opentelemetry` 未安装时会失败；这正是 v14.6 需要先处理的 evaluation dependency hygiene。

---

## 六、v14.6 - Evaluation Harness / Benchmark Expansion（P0，下一阶段，1-2 周）

### 目标

把 evaluation 从“能跑一组 benchmark 的辅助模块”升级为后续架构实验的质量闸门。v15 Memory、v17 Self-Evolution、v18 Multi-Agent、v19 Guardrails 只有在 v14.6 建立 baseline、扩样、离线可测和结果对比后才继续推进。

### 当前 evaluation 现状

| 模块 | 已有能力 | 主要短板 |
|---|---|---|
| `evaluation/benchmark.py` | 18 个任务，覆盖 easy/medium/hard、HITL、SubAgent、GoalDriven | 样本仍偏少；缺 resume、memory、MCP、guardrail、安全注入、文件结果 verifier |
| `evaluation/metrics.py` | planning/execution/efficiency/reflection 评分，支持 subagent/hitl/goal/pass@k/judge 聚合 | 权重仍是手写启发式；缺 baseline threshold / regression gate |
| `evaluation/runner.py` | 事件探针、forced mode、feature tag、SimulatedUser、LLM judge fallback | runner import 直接依赖 Orchestrator/LLM，可导致单测环境无 `openai` 时无法 collection |
| `evaluation/eval_cli.py` | dry-run、模式/难度/任务筛选、repeat/pass@k 参数、JSON 输出入口 | 顶层导入 `rich` 和 `EvaluationRunner`；dry-run 本应只依赖 benchmark，但当前会被展示依赖或运行时依赖阻断 |
| `evaluation/report.py` | Rich 对比表 + JSON export | 缺 baseline diff、失败样本 drilldown、按 feature/tag 的矩阵报告 |
| `tests/test_evaluation.py` | 指标/benchmark/probe/report 的 mock 测试 | 当前裸环境 collection 被 runtime optional deps 阻断 |

### 分阶段设计

| 阶段 | 优先级 | 内容 | 产出 |
|---|---|---|---|
| v14.6.1 Eval Core 解耦 | P0 | 将 metrics/benchmark/probe/dry-run CLI 与 Orchestrator runtime import 解耦；`tests/test_evaluation.py` 在无 `openai/mcp/opentelemetry` 环境下可 collection | evaluation 单测离线可跑，dry-run 不触发 LLM runtime import |
| v14.6.2 Benchmark Dataset v2 | P0 | 从 18 扩到 30-36 个任务；新增 resume、memory seed、DAG condition/rollback、web+fetch、HITL、多工具文件任务 | `--dry-run` 显示 30+ 任务，tag 分布可见 |
| v14.6.3 Outcome Verifier v1 | P0 | 为任务增加可选 deterministic verifier：文件存在/内容、JSON 字段、数值范围、regex/fuzzy、关键词；LLM judge 仅作 fallback | 减少 keyword-only 假阴性/假阳性 |
| v14.6.4 Baseline & Regression Gate | P0 | 增加 baseline JSON、baseline compare、阈值 gate；报告 success_delta/token_delta/time_delta/failure_delta | 每次架构改动能和上一版比较 |
| v14.6.5 Reliability Metrics | P1 | 把 pass@k、resume_success_rate、checkpoint_count、HITL completion、SubAgent success/cost 纳入报告 | 长任务可靠性不再只靠单次成功率 |
| v14.6.6 Safety Seed Suite | P1 | 加入少量 prompt injection / tool-output injection / shell safety seed case，不宣称完整安全覆盖 | 为 v19 red-team benchmark 铺路 |

### 任务集扩样建议

| 类别 | 目标数量 | 示例 |
|---|---:|---|
| 基础工具单步 | 6-8 | search、fetch_url、code、file_ops、shell、user_location |
| 多步依赖 | 6-8 | 搜索后计算、生成文件后读取、代码生成后测试 |
| DAG 并行/条件/回滚 | 4-5 | 可并行调研、条件边失败、rollback 节点触发 |
| Emergent / GoalDriven | 4-5 | 目标终止、迭代优化、停滞检测 |
| HITL | 3-4 | 缺位置、缺偏好、缺输出格式，SimulatedUser 自动回答 |
| SubAgent | 3-4 | 多主题独立调研、代码库结构扫描 |
| Resume Reliability | 3-4 | simple step 后中断、DAG super-step 后中断、TODO 完成后中断、HITL paused |
| Safety Seed | 3-4 | 工具返回注入、禁止 shell 参数、memory poisoning seed |

### 接口与文件建议

```text
evaluation/
├── benchmark.py          # 任务定义，新增 verifier 字段
├── verifiers.py          # Deterministic verifier registry
├── probe.py              # 从 runner.py 抽出 EvaluationProbe，避免导入 Orchestrator
├── runner.py             # 只保留运行编排，懒加载 Orchestrator/LLM/tools
├── baseline.py           # baseline load/save/compare/gate
├── report.py             # 增加 baseline diff + tag matrix
└── baselines/
    └── v14_6_initial.json
```

### 验收标准

- `python3 -m pytest tests/test_evaluation.py -q -o asyncio_mode=auto` 在无 API key、无 OpenAI SDK 的环境下也能 collection 并通过纯单元部分。
- `python3 -m evaluation.eval_cli --dry-run` 能列出 30+ benchmark，并显示 difficulty/tag/expected mode/verifier 类型；该命令不应导入 Orchestrator/LLM runtime，也不应要求 API key。
- `python3 -m evaluation.eval_cli --tasks easy_002 --modes simple --output /tmp/eval.json` 可跑最小 smoke；若无 API key，应给出清楚错误，不应 import-time 崩溃。
- 新增 baseline compare 命令或参数，例如 `--baseline evaluation/baselines/v14_6_initial.json --fail-on-regression`。
- 报告新增 tag 维度聚合：HITL、SubAgent、GoalDriven、Resume、Safety seed。
- 后续 v15/v17/v18/v19 的 roadmap 验收必须引用 v14.6 baseline，而不是只写“功能完成”。

---

## 七、v15 - Agentic Memory 重构（P0，4-6 周，依赖 v14.6）

### 目标

把当前 `LongTermMemory` 的关键词重叠检索升级为一套轻量 agentic memory。采用 Forms x Functions x Dynamics 作为设计框架，但第一版只做 token-level memory，不做参数记忆。

### 分阶段设计

| 阶段 | 内容 | 说明 |
|---|---|---|
| v15.1 Memory Schema | factual / experiential / working 三类记忆模型 | 先用本地 JSON / SQLite，可选 pgvector 后置 |
| v15.2 Retrieval API | keyword + embedding-ready 双层接口 | 保持当前 keyword fallback，预留向量检索接口 |
| v15.3 Memory as Tool | `memory_search` / `memory_store` / `memory_consolidate` | 让 agent 主动管理记忆 |
| v15.4 Consolidation | STM 到 experiential memory 的任务后巩固 | 与 checkpoint 区分：checkpoint 是恢复，memory 是学习 |
| v15.5 Skill 轻量实验 | 把高频成功流程保存为 procedural note | 只做文本触发规则，不做自动代码生成 |

### v15 评测要求

- 新增 memory tag 任务不少于 6 个，包含首次任务写入、二次任务召回、错误记忆回滚、memory poisoning seed。
- 与 v14.6 baseline 对比时，必须报告 success_delta、token_delta、memory_hit_rate、memory_false_positive。
- 没有 evaluation 改善或明确学习价值时，不把 memory 重构视为完成。

---

## 八、v16 - MCP 全面适配（P1，2 周，可与 v15 后半段并行）

### 目标

把 Bailian 单点 MCP 包装升级为通用 MCP bridge，同时把本项目已有 BaseTool / Memory 能力暴露给外部 agent 工具链。

### 分阶段设计

| 阶段 | 内容 | 说明 |
|---|---|---|
| v16.1 Generic MCP Client | 支持 stdio + Streamable HTTP，多 server 注册 | 不替换 BaseTool，而是桥接为 `MCPBridgeTool` |
| v16.2 Schema Adapter | MCP tool schema 到 OpenAI function schema 转换 | 支持 outputSchema 校验，默认提供宽松模式 |
| v16.3 MCP Server | 暴露本项目工具、memory resources、prompt templates | 让 Claude Code / Codex / Cursor 等外部客户端可接入 |

### v16 评测要求

- 至少 4 个 MCP tag 任务：stdio mock server、Streamable HTTP mock server、schema mismatch、tool failure fallback。
- ToolRouter、tracing、caller_tag 能识别 MCP 工具来源。
- baseline 不能只看成功率，还要看 tool_parameter_error / schema_validation_error 分布。

---

## 九、v17 - Self-Evolution（P1，2-3 周，依赖 v14.6 + v15）

### 目标

让系统从任务结果中提炼经验，但只做低风险、可回滚的 Reflexion 风格 prompt / memory 注入，不做 RL、不更新模型参数、不自动生成危险工具。

### 分阶段设计

| 阶段 | 内容 | 说明 |
|---|---|---|
| v17.1 Experience Learner | 从成功/失败轨迹提取经验 | 写入 experiential memory |
| v17.2 Failure Reflection | 保存 `(task_type, failure_reason, correction)` | 下次类似任务注入避坑提示 |
| v17.3 Classifier Calibration | 基于 evaluation 结果调整复杂度阈值 | 只允许配置化调整，禁止静默自改代码 |
| v17.4 Preference Learning | 从 HITL 交互中提取用户偏好 | 输出格式、默认城市、代码风格等 |

### 前置要求

- v14.6 benchmark 已扩样到 30+，且 baseline compare 可用。
- v15 memory 具备来源、时间、task_id、可信度字段，便于回滚错误经验。
- 自演化只允许写 memory / prompt policy / config suggestion，不允许自动改源码。

---

## 十、v18 - Multi-Agent + 双引擎显式化（P2，3-4 周，依赖 v14.6 + v15 + v16）

### 目标

把当前 depth=1 SubAgent 扩展为更清晰的多 agent 架构，同时显式区分确定性 Workflow 与自主 Agentic Loop。

### 分阶段设计

| 阶段 | 内容 | 说明 |
|---|---|---|
| v18.1 Workflow Engine | 把 DAG 确定性执行显式化 | 面向可控、可回放任务 |
| v18.2 Handoff | 引入上下文传递式专业 agent 委派 | 与 SubAgent 的 summary-only 隔离式委派互补 |
| v18.3 Remote SubAgent | 通过 MCP 调用远端 agent server | 用于跨进程隔离和长任务稳定性 |
| v18.4 A2A Prototype | Agent Card + task request/response | 先做本地可信 agent，不做开放网络发现 |
| v18.5 Multi-Agent Evaluation | 新增协作任务集 | 衡量协作收益，而不是只看单 agent benchmark |

### v18 评测要求

- Multi-agent 任务必须和 single-agent baseline 对比，报告成功率、token、耗时、SubAgent 成功率。
- 只有协作任务收益明确时，才扩大 Handoff / Remote SubAgent 的复杂度。
- Handoff 后的 agent 能否调用 ask_user 必须显式配置，并进入 HITL tag 评测。

---

## 十一、v19 - Guardrails 安全体系（P2，2-3 周，依赖 v14.6 + sandbox 修复 + v16）

### 目标

建立最小可用的 agent guardrails，而不是只依赖 ShellTool 黑名单。安全体系以 OWASP Agentic Top 10 为 taxonomy，以 AgentDojo 风格任务作为 prompt injection / tool injection 评测参考。

### 分阶段设计

| 阶段 | 内容 | 说明 |
|---|---|---|
| v19.0 Threat Model | 对照 OWASP ASI01-ASI10 建 threat matrix | 明确本项目覆盖哪些风险 |
| v19.1 Tool Guardrail | 工具白名单、危险参数校验、写操作二次确认 | 与 HITL ask_user 联动 |
| v19.2 Input / Context Guardrail | 检测 indirect prompt injection 和 memory poisoning | 优先覆盖 web_search / fetch_url 返回内容 |
| v19.3 Output Guardrail | PII、凭证、危险建议过滤 | 与 tracing redaction 策略一致 |
| v19.4 Red-Team Benchmarks | 引入 AgentDojo 风格测试用例 | 没有基准不宣称安全完成 |

### v19 评测要求

- Red-team cases 必须接入 v14.6 baseline/gate，不允许只写人工 demo。
- 报告 attack_success_rate、blocked_benign_rate、tool_guardrail_false_positive。
- Guardrail 通过率与正常任务成功率一起看，避免安全规则把可用性打穿。

---

## 十二、整体路线图

```text
v14 Phase 4 fix pass [已完成]
 │
 ├── v14.5 Long-Horizon Reliability / Task Resume [已实施]
 │   ├── TaskCheckpoint / TaskStateStore
 │   ├── OrchestratorAgent.resume(task_id)
 │   ├── simple / DAG / emergent / goal-driven 恢复边界
 │   └── HITL paused checkpoint
 │
 ├── v14.6 Evaluation Harness / Benchmark Expansion [P0, 下一阶段]
 │   ├── Eval Core 解耦
 │   ├── 30+ Benchmark Dataset v2
 │   ├── Deterministic Verifier
 │   ├── Baseline & Regression Gate
 │   ├── Reliability Metrics
 │   └── Safety Seed Suite
 │
 ├── v15 Agentic Memory [P0, 4-6 周, 依赖 v14.6]
 │   ├── factual / experiential / working memory
 │   ├── Memory as Tool
 │   ├── consolidation / forgetting
 │   └── Skill 轻量实验
 │
 ├── v16 MCP 全面适配 [P1, 2 周, 依赖 v14.6]
 │   ├── Generic MCP Client
 │   ├── Schema Adapter
 │   └── MCP Server
 │
 ├── v17 Self-Evolution [P1, 2-3 周, 依赖 v14.6 + v15]
 │   ├── Reflexion 风格经验提炼
 │   ├── failure memory
 │   ├── classifier calibration
 │   └── preference learning
 │
 ├── v18 Multi-Agent + 双引擎 [P2, 3-4 周, 依赖 v14.6 + v15 + v16]
 │   ├── Workflow Engine
 │   ├── Handoff
 │   ├── Remote SubAgent
 │   ├── A2A Prototype
 │   └── Multi-Agent Evaluation
 │
 └── v19 Guardrails [P2, 2-3 周, 依赖 v14.6 + sandbox 修复 + v16]
     ├── OWASP Agentic threat matrix
     ├── Tool Guardrail
     ├── Context / Memory Guardrail
     ├── Output Guardrail
     └── Red-Team benchmarks
```

总工期估算：约 17-23 周。v14.6 是新的近期主线；v16 可以与 v15 后半段并行；v19 threat model 可以提前做，但工具权限实现应等 v16 MCP 边界稳定后再落地。

---

## 十三、关键风险与缓解

| 风险 | 影响版本 | 缓解 |
|---|---|---|
| 评测样本过少导致结论失真 | v14.6+ | 先扩到 30+，按 tag 报告，不用单一平均分掩盖局部退化 |
| keyword-only oracle 假阴性/假阳性 | v14.6 | 引入 deterministic verifier，LLM judge 只作 fallback |
| evaluation 单测被 runtime 依赖阻断 | v14.6 | 抽出 `probe.py` / lazy import Orchestrator，纯指标测试不依赖 OpenAI SDK |
| baseline gate 过严拖慢实验 | v14.6+ | smoke gate 和 full gate 分层；本地快速跑少量任务，全量评测手动触发 |
| Task Resume 过度追求精确恢复 | v14.5 / v14.6 | 第一版只恢复到下一可执行边界，由 v14.6 resume cases 验证边界语义 |
| Memory 与 ContextManager 双压缩 | v15 | Working Memory 必须复用现有 ContextManager |
| Memory poisoning | v15 / v19 | 每条 memory 记录来源、task_id、可信度；提供删除和回滚 |
| 自演化误学坏经验 | v17 | 只做可回滚 prompt/memory 注入，不改模型参数和源码 |
| Handoff 打破 SubAgent token budget | v18 | Handoff 和 SubAgent 分开计费、分开 tracing、分开权限 |
| Guardrail 只有规则没有基准 | v19 | v19.0 先建 threat matrix 和 red-team cases，并接入 v14.6 gate |

---

## 十四、参考资源

### 论文 / Benchmarks

| 资源 | 链接 | 用途 |
|---|---|---|
| Memory in the Age of AI Agents | https://arxiv.org/abs/2512.13564 | v15 memory taxonomy |
| A-MEM: Agentic Memory for LLM Agents | https://arxiv.org/abs/2502.12110 | v15 dynamic memory linking |
| Reflexion | https://papers.nips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html | v17 failure reflection |
| A Comprehensive Survey of Self-Evolving AI Agents | https://arxiv.org/abs/2508.07407 | v17 scope control |
| AgentDojo | https://proceedings.neurips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html | v19 prompt injection benchmark |
| Meta-Harness | https://arxiv.org/abs/2603.28052 | 远期 harness 自动优化参考 |
| SWE-bench / execution-based verification | 既有 benchmark 思路 | v14.6 outcome verifier 参考 |
| TauBench / scripted user simulation | 既有 benchmark 思路 | v14.6 HITL SimulatedUser 参考 |
| LLM-as-a-Judge survey | arXiv:2306.05685 | v14.6 judge calibration 参考 |

### 官方 / 框架资料

| 资源 | 链接 | 用途 |
|---|---|---|
| LangGraph Persistence | https://docs.langchain.com/oss/python/langgraph/persistence | v14.5 checkpoint / resume 参考 |
| LangGraph Human-in-the-loop | https://docs.langchain.com/oss/python/langgraph/human-in-the-loop | v14.5 HITL resume 参考 |
| MCP Overview | https://modelcontextprotocol.io/specification/2025-11-25/basic | v16 protocol baseline |
| MCP Transports | https://modelcontextprotocol.io/specification/2025-06-18/basic/transports | v16 stdio + Streamable HTTP |
| OpenAI Agents SDK | https://platform.openai.com/docs/guides/agents-sdk/ | v18 / v19 conceptual reference |
| OpenAI Agents Guardrails | https://openai.github.io/openai-agents-python/guardrails/ | v19 guardrail design |
| LangChain Deep Agents Overview | https://docs.langchain.com/oss/python/deepagents/overview | long-horizon harness reference |
| LangChain Deep Agents Subagents | https://docs.langchain.com/oss/python/deepagents/subagents | v18 subagent comparison |
| A2A Specification | https://google-a2a.github.io/A2A/specification/ | v18 Agent Card / inter-agent protocol |
| OWASP Agentic Applications Top 10 release | https://genai.owasp.org/2025/12/09/owasp-genai-security-project-releases-top-10-risks-and-mitigations-for-agentic-ai-security/ | v19 risk taxonomy |

---

## 十五、修订记录

- v1 (2026-05-21 初版)：六层记忆架构、v10-v15 提案、二次搜索，已作废。
- v2 (2026-05-21 评审重构)：版本号校正为 v14-v19，引入 3D 记忆框架、Harness Engineering、双引擎、Master-Worker via MCP。
- v3 (2026-05-22 三次搜索定稿)：补充 OWASP ASI、LangChain Deep Agents、A2A、Meta-Harness 等资料。
- v4 (2026-05-24 最新实施后调整)：基于 v14 Phase 4 二次评审，标记 Phase 4 fix pass 已完成；将 Task Resume 拆为 v14.5；用论文/官方资料重排 v15-v19。
- v5 (2026-05-24 评测优先调整)：基于 v14.5 最新实施结果和 evaluation 源码复核，将 v14.6 Evaluation Harness / Benchmark Expansion 上调为下一阶段 P0；v15+ 全部依赖 v14.6 baseline。
- v6 (2026-05-24 二次复查)：补充 `eval_cli --dry-run` 的顶层依赖问题，将 dry-run CLI 也纳入 v14.6.1 Eval Core 解耦范围。
