# 多 Agent 架构全景与工业旗舰实践（2025–2026）

> **调研日期**: 2026-05-13
> **数据截止**: 2026-05-13（arXiv / Anthropic / Microsoft / Cognition / Cursor / OpenAI / LangChain 官方博客与 cookbook）
> **本文定位**: 聚焦"**多 Agent / Subagent 架构**"在 2025–2026 工业旗舰系统中的**架构决策与工程范式**，与 `02-核心论文摘要卡片.md` 的学术视角形成互补
> **阅读前置**: 建议先读 `01-业内最新Agent范式全景调研.md` 第二节范式分层地图，理解 L4 协作层在项目演进中的位置

---

## 零、一句话结论与总图

> **2025 的行业共识正在从"多 Agent 万能论"回摆到"慎用多 Agent、先做好单 Agent 上下文工程"**。Anthropic、Microsoft 往"结构化多 Agent"走；Cognition/Devin 公开反对；Cursor 用"并行沙盒 + 单 Agent 内核"折中。**两派争议的核心是同一件事：上下文如何在 agent 之间共享**。

五大拓扑 + 八大工业系统速览：

```
                 ┌────────────── Orchestrator-Worker ──────────────┐
                 │  Anthropic Research / Magentic-One / Claude Code │
  Supervisor ────┤                                                  │
                 │  Manus Leader / MetaGPT / CrewAI                 │
                 └──────────────────────────────────────────────────┘
                 ┌────────────── Handoff / Routine ────────────────┐
  Network    ────┤  OpenAI Swarm / OpenAI Agents SDK                │
                 └──────────────────────────────────────────────────┘
                 ┌────────────── Hierarchical ─────────────────────┐
  Hierarchical ──┤  Agent-S2/S3 / LangGraph Hierarchical            │
                 └──────────────────────────────────────────────────┘
                 ┌────────────── Pipeline / MoA ───────────────────┐
  Pipeline   ────┤  Chain-of-Agents / Mixture-of-Agents             │
                 └──────────────────────────────────────────────────┘
                 ┌────────────── Single-thread + Sandbox ──────────┐
  Anti-MA    ────┤  Devin / Cursor 2.0 Worktree + Composer          │
                 └──────────────────────────────────────────────────┘
```

---

## 一、Anthropic 双轨实践：Research System 与 Claude Code Subagent

Anthropic 是少数同时开源两套相反哲学的公司：
- **Research System（2025.06 博客）**：显式 orchestrator-worker 多 agent，适合**广度优先探索型**任务
- **Claude Code Subagent（2025.08 GA）**：Task-tool 触发的**隔离式 Subagent**，深度=1，适合**编码类长时程任务**

### 1.1 Anthropic Research System（多 Agent 正例）

统一模板：

| 维度 | 设计 |
|------|------|
| 定位 | 多源资料深度调研（突破单 context 窗口） |
| 通信拓扑 | Orchestrator-Worker（LeadResearcher + N SearchSubagents + CitationAgent） |
| Subagent 策略 | LeadResearcher **显式规划 → 并行分发 → 结果汇总**；Subagent 独立 context，只回传结论 |
| 上下文隔离 | 每个 subagent 独立 context；外部**记忆系统**（共享 scratchpad）持久化阶段成果 |
| 工具接入 | 所有 subagent 共享工具集；search / read_page / cite 被建模为 tool |
| 失败处理 | Lead 观察 subagent 结果，必要时 re-dispatch；外部 memory 存 "completed phases" 避免丢失 |
| 可观测 | 每 subagent 一条 trace，lead 汇总为一棵 plan tree |
| 关键权衡 | **Token 消耗约为单 agent 15×**，但在**研究类 breadth-first 任务** 上成功率显著提升 |

**关键工程洞察**（原文摘录）：
- "We implemented patterns where agents **summarize completed work phases** and store essential information in **external memory** before proceeding"
- 每次 subagent 调用都要精心设计**任务描述 prompt**，否则 subagent 会"跑偏"（这就是后面 Cognition 抨击的点）
- Token × 15 的成本只在**高价值知识性任务**划算；代码/工具链类任务**ROI 远不如单线程**

> 链接: https://www.anthropic.com/engineering/multi-agent-research-system

### 1.2 Claude Code Subagent（工程最佳实践）

Claude Code 选了一条**极度克制**的 Subagent 路线：

