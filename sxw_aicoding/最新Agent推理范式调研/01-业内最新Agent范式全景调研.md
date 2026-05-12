# 业内最新 Agent 推理/规划/执行范式全景调研（2024–2026）

> **调研目的**: 为 Manus Demo 教学项目（v8 已融入 ReflAct 思想）寻找下一批可融入验证的前沿范式
> **调研日期**: 2026-05-12
> **核心筛选原则**:
> 1. 范式本身具备**学术辨识度**（可溯源到高质量论文或工业旗舰系统）
> 2. 可在现有 `orchestrator.py` 架构下**以 v9+ 特性开关**形式叠加，不需要重写底座
> 3. 与现有 v5/v8 形成**可观测的对照实验**，提升教学/研究价值
> 4. 实现复杂度可控（单个范式 500–1500 行新增代码量）

---

## 一、大图谱：2022→2026 范式演进时间轴

```
2022 ─ ReAct                    Thought-Action-Observation 交错
2023 ─ Reflexion                失败后自然语言反思改写策略
     ─ Self-Refine              同模型多轮自评-自修
     ─ Plan-and-Solve           先规划后执行
     ─ Voyager                  skill library + 课程学习
     ─ Tree-of-Thoughts         树搜索探索多思路
     ─ LATS                     MCTS + 反思，搜索+推理+行动统一
     ─ CodeAct                  代码即动作（统一动作空间）
     ─ Chain-of-Agents (CoA)    长上下文多 LLM 协作
2024 ─ OpenAI o1                RL 内化"慢思考"
     ─ Claude Sonnet            Subagent + 上下文压缩
2025 ─ DeepSeek-R1              RL-only 激发推理，开源复刻 o1
     ─ OpenAI o3                工具调用内化进推理过程
     ─ Manus / Devin / Cursor   产品级多智能体协作范式
     ─ ReflAct (EMNLP 2025)     状态-目标反思，锚定防漂移  ← v8 已融入
     ─ CodeAct in Microsoft AF  CodeAct 主流化
     ─ GoalAct                  全局规划 + 分层执行
     ─ Memp                     可学习可更新的过程性记忆
     ─ MUSE                     经验驱动自进化（长时程任务 SOTA）
     ─ Model-native Survey      范式转型：从外部 Pipeline → 模型内化
2026 ─ Planner-Centric Survey   规划器中心化 vs ReAct 中心化
     ─ SWE-Evo / GAIA2          长时程评测基准成熟
```

**一句话演进主线**：
> 从"**外部 Pipeline 编排**"（ReAct / Plan-and-Solve / Reflexion）
> 走向"**模型内化能力**"（o1 / R1 / K2 / o3）
> 同时在应用层涌现"**角色-协作范式**"（CoA / Manus / Claude Code Subagent）
> 和"**自进化范式**"（Voyager / Memp / MUSE）。

---

## 二、范式分层地图（按 v8 现有架构对齐）

我们把业内范式切成 5 层，便于和项目现有模块对照：

```
                    ┌──────────────────────────────────────┐
   L5 Self-Evolve   │ MUSE / Voyager / Memp                │  ← 项目暂无
                    ├──────────────────────────────────────┤
   L4 Multi-Agent   │ Chain-of-Agents / Claude Subagent    │  ← 项目只有 Orchestrator 单线
                    │ Manus 多角色 / ChatDev              │
                    ├──────────────────────────────────────┤
   L3 Reasoning     │ o1 / R1 Long CoT / LATS (MCTS)      │  ← v8 是外层反思，无搜索
                    │ ReflAct (状态反思) / Reflexion      │     v8 已融入 ReflAct
                    ├──────────────────────────────────────┤
   L2 Planning      │ Plan-and-Solve / ADaPT / GoalAct    │  ← v1/v2 是 Plan-and-Solve
                    │ Backward Planning (v8)              │     v8 是 Backward
                    ├──────────────────────────────────────┤
   L1 Execution     │ ReAct / CodeAct / Tool Calling      │  ← 项目是 ReAct
                    └──────────────────────────────────────┘
```

**项目空白点**（= 升级机会）：
- **L1 执行层**: 未引入 CodeAct，所有 action 还是 JSON tool call，多步依赖需要多轮 round-trip
- **L3 推理层**: 未引入树搜索（LATS），只有线性反思；未利用 o1/R1 风格的 long CoT
- **L4 协作层**: `Orchestrator` 内部是单线程 LLM 循环，缺少"多专业 agent 协作"范式
- **L5 自进化**: 完全空白，无 skill library，无过程性记忆沉淀

