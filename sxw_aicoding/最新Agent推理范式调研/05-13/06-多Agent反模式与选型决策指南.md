# 多 Agent 反模式与选型决策指南

> **调研日期**: 2026-05-13
> **定位**: 本调研系列的**独特价值产出**——业内至今缺乏系统总结的"**什么时候别用多 Agent、要用时怎么选**"决策手册
> **主要理论来源**:
> - Anthropic《Building Effective Agents》(2024.12)
> - Cognition《Don't Build Multi-Agents》(2025.06)
> - arXiv:2503.13657《Why Do Multi-Agent LLM Systems Fail?》MAST 失败分类
> - Microsoft《Taxonomy of Failure Mode in Agentic AI Systems》白皮书
> - NeurIPS 2025《Debate or Vote》
> **使用方式**: 做多 Agent 架构决策时**按顺序走完 §1 → §2 → §3 → §4**，即可把风险降到最低

---

## §1 十大多 Agent 反模式

> **总体共识**：2025 年业内**没有一个失败模式是"技术不到位"**导致的，全部是**架构决策错误**。90% 的多 Agent 灾难本可通过设计阶段的反模式检查避免。

### 反模式 #1：角色爆炸（Role Explosion）

| 项 | 内容 |
|----|------|
| 症状 | Agent 数量 N > 5 后，成功率不升反降；单任务成本指数上涨 |
| 根因 | 每个额外角色都引入新的**决策节点**和**通信边**；N 个 agent 的交互复杂度是 O(N²) |
| 代表案例 | 早期 MetaGPT 的"软件公司全流程"设定 10+ 角色，实测 5 个角色以上边际收益转负 |
| 数据 | arXiv:2503.13657 MAST 分析：Role-heavy 系统的平均成功率 **比 Role-lean 低 11%** |
| 规避策略 | **上限 5 角色原则**；新增角色前问"这个职责单 agent 加 prompt 能做吗？"；角色合并优先于拆分 |

### 反模式 #2：上下文泄漏 / Subagent 历史污染

| 项 | 内容 |
|----|------|
| 症状 | Subagent 拿到了父 agent 的完整历史，被无关细节带偏；或 subagent 结果污染主 context |
| 根因 | 缺少**显式边界**：父-子 agent 共享 context 时，双向干扰不可避免 |
| 代表案例 | AutoGen v0.2 默认把 conversation history 全量传递 subagent，长 session 下 subagent 输出严重漂移 |
| 规避策略 | **Claude Code 方案**：subagent **独立 context + 只回传 summary**；AutoGen v0.4 actor model 消息驱动；明确 subagent 的**"可见集"**清单 |
| 关键判据 | "如果把这个 subagent 换成一个纯函数，它还能工作吗？" 不能 → 说明 context 耦合过深 |

### 反模式 #3：通信死循环 / 无限 Handoff

| 项 | 内容 |
|----|------|
| 症状 | Agent A 转给 B，B 转回 A，陷入 ping-pong；或 N 个 agent 形成环 |
| 根因 | Network 拓扑无**终止判据**；每个 agent 都认为"这不是我该处理的"，把球踢来踢去 |
| 代表案例 | 早期 Swarm demo 中 Triage → Sales → Triage 循环问题 |
| 数据 | MAST 失败分析：**Step Repetition 占 17.14%**，是最常见失败 |
| 规避策略 | **Handoff 次数硬上限**（如 MAX_HANDOFFS=5）；**去重窗口**（禁止转到最近 K 个 agent）；引入 **Supervisor 兜底** |

### 反模式 #4：职责重叠导致双写冲突

| 项 | 内容 |
|----|------|
| 症状 | 多个 agent 同时修改同一状态/文件，产生冲突 |
| 根因 | 角色边界模糊（"Reviewer"和"QA"都觉得自己该提 feedback） |
| 代表案例 | ChatDev 早期版本 Engineer 和 CTO 重复写 code |
| 规避策略 | **职责矩阵法**（每个资源只有 1 个 Owner）；**物理隔离**（Cursor 2.0 的 git worktree 方案值得借鉴） |

### 反模式 #5：Self-Critique Paradox（同模型自评悖论）

| 项 | 内容 |
|----|------|
| 症状 | 用同一个 LLM 作 actor 和 critic，简单任务成功率**反而下降**（Snorkel 2025 数据）；critic 要么全盘认可，要么过度挑刺 |
| 根因 | 同模型同 prompt family 存在**认知偏差同源性** |
| 数据 | NeurIPS 2025《Debate or Vote》：**多数投票的增益 >> 辩论机制的增益** |
| 规避策略 | **独立 critic 模型**（v8.1 方案核心）；或用**majority voting over sampled answers**替代 critic |
| 关联 | 本项目 `goal_driven_planner.py` 的 `_goal_reflect` 是典型高危点 |