| 维度 | 设计 |
|------|------|
| 定位 | 工程师日常编码助手，长 session 多文件修改 |
| 通信拓扑 | 主 agent 用 `Task(description, subagent_type)` 单次派生 Subagent，**深度限制为 1**（Subagent 不能再 spawn） |
| Subagent 策略 | 每个 subagent = **独立 system prompt + 独立 context 窗口 + 独立工具子集** |
| 上下文隔离 | 只回传**摘要**，父 context 不暴露 subagent 完整 history |
| 工具接入 | 父 agent 声明允许工具白名单；subagent 自带受限 tool 集（例如 code-review-subagent 只能 read） |
| 失败处理 | 主 agent 可复派；subagent 内部 loop 由自身处理，上层不感知 |
| 可观测 | Summary isolation：父 context 只收摘要，天然降低噪声；filesystem 是共享 scratchpad |
| 关键权衡 | **深度 1 限制**避免了递归爆炸；**only summary returns**避免了 Cognition 诟病的"子任务误解污染"问题 |

**为何这是当前最稳妥的工业设计**：
- "Context-only isolation（AutoGen）shares the filesystem but separates conversation histories. Claude Code **separates both** except for returned summaries." —— arXiv:2604.14228
- depth=1 限制等价于"**禁止层级递归**"，把多 Agent 的爆炸风险压缩到可控范围
- "**Named, isolated Claude instance with its own system prompt**" 这句定义值得作为 Subagent 的参考标准

> 链接:
> - Subagent 官方文档: https://docs.claude.com/en/docs/claude-code/sub-agents
> - InfoQ 技术解读: https://www.infoq.com/news/2025/08/claude-code-subagents/
> - 学术复盘（arXiv 2604.14228）: https://arxiv.org/html/2604.14228v1

---

## 二、Microsoft Magentic-One + AutoGen v0.4：学院派工程化

### 2.1 Magentic-One（arXiv:2411.04468）

| 维度 | 设计 |
|------|------|
| 定位 | **通用型**多 agent 团队，Generalist Multi-Agent System |
| 通信拓扑 | 1 Orchestrator + 4 Specialist（WebSurfer / FileSurfer / Coder / ComputerTerminal） |
| Orchestrator 机制 | 维护两个 ledger：**Task Ledger**（事实+计划）+ **Progress Ledger**（进展+停滞信号），循环 re-plan |
| Subagent 策略 | 专员 agent 无规划能力，**只负责领域动作**（浏览/读文件/写代码/跑命令） |
| 上下文隔离 | 专员有各自历史，但**Orchestrator 拥有全局视图**，必要时复述事实 |
| 失败处理 | Progress Ledger 检测停滞 → Orchestrator 主动 re-plan → 或升级到人 |
| 可观测 | 开源实现直接接 AutoGen AgentChat，每 step 有 event |
| 关键权衡 | **双 ledger 设计**是 2024-2025 多 agent 规划领域最清晰的机制之一，值得学习 |

**与 Research System 的差异**：
- Magentic-One 是**领域分工型**（按动作类型切专员），不是**任务分发型**
- 关键创新 = Progress Ledger 的**停滞识别 + 自主 re-plan**，相当于把 v8 ReflAct 的精神从"任务目标反思"搬到了"团队进度反思"

> 链接:
> - 论文: https://arxiv.org/abs/2411.04468
> - MSR 博客: https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/

### 2.2 AutoGen v0.4（2025.01）

从 v0.2 对话式协作转向 **Actor Model** 异步消息传递，这是**工程架构层的重大升级**：

| 维度 | 设计 |
|------|------|
| 通信原语 | Agent = Actor，通过**异步 message passing**交互（不再是同步 chat） |
| 扩展性 | 支持跨语言、跨进程（Python + .NET 互通）、分布式部署 |
| 可观测 | 原生 OpenTelemetry，每个消息都有 span |
| 分层 | **Core**（actor 原语）+ **AgentChat**（对话式高层 API）+ **Extensions**（工具/模型/UI） |

**给本项目的启示**：v8 的 `_emit` 事件多播是简化版 actor；真正走向多 agent 时，**消息传递**比**方法调用**更健壮。

> 链接: https://microsoft.github.io/autogen/stable/

---

## 三、OpenAI Swarm → Agents SDK：极简 Handoff 范式

### 3.1 设计哲学