---

## 三、10 个值得融入的前沿范式（按优先级排序）

### 🥇 优先级 S：直接切入、ROI 最高

#### S1. CodeAct —— 代码即动作

- **来源**: Wang et al. ICML 2024 + Apple ML + Microsoft Agent Framework (2025)
- **核心思想**: 将 "输出 JSON tool_call" 替换为 "输出 Python 代码块"，在沙盒中执行。一次 LLM 回合可以**组合多个工具**（循环、条件、数据传递）。
- **关键论据**: MSR 实测 CodeAct 比 JSON tool-calling **减少 50%+ LLM 回合数**，在 M365 Copilot 已落地
- **为何适合本项目**:
  - 本项目已有 `tools/code_executor.py` 和 `tools/shell_tool.py`，基础设施齐全
  - 与 v5/v8 的 TODO-driven 循环是**正交升级**，可独立评测
  - **教学张力极强**: "JSON 工具调用 vs CodeAct" 是 2024-2025 年最热话题
- **项目融入切入点**: 新增 `agents/codeact_executor.py`，在 `orchestrator._execute_*` 系列里增加 `ENABLE_CODE_ACT` 开关
- **预期增量**: 推理轮次减少 30-50%（v5/v8 在 evaluation/ 可直接对比）

#### S2. LATS / MCTS 树搜索推理

- **来源**: Zhou et al. ICML 2024 ("Language Agent Tree Search")
- **核心思想**: 把 ReAct 的线性执行改成**蒙特卡洛树搜索** + 反思。每个节点是一个 state，生成 k 个候选 action → rollout → LM 打分 → 回传 → 扩展最优分支。
- **关键论据**: LATS 在 HumanEval 和 WebShop 同时达到 SOTA
- **为何适合本项目**:
  - 与 v8 的"线性反思"形成天然对照：**单路径反思 vs 多路径搜索**
  - 现有 `dag/` 模块已有状态机基础，树结构只是"有限分支 DAG"
  - **教学意义巨大**: "为什么 o1 能做很长的推理？因为它在潜空间做了 LATS"
- **项目融入切入点**: 新增 `agents/lats_planner.py`，定义 `TreeNode(state, score, children)`，复用 `tools/router.py` 提供候选 action
- **预期增量**: 在复杂推理任务上成功率提升，代价是 LLM 调用量增大（可用 `TREE_WIDTH`/`TREE_DEPTH` 控制）

#### S3. Chain-of-Agents (CoA) 多 Agent 分段协作

- **来源**: Google Research NeurIPS 2024 (arxiv 2406.02818)
- **核心思想**: 对长文档/长任务分段，每段由一个 Worker Agent 处理并产出"中间信息块"，Manager Agent 串起所有块做综合推理。**完全 training-free**，比 RAG 更能保留全局信息。
- **关键论据**: 9 项 QA/summary 任务平均提升 10%+，尤其在超长文档上
- **为何适合本项目**:
  - 本项目目前 `context/manager.py` 用 token budget + truncation 处理长上下文，信息丢失严重
  - CoA 可独立改造为"长任务分段"（v5/v8 的 TODO 列表本质就是段）
  - **教学张力**: "RAG vs 长上下文 LLM vs CoA" 三条线的对比在业界正热
- **项目融入切入点**: 新增 `agents/chain_coordinator.py`，封装 Worker/Manager 两类 role，用 `knowledge/retriever.py` 改造成分段器
- **预期增量**: 在长文档类任务（阅读理解、合同解析）上显著超越单 agent

### 🥈 优先级 A：有价值但实现较重

#### A1. MUSE —— 经验驱动自进化（过程性记忆）

- **来源**: Fang et al. 2510.08002 (Oct 2025)
- **核心思想**: 三层分层记忆（strategic / tactical / episodic），任务完成后把轨迹**结构化沉淀到 procedural memory**，下次相似任务直接复用。在 TAC 基准上 SOTA。
- **为何适合本项目**:
  - 本项目 `memory/long_term.py` 仅有简单 key-value，未做"经验抽象"
  - 与 Voyager 的 skill library 思想同源，但更偏向"流程复用"而非"代码片段复用"
  - **教学张力**: "如何让 Agent 从失败中真正学到东西"是 2025 年最受关注的问题
