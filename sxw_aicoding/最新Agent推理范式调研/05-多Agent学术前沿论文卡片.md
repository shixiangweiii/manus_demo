# 多 Agent 学术前沿论文卡片（2024H2–2026H1）

> **调研日期**: 2026-05-13
> **收录范围**: 2024 年下半年 – 2026 年上半年多 Agent / Subagent / 角色协作方向的高影响论文
> **卡片模板**: 论文信息 → 一句话定性 → 核心贡献 → 方法要点 → 关键实验 → 本项目启示 → 链接
> **与 02-核心论文摘要卡片.md 的关系**: 02 偏通用范式（CodeAct/LATS/ReflAct/R1/MUSE），本文专注**多 Agent 协作层**

---

## 阅读路径

```
┌─ 基石与方法论 ─────────────────────────────┐
│  01 Anthropic "Building Effective Agents"  │ 工业最权威 taxonomy
│  02 Magentic-One                            │ 通用多 agent 团队范式
│  03 Why Multi-Agent LLM Systems Fail       │ 失败模式系统分析
└────────────────────────────────────────────┘
┌─ 自动化与优化 ─────────────────────────────┐
│  04 AFlow (ICLR 2025 Oral)                 │ MCTS 搜索最优 workflow
│  05 ADAS                                    │ 自动设计 agent 系统
│  06 MetaAgent                               │ 自动构造多 agent
└────────────────────────────────────────────┘
┌─ 集成与聚合 ───────────────────────────────┐
│  07 Mixture-of-Agents (MoA)                 │ 分层聚合多模型
│  08 Chain-of-Agents (NeurIPS 2024)         │ 长上下文分块协作
│  09 Multi-Agent Debate or Vote             │ 投票 vs 辩论 (NeurIPS'25)
└────────────────────────────────────────────┘
┌─ 场景化应用 ───────────────────────────────┐
│  10 Agent-S2                                │ computer-use 分层
│  11 MetaGPT / ChatDev 2.0                   │ 软件工程 SOP
│  12 Agent Laboratory                        │ 科研多 agent
└────────────────────────────────────────────┘
```

---

## 基石与方法论

### 📘 论文 01: Building Effective AI Agents: Architecture Patterns and Implementation Frameworks

| 字段 | 内容 |
|------|------|
| 作者 | Anthropic Applied AI Team |
| 发表 | Anthropic 官方技术报告（2024.12，2025.10 eBook 扩展版） |
| 一句话定性 | **工业界迄今最权威的 Agent 模式 taxonomy**，定义了"workflow vs agent"以及"orchestrator-workers"等核心术语 |

**核心贡献**:
- 明确区分 **Workflow**（固定步骤）vs **Agent**（LLM 动态决策）两大范式
- 给出 5 种 workflow 模式：Prompt Chaining / Routing / Parallelization / Orchestrator-Workers / Evaluator-Optimizer
- 核心原则: "**Simplicity first**" —— 能用 workflow 解决就不要上 agent；能用单 agent 解决就不要上多 agent
- 引用实例: Coinbase / Intercom / Thomson Reuters 的生产级实践

**方法要点**:
- **Orchestrator-Workers**: 动态任务分解（不同于 Parallelization 的静态切分）
- **Evaluator-Optimizer**: 生成器 + 评估器循环（等价于 Reflexion / ReflAct 精神的工业化命名）
- 强调**明确的成功判据**和**人在回路**（human-in-the-loop）的必要性

**对本项目启示**:
- v8 的 GoalDrivenPlanner 本质是 **Evaluator-Optimizer + Orchestrator-Workers 的混合**
- 推荐把这份报告作为项目 docs/agent-patterns.md 的基准词汇表
- "Simplicity first" 原则应写入 `sxw_aicoding/docs/` 的开发哲学章节

**链接**:
- 官方页面: https://www.anthropic.com/research/building-effective-agents
- PDF 下载: https://resources.anthropic.com/hubfs/Building%20Effective%20AI%20Agents-%20Architecture%20Patterns%20and%20Implementation%20Frameworks.pdf

---

### 📘 论文 02: Magentic-One — A Generalist Multi-Agent System for Solving Complex Tasks

| 字段 | 内容 |
|------|------|
| 作者 | Microsoft Research（Adam Fourney 等） |
| 发表 | arXiv:2411.04468（2024.11） |
| 一句话定性 | **"通用型多 Agent 团队"**的开源标杆，首次把 Orchestrator-Worker 模式工程化为可复现 benchmark |