### 反模式 #6：Summary Loss（子 Agent 摘要漏信息）

| 项 | 内容 |
|----|------|
| 症状 | Subagent 返回摘要给父 agent，关键细节被抽象掉，导致父 agent 做错后续决策 |
| 根因 | 缺少**摘要忠实度校验**；subagent 的 summary prompt 过度"优化"成人读格式 |
| 代表案例 | Anthropic Research System 原文提及："need to invest in figuring out what ends up being the key information"（Cognition 博客同主题） |
| 规避策略 | **结构化 artifact**（JSON schema 强制字段）；**raw trace 可回溯**（filesystem scratchpad 保留完整对话，summary 只是 index）；fine-tune 专用 summarization model |

### 反模式 #7：Tool Schema 分裂

| 项 | 内容 |
|----|------|
| 症状 | 不同 agent 定义了"干同一件事"的不同工具；或同名工具签名不一致 |
| 根因 | 角色化设计鼓励每个 agent **自建工具集**，缺少统一注册中心 |
| 代表案例 | 多个开源项目中各 agent 自建 `read_file` 变体，参数不兼容 |
| 规避策略 | **单一 ToolRegistry**（类似 MCP 思路）；角色能力 = 工具 ACL 而非工具复刻；引入 **Model Context Protocol** 作统一接口 |

### 反模式 #8：成本不可控（Token Explosion）

| 项 | 内容 |
|----|------|
| 症状 | 多 agent 成本飙升，达到单 agent 的 10-30 倍 |
| 根因 | 每个 agent 都独立消耗 context；Orchestrator 常需观察所有 subagent 输出 |
| 数据 | Anthropic Research System 原文："roughly **15×** tokens of single agent"；只有**高价值 research 任务**划算 |
| 规避策略 | **Token 预算熔断**（per-agent + 全局双层）；**冷启动模式**（简单任务走单 agent，检测复杂度再升级）；记录 cost-per-task，**离线对比单 vs 多 agent ROI** |

### 反模式 #9：可观测性断裂

| 项 | 内容 |
|----|------|
| 症状 | 失败时无法定位是哪个 agent 出错；trace 被切成多段无法串联 |
| 根因 | Subagent 独立 tracer；父-子 span 未关联；消息传递丢失 correlation_id |
| 规避策略 | **OpenTelemetry 全局 TraceContext 透传**；父 span 标注 `subagent.id`；Agent 间消息**必须携带 trace context**（类比 HTTP header） |
| 项目关联 | 本项目 `tracing/bridge.py` 目前处理单 agent 良好，扩展多 agent 需严格遵循此原则 |

### 反模式 #10：评测失真

| 项 | 内容 |
|----|------|
| 症状 | 端到端指标高（成功率 85%），但拆开看每个 agent 都是勉强合格；单 agent 缺陷被"多数通过"掩盖 |
| 根因 | 评测只看最终输出，不看中间 agent 行为；agent 间的失败互相抵消 |
| 规避策略 | **每 agent 独立指标**（per-agent success rate / tool accuracy）；**失败归因**（failure attribution to specific agent）；引入 MAST 14 维诊断 |

---

## §2 选型决策树：单 Agent vs 多 Agent

> **核心原则**（Anthropic & Cognition 共识）: **能用单 Agent 解决就不要上多 Agent**。多 Agent 只解决**单 agent 真的解决不了**的问题。

### 判据清单（5 问）

按顺序回答，任何一个 YES 就考虑多 agent；**全部 NO 则坚持单 agent**。

```
┌─ Q1 ─ 任务需要的上下文总量 > 单模型窗口（>200K tokens）？
│       YES → 考虑 Chain-of-Agents（长文档）或 Subagent（研究任务）
│
├─ Q2 ─ 任务有明确的"专业领域切分"（如 browser / code / shell 明显不重叠）？
│       YES → 考虑 Orchestrator-Worker（Magentic-One 风格）
│
├─ Q3 ─ 任务需要并行探索多个独立假设（如研究广度扫描、多方案竞争）？
│       YES → 考虑 Anthropic Research System 或 Cursor Worktree 并行
│
├─ Q4 ─ 任务需要最终答案经过"多模型一致性"验证？
│       YES → 考虑 Mixture-of-Agents / Majority Voting（不是 Debate）
│
└─ Q5 ─ 任务是多轮对话分流（客服路由类）？
        YES → 考虑 Network Handoff（Swarm）
```

### 反过来：以下情形**坚持单 Agent**