- **项目融入切入点**: 扩展 `memory/` 增加 `procedural.py`，在 `reflector.py` 完成任务后触发"经验沉淀"
- **预期增量**: 第 2 次执行相似任务时轮次减少 40%+；可设计"首次 vs 重复执行"对照实验

#### A2. Self-Correction / Critic Verifier 双 Agent 校验

- **来源**: Snorkel AI (2025) + MIT/Berkeley 多篇 self-correction 论文
- **核心思想**: 避免"自我批评悖论"——让 **Generator 和 Critic 是不同的 agent**（不同 prompt 或不同模型），避免模型看不见自己盲区。
- **关键论据**: Snorkel 实验证明同模型 self-refine 在简单任务上反而**降低性能**；独立 critic 能显著提升
- **为何适合本项目**:
  - 当前 v8 `_goal_reflect` 是"LLM 自己反思自己的 todo_list"，符合"自我批评悖论"警告
  - 可引入独立的 `CriticAgent`，作为 v8 的增强开关
- **项目融入切入点**: 新建 `agents/critic.py`，在 `reflector.py` 里加 `ENABLE_DUAL_CRITIC` 开关
- **预期增量**: 在简单任务上反思质量不降反升；复杂任务可捕获"虚假完成"类错误

#### A3. Model-native Paradigm —— 利用 o1/R1 作为推理后端

- **来源**: Jitao Sang et al. "Beyond Pipelines" survey 2510.16720
- **核心思想**: 放弃外部 Pipeline 编排的反思/规划，直接让 Reasoning Model（o1 / R1 / QwQ）用内化的 long CoT 做整件事。
- **项目融入意义**:
  - **教学对照极有价值**: 让学生对比 "Pipeline-based v8 (894 行代码) vs Model-native o1 (1 行 API 调用)"，直观感受两种范式的优劣
  - 不需要复刻 o1，只需新增 `llm/reasoning_client.py` 接一个 R1/o3-mini API
- **项目融入切入点**: 在 `llm/client.py` 旁增加 `reasoning_client.py`；`orchestrator` 加 `ENABLE_MODEL_NATIVE` 路径直接把任务 + 工具列表扔给 reasoning model
- **预期增量**: 可能在某些任务上 Pipeline 完败或碾压 Model-native，这正是最好的教学素材

### 🥉 优先级 B：适合做"小实验"的范式

#### B1. GoalAct —— 全局规划 + 分层执行

- **来源**: arxiv 2504.16563 (2025)
- **核心思想**: 持续更新的全局规划 + 多层（角色-子任务-动作）执行，设计上**介于 v1/v2 显式规划和 v5/v8 隐式规划之间**
- **融入建议**: 作为 v1/v2 的 2025 年升级版引入，突出"规划与执行的解耦"是 2025 年的主流认知

#### B2. Memp —— 程序性记忆的学习式更新

- **来源**: arxiv 2508.06433 (2025)
- **核心思想**: 把 Voyager skill library 的"代码片段"升级为"可学习的行为描述 + embedding 检索"。Agent 每次执行后，Memp 会更新"该情况下该怎么做"的记忆。
- **融入建议**: 作为 MUSE (A1) 的补充方案，在 `memory/long_term.py` 的基础上加入行为描述检索

#### B3. Tree-of-Thoughts 离散推理增强

- **来源**: Yao et al. NeurIPS 2023
- **核心思想**: 在**单次推理**内做思想树搜索（非行动树），适合数学/逻辑子任务
- **融入建议**: 作为 LATS (S2) 的 L3 子模块——树搜索只在"思考"阶段启用，降低整体成本

#### B4. Hypothesis-Driven / Scientific Agent Loop

- **来源**: A Survey of LLM-based Scientific Agents (arxiv 2503.24047)
- **核心思想**: hypothesis → experiment → analysis → conclusion 四阶段循环，比通用 ReAct 更结构化，适合科研类任务
- **融入建议**: 在 `evaluation/benchmark.py` 加一个"科研类任务基准"，用这个循环跑

### 🪨 优先级 C：短期不建议，留档备查

- **Reflexion** (2023): v8 的 `_reanchor_goal` 已经覆盖其思想
- **Self-Refine** (2023): v8 每 3 轮主动刷新 todo 已覆盖
- **Plan-and-Solve** (2023): v1/v2 已经是此范式
- **Voyager** (2023): skill library 思想已被 Memp (B2) 和 MUSE (A1) 吸收升级
- **SimWorld / 具身智能** (NeurIPS 2025): 需要物理仿真环境，偏离教学 Demo 定位

---

