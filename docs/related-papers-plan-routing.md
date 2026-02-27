# 混合规划路由相关论文综述

> 本文档汇总与 v4 混合规划路由机制相关的学术论文，
> 按与本项目的关联度分为四个类别，并标注推荐阅读优先级。

---

## 一、任务/查询路由（最直接相关）

这些论文解决的核心问题和我们一样：**根据输入的特征，决定走哪条处理路径**。

### 1.1 RouteLLM — 基于偏好数据学习路由

- **标题**: RouteLLM: Learning to Route LLMs from Preference Data
- **会议**: ICLR 2025
- **链接**: [https://arxiv.org/abs/2406.18665](https://arxiv.org/abs/2406.18665)
- **核心思想**: 从人类偏好数据训练路由分类器，动态决定将查询发送到强模型（如 GPT-4）还是弱模型（如 Mixtral），实现推理成本降低 2x 以上且不牺牲响应质量。路由器具备强迁移学习能力，对未参与训练的 LLM 对也能保持性能。
- **与 v4 关联**: 我们 Stage 2 LLM 分类的灵感来源——"不是所有查询都需要最强的处理方式"。

### 1.2 DAAO — 难度感知的智能体编排

- **标题**: Difficulty-Aware Agent Orchestration in LLM-Powered Workflows
- **会议**: ICLR 2025
- **链接**: [https://arxiv.org/abs/2509.11079](https://arxiv.org/abs/2509.11079)
- **核心思想**: 包含三个模块：(1) VAE 将查询映射为 [0,1] 难度分数；(2) 模块化算子分配器按难度选择协作协议（CoT / 多 Agent 辩论等）；(3) 成本-性能感知的 LLM 路由器。难度估计基于工作流成败自迭代更新。在 6 个基准上超越现有多 Agent 系统。
- **与 v4 关联**: "先低成本估难度，再分配资源"的设计依据。我们的规则打分机制是其 VAE 的简化版。

### 1.3 FrugalGPT — LLM 级联降本开山之作

- **标题**: FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
- **作者**: Lingjiao Chen, Matei Zaharia, James Zou (Stanford)
- **年份**: 2023
- **链接**: [https://arxiv.org/abs/2305.05176](https://arxiv.org/abs/2305.05176)
- **核心思想**: 提出三种 LLM 成本优化策略——prompt 适配、LLM 近似、LLM 级联。FrugalGPT 实现了级联策略：按成本从低到高依次尝试模型，直到质量满意为止。可在保持 GPT-4 效果的同时降低 98% 成本，或同等成本下提升 4% 准确率。
- **与 v4 关联**: **开山之作**，奠定了"不同查询需要不同级别处理"的理论基础。我们的 simple/complex 二分路由是级联思想的简化应用。
- **代码**: [https://github.com/stanford-futuredata/FrugalGPT](https://github.com/stanford-futuredata/FrugalGPT)

### 1.4 AutoMix — 自验证驱动的模型混合

- **标题**: AutoMix: Automatically Mixing Language Models
- **会议**: NeurIPS 2024
- **链接**: [https://arxiv.org/abs/2310.12963](https://arxiv.org/abs/2310.12963)
- **核心思想**: 小模型先回答 → few-shot 自验证评估置信度 → 不确定时升级到大模型。使用 POMDP 元验证器修正噪声验证信号。无需微调或模型权重访问，纯黑盒 API 即可。成本降低 50%+，增量收益/成本比提升 57-86%。
- **与 v4 关联**: 与我们"规则先筛 → LLM 兜底"的两阶段思路**高度类似**——都是先用低成本手段判断，不确定再升级。
- **代码**: [https://github.com/automix-llm/automix](https://github.com/automix-llm/automix)

### 1.5 Routesplain — 基于概念的可解释路由

- **标题**: Routesplain: Towards Faithful and Intervenable Routing for Software-related Tasks
- **投稿**: ICLR 2026
- **链接**: [https://arxiv.org/abs/2511.09373](https://arxiv.org/abs/2511.09373)
- **核心思想**: 从查询中提取人类可解释的概念（任务类型、领域、推理复杂度），仅基于概念进行路由。在 16 个 SOTA LLM 和 8 个软件工程任务上验证，性能超越所有黑盒路由方法，且支持概念级干预以改进路由。
- **与 v4 关联**: 我们的规则快筛本质上就是一种手工概念提取（多步词、条件词、动作动词等）。Routesplain 将其系统化和自动化。

### 1.6 xRouter — RL 训练的成本感知路由

- **标题**: xRouter: Training Cost-Aware LLMs Orchestration System via Reinforcement Learning
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2510.08439](https://arxiv.org/abs/2510.08439)
- **核心思想**: 用 RL 端到端训练路由器 Agent（基于 Qwen2.5-7B），奖励函数显式编码成本-性能权衡：`R = R_binary × (K - λC)`。路由器通过工具调用机制决定直接回答还是调用外部模型。
- **与 v4 关联**: 如果要将 v4 的规则分类升级为学习型路由器，xRouter 是直接参考方案。

### 1.7 Router-R1 — 多轮路由与聚合

- **标题**: Router-R1: Teaching LLMs Multi-Round Routing and Aggregation via Reinforcement Learning
- **会议**: NeurIPS 2025
- **链接**: [https://arxiv.org/abs/2506.09033](https://arxiv.org/abs/2506.09033)
- **核心思想**: 路由器本身是 LLM，交替执行"思考"和"路由"动作，可在多轮交互中动态调用多个模型并聚合结果。仅基于模型描述符（价格、延迟、示例性能）训练，对未见模型有强泛化能力。
- **与 v4 关联**: 多轮对话场景下的路由升级方向——当前 v4 是单次分类，未来可扩展为多轮自适应。
- **代码**: [https://github.com/ulab-uiuc/Router-R1](https://github.com/ulab-uiuc/Router-R1)

### 1.8 GraphPlanner — 图神经网络驱动的多 Agent 路由

- **标题**: GraphPlanner: Graph-Based Agentic Routing for LLMs
- **投稿**: ICLR 2026
- **链接**: [https://openreview.net/forum?id=ZdGB7MNQDT](https://openreview.net/forum?id=ZdGB7MNQDT)
- **核心思想**: 将工作流生成建模为 MDP，使用异构图 GARNet 捕获 query-agent-response 交互，RL 联合优化任务性能和计算效率。在 14 个任务上准确率提升 9.3%，GPU 使用从 186 GiB 降至 1 GiB。
- **与 v4 关联**: 将路由扩展到多 Agent 图协作场景的前沿方案，远期可参考。

---

## 二、自适应推理深度（"何时深思、何时快答"）

这些论文和我们的问题本质相同：**根据任务难度调整计算资源投入**。

### 2.1 CogRouter — 步级认知深度自适应

- **标题**: Think Fast and Slow: Step-Level Cognitive Depth Adaptation for LLM Agents
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2602.12662](https://arxiv.org/abs/2602.12662)
- **核心思想**: 基于 ACT-R 认知理论定义 4 层认知深度（本能反应 → 策略规划），通过两阶段训练（CoSFT + CoPO）让 Agent 在每步动态选择认知深度。Qwen2.5-7B 胜过 GPT-4o (+40.3%) 和 o3 (+18.3%)，省 62% token。
- **与 v4 关联**: 最贴近"按步调整复杂度"的实现。如果 v4 要做步级别的动态调整（不仅仅是任务级分类），这是首选参考。
- **代码**: [https://github.com/rhyang2021/CogRouter](https://github.com/rhyang2021/CogRouter)

### 2.2 HiPO — 混合策略优化

- **标题**: HiPO: Hybrid Policy Optimization for Dynamic Reasoning in LLMs
- **年份**: 2025
- **链接**: [https://openreview.net/forum?id=txF1Z2cVMZ](https://openreview.net/forum?id=txF1Z2cVMZ)
- **核心思想**: 构建 Think-on / Think-off 配对数据，用混合 RL 奖励系统让 LLM 学会何时启用深度推理、何时直接回答。在数学、编码、通识任务上显著减少 token 同时保持准确率。
- **与 v4 关联**: 与我们"simple 直接执行 / complex 深度规划"的二分思想一致，但在模型内部实现而非架构层面。

### 2.3 Plan-and-Budget — 分解 + 预算分配

- **标题**: Plan and Budget: Effective and Efficient Test-Time Scaling on Large Language Model Reasoning
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2505.16122](https://arxiv.org/abs/2505.16122)
- **核心思想**: 两阶段框架：(1) 将查询分解为子问题；(2) 按估计复杂度用衰减调度器（线性/多项式/余弦）分配 token 预算。提出 BBAM 贝叶斯预算模型和 E³ 效率度量（Accuracy² / Tokens）。准确率 +70%，token -39%。
- **与 v4 关联**: 如果想在 v1 扁平路径内部也做 token 预算优化，这是直接参考。

### 2.4 SelfBudgeter — 自主 Token 预算

- **标题**: SelfBudgeter: Adaptive Token Allocation for Efficient LLM Reasoning
- **年份**: 2025
- **链接**: [https://openreview.net/forum?id=e7EBzbi8Qd](https://openreview.net/forum?id=e7EBzbi8Qd)
- **核心思想**: 双阶段训练让 LLM 自主决定输出长度，实现 48-61% 响应长度压缩，不显著损失质量。
- **与 v4 关联**: 更细粒度的 token 节省方案，可与我们的路由机制正交组合。

### 2.5 Thinkless — 激进的"少思考"方案

- **标题**: Thinkless: LLM Learns When to Think
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2505.13379](https://arxiv.org/abs/2505.13379)
- **核心思想**: 解耦组相对策略优化（Decoupled Group RPO），让 LLM 自主决定何时启用扩展推理。减少 50-90% 的长链思维使用，维持大部分性能。
- **与 v4 关联**: 如果想让 LLM 本身（而非外部分类器）决定推理深度的参考。

---

## 三、自适应任务分解与规划

这些论文关注**任务规划本身如何自适应调整**，与我们的 v1/v2 双路径互补。

### 3.1 ADaPT — "先试后分解"

- **标题**: ADaPT: As-Needed Decomposition and Planning with Language Models
- **会议**: NAACL 2024 Findings
- **作者**: Allen Institute for AI
- **链接**: [https://arxiv.org/abs/2311.05772](https://arxiv.org/abs/2311.05772)
- **核心思想**: 不做前置分类，先让 executor 直接执行任务，失败了才调 planner 递归分解为 AND/OR 子任务树。在 ALFWorld (+28.3%)、WebShop (+27%)、TextCraft (+33%) 上大幅超越基线。
- **与 v4 关联**: 我们在调研阶段考虑过的**方案 D**（先试后分解），最终选择了前置分类方案。ADaPT 的优点是永远不会误分类，但缺点是复杂任务会浪费一次失败的简单尝试。理解它有助于论证我们的设计选择。
- **代码**: [https://github.com/archiki/ADaPT](https://github.com/archiki/ADaPT)

### 3.2 RP-ReAct — 解耦推理-规划与执行

- **标题**: Reason-Plan-ReAct: A Reasoner-Planner Supervising a ReAct Executor for Complex Enterprise Tasks
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2512.03560](https://arxiv.org/abs/2512.03560)
- **核心思想**: 将战略规划（Reasoner-Planner Agent）和低层执行（Proxy-Execution Agent）解耦为独立 Agent。RPA 用大推理模型持续分析执行结果，PEA 用 ReAct 将抽象步骤翻译为工具调用。外部存储策略解决上下文窗口溢出问题。
- **与 v4 关联**: 与我们 Planner/Executor 分离架构一致，但更强调企业场景下的上下文管理和数据隐私。

### 3.3 MAP — 脑科学启发的模块化规划

- **标题**: Improving Planning with Large Language Models: A Modular Agentic Architecture
- **期刊**: Nature Communications, 2025
- **链接**: [https://www.nature.com/articles/s41467-025-63804-5](https://www.nature.com/articles/s41467-025-63804-5)
- **核心思想**: 基于认知神经科学的模块化架构：Planner + Executor + Verifier + Generator 通过共享内存协作。灵感来自冲突监控、状态预测、状态评估等认知过程。在图遍历、汉诺塔、PlanBench 上显著超越 CoT 和 ToT 基线，且对小模型（Llama3-70B）也有效。
- **与 v4 关联**: 验证了"模块化规划"思路的有效性。发表在 Nature 子刊，学术权威性高。

### 3.4 STRIDE — 系统化 AI 模态选择

- **标题**: STRIDE: A Systematic Framework for Selecting AI Modalities — Agentic AI, AI Assistants, or LLM Calls
- **会议**: NeurIPS 2025
- **链接**: [https://arxiv.org/abs/2512.02228](https://arxiv.org/abs/2512.02228)
- **核心思想**: 五维度评估框架（任务分解结构、动态推理需求、工具交互评分、自反思需求、智能体适用性），决定任务应该用：(1) 直接 LLM 调用；(2) AI 助手；(3) 自主 Agent。30 个真实任务验证，92% 分类准确率，减少 45% 不必要的 Agent 部署，节省 37% 成本。
- **与 v4 关联**: **最接近我们 v4 的目标**——在 Agent（复杂）和简单调用之间做选择。STRIDE 是 3 级分类（LLM / Assistant / Agent），我们是 2 级（simple / complex）。如果要升级为 3 级路由，STRIDE 是首选参考。

---

## 四、推荐阅读优先级

按与 v4 实现的关联度和实用价值排序：


| 优先级       | 论文                                 | 理由                                |
| --------- | ---------------------------------- | --------------------------------- |
| **P0 必读** | **STRIDE** (NeurIPS 2025)          | 最贴近我们的需求——什么时候需要 Agent，什么时候简单调用就够 |
| **P0 必读** | FrugalGPT (Stanford 2023)          | 开山鼻祖，奠定"级联路由降本"理论基础               |
| **P0 必读** | AutoMix (NeurIPS 2024)             | 与我们"两阶段"设计最相似：先低成本验证，不确定再升级       |
| **P1 推荐** | CogRouter (2025)                   | 做更细粒度的步级认知深度调整                    |
| **P1 推荐** | ADaPT (NAACL 2024)                 | 我们方案的主要替代方案，理解它有助于论证设计选择          |
| **P1 推荐** | Plan-and-Budget (2025)             | 进一步优化 token 预算分配                  |
| **P2 扩展** | RouteLLM (ICLR 2025)               | 已在 v4 文档中引用，学习型路由的代表              |
| **P2 扩展** | DAAO (ICLR 2025)                   | 已在 v4 文档中引用，难度感知编排的代表             |
| **P2 扩展** | Routesplain (2025)                 | 可解释路由的方向                          |
| **P2 扩展** | MAP (Nature Comms 2025)            | 模块化规划的学术权威验证                      |
| **P3 前沿** | xRouter / Router-R1 / GraphPlanner | RL 训练型路由器的前沿探索，远期参考               |
| **P3 前沿** | HiPO / SelfBudgeter / Thinkless    | 模型内部自适应推理深度，正交方向                  |


---

## 五、与 v4 设计选择的对照

我们在 v4 中做出的关键设计选择，以及对应的学术依据：


| 设计选择                | 学术依据                                         |
| ------------------- | -------------------------------------------- |
| 两阶段分类（规则 + LLM 兜底）  | AutoMix 的"小模型先试 → 大模型兜底"；Routesplain 的概念提取路由 |
| 前置分类而非后置升级          | 对比 ADaPT 的"先试后分解"，我们选择避免复杂任务浪费首次失败的开销        |
| 规则打分用多步词/条件词/动作动词   | Routesplain 的概念提取思想；STRIDE 的多维度评估框架          |
| simple/complex 二分路由 | RouteLLM 的强/弱模型二分；FrugalGPT 的级联策略简化版         |
| 失败时默认降级为 complex    | FrugalGPT 的安全兜底原则——宁可多花成本也不错过质量              |
| PLAN_MODE 配置覆盖      | 工程实践标配，便于 A/B 测试和调试                          |