**核心贡献**:
- 提出 **Task Ledger + Progress Ledger 双台账**设计（事实/计划 + 进展/停滞信号）
- 5 个专员的明确分工: Orchestrator / WebSurfer / FileSurfer / Coder / ComputerTerminal
- 在 GAIA / AssistantBench / WebArena 上接近当时 SOTA（GAIA 38%）

**方法要点**:
```
while not done:
    1. Orchestrator 审查 Task Ledger (facts + plan)
    2. Orchestrator 根据 Progress Ledger 决定：
       - 继续 → 分派任务给专员
       - 停滞 → 修正 plan 或升级
       - 完成 → 汇总输出
    3. 专员执行领域动作，返回结果
    4. 更新 Progress Ledger
```

**关键实验**:
- GAIA Level 1: **55.3%**
- GAIA 全平均: **38%**（SOTA 当时约 40%）
- 失败分析显示**停滞识别**是胜负手（没有 Progress Ledger 的消融组下降 12%）

**对本项目启示**:
- **Progress Ledger 机制**可直接抽出来增强 v8 的 stagnation 检测（当前 v8 的 `GOAL_DRIVEN_STAGNATION_WINDOW` 只有简单计数，没有"为何停滞"的结构化记录）
- Orchestrator 用**双台账**+ LLM 做元推理的思路，可作为 v10 升级时 OrchestratorAgent 的重构参考
- 专员分工（browsing / file / code / shell）与本项目 tools/ 下的 4 个工具**一一对应**，升级为 agent 的成本低

**链接**:
- 论文: https://arxiv.org/abs/2411.04468
- 微软博客: https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/
- 代码: https://github.com/microsoft/autogen/tree/main/python/packages/autogen-magentic-one

---

### 📘 论文 03: Why Do Multi-Agent LLM Systems Fail?

| 字段 | 内容 |
|------|------|
| 作者 | UC Berkeley / OpenPipe 等 |
| 发表 | arXiv:2503.13657（2025.03） |
| 一句话定性 | **首份系统性多 Agent 失败模式分类学（MAST）**，把"多 Agent 为什么难用"量化到可操作级别 |

**核心贡献**:
- 分析 5 个主流 MAS 框架（AutoGen / MetaGPT / ChatDev / LangGraph 等）在 150+ 任务上的失败
- 提出 **MAST（Multi-Agent System Taxonomy）** 14 种失败模式，分 3 大类：
  - **Specification & System Design**（设计类，占 37%）
  - **Inter-Agent Misalignment**（协作类，占 31%）
  - **Task Verification**（验证类，占 32%）
- 关键发现:
  - Step Repetition: **17.14%**（最常见）
  - Fail to Follow Task Requirements: **10.98%**
  - Role Confusion: **0.5%**（角色明确后意外很少）

**方法要点**:
- 6 位专家人工标注 + LLM-as-Judge 辅助
- 每个失败模式配**症状 / 成因 / 改进建议**三元组

**对本项目启示**:
- **MAST 14 模式**可作为**测试用例设计清单**——v8/v9 的评测应该显式覆盖每一类失败
- Step Repetition 17% 的高发率提示：**环路检测**比反思更关键（呼应项目 cycle_detection 测试的存在合理性）
- 本文是本项目下一份"06-多Agent反模式与选型决策指南"的主要理论来源

**链接**: https://arxiv.org/abs/2503.13657 | OpenReview: https://openreview.net/pdf?id=wM521FqPvI

---

## 自动化与优化

### 📘 论文 04: AFlow — Automating Agentic Workflow Generation

| 字段 | 内容 |
|------|------|
| 作者 | FoundationAgents（Jiayi Zhang 等，MetaGPT 团队） |
| 发表 | ICLR 2025 **Oral** |
| 一句话定性 | **用 MCTS 自动搜索最优 agentic workflow 的 code 表示**，把 agent 设计从手艺变成算法 |

**核心贡献**:
- 把 agent workflow 建模为**代码空间中的节点图**（每个节点是一个 LLM 调用/工具调用）
- 使用 **MCTS 搜索 + LLM 变异算子**迭代生成 workflow 变体
- 在 6 个基准（HumanEval / MBPP / GSM8K / MATH / HotpotQA / DROP）上**平均超过人工 workflow**