## 四、与 v8 的正交性矩阵

| 候选范式 | 与 v8 ReflAct 协同 | 与 v5 隐式 TODO 协同 | 独立实验价值 | 实现量 |
|---------|-------------------|--------------------|------------|--------|
| S1 CodeAct | ✅ 完美正交（执行层） | ✅ 完美正交 | ⭐⭐⭐⭐⭐ | 🟢 小 (~300 行) |
| S2 LATS | ⚠️ 重叠（都有反思，v8 线性 vs LATS 树） | ✅ 正交 | ⭐⭐⭐⭐⭐ | 🟡 中 (~800 行) |
| S3 CoA | ✅ 正交（协作层） | ✅ 正交 | ⭐⭐⭐⭐ | 🟡 中 (~500 行) |
| A1 MUSE | ✅ 正交（记忆层） | ✅ 正交 | ⭐⭐⭐⭐ | 🔴 大 (~1200 行) |
| A2 Critic | ⚠️ 替代 v8 的 `_goal_reflect` | ✅ 正交 | ⭐⭐⭐ | 🟢 小 (~200 行) |
| A3 Model-native | ❌ 替代整个 v8 | ❌ 替代整个 v5 | ⭐⭐⭐⭐⭐（作为对照） | 🟢 小 (~150 行) |

**推荐 v9 路线**（详见 `03-v9融入路线图.md`）：
> **v9 = CodeAct 执行升级 (S1) + LATS 搜索推理 (S2) + Critic 双核校验 (A2)**
> **v10 = CoA 多 Agent 协作 (S3) + MUSE 经验沉淀 (A1)**
> **v11 = Model-native 对照 (A3) —— 作为"外部 Pipeline 范式"终章的压轴对比**

---

## 五、与本项目的范式匹配性快照

| 项目现有 | 对应业内范式 | 年代 | 是否需升级 |
|---------|-------------|------|----------|
| v1/v2 Planner-Executor | Plan-and-Solve (Wang 2023) | 2023 | 🟢 代表性已足够 |
| v2 DAG 超步 | Pregel/BSP (非 Agent 原创) | 2010 | 🟢 已是成熟方案 |
| v3 动态 DAG | 无严格论文对应 | — | 🟡 可增加 LATS 对照 |
| v4 混合路由 | AutoGen Router + 自研 | 2024 | 🟢 已是主流 |
| v5 隐式 TODO | Claude Code / Cursor 2024 | 2024 | 🟢 已是当红范式 |
| v6 Tracing 可视化 | OpenTelemetry + 自研 | — | 🟢 工程完善 |
| v7 评测体系 | 对标 AgentBench | 2024 | 🟡 可加 GAIA2/SWE-Evo |
| v8 ReflAct | Kim et al. EMNLP 2025 | 2025 | ✅ 已是最新范式 |
| **缺 CodeAct** | Wang ICML 2024 | 2024 | ⚠️ **v9 必补** |
| **缺 LATS** | Zhou ICML 2024 | 2024 | ⚠️ **v9 推荐** |
| **缺多 Agent 协作** | CoA / Claude Subagent | 2024-2025 | ⚠️ **v10 推荐** |
| **缺自进化记忆** | MUSE / Memp | 2025 | ⚠️ **v10 推荐** |
| **缺 Model-native 对照** | o1 / R1 / QwQ | 2024-2025 | ⚠️ **v11 推荐**（对照组） |

---

## 六、原始资料与论文索引

### 综述类（必读）

1. **Beyond Pipelines: A Survey of the Paradigm Shift toward Model-native Agentic AI** (Sang et al. 2025)
   - https://arxiv.org/abs/2510.16720
   - 仓库列表: https://github.com/ADaM-BJTU/model-native-agentic-ai
   - 本项目的**定位参照物**：项目目前属于典型的 Pipeline-based paradigm

2. **LLM-based Agentic Reasoning Frameworks: A Survey** (Aug 2025)
   - https://arxiv.org/html/2508.17692v1
   - 范式分类地图

3. **A Survey of Self-Evolving Agents** (2025)
   - https://arxiv.org/html/2507.21046v4
   - 仓库: https://github.com/XMUDeepLIT/Awesome-Self-Evolving-Agents

4. **A Survey on Evaluation of LLM-based Agents** (arxiv 2503.16416)
   - https://arxiv.org/html/2503.16416v2
   - 评测基准全景

### 核心范式论文

5. **ReflAct** (Kim et al. EMNLP 2025) — 已融入 v8
   - https://arxiv.org/abs/2505.15182