| 情形 | 原因 |
|------|------|
| 编码 / 调试 / 重构任务 | Cognition 原则 2：decision coherence 压倒并行 |
| 短时程（< 20 轮） | 多 agent 协调开销 > 并行收益 |
| 上下文 < 50K tokens | 单窗口够用，拆分反而丢失关联 |
| 无法清晰切分的任务 | 任务边界模糊 → subagent 必然误解子任务 |
| 需要严格成本控制 | 多 agent = 至少 3-15× 成本 |

### 速查表

| 任务类型 | 推荐架构 | 典型代表 |
|---------|---------|---------|
| 长文档 QA / 摘要 | Chain-of-Agents | Google CoA |
| 通用助理 | Orchestrator-Worker + depth=1 Subagent | Anthropic Research |
| 长时程编码 | **单 Agent + 外部 memory + compression** | Devin |
| 并行探索编码 | 单 Agent × N + Worktree 隔离 | Cursor 2.0 |
| 客服 / Triage | Network Handoff | Swarm |
| 多源聚合回答 | Mixture-of-Agents（投票） | Together MoA |
| Computer-use | Hierarchical (Generalist/Specialist) | Agent-S2 |
| 代码工厂 / 软件 SOP | Role-based + Message Pool | MetaGPT |

---

## §3 拓扑选择矩阵（任务特征 × 推荐拓扑）

> 先看任务特征，再对 §1 反模式做自检，最后选拓扑。

| 任务特征 | 首选拓扑 | 次选拓扑 | 不推荐 |
|---------|---------|---------|-------|
| **长文档处理** (>100K tokens) | Pipeline (CoA) | Orchestrator-Worker | Network |
| **代码工程** (模块化修改) | Single-thread + Sandbox 并行 | Orchestrator-Worker（深度=1） | 多层 Hierarchical |
| **浏览器自动化** | Hierarchical (Agent-S2 式) | Orchestrator-Worker | Network Handoff |
| **科研探索** | Orchestrator-Worker (research 型) | Hierarchical | 单 agent（上下文不够） |
| **对话助手 / 客服** | Network Handoff (Swarm) | Single-thread + Routing | Orchestrator（过度设计） |
| **结构化数据处理** | Pipeline (MapReduce 式) | Single-thread | Network |
| **高准确率问答** | Mixture-of-Agents 投票 | 单 agent + sampling | Debate（增益不如投票） |
| **长时程通用任务** | Single-thread + 外部 memory | Orchestrator-Worker | Network（易死循环） |

### 拓扑反模式映射

| 拓扑 | 最易踩的反模式 | 必做防御 |
|------|------------|---------|
| Orchestrator-Worker | #8 成本爆炸, #6 Summary Loss | Token 熔断 + 结构化 artifact |
| Network (Handoff) | #3 死循环, #7 工具分裂 | Handoff 次数限制 + 统一 ToolRegistry |
| Hierarchical | #1 角色爆炸, #4 职责重叠 | 5 角色上限 + 职责矩阵 |
| Pipeline (CoA/MoA) | #6 Summary Loss | 结构化 chunk schema |
| Network (Debate) | #5 Self-Critique Paradox | 改投票 或 独立 critic 模型 |
| Single-thread | 上下文爆炸 | 外部 memory + compression model |

---

## §4 工程落地 Checklist

> **多 Agent 系统上线前的 12 项自检清单**。任何一项未勾选都属于已知风险。

### 架构层 (4 项)

- [ ] **拓扑明确**：已在 §3 矩阵中选定一种主拓扑，避免混合多种拓扑
- [ ] **角色上限**：总 agent 数 ≤ 5（含 orchestrator），超过需书面论证
- [ ] **Subagent 深度**：默认 depth=1（Claude Code 原则）；需要 depth>1 必须有环检测
- [ ] **终止判据**：每个 agent 有明确的"我的活干完了"信号；全局有 hard limit

### 上下文层 (3 项)

- [ ] **Context 边界**：每个 subagent 的"可见集"已文档化
- [ ] **Summary 忠实度**：subagent 返回结构化 artifact（JSON schema），非自由文本
- [ ] **Trace 回溯**：共享 filesystem / scratchpad 保留完整对话，summary 只是 index

### 工具层 (2 项)

- [ ] **单一 ToolRegistry**：所有 agent 从统一注册表获取工具；角色 = 工具白名单 ACL
- [ ] **MCP 兼容性**：优先使用 MCP (Model Context Protocol) 对接外部工具，避免自建分裂

### 评测层 (3 项)

- [ ] **独立 Critic**：如有评估环节，critic 模型必须**独立**（Snorkel / NeurIPS 2025 双证据）
- [ ] **Per-agent 指标**：每 agent 独立 success rate / token / tool accuracy
- [ ] **失败归因**：失败案例能定位到具体 agent（MAST 14 维打标）

---