OpenAI 走了**最轻量**的路线，只有三个概念：
- **Agent**：带 instructions + tools 的实体
- **Handoff**：Agent 返回 "请转交给 X" 即切换控制权
- **Routine**：一组 agents + handoffs 构成的协作图

```python
# Swarm 核心模式
triage = Agent(name="Triage", instructions="...", functions=[transfer_to_sales, transfer_to_support])
sales = Agent(name="Sales", instructions="...")
support = Agent(name="Support", instructions="...")
```

### 3.2 关键工程选择

| 维度 | 设计 |
|------|------|
| 通信拓扑 | **Network（任意 agent 可 handoff 到任意 agent）** |
| 状态共享 | Handoff 时**连同整个对话历史**一起切换，接手 agent 全知 |
| 上下文隔离 | **无隔离**，历史累计（优点：避免 Cognition 批评的"decision divergence"；缺点：长对话 context 爆炸） |
| 工具接入 | 工具和 handoff 都表示为 function，LLM 自主选择 |
| 适用场景 | **对话分流型**（客服、triage、路由）；长时程编码/研究任务不适用 |

**现状**: 2025 年 Swarm 已升级为**OpenAI Agents SDK**（production-ready），加入 guardrails / tracing / 并行 tool call；但 handoff 哲学保留。

> 链接:
> - Swarm repo: https://github.com/openai/swarm
> - Agents SDK cookbook: https://developers.openai.com/cookbook/examples/orchestrating_agents

---

## 四、Cognition / Devin：反多 Agent 流派

### 4.1 核心主张（2025.06 博客《Don't Build Multi-Agents》）

**两条极强的原则**（Walden Yan）：

> **Principle 1**: Share context, and share **full agent traces**, not just individual messages.
>
> **Principle 2**: Actions carry **implicit decisions**, and conflicting decisions carry bad results.

### 4.2 失败案例论证

原文举"Flappy Bird 克隆"任务：
- Subagent 1 误解为 Super Mario Bros 风格背景
- Subagent 2 做了风格不匹配的鸟
- **即使复制原始任务描述给两个 subagent，它们也会做出"隐式决策"，导致风格不一致**
- **因此：非"full-trace 共享"的多 Agent 架构天然不可靠**

### 4.3 Devin 的真实架构

| 维度 | 设计 |
|------|------|
| 拓扑 | **单线程长时程 Agent**（不是多 Agent） |
| 上下文工程 | 一个专门的 **compression LLM** 把历史轨迹压缩为"关键事件 + 决策"注入下一轮 |
| 工具 | Browser / Editor / Shell / Planner 都是工具，不是 agent |
| 记忆 | 外部 memory 保存 "completed phases"，而不是靠子 agent 隔离 |
| 关键权衡 | 放弃并行以换取**decision coherence**；相信"**单 agent 变聪明**比**多 agent 协调**更容易" |

**对本项目的启发**：这一派对 v8 这类"目标驱动单 agent + 动态 TODO"路线是**强正反馈**。如果一定要上多 agent，必须：
- 共享**完整 trace**（不能只发任务描述）
- 独立 **compression 模型**（不是同 agent 自己总结）
- 限制并行度到**可验证决策一致性**的范围

> 链接: https://cognition.ai/blog/dont-build-multi-agents

---

## 五、Cursor 2.0 / 3.0：并行 Worktree 折中方案

2025.10 Cursor 2.0 引入了有趣的中间方案：**最多 8 个 agent 并行**，但通过 **git worktree 物理隔离** 避免冲突。

| 维度 | 设计 |
|------|------|
| 拓扑 | Composer UI 协调，**每个 agent 独立 worktree / 远程 sandbox** |
| 上下文隔离 | **文件系统 + git 分支级隔离**（天然解决 Cognition 原则 2 中"冲突决策"问题——因为每个 agent 改的是不同分支） |
| 合并策略 | 用户手动挑选最佳 agent 结果合并（agent 不自动合并） |
| 适用场景 | **探索式编码**（同一个需求让多个 agent 跑不同实现，挑最好的） |
| 关键洞察 | **不是让多个 agent 协作**，而是**让多个单 agent 竞争**—— 本质仍是 single-thread agent 哲学 |

Cursor 3.0（2026 Q1 传闻）进一步引入 **Background Agent**：后台异步跑长任务 + 本地↔云 handoff。

> 链接:
> - Cursor 2.0 博客: https://cursor.com/blog/2-0
> - Cursor 3 评测: https://www.datacamp.com/blog/cursor-3