6. **CodeAct** (Wang et al. ICML 2024)
   - Apple Research: https://machinelearning.apple.com/research/codeact
   - MS 工程化: https://devblogs.microsoft.com/agent-framework/codeact-with-hyperlight/
   - 开源实现参考: https://github.com/xingyaoww/code-act

7. **LATS** (Zhou et al. ICML 2024)
   - https://arxiv.org/abs/2310.04406
   - 智源中文解读: https://hub.baai.ac.cn/view/31413

8. **Chain-of-Agents** (Google Research NeurIPS 2024)
   - https://arxiv.org/abs/2406.02818
   - Google Blog: https://research.google/blog/chain-of-agents-large-language-models-collaborating-on-long-context-tasks/

9. **MUSE: Experience-Driven Self-Evolving Agent** (Oct 2025)
   - https://arxiv.org/abs/2510.08002

10. **DeepSeek-R1** (DeepSeek 2025)
    - https://arxiv.org/abs/2501.12948
    - 开源权重: https://github.com/deepseek-ai/deepseek-r1

11. **Memp: Exploring Agent Procedural Memory** (2025)
    - https://arxiv.org/html/2508.06433v2

12. **GoalAct: Global Planning + Hierarchical Execution** (2025)
    - https://arxiv.org/html/2504.16563v2

### 工业系统参考

13. **Claude Code Architecture**
    - 官方 Context Engineering: https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
    - 社区深度分析: https://github.com/FlorianBruniaux/claude-code-ultimate-guide

14. **Manus Architecture Deep Dive**
    - https://www.theunwindai.com/p/architecture-behind-manus-ai-agent
    - 综合对比: https://cloud.tencent.com/developer/article/2662894

15. **OpenManus** (MetaGPT 团队 2025)
    - https://github.com/FoundationAgents/OpenManus

### 评测基准

16. **GAIA2 / SWE-Bench Pro / SWE-Evo**（2025-2026 长时程评测）
   - SWE-Evo: https://arxiv.org/html/2512.18470v5

17. **The 2025 AI Agent Index** (30 SOTA agents 编录)
   - https://arxiv.org/html/2602.17753v1

---

## 七、本次调研的主要发现

1. **2025 年最大转折**: 范式重心从"**外部编排**"（Pipeline）转向"**模型内化**"（o1/R1/K2）。本项目作为"教学用 Pipeline 参考实现"，反而因其**可解释性强、分层清晰**，在模型内化浪潮下**教学价值变得更高**——学生可以通过阅读项目代码，理解"模型内化之前业界是怎么做的"。

2. **CodeAct 是本年度 ROI 最高的升级**: 几乎没有哪个主流工业系统（Manus / OpenManus / Claude Code）不用 CodeAct，而本项目恰恰还是纯 JSON tool-calling。补齐这一块是 v9 的首要目标。

3. **LATS 和 ReflAct 的组合未被充分探索**: ReflAct 是"每轮**结构化**反思"，LATS 是"**多分支**反思"。二者正交，结合点在"树搜索每个扩展节点都做一次 ReflAct 式状态对比"。这是**可以在本项目里先做出来、再发论文**的组合点。

4. **Self-critique paradox 对 v8 是重要警示**: Snorkel AI 2025 的实验表明同模型 self-refine 在简单任务上反而有害。v8 的 `_goal_reflect` 正好踩中这个陷阱——建议以 Critic Agent 改造作为 v8.1 快速修补。

5. **模型内化对照组很关键**: 仅在项目里堆叠越来越多的外部 Pipeline 范式（v1→v10），而不设"扔给 reasoning model 一把梭"的对照组，会让教学项目**与真实业界脱节**。v11 建议加一条 `ENABLE_MODEL_NATIVE` 直连 R1/o3 的实验路径。

---

## 八、下一步动作

- 📄 **调研报告**: 本篇 + `02-核心论文摘要卡片.md` + `03-v9融入路线图.md`
- 📥 **待下载保存**: 如需要全文 PDF，建议下载：
  - ReflAct (v2) PDF → `./papers/2505.15182_ReflAct.pdf`
  - CodeAct ICML 2024 PDF
  - LATS ICML 2024 PDF
  - MUSE 2025.10 PDF
  - Model-native Survey 2025.10 PDF
- 🛠️ **代码实验**: 按 `03-v9融入路线图.md` 按顺序实现 S1 / S2 / A2 三个范式的 v9 里程碑
