# 多 Agent 架构调研增量计划

## 背景与边界

- 基线：`sxw_aicoding/最新Agent推理范式调研/` 现有 4 份文档（调研日期 2026-05-12）已覆盖 CodeAct / LATS / CoA / MUSE / R1 / Model-native Survey 等通用范式。
- 本轮焦点：**多 Agent 架构、Subagent 最佳实践**（工业旗舰 + 学术前沿 + 反模式选型）。
- 范围约束：**纯外部论文与工程实践**，不做源码 gap 分析；不写 v9/v10 融入路线图改动。
- 输出风格：**摒弃历史排版束缚**，以"最新、最正确、最直接"的方式组织新文档；现有 4 份文件不删不改，保持时序档案价值。

## 产出物（3 份新文档 + 1 份索引更新）

### Task 1: 新增 `04-多Agent架构全景与工业旗舰实践.md`

聚焦 2025-2026 工业级多 Agent 系统的**架构决策与工程范式**，每个系统按统一模板组织：定位 / 通信拓扑 / Subagent 策略 / 上下文隔离 / 工具接入 / 失败处理 / 可观测 / 关键工程权衡。

覆盖对象（按成熟度排序）：
- **Claude Code Subagent 体系**（Anthropic 2025 官方 cookbook + `spawn_subagent` 机制 + context isolation 原则）
- **Cursor Agent Mode**（多模型协作 + background agent + composer 串行/并行编排）
- **Devin (Cognition)**（长时程任务 + planner/executor/memory/browser 四子 agent）
- **Manus / OpenManus (MetaGPT 系)**（Leader + Worker 角色分工 + Agent SOP）
- **OpenAI Swarm / Agent SDK**（handoff + routine 轻量编排）
- **Microsoft AutoGen v0.4 + Magentic-One**（actor-based + Orchestrator/WebSurfer/FileSurfer/Coder/ComputerTerminal 五专员）
- **LangGraph Multi-Agent**（supervisor / network / hierarchical 三种拓扑 + state graph）
- **CrewAI / AgentScope / LlamaIndex AgentWorkflow**（补充生态对比）

结尾输出 **多 Agent 拓扑对照矩阵**（supervisor / network / hierarchical / pipeline / blackboard 五种拓扑 × 适用场景 × 代表系统）。

### Task 2: 新增 `05-多Agent学术前沿论文卡片.md`

精选 2024H2–2026H1 多 Agent 方向 10-12 篇代表性论文，延用既有卡片模板（论文信息 / 一句话定性 / 核心贡献 / 方法要点 / 关键实验 / 启示 / 链接）。初步候选：
- **Magentic-One** (Microsoft 2024.11, arXiv:2411.04468) — 通用多 agent 团队 orchestrator
- **Chain-of-Agents** (Google NeurIPS 2024) — 已在 02 提到，此处补充后续 follow-up
- **AFlow** (ICLR 2025) — 自动化 agent workflow 生成
- **ADAS: Automated Design of Agentic Systems** (2024.08)
- **Agent-S / Agent-S2** (Simular AI 2024-2025) — computer use 多 agent
- **AgentVerse / ChatDev 后续**（软件工程多角色协作）
- **Mixture-of-Agents (MoA)** (Together AI 2024) — 分层推理聚合
- **MetaGPT + X-Agent** 最新改进
- **Agent Laboratory / Virtual Lab** (2024-2025) — 科研多 agent
- **Anthropic "Building Effective Agents"** (2024.12 技术报告，工业界最权威的多 agent 模式总结)
- **GAIA2 / AssistantBench / TheAgentCompany** — 多 agent 评测基准进展
- 视检索结果补充 **Hierarchical Agents / Debate / Self-consistency Multi-Agent** 方向代表作

### Task 3: 新增 `06-多Agent反模式与选型决策指南.md`

这是本轮调研的**独特价值点**，业内至今缺乏系统总结。结构：

1. **10 大多 Agent 反模式**（每个配"症状 / 根因 / 代表踩坑案例 / 规避策略"）
   - 角色爆炸（N>5 后边际收益负）
   - 上下文泄漏 / Subagent 历史污染
   - 通信死循环 / 无限 handoff
   - 职责重叠导致双写冲突
   - LLM-as-Judge 同模型自评悖论（Snorkel 2025）
   - Summary loss（子 agent 摘要漏掉关键信息）
   - Tool schema 分裂（每个 agent 自建工具集）
   - 成本不可控（N 个 agent × M 轮 × long context）
   - 可观测性断裂（trace 无法串联）
   - 评测失真（端到端指标掩盖单 agent 缺陷）

2. **选型决策树**：单 Agent vs 多 Agent 的判据清单（任务复杂度 / 领域专业化需求 / 上下文规模 / 并行收益 / 调试成本）— 参考 Anthropic "Building Effective Agents" 的 workflow vs agent 分层思路。

3. **拓扑选择矩阵**：给定任务特征（长文档 / 代码工程 / 浏览器操作 / 科研探索 / 对话助手），推荐拓扑（supervisor / hierarchical / network / pipeline）。

4. **工程落地 checklist**：上下文隔离、工具注册表统一、观测骨架、成本熔断、Critic 独立、Handoff 协议、Memory 层级。

### Task 4: 更新 `README.md` 索引

在现有索引表底部追加「多 Agent 专题（2026-05-13 增补）」小节，列出 3 份新文档的链接与阅读顺序建议（04 工业 → 05 学术 → 06 反模式决策）；保留原有章节不动。

## 不做什么

- 不修改 `01/02/03` 三份原始文档内容。
- 不改动任何源码、配置、测试。
- 不输出 v9/v10 具体代码或特性开关改动建议（本轮纯调研）。
- 不做源码 gap 分析章节（按用户确认排除）。

## 交付验收标准

- 3 份新 md 文件字数 2000-4000 字/篇，论文/系统引用均带可访问链接（arXiv / 官方 cookbook / GitHub）。
- 每份文档首部标注调研日期 2026-05-13 与数据来源检索日期。
- README 索引可直接定位新增 3 份文档。
- 所有对外部系统/论文的结论均可溯源（论文链接 / 官方博客 / 代码仓库）。