**启发**：`git worktree` 级别的物理隔离是被严重低估的"多 Agent 解耦"武器；软件工程类任务比通用任务更适合并行。

---

## 六、LangGraph 多 Agent 三大拓扑

LangChain 团队把多 Agent 抽象为三种**显式拓扑**，是目前最清晰的教学分类：

### 6.1 Supervisor 拓扑

```
         ┌──── Supervisor ────┐
         │                     │
    Worker A ←────→ Worker B ←────→ Worker C
    (all routed via supervisor)
```

- Supervisor 是**唯一决策者**，决定把任务分给谁、是否结束
- 工作 agent 之间**不直接通信**
- **稳定性最好，成本最高**（每 step Supervisor 都要 LLM 决策）
- 对应项目: Anthropic Research System / Magentic-One / Claude Code

### 6.2 Network 拓扑

```
    A ←──→ B ←──→ C
    ↑  ╲   │   ╱  ↑
    └───────────────┘
    (any-to-any handoff)
```

- 任何 agent 可 handoff 到任意 agent
- 灵活但易形成**通信环路**
- 对应项目: OpenAI Swarm / 早期 AutoGen v0.2

### 6.3 Hierarchical 拓扑

```
           Top Supervisor
          /              \
     Team A Sup        Team B Sup
     /      \           /      \
   A1       A2        B1       B2
```

- 多层 Supervisor，**递归套娃**
- 适合非常复杂的任务（代码工厂、科研助手）
- 对应项目: Agent-S2 hierarchical / LangGraph Studio example

### 6.4 官方参考

> 链接:
> - LangGraph Multi-Agent 指南: https://langchain-ai.github.io/langgraph/concepts/multi_agent/
> - LangGraph Supervisor SDK: https://reference.langchain.com/python/langgraph-supervisor

---

## 七、Manus / MetaGPT / CrewAI：角色驱动派

### 7.1 Manus（2025.03 出圈）

根据官方博客与 arXiv:2505.02024 综述：

| 维度 | 设计 |
|------|------|
| 拓扑 | 对用户暴露**单一 agent**；内部推测为 **Leader + Specialist pool**（类 Magentic-One） |
| 核心创新 | **Agent Skills** 机制 —— 把可复用动作抽象为 skill（类 Voyager），形成技能库 |
| 上下文隔离 | 通过 skill 签名暴露能力，隐藏内部 subagent 实现 |
| 适用场景 | **通用助理型**（网页、文档、代码、数据一条龙） |
| 关键洞察 | 对外**隐藏多 agent 复杂性**、对内按 skill 分工，是工业产品的主流做法 |

> 链接: https://manus.im/blog/manus-skills

### 7.2 MetaGPT / ChatDev（软件工程多角色）

| 维度 | 设计 |
|------|------|
| 核心哲学 | `Code = SOP(Team)` —— 把软件公司 SOP 编码为 agent 工作流 |
| 角色 | PM / Architect / Engineer / QA / Reviewer |
| 通信 | **Message Pool**（发布-订阅）+ Role-based routing |
| 工具 | 每个角色有领域工具（PM-用 design tool，Engineer-用 code tool） |
| 适用场景 | 需求→代码→测试的**流水线型**任务 |
| 关键风险 | 角色越多越容易"角色幻觉"和"职责重叠"（见 06 文档反模式第 4 条） |

> 链接:
> - MetaGPT: https://github.com/FoundationAgents/MetaGPT
> - ChatDev 2.0: https://github.com/OpenBMB/ChatDev

### 7.3 CrewAI

- 主打**声明式 YAML 配置**角色 + 工具
- 2025 生态对标 LangGraph 的低代码版本
- 适合 PoC 和中小规模场景

> 链接: https://docs.crewai.com/

---

## 八、Agent-S / S2 / S3：Computer Use 分层范式

Simular AI 系列是 computer-use 场景的多 agent 标杆：

- **Agent-S（2024.10）**：Manager / Worker / Evaluator 三层，Manager 做宏观规划
- **Agent-S2（arXiv:2504.00906, 2025.04）**：**Compositional Generalist-Specialist**，通用 Planner 分派给 Specialist
- **Agent-S3（2025.10, arXiv:2510.02250）**：接近人类水平的 computer use

**与本项目关联度**：项目目前没有 computer-use 模态，这条线**暂不优先**，但"Generalist 规划 + Specialist 执行"的分层思想与 Magentic-One 一致，可作**L4 协作层范式**参考。