**方法要点**:
```
state = 当前 workflow 图
action = {add/delete/modify node, change topology}
reward = 在验证集上的任务成功率
selection: UCT
expansion: LLM 作为 mutation operator
```

**对本项目启示**:
- **可作为 v12+ 的展望**：目前 v1→v8 的规划路径全是**手工设计**，AFlow 展示了自动化可能
- MCTS 思想与 02 卡片 LATS 同源，**树搜索已经成为 agent 设计的通用工具**
- 相关代码开源可直接试用: https://github.com/FoundationAgents/AFlow

**关键实验**（节选）:
- HumanEval: **91.4%**（超过单 agent GPT-4 和人工 workflow）
- MATH: **49.9%**（当时 SOTA）

**链接**:
- 论文 PDF: https://arxiv.org/pdf/2410.10762
- OpenReview: https://openreview.net/forum?id=z5uVAKwmjf
- 代码: https://github.com/FoundationAgents/AFlow

---

### 📘 论文 05: ADAS — Automated Design of Agentic Systems

| 字段 | 内容 |
|------|------|
| 作者 | Shengran Hu (UBC), Cong Lu (Oxford), Jeff Clune 等 |
| 发表 | arXiv:2408.08435（2024.08, NeurIPS 2024） |
| 一句话定性 | 用 LLM 作为 **meta-agent** 自动发明新 agent 结构，比人工设计稳定领先 |

**核心贡献**:
- 把 "agent 设计" 本身变成**Python 代码生成任务**：meta-agent 写出整个新 agent 的代码
- 提出 **Meta Agent Search** 算法：meta-agent 读历史成果 → 生成新 agent 代码 → 评测 → 进入档案库
- 发现的新 agent 在 ARC / DROP / GSM8K 上跑赢人工设计的基线

**方法要点**:
- **搜索空间 = 图灵完备代码空间**（而非 AFlow 的结构化图空间），表达力更强
- Archive 机制借鉴 Quality-Diversity 进化算法

**对本项目启示**:
- 展示了"**让 LLM 写 agent**"的可行性，是自进化研究的重要一环
- 与 AFlow 对比：AFlow 偏**结构化搜索**，ADAS 偏**开放式代码生成**，教学价值互补
- 项目可在 v12 展望章节引用

**链接**: https://arxiv.org/abs/2408.08435

---

### 📘 论文 06: MetaAgent — Automatically Constructing Multi-Agent Systems

| 字段 | 内容 |
|------|------|
| 发表 | arXiv:2507.22606（2025.07） |
| 一句话定性 | 面向通用任务的"**多 Agent 自动构造器**"，是 AFlow/ADAS 在**多 agent 场景**的扩展 |

**核心贡献**:
- 针对输入任务**自动生成一组 agent 角色 + 工具分配 + 通信协议**
- 评测显示 MetaAgent 能复现/超越 MetaGPT / ChatDev 等手工设计

**对本项目启示**:
- 自动化方向进一步延伸到**多 agent 拓扑设计**层面
- 与 Anthropic "Building Effective Agents" 的"慎用多 agent"构成**两极张力**：自动化让多 agent 设计变便宜，但不代表多 agent 就该用

**链接**: https://arxiv.org/html/2507.22606v1

---

## 集成与聚合

### 📘 论文 07: Mixture-of-Agents (MoA) Enhances Large Language Model Capabilities

| 字段 | 内容 |
|------|------|
| 作者 | Together AI 团队 |
| 发表 | arXiv:2406.04692（2024.06） |
| 一句话定性 | **分层聚合多 LLM**的简单有效方案，在 AlpacaEval 上 65.1%（超过 GPT-4o） |

**核心贡献**:
- 发现 **"LLM collaborativeness"**：看到其他模型答案后，LLM 会生成更好的答案，即使对方答案更差
- 提出**多层 mixture**架构：
  - 第 1 层：N 个 proposer 并行生成候选
  - 第 2 层：aggregator 综合成单一答案
  - 可堆叠多层
- 用 6 个开源模型（Qwen / LLaMA / Mixtral / WizardLM）超过闭源 GPT-4o

**方法要点**:
```
answer_l = Aggregator(x, [answer_{l-1, i} for i in 1..N])
```

**对本项目启示**:
- MoA 是**纯推理层多 agent**（不动作、不规划），是成本最低的多 agent 玩法
- 可在 v10/v11 作为**Reflector 模块**的升级：多个模型投票评估更稳健
- 呼应 02 卡片中 LATS 的"多路径探索"思想，但在**答案空间而非动作空间**

