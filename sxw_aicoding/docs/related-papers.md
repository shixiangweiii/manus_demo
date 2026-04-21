
# 混合规划路由相关论文综述

> 本文档汇总与本项目混合规划路由机制相关的学术论文，
> 按关联度分为六个类别，并标注推荐阅读优先级。

---

## 一、任务/查询路由（最直接相关）

这些论文解决的核心问题和我们一样：**根据输入的特征，决定走哪条处理路径**。

### 1.1 RouteLLM — 基于偏好数据学习路由

- **标题**: RouteLLM: Learning to Route LLMs from Preference Data
- **会议**: ICLR 2025
- **链接**: [https://arxiv.org/abs/2406.18665](https://arxiv.org/abs/2406.18665)
- **核心思想**: 从人类偏好数据训练路由分类器，动态决定将查询发送到强模型（如 GPT-4）还是弱模型（如 Mixtral），实现推理成本降低 2x 以上且不牺牲响应质量。路由器具备强迁移学习能力，对未参与训练的 LLM 对也能保持性能。
- **与项目关联**: 我们三路由输出（simple/complex/emergent）的灵感来源——"不是所有查询都需要最强的处理方式"。v4/v5 的两阶段分类器（规则快筛 + LLM兜底）与 RouteLLM 的思想一致，但我们用规则启发式替代了训练型路由器。

### 1.2 DAAO — 难度感知的智能体编排

- **标题**: Difficulty-Aware Agent Orchestration in LLM-Powered Workflows
- **会议**: ICLR 2025
- **链接**: [https://arxiv.org/abs/2509.11079](https://arxiv.org/abs/2509.11079)
- **核心思想**: 包含三个模块：(1) VAE 将查询映射为 [0,1] 难度分数；(2) 模块化算子分配器按难度选择协作协议（CoT / 多 Agent 辩论等）；(3) 成本-性能感知的 LLM 路由器。难度估计基于工作流成败自迭代更新。在 6 个基准上超越现有多 Agent 系统。
- **与项目关联**: "先低成本估难度，再分配资源"的设计依据。我们的 `rule_classify()` 机制（多步词、条件词、动作动词检测）是其 VAE 的简化版——用规则特征替代神经网络难度估计。

### 1.3 FrugalGPT — LLM 级联降本开山之作

- **标题**: FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
- **作者**: Lingjiao Chen, Matei Zaharia, James Zou (Stanford)
- **年份**: 2023
- **链接**: [https://arxiv.org/abs/2305.05176](https://arxiv.org/abs/2305.05176)
- **核心思想**: 提出三种 LLM 成本优化策略——prompt 适配、LLM 近似、LLM 级联。FrugalGPT 实现了级联策略：按成本从低到高依次尝试模型，直到质量满意为止。可在保持 GPT-4 效果的同时降低 98% 成本，或同等成本下提升 4% 准确率。
- **与项目关联**: **开山之作**，奠定了"不同查询需要不同级别处理"的理论基础。我们的 simple/complex/v5-emergent 三路由是级联思想的工程实现。
- **代码**: [https://github.com/stanford-futuredata/FrugalGPT](https://github.com/stanford-futuredata/FrugalGPT)

### 1.4 AutoMix — 自验证驱动的模型混合

- **标题**: AutoMix: Automatically Mixing Language Models
- **会议**: NeurIPS 2024
- **链接**: [https://arxiv.org/abs/2310.12963](https://arxiv.org/abs/2310.12963)
- **核心思想**: 小模型先回答 → few-shot 自验证评估置信度 → 不确定时升级到大模型。使用 POMDP 元验证器修正噪声验证信号。无需微调或模型权重访问，纯黑盒 API 即可。成本降低 50%+，增量收益/成本比提升 57-86%。
- **与项目关联**: 与我们"规则先筛 → LLM 兜底"的两阶段思路**高度类似**——都是先用低成本手段判断，不确定再升级。我们的 `classify_task()` 方法正是这种思想的实现。
- **代码**: [https://github.com/automix-llm/automix](https://github.com/automix-llm/automix)

### 1.5 Routesplain — 基于概念的可解释路由

- **标题**: Routesplain: Towards Faithful and Intervenable Routing for Software-related Tasks
- **投稿**: ICLR 2026
- **链接**: [https://arxiv.org/abs/2511.09373](https://arxiv.org/abs/2511.09373)
- **核心思想**: 从查询中提取人类可解释的概念（任务类型、领域、推理复杂度），仅基于概念进行路由。在 16 个 SOTA LLM 和 8 个软件工程任务上验证，性能超越所有黑盒路由方法，且支持概念级干预以改进路由。
- **与项目关联**: 我们的规则快筛本质上就是一种手工概念提取（多步词、条件词、动作动词等）。Routesplain 将其系统化和自动化，未来可考虑用 LLM 自动提取概念替代手工规则。

### 1.6 xRouter — RL 训练的成本感知路由

- **标题**: xRouter: Training Cost-Aware LLMs Orchestration System via Reinforcement Learning
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2510.08439](https://arxiv.org/abs/2510.08439)
- **核心思想**: 用 RL 端到端训练路由器 Agent（基于 Qwen2.5-7B），奖励函数显式编码成本-性能权衡：`R = R_binary × (K - λC)`。路由器通过工具调用机制决定直接回答还是调用外部模型。
- **与项目关联**: 如果要将当前的规则分类升级为学习型路由器，xRouter 是直接参考方案。当前 v4/v5 选择规则路由是为了可解释性和零训练成本。

### 1.7 Router-R1 — 多轮路由与聚合

- **标题**: Router-R1: Teaching LLMs Multi-Round Routing and Aggregation via Reinforcement Learning
- **会议**: NeurIPS 2025
- **链接**: [https://arxiv.org/abs/2506.09033](https://arxiv.org/abs/2506.09033)
- **核心思想**: 路由器本身是 LLM，交替执行"思考"和"路由"动作，可在多轮交互中动态调用多个模型并聚合结果。仅基于模型描述符（价格、延迟、示例性能）训练，对未见模型有强泛化能力。
- **与项目关联**: 多轮对话场景下的路由升级方向——当前 v4 是单次分类，未来可扩展为多轮自适应路由。
- **代码**: [https://github.com/ulab-uiuc/Router-R1](https://github.com/ulab-uiuc/Router-R1)

### 1.8 GraphPlanner — 图神经网络驱动的多 Agent 路由

- **标题**: GraphPlanner: Graph-Based Agentic Routing for LLMs
- **投稿**: ICLR 2026
- **链接**: [https://openreview.net/forum?id=ZdGB7MNQDT](https://openreview.net/forum?id=ZdGB7MNQDT)
- **核心思想**: 将工作流生成建模为 MDP，使用异构图 GARNet 捕获 query-agent-response 交互，RL 联合优化任务性能和计算效率。在 14 个任务上准确率提升 9.3%，GPU 使用从 186 GiB 降至 1 GiB。
- **与项目关联**: 将路由扩展到多 Agent 图协作场景的前沿方案，远期可参考。

---

## 二、多Agent协作与规划

这些论文关注**多个智能体如何协作完成任务**，与我们的 v2 DAG 规划器架构相关。

### 2.1 RP-ReAct — 解耦推理-规划与执行

- **标题**: Reason-Plan-ReAct: A Reasoner-Planner Supervising a ReAct Executor for Complex Enterprise Tasks
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2512.03560](https://arxiv.org/abs/2512.03560)
- **核心思想**: 将战略规划（Reasoner-Planner Agent）和低层执行（Proxy-Execution Agent）解耦为独立 Agent。RPA 用大推理模型持续分析执行结果，PEA 用 ReAct 将抽象步骤翻译为工具调用。外部存储策略解决上下文窗口溢出问题。
- **与项目关联**: 与我们 Planner/Executor 分离架构一致，但更强调企业场景下的上下文管理和数据隐私。我们的 v2 DAG 规划器采用了类似的分层设计。

### 2.2 MAP — 脑科学启发的模块化规划

- **标题**: Improving Planning with Large Language Models: A Modular Agentic Architecture
- **期刊**: Nature Communications, 2025
- **链接**: [https://www.nature.com/articles/s41467-025-63804-5](https://www.nature.com/articles/s41467-025-63804-5)
- **核心思想**: 基于认知神经科学的模块化架构：Planner + Executor + Verifier + Generator 通过共享内存协作。灵感来自冲突监控、状态预测、状态评估等认知过程。在图遍历、汉诺塔、PlanBench 上显著超越 CoT 和 ToT 基线，且对小模型（Llama3-70B）也有效。
- **与项目关联**: 验证了"模块化规划"思路的有效性。发表在 Nature 子刊，学术权威性高。我们的架构（Planner/Executor/Reflector）与之呼应。

### 2.3 ADaPT — "先试后分解"

- **标题**: ADaPT: As-Needed Decomposition and Planning with Language Models
- **会议**: NAACL 2024 Findings
- **作者**: Allen Institute for AI
- **链接**: [https://arxiv.org/abs/2311.05772](https://arxiv.org/abs/2311.05772)
- **核心思想**: 不做前置分类，先让 executor 直接执行任务，失败了才调 planner 递归分解为 AND/OR 子任务树。在 ALFWorld (+28.3%)、WebShop (+27%)、TextCraft (+33%) 上大幅超越基线。
- **与项目关联**: 我们在调研阶段考虑过的替代方案（先试后分解），最终选择了前置分类方案。ADaPT 的优点是永远不会误分类，但缺点是复杂任务会浪费一次失败的简单尝试。理解它有助于论证我们的设计选择。
- **代码**: [https://github.com/archiki/ADaPT](https://github.com/archiki/ADaPT)

### 2.4 STRIDE — 系统化 AI 模态选择

- **标题**: STRIDE: A Systematic Framework for Selecting AI Modalities — Agentic AI, AI Assistants, or LLM Calls
- **会议**: NeurIPS 2025
- **链接**: [https://arxiv.org/abs/2512.02228](https://arxiv.org/abs/2512.02228)
- **核心思想**: 五维度评估框架（任务分解结构、动态推理需求、工具交互评分、自反思需求、智能体适用性），决定任务应该用：(1) 直接 LLM 调用；(2) AI 助手；(3) 自主 Agent。30 个真实任务验证，92% 分类准确率，减少 45% 不必要的 Agent 部署，节省 37% 成本。
- **与项目关联**: **最接近我们的目标**——在 Agent（复杂）和简单调用之间做选择。STRIDE 是 3 级分类（LLM / Assistant / Agent），我们现在是 3 级（simple / complex / emergent）。如果要升级评估维度，STRIDE 是首选参考。

---

## 三、自适应执行与容错

这些论文关注**执行过程中的动态调整和错误恢复**，与我们的 Reflector 和自适应机制相关。

### 3.1 CogRouter — 步级认知深度自适应

- **标题**: Think Fast and Slow: Step-Level Cognitive Depth Adaptation for LLM Agents
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2602.12662](https://arxiv.org/abs/2602.12662)
- **核心思想**: 基于 ACT-R 认知理论定义 4 层认知深度（本能反应 → 策略规划），通过两阶段训练（CoSFT + CoPO）让 Agent 在每步动态选择认知深度。Qwen2.5-7B 胜过 GPT-4o (+40.3%) 和 o3 (+18.3%)，省 62% token。
- **与项目关联**: 最贴近"按步调整复杂度"的实现。如果 v4 要做步级别的动态调整（不仅仅是任务级分类），这是首选参考。
- **代码**: [https://github.com/rhyang2021/CogRouter](https://github.com/rhyang2021/CogRouter)

### 3.2 HiPO — 混合策略优化

- **标题**: HiPO: Hybrid Policy Optimization for Dynamic Reasoning in LLMs
- **年份**: 2025
- **链接**: [https://openreview.net/forum?id=txF1Z2cVMZ](https://openreview.net/forum?id=txF1Z2cVMZ)
- **核心思想**: 构建 Think-on / Think-off 配对数据，用混合 RL 奖励系统让 LLM 学会何时启用深度推理、何时直接回答。在数学、编码、通识任务上显著减少 token 同时保持准确率。
- **与项目关联**: 与我们"simple 直接执行 / complex 深度规划"的二分思想一致，但在模型内部实现而非架构层面。

### 3.3 Plan-and-Budget — 分解 + 预算分配

- **标题**: Plan and Budget: Effective and Efficient Test-Time Scaling on Large Language Model Reasoning
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2505.16122](https://arxiv.org/abs/2505.16122)
- **核心思想**: 两阶段框架：(1) 将查询分解为子问题；(2) 按估计复杂度用衰减调度器（线性/多项式/余弦）分配 token 预算。提出 BBAM 贝叶斯预算模型和 E³ 效率度量（Accuracy² / Tokens）。准确率 +70%，token -39%。
- **与项目关联**: 如果想在 v1 扁平路径内部也做 token 预算优化，这是直接参考。

### 3.4 SelfBudgeter — 自主 Token 预算

- **标题**: SelfBudgeter: Adaptive Token Allocation for Efficient LLM Reasoning
- **年份**: 2025
- **链接**: [https://openreview.net/forum?id=e7EBzbi8Qd](https://openreview.net/forum?id=e7EBzbi8Qd)
- **核心思想**: 双阶段训练让 LLM 自主决定输出长度，实现 48-61% 响应长度压缩，不显著损失质量。
- **与项目关联**: 更细粒度的 token 节省方案，可与我们的路由机制正交组合。

### 3.5 Thinkless — 激进的"少思考"方案

- **标题**: Thinkless: LLM Learns When to Think
- **年份**: 2025
- **链接**: [https://arxiv.org/abs/2505.13379](https://arxiv.org/abs/2505.13379)
- **核心思想**: 解耦组相对策略优化（Decoupled Group RPO），让 LLM 自主决定何时启用扩展推理。减少 50-90% 的长链思维使用，维持大部分性能。
- **与项目关联**: 如果想让 LLM 本身（而非外部分类器）决定推理深度的参考。

---

## 四、知识检索与RAG

这些论文关注**如何有效检索和利用外部知识**，与我们的 ContextManager 和 RAG 机制相关。

### 4.1 ReAct — 推理与行动的协同

- **标题**: ReAct: Synergizing Reasoning and Acting in Language Models
- **会议**: ICLR 2023
- **链接**: [https://arxiv.org/abs/2210.03629](https://arxiv.org/abs/2210.03629)
- **核心思想**: 提出 ReAct 范式：交替进行"思考"（推理）和"行动"（工具调用），让 LLM 通过推理链生成任务规划，通过工具调用获取外部信息，并在观察结果后更新推理。在 HotpotQA 和 Fever 上显著超越纯 CoT 和纯行动基线。
- **与项目关联**: **基础范式**，我们的 v5 隐式规划器和 ReAct 循环都基于此思想。`EmergentPlannerAgent` 的 `while(has_pending_todos)` 主循环本质上是 ReAct 的工程实现。
- **代码**: [https://github.com/ysymyth/reAct](https://github.com/ysymyth/reAct)

### 4.2 Toolformer — 工具调用的自我监督学习

- **标题**: Toolformer: Language Models Can Teach Themselves to Use Tools
- **会议**: ICLR 2023
- **链接**: [https://arxiv.org/abs/2302.04761](https://arxiv.org/abs/2302.04761)
- **核心思想**: 提出自我监督学习框架，让 LLM 自己决定何时调用 API（计算器、搜索引擎、翻译器等）。通过在文本中插入 API 调用占位符，让模型自标注哪些位置需要工具，然后微调模型学会自主决策。在多个任务上显著提升性能。
- **与项目关联**: 验证了"LLM 自主工具调用"的可行性。我们的 `ToolRouter` 和 ReAct 循环让 LLM 自主选择工具，与 Toolformer 的思想一致，但我们是推理时决策而非训练时学习。
- **代码**: [https://github.com/lucidrains/toolformer-pytorch](https://github.com/lucidrains/toolformer-pytorch)

### 4.3 Inner Monologue — 内心独白与自我反思

- **标题**: Inner Monologue: Embodied Reasoning through Planning with Language Models
- **会议**: CoRL 2022
- **链接**: [https://arxiv.org/abs/2207.05608](https://arxiv.org/abs/2207.05608)
- **核心思想**: 提出内心独白机制：LLM 在执行过程中持续生成内部推理（不输出给用户），用于规划下一步、评估当前状态、检测错误。在具身机器人任务上显著提升成功率。
- **与项目关联**: 我们的 `Reflector` 和 TODO 列表更新机制与之呼应——LLM 在执行过程中持续反思和调整计划，而非一次性规划完成。
- **代码**: [https://github.com/askforalfred/alfred](https://github.com/askforalfred/alfred)

### 4.4 Reflexion — 自我反思的错误修复

- **标题**: Reflexion: Language Agents with Verbal Reinforcement Learning
- **会议**: NeurIPS 2023
- **链接**: [https://arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366)
- **核心思想**: 提出反思机制：Agent 执行失败后，用 LLM 生成"反思文本"（错误原因分析、改进建议），将其作为上下文重新规划。在 AlfWorld、HotpotQA、HumanEval 上大幅超越基线。
- **与项目关联**: 我们的 `Reflector` 模块正是基于此思想——当任务失败或需要调整时，生成反思反馈并触发计划自适应（`PlanAdaptation`）。
- **代码**: [https://github.com/noahshinn024/reflexion](https://github.com/noahshinn024/reflexion)

---

## 五、隐式规划与涌现行为

这些论文关注**如何通过动态交互而非预定义计划实现复杂任务**，与我们的 v5 隐式规划器直接相关。

### 5.1 ReAct — 推理与行动的协同（重复引用）

- **标题**: ReAct: Synergizing Reasoning and Acting in Language Models
- **会议**: ICLR 2023
- **链接**: [https://arxiv.org/abs/2210.03629](https://arxiv.org/abs/2210.03629)
- **核心思想**: 交替进行"思考"（推理）和"行动"（工具调用），让 LLM 通过推理链生成任务规划，通过工具调用获取外部信息，并在观察结果后更新推理。
- **与 v5 隐式规划关联**: **核心理论基础**。`EmergentPlannerAgent` 的 `while(has_pending_todos)` 主循环正是 ReAct 的工程实现——每轮迭代都包含：选择 TODO → 思考 → 调用工具 → 观察结果 → 更新 TODO。规划不是预先生成的，而是在工具调用过程中自然涌现。

### 5.2 Toolformer — 工具调用的自我监督学习（重复引用）

- **标题**: Toolformer: Language Models Can Teach Themselves to Use Tools
- **会议**: ICLR 2023
- **链接**: [https://arxiv.org/abs/2302.04761](https://arxiv.org/abs/2302.04761)
- **核心思想**: 自我监督学习框架，让 LLM 自己决定何时调用 API。通过在文本中插入 API 调用占位符，让模型自标注哪些位置需要工具。
- **与 v5 隐式规划关联**: 验证了"LLM 自主工具调用"的可行性。我们的 `ToolRouter` 和 ReAct 循环让 LLM 在执行过程中动态决定调用哪个工具，而非预先规划好工具调用序列。

### 5.3 Inner Monologue — 内心独白与自我反思（重复引用）

- **标题**: Inner Monologue: Embodied Reasoning through Planning with Language Models
- **会议**: CoRL 2022
- **链接**: [https://arxiv.org/abs/2207.05608](https://arxiv.org/abs/2207.05608)
- **核心思想**: LLM 在执行过程中持续生成内部推理（不输出给用户），用于规划下一步、评估当前状态、检测错误。
- **与 v5 隐式规划关联**: TODO 列表的动态更新机制与之呼应——LLM 在执行过程中通过"内心独白"（系统提示词引导）持续反思和调整 TODO，而非一次性规划完成。

### 5.4 Reflexion — 自我反思的错误修复（重复引用）

- **标题**: Reflexion: Language Agents with Verbal Reinforcement Learning
- **会议**: NeurIPS 2023
- **链接**: [https://arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366)
- **核心思想**: Agent 执行失败后，用 LLM 生成"反思文本"（错误原因分析、改进建议），将其作为上下文重新规划。
- **与 v5 隐式规划关联**: TODO 列表的失败重试机制——当某个 TODO 执行失败时，系统将其状态回退为 PENDING 以便重试，这与 Reflexion 的"失败后反思重试"思想一致。

### 5.5 Chain-of-Thought — 思维链推理

- **标题**: Chain-of-Thought Prompting Elicits Reasoning in Large Language Models
- **会议**: NeurIPS 2022
- **链接**: [https://arxiv.org/abs/2201.11903](https://arxiv.org/abs/2201.11903)
- **核心思想**: 通过在 prompt 中提供推理示例（"Let's think step by step"），激发 LLM 生成显式推理链，大幅提升复杂推理任务性能。
- **与 v5 隐式规划关联**: 我们的系统提示词 `EMERGENT_PLANNER_SYSTEM_PROMPT` 要求 LLM "Review the current TODO list and select the next actionable item" 和 "Reason about what to do"，这正是 CoT 思想在隐式规划中的应用。

### 5.6 Tree-of-Thoughts — 树状思维探索

- **标题**: Tree of Thoughts: Deliberate Problem Solving with Large Language Models
- **会议**: NeurIPS 2023
- **链接**: [https://arxiv.org/abs/2305.10601](https://arxiv.org/abs/2305.10601)
- **核心思想**: 将推理过程建模为树状结构，每个节点是一个"思维"，通过搜索（BFS/DFS/启发式）探索多条路径并评估，选择最优路径。在 24 个任务上显著超越 CoT。
- **与 v5 隐式规划关联**: TODO 列表的动态演化可以看作是一种简化的树状探索——当发现新工作时会添加新的 TODO，形成分支。未来可考虑将 TODO 列表升级为树状结构以支持更复杂的探索。

### 5.7 AutoGPT — 自主任务分解与执行

- **标题**: Autonomous GPT-4: An Autonomous Agent for Complex Task Execution
- **年份**: 2023
- **链接**: [https://github.com/Significant-Gravitas/AutoGPT](https://github.com/Significant-Gravitas/AutoGPT)
- **核心思想**: 给定一个高层目标，AutoGPT 自主分解为子任务，循环执行：思考 → 规划 → 执行 → 评估 → 修正。使用向量数据库存储记忆，支持长期上下文。
- **与 v5 隐式规划关联**: 与我们的 `EmergentPlannerAgent` 架构高度相似——都是给定任务后自主分解为 TODO 列表，然后循环执行。AutoGPT 更强调记忆和自主性，我们更强调工具调用的可控性。
- **代码**: [https://github.com/Significant-Gravitas/AutoGPT](https://github.com/Significant-Gravitas/AutoGPT)

### 5.8 BabyAGI — 任务驱动的自主 Agent

- **标题**: BabyAGI: A Task-Driven Autonomous Agent
- **年份**: 2023
- **链接**: [https://github.com/yoheinakajima/babyagi](https://github.com/yoheinakajima/babyagi)
- **核心思想**: 给定一个目标和初始任务，BabyAGI 循环执行：从任务列表取下一个任务 → 用 LLM 生成新任务 → 执行任务 → 根据结果丰富任务列表。使用向量数据库存储上下文。
- **与 v5 隐式规划关联**: TODO 列表的动态管理机制与之呼应——都是通过循环执行和动态添加任务来推进目标。BabyAGI 更强调任务生成，我们更强调任务完成和工具调用。
- **代码**: [https://github.com/yoheinakajima/babyagi](https://github.com/yoheinakajima/babyagi)

---

## 六、LLM 可靠性与重试机制

这些论文关注**如何提高 LLM API 调用的可靠性**，与我们的 v6 retry 机制直接相关。

### 6.1 Exponential Backoff — 指数退避重试

- **标题**: Designing Applications for the Cloud: Best Practices for Reliability and Scalability
- **作者**: Amazon Web Services
- **年份**: 2012
- **链接**: [https://docs.aws.amazon.com/whitepapers/latest/designing-applications-for-the-cloud/best-practices-for-reliability-and-scalability.html](https://docs.aws.amazon.com/whitepapers/latest/designing-applications-for-the-cloud/best-practices-for-reliability-and-scalability.html)
- **核心思想**: 指数退避是一种重试策略，在失败后等待时间按指数增长（如 1s, 2s, 4s, 8s...），避免在系统过载时雪崩。是分布式系统可靠性设计的标准实践。
- **与 v6 retry 关联**: **直接实现**。我们的 `LLMClient` 中的重试逻辑使用指数退避：`wait_time = self.backoff_factor ** attempt`。这是处理 RateLimitError、APITimeoutError 等瞬态错误的标准方法。

### 6.2 Retry with Jitter — 带抖动的重试

- **标题**: Exponential Backoff And Jitter
- **作者**: Google Cloud
- **年份**: 2019
- **链接**: [https://cloud.google.com/architecture/general-design-practices#handle_retries_with_exponential_backoff](https://cloud.google.com/architecture/general-design-practices#handle_retries_with_exponential_backoff)
- **核心思想**: 在指数退避基础上加入随机抖动（如 `wait_time = base * (2^attempt) + random()`），避免多个客户端同时重试导致的"惊群效应"（thundering herd problem）。
- **与 v6 retry 关联**: 未来优化方向。当前 v6 使用纯指数退避，未来可考虑加入抖动以提升大规模并发场景下的稳定性。

### 6.3 Circuit Breaker — 熔断器模式

- **标题**: Circuit Breaker Pattern
- **作者**: Microsoft Azure
- **年份**: 2020
- **链接**: [https://docs.microsoft.com/en-us/previous-versions/msp-n-p/ff650767(v=pandp.10)](https://docs.microsoft.com/en-us/previous-versions/msp-n-p/ff650767(v=pandp.10))
- **核心思想**: 熔断器模式在连续失败达到阈值后"熔断"（直接返回错误，不再调用），避免持续失败浪费资源。一段时间后"半开"（尝试少量调用），成功则"关闭"（恢复正常），失败则继续"熔断"。
- **与 v6 retry 关联**: 未来增强方向。当前 v6 仅在单次调用内重试，未来可考虑实现熔断器以应对 LLM 服务长时间不可用的情况。

### 6.4 Rate Limiting — 速率限制

- **标题**: Rate Limiting Best Practices
- **作者**: Stripe
- **年份**: 2022
- **链接**: [https://stripe.com/blog/rate-limiters](https://stripe.com/blog/rate-limiters)
- **核心思想**: 速率限制通过令牌桶、漏桶等算法控制请求速率，避免超过服务承载能力。分布式系统中常用的可靠性保障机制。
- **与 v6 retry 关联**: 配合使用。v6 的重试机制在触发 RateLimitError 时会自动退避，但更好的做法是在客户端侧主动实现速率限制，避免触发限流。

### 6.5 Timeout and Deadlines — 超时与截止时间

- **标题**: Timeouts, Deadlines, and Retries
- **作者**: Google Cloud
- **年份**: 2019
- **链接**: [https://cloud.google.com/architecture/general-design-practices#set_timeouts_and_deadlines](https://cloud.google.com/architecture/general-design-practices#set_timeouts_and_deadlines)
- **核心思想**: 为所有外部调用设置合理的超时时间，避免无限等待。对于长时间任务，设置截止时间（deadline），超时后主动失败而非无限重试。
- **与 v6 retry 关联**: 我们的 `LLMClient` 使用 OpenAI SDK 的默认超时，未来可考虑根据任务类型设置差异化超时（如简单任务 10s，复杂任务 60s）。

### 6.6 Idempotency — 幂等性设计

- **标题**: Idempotency Best Practices
- **作者**: Stripe
- **年份**: 2022
- **链接**: [https://stripe.com/blog/idempotency](https://stripe.com/blog/idempotency)
- **核心思想**: 幂等性指多次调用产生相同结果。对于重试场景，必须保证操作是幂等的（如使用唯一请求 ID），避免重复执行导致副作用。
- **与 v6 retry 关联**: 当前 v6 的重试是"重试整个 LLM 调用"，这是幂等的（LLM 调用无副作用）。但如果未来重试范围扩大到工具调用，需要考虑幂等性设计。

---

## 七、推荐阅读优先级

按与本项目实现的关联度和实用价值排序：

| 优先级       | 论文                                 | 理由                                |
| --------- | ---------------------------------- | --------------------------------- |
| **P0 必读** | **ReAct** (ICLR 2023)              | v5 隐式规划的核心理论基础                    |
| **P0 必读** | **STRIDE** (NeurIPS 2025)          | 最贴近我们的需求——什么时候需要 Agent，什么时候简单调用就够 |
| **P0 必读** | FrugalGPT (Stanford 2023)          | 开山鼻祖，奠定"级联路由降本"理论基础               |
| **P0 必读** | AutoMix (NeurIPS 2024)             | 与我们"两阶段"设计最相似：先低成本验证，不确定再升级       |
| **P1 推荐** | CogRouter (2025)                   | 做更细粒度的步级认知深度调整                    |
| **P1 推荐** | ADaPT (NAACL 2024)                 | 我们方案的主要替代方案，理解它有助于论证设计选择          |
| **P1 推荐** | Plan-and-Budget (2025)             | 进一步优化 token 预算分配                  |
| **P1 推荐** | Reflexion (NeurIPS 2023)           | Reflector 模块的理论基础                   |
| **P1 推荐** | Toolformer (ICLR 2023)             | 验证 LLM 自主工具调用的可行性                   |
| **P2 扩展** | RouteLLM (ICLR 2025)               | 学习型路由的代表，未来升级方向                   |
| **P2 扩展** | DAAO (ICLR 2025)                   | 难度感知编排的代表，规则分类的理论依据              |
| **P2 扩展** | Routesplain (2025)                 | 可解释路由的方向                          |
| **P2 扩展** | MAP (Nature Comms 2025)            | 模块化规划的学术权威验证                      |
| **P2 扩展** | Inner Monologue (CoRL 2022)        | TODO 动态更新的理论基础                     |
| **P3 前沿** | xRouter / Router-R1 / GraphPlanner | RL 训练型路由器的前沿探索，远期参考               |
| **P3 前沿** | HiPO / SelfBudgeter / Thinkless    | 模型内部自适应推理深度，正交方向                  |
| **P3 前沿** | AutoGPT / BabyAGI                  | 自主 Agent 的工程实践参考                    |
| **P3 前沿** | Tree-of-Thoughts (NeurIPS 2023)    | TODO 列表升级为树状结构的参考                 |
| **P4 工程** | Exponential Backoff / Circuit Breaker | v6 retry 机制的工程实践参考                  |

---

## 八、与项目设计选择的对照

我们在项目中做出的关键设计选择，以及对应的学术依据：

| 设计选择                | 学术依据                                         |
| ------------------- | -------------------------------------------- |
| 三路由输出（simple/complex/emergent） | RouteLLM 的强/弱模型二分；FrugalGPT 的级联策略；STRIDE 的多级分类 |
| 两阶段分类（规则 + LLM 兜底）  | AutoMix 的"小模型先试 → 大模型兜底"；Routesplain 的概念提取路由 |
| 前置分类而非后置升级          | 对比 ADaPT 的"先试后分解"，我们选择避免复杂任务浪费首次失败的开销        |
| 规则打分用多步词/条件词/动作动词   | Routesplain 的概念提取思想；STRIDE 的多维度评估框架          |
| v5 隐式规划的 while 循环    | ReAct 的"思考-行动"循环；Inner Monologue 的持续反思机制      |
| TODO 列表动态管理          | AutoGPT/BabyAGI 的任务列表机制；Reflexion 的失败重试        |
| v6 retry 指数退避       | AWS/Azure/Google Cloud 的分布式系统可靠性实践          |
| Planner/Executor/Reflector 分离 | MAP 的模块化架构；RP-ReAct 的推理-执行解耦             |
| 失败时默认降级为 complex    | FrugalGPT 的安全兜底原则——宁可多花成本也不错过质量              |
| PLAN_MODE 配置覆盖      | 工程实践标配，便于 A/B 测试和调试                          |

---

## 九、版本演进与论文关联

### v4 混合规划路由
- **核心创新**: 两阶段分类器（规则快筛 + LLM 兜底）自动选择 v1/v2
- **关联论文**: RouteLLM, DAAO, AutoMix, FrugalGPT, STRIDE
- **设计依据**: "60-70% 显然请求零成本处理，30-40% 模糊区间触发 LLM 分类"

### v5 隐式规划器（Claude Code 风格）
- **核心创新**: 无独立规划阶段，通过 TODO 列表和 while(tool_use) 循环自然涌现
- **关联论文**: ReAct, Toolformer, Inner Monologue, Reflexion, AutoGPT, BabyAGI
- **设计依据**: "规划在执行过程中自然涌现，而非预先完整规划"

### v6 LLM Retry 机制
- **核心创新**: 指数退避重试，支持 RateLimitError、APITimeoutError、APIError
- **关联论文**: Exponential Backoff (AWS), Circuit Breaker (Azure), Rate Limiting (Stripe)
- **设计依据**: "分布式系统可靠性标准实践，处理瞬态错误"

---

## 十、未来研究方向

基于当前实现和相关论文，值得探索的方向：

1. **从规则路由到学习型路由**: 参考 xRouter/Router-R1，用 RL 训练路由器替代规则分类
2. **从任务级到步级认知深度**: 参考 CogRouter，在执行过程中动态调整每步的推理深度
3. **从扁平 TODO 到树状探索**: 参考 Tree-of-Thoughts，将 TODO 列表升级为树状结构支持多路径探索
4. **从单次重试到熔断器**: 参考 Circuit Breaker Pattern，实现熔断器应对长时间服务不可用
5. **从指数退避到带抖动重试**: 参考 Google Cloud 的最佳实践，加入随机抖动避免惊群效应
6. **从概念提取到自动概念路由**: 参考 Routesplain，用 LLM 自动提取概念替代手工规则
7. **从二分路由到多级路由**: 参考 STRIDE，扩展为 3 级分类（LLM / Assistant / Agent）
8. **从隐式规划到混合规划**: 结合 v2 显式 DAG 和 v5 隐式规划，根据任务特征动态选择

---

**文档版本**: v6.0  
**最后更新**: 2026-04-20  
**维护者**: Aone Copilot