> 链接: https://arxiv.org/html/2504.00906v1

---

## 九、五大拓扑对照矩阵

| 拓扑 | 代表系统 | 上下文共享 | 并行度 | 决策一致性 | 成本 | 最适任务 |
|------|---------|----------|-------|----------|------|---------|
| **Orchestrator-Worker** | Anthropic Research / Magentic-One | Orchestrator 全知, Worker 隔离 | 中 | 高（集中决策） | ~15× | 研究调研 / 广度探索 |
| **Supervisor** | LangGraph Supervisor / Manus | Supervisor 汇总 | 中 | 高 | ~8× | 客服路由 / 模块化任务 |
| **Network (Handoff)** | Swarm / Agents SDK | 全量历史随 handoff 流转 | 低（串行控制权） | 中 | ~2× | 对话分流 |
| **Hierarchical** | Agent-S2 / LangGraph Hierarchical | 每层独立 + 顶层全局 | 高 | 中 | ~20× | 复杂工程 / 科研 |
| **Pipeline / MoA** | Chain-of-Agents / Mixture-of-Agents | 顺序传递中间产物 | 低（串行） | 中 | ~5× | 长文档 / 集成回答 |
| **Single-thread + Parallel Sandbox** | Devin / Cursor 2.0 | 单 agent 单 context | 外部并行（worktree） | 高（单 agent 决策） | ~3× | 编码 / 长时程 |

---

## 十、对本项目（Manus Demo v8）的直接启示（仅结论）

> **本节只下结论，不展开实施；具体路线图见 03 文档与下一轮 v9 计划**

1. 若要在 v9/v10 融入多 Agent，**Claude Code 的 depth=1 Subagent + Anthropic Research 的 orchestrator-worker + summary-only return** 组合是**风险最低的起点**
2. **OpenAI Swarm 式 network handoff 不推荐作为主拓扑**，因为 Cognition 的原则 1/2 对教学场景同样成立（决策一致性比并行更重要）
3. `TracingBridge` 和 `_emit` 事件多播可天然演化为 **Actor Model**（参考 AutoGen v0.4）；未来多 agent 跨进程扩展时价值显现
4. 若要展示"多 Agent 竞争"教学 Demo，**Cursor 2.0 的 worktree 方案**比传统"协作对话"更适合项目已有的 sandbox 基础设施
5. **不必做完整 Magentic-One**，但**Progress Ledger（停滞检测 + re-plan）**机制可单独抽出来增强 v8 的目标反思能力

---

## 附录 A：本文核心资料清单

| 主题 | 来源 | 链接 |
|------|------|------|
| Anthropic Multi-Agent Research | Anthropic 官方博客 | https://www.anthropic.com/engineering/multi-agent-research-system |
| Claude Code Subagent | 官方文档 + InfoQ | https://www.infoq.com/news/2025/08/claude-code-subagents/ |
| Building Effective Agents | Anthropic（2024.12） | https://www.anthropic.com/research/building-effective-agents |
| Magentic-One | Microsoft Research | https://arxiv.org/abs/2411.04468 |
| AutoGen v0.4 | MSR | https://microsoft.github.io/autogen/stable/ |
| OpenAI Swarm | GitHub | https://github.com/openai/swarm |
| Orchestrating Agents Cookbook | OpenAI Developers | https://developers.openai.com/cookbook/examples/orchestrating_agents |
| Don't Build Multi-Agents | Cognition 博客 | https://cognition.ai/blog/dont-build-multi-agents |
| Cursor 2.0 & Composer | Cursor 官方博客 | https://cursor.com/blog/2-0 |
| LangGraph Multi-Agent | LangChain 文档 | https://langchain-ai.github.io/langgraph/concepts/multi_agent/ |
| Manus Skills | Manus 官方博客 | https://manus.im/blog/manus-skills |
| MetaGPT | GitHub | https://github.com/FoundationAgents/MetaGPT |
| ChatDev 2.0 | GitHub | https://github.com/OpenBMB/ChatDev |
| Agent-S2 | arXiv | https://arxiv.org/html/2504.00906v1 |
| Why Multi-Agent LLM Systems Fail | arXiv | https://arxiv.org/abs/2503.13657 |

下一份文档（`05-多Agent学术前沿论文卡片.md`）将对上述系统对应的 10-12 篇代表论文做逐篇摘要。