**链接**:
- 论文: https://arxiv.org/abs/2406.04692
- Together 博客: https://www.together.ai/blog/together-moa
- 代码: https://github.com/togethercomputer/moa

---

### 📘 论文 08: Chain-of-Agents (CoA) for Long Context Tasks

| 字段 | 内容 |
|------|------|
| 作者 | Google Research |
| 发表 | NeurIPS 2024（arXiv:2406.02818） |
| 一句话定性 | **Manager-Worker 串行协作**解决超长上下文任务，在长文档 QA 上超过 RAG 10%+ |
| 与 02 文档关系 | 02 已收录，本处补充**2025 后续工作** |

**补充进展（2025）**:
- 有多个 follow-up 把 CoA 扩展到**多模态长文档**（视频字幕、幻灯片）
- LlamaIndex AgentWorkflow 的 `ChainWorkflow` 原语即受 CoA 启发

**对本项目启示**:
- `context/manager.py` 的 token budget + truncation 可演进为 **CoA chunked worker**
- 与 Subagent 方案形成选择：**CoA = 串行传递**，Subagent = 并行隔离

**链接**: https://arxiv.org/abs/2406.02818

---

### 📘 论文 09: Debate or Vote — Which Yields Better Decisions in Multi-Agent LLMs?

| 字段 | 内容 |
|------|------|
| 发表 | NeurIPS 2025 **Spotlight** |
| 一句话定性 | **多 Agent 辩论的"皇帝新装"** —— 大多数增益其实来自简单投票，辩论本身贡献有限 |

**核心贡献**:
- 在 7 个 NLP 基准上对比 Multi-Agent Debate(MAD) vs Majority Voting
- 关键发现: **"Majority Voting alone accounts for most of the performance gains"**
- 辩论的边际收益主要在**需要多步推理**的任务上（数学/逻辑）

**对本项目启示**:
- **不要为了"多 agent"而多 agent**：如果目标是提升答案质量，**先试投票再试辩论**
- 对 v8 Reflector 模块的启示：**多次采样 + 投票**比"让另一个 agent 批判"更稳
- 这篇论文是 06 反模式文档第 5 条"Self-Critique Paradox"的**直接证据延续**

**链接**:
- NeurIPS 页面: https://neurips.cc/virtual/2025/poster/116557
- 代码: https://github.com/deeplearning-wisc/debate-or-vote

---

## 场景化应用

### 📘 论文 10: Agent-S2 — Compositional Generalist-Specialist Framework for Computer Use

| 字段 | 内容 |
|------|------|
| 作者 | Simular AI |
| 发表 | arXiv:2504.00906（2025.04） |
| 一句话定性 | **Computer-use 场景的分层多 agent 范式** —— Generalist 做规划、Specialist 做执行 |

**核心贡献**:
- Manager (Generalist) 做 **high-level planning**，分派给 Specialist 做 GUI 执行
- Evaluator 层做结果验证与重试
- 在 OSWorld / WindowsAgentArena 上显著领先

**方法要点**:
- **Compositional**: Specialist 可热插拔（换成不同 vision/grounding 模型）
- **Hierarchical**: 严格 3 层，职责边界清晰

**对本项目启示**:
- 当前项目无 computer-use 模态，**暂不直接适用**
- 但"**Generalist Planner + Specialist Executor + Evaluator**"的三层结构与 v8 的 Planner/Executor/Reflector 高度同构，是**横向对齐**的佐证
- Agent-S3（2025.10, arXiv:2510.02250）已达人类水平，值得跟踪

**链接**: https://arxiv.org/html/2504.00906v1

---

### 📘 论文 11: MetaGPT + ChatDev 2.0 — Software Engineering Multi-Agent Systems

| 字段 | 内容 |
|------|------|
| 核心哲学 | **Code = SOP(Team)** —— 软件公司 SOP 作为 agent 协作协议 |
| MetaGPT 代表作 | arXiv:2308.00352（2023.08, ICLR 2024），持续更新 |
| ChatDev 2.0 | 2025 Q4 开源的 Zero-Code 多 agent 平台 |

**核心贡献**:
- **Role-based division**: PM / Architect / Engineer / QA / Reviewer
- **Message Pool**（发布-订阅）而非直接消息传递，解耦角色
- **SOP 模板**: 每个角色有结构化输出格式（设计文档 / 代码 / 测试用例）