## §5 项目特定结论：Manus Demo v8 下一步决策建议

> 下结论，不改代码。真正实施见 03 文档与后续 v9 规划。

### 5.1 v8 现状对照反模式自检

| 反模式 | v8 现状 | 风险等级 |
|-------|--------|---------|
| #1 角色爆炸 | 当前 4 个 agent（Orch/Planner/Executor/Reflector）| ✅ 安全 |
| #2 上下文泄漏 | BaseAgent 独立 `_messages`；Orchestrator 只组合不共享 history | ✅ 良好 |
| #3 死循环 | 无 agent-to-agent handoff | ✅ 不适用 |
| #4 职责重叠 | 边界清晰 | ✅ 良好 |
| #5 Self-Critique Paradox | `_goal_reflect` 用同模型自评 | ⚠️ 中风险，v8.1 独立 Critic 方案修补 |
| #6 Summary Loss | 无显式 summary 机制（尚未引入 subagent） | ➖ N/A |
| #7 工具分裂 | 统一 `tools/router.py`（良好） | ✅ 良好 |
| #8 成本爆炸 | 单 agent 单线程 | ✅ 安全 |
| #9 可观测性 | v7 Tracing 完备 | ✅ 优秀 |
| #10 评测失真 | 4 维加权 + per-agent 指标 | ✅ 良好 |

### 5.2 若走多 Agent 路线的最小风险配方

- **拓扑**: **Orchestrator-Worker + depth=1 Subagent**（Claude Code 风格，最克制）
- **入口**: 在 `tools/` 下新增 `spawn_subagent` 工具（v10-A 计划已定），**不**引入 network handoff
- **Context**: Subagent 独立 context，**只返回结构化 artifact**（Pydantic model）
- **Critic**: 必须**独立 prompt / 可独立模型**（v8.1 修复 #5）
- **观测**: TraceContext 全链路透传，父-子 span 显式关联
- **成本**: 默认 OFF，仅在任务复杂度评估满足 §2 Q1-Q5 任一条件时开启
- **上限**: `MAX_SUBAGENT_DEPTH=1`, `MAX_CONCURRENT_SUBAGENTS=3`

### 5.3 **明确不建议**走的路线

- ❌ **Network Handoff**（Swarm 风格）：项目是任务型而非对话分流型，不适用
- ❌ **多层 Hierarchical**：角色爆炸风险高，教学 Demo 不必要复杂
- ❌ **Debate-based Reflector**：NeurIPS 2025 证据显示**投票 > 辩论**，直接用 MoA 聚合
- ❌ **Single-thread 否定多 Agent**：项目作为教学 Demo 必须**演示多 Agent 范式**，不能完全跟 Devin 走反多 agent 路线

---

## 附录 A：关键决策引用索引

| 决策 | 依据 |
|------|------|
| "单 agent 能做就不多 agent" | Anthropic《Building Effective Agents》 |
| "多 agent 天然不可靠" | Cognition《Don't Build Multi-Agents》原则 1/2 |
| "Subagent depth=1" | Claude Code 官方设计 |
| "独立 Critic" | Snorkel 2025 Self-Critique Paradox |
| "投票 > 辩论" | NeurIPS 2025《Debate or Vote》 |
| "14 失败模式" | arXiv:2503.13657 MAST |
| "5 角色上限" | MetaGPT/ChatDev 经验数据 |
| "Subagent 成本 15×" | Anthropic Research System 博客 |

---

## 附录 B：决策流程图

```
             任务到达
                │
                ▼
       ┌─ 上下文 < 50K？ ──────YES──→ 单 Agent + ReAct/ReflAct
       │                                    │
       NO                                   │
       │                                    │
       ▼                                    │
  有并行收益？──NO──→ 单 Agent + 外部 compression (Devin 式)
       │
       YES
       │
       ▼
  有清晰领域切分？──NO──→ 单 Agent × N 并行 (Cursor Worktree)
       │
       YES
       │
       ▼
  需要最终共识？
       ├──YES──→ Mixture-of-Agents (Voting)
       │
       NO
       │
       ▼
  是对话分流？
       ├──YES──→ Network Handoff (Swarm)
       │
       NO
       │
       ▼
  【推荐终点】Orchestrator-Worker + depth=1 Subagent
              + 独立 Critic + 结构化 Artifact
              + TraceContext 透传
              + Token 双层熔断
```

---

本指南与系列其他文档的关系：

- `04-多Agent架构全景与工业旗舰实践.md`：看**业内怎么做**
- `05-多Agent学术前沿论文卡片.md`：看**学界怎么研究**
- `06-多Agent反模式与选型决策指南.md`（本文）：看**怎么决策**

三者合起来构成 2026-05-13 时点的多 Agent 完整调研视图。