**2025 新进展**:
- ChatDev 2.0 引入**可视化角色编辑器**，降低多 agent 定制门槛
- MetaGPT 集成 AFlow 自动 workflow 优化

**对本项目启示**:
- **Message Pool 模式**是 v10 事件总线升级的优秀参考（当前 v8 `_emit` 是单向广播）
- **角色模板化**可启发项目的 skill library 设计
- **警告**: 多角色系统易踩 06 文档第 4 条"职责重叠"陷阱

**链接**:
- MetaGPT: https://github.com/FoundationAgents/MetaGPT
- ChatDev 2.0: https://github.com/OpenBMB/ChatDev

---

### 📘 论文 12: Agent Laboratory / Virtual Lab — 科研多 Agent

| 字段 | 内容 |
|------|------|
| 代表作 | AMD Agent Laboratory (arXiv:2501.04227, 2025.01) / Stanford Virtual Lab |
| 一句话定性 | 把"**科研流程（读论文→设计实验→跑数据→写论文）**"编码为多 agent 协作 |

**核心贡献**:
- 角色分工: **PhD Student / Postdoc / Professor / Reviewer**
- 每阶段有**明确 artifact**（文献综述、实验设计、代码、论文草稿）
- 闭环评估: Reviewer agent 给出 NeurIPS-style 评分

**对本项目启示**:
- 科研多 agent 是**长时程 + 知识密集型**场景的最佳试验田
- 项目演进到自进化层时，可借鉴"**每个阶段有 artifact**"的设计强化 reflector 输出
- 可作为未来评测基准扩展的方向（当前 12 个任务偏短时程）

**链接**: https://arxiv.org/abs/2501.04227

---

## 综合对照矩阵

| # | 论文 | 类别 | 多 Agent 拓扑 | 核心机制 | 对本项目 ROI |
|---|------|------|-------------|---------|------------|
| 01 | Building Effective Agents | 方法论 | N/A（词汇表） | Workflow vs Agent 分类 | ★★★★★（基础术语必看） |
| 02 | Magentic-One | 通用团队 | Orchestrator-Worker | 双 Ledger + 停滞检测 | ★★★★★（Progress Ledger 可直接学） |
| 03 | Why MAS Fail | 失败学 | N/A | MAST 14 模式分类 | ★★★★★（反模式文档基础） |
| 04 | AFlow | 自动化 | 动态 | MCTS 搜索代码 workflow | ★★★（v12 展望） |
| 05 | ADAS | 自动化 | 动态 | Meta-Agent Search | ★★（研究参考） |
| 06 | MetaAgent | 自动化 | 动态 | 多 agent 自动构造 | ★★ |
| 07 | Mixture-of-Agents | 聚合 | 分层并行 | Proposer + Aggregator | ★★★★（Reflector 升级候选） |
| 08 | Chain-of-Agents | 长上下文 | Pipeline | Manager-Worker 串行 | ★★★★（context/manager 升级） |
| 09 | Debate or Vote | 聚合 | 网络 | 投票 > 辩论 | ★★★★（反 self-critique paradox） |
| 10 | Agent-S2 | Computer use | Hierarchical | Generalist-Specialist | ★★（横向对齐证据） |
| 11 | MetaGPT / ChatDev | 软件工程 | Role-based | Message Pool + SOP | ★★★（事件总线参考） |
| 12 | Agent Laboratory | 科研 | Role-based | Artifact-driven | ★★（远期评测方向） |

---

## 本文与 02 卡片的分工

| 主题 | 02 文档已覆盖 | 本文新增 |
|------|------------|---------|
| 执行层 | CodeAct | — |
| 推理层 | LATS / R1 / ReflAct | — |
| 规划层 | GoalAct / Backward | AFlow（自动 workflow） |
| **协作层** | Chain-of-Agents 初稿 | **Magentic-One / ADAS / MoA / MAS 失败学 / MetaGPT / Agent-S2**（核心增量） |
| 记忆层 | MUSE / Memp | — |
| 自进化层 | Self-Evolving Survey | ADAS / MetaAgent |
| 综述 | Model-native Survey | Building Effective Agents |

下一份文档（`06-多Agent反模式与选型决策指南.md`）将基于以上论文结论，系统总结**什么时候别用多 Agent、要用多 Agent 时用什么拓扑**。
