# Manus Demo 后续迭代路线图 (v14 ~ v19)

> **生成日期**: 2026-05-21（初版）→ 2026-05-22（三次搜索定稿）
> **当前版本**: v13.0（HITL ask_user + Wave-1..7 SubAgent overhaul）
> **数据来源**: 代码事实校对 + 三轮公网搜索（智谱 Quark/Sogou/Pro + 通义百炼 + 博查 AI + WebSearch）+ ATA 内部搜索（aone-kit）

---

## 一、前言与版本号校对

本 roadmap 是对 `iteration-roadmap-v10-v15.md` 的重构。原 roadmap 写于 v9.1 时代，把 v10/v12/v13 命名为 Memory/MCP/Multi-Agent 三个未来主题，但这三个版本号已被实际交付占用：

| 已交付版本 | 内容 | 证据 |
|---|---|---|
| **v10** | 联网搜索（DDGS 实现 + Bailian MCP 优先） | `config.py:85-86` `# --- Web Search (v10) ---`；`tools/web_search.py` 258 行 |
| **v11** | Bailian MCP 出站集成（WebSearch + WebParser） | `config.py:90-91` `# --- Bailian MCP (Aliyun Search & WebParser, v11) ---`；`tools/mcp_client.py` 135 行 |
| **v12** | （跳过，无该版本） | `config.py` 中无 v12 标记 |
| **v13** | HITL ask_user（双门控 + asyncio.Future 桥接） | `config.py:139-140` `# --- v13.0 Human-in-the-Loop Feature Flags ---`；`tools/ask_user.py` 192 行；`CLAUDE.md:10` 权威版本号 |

因此本 roadmap **从 v14 起算**。版本号 v10/v11/v13 不再可用，v12 已废弃不再补。原 roadmap 文档的 P0-P5 提案已根据实际状况重新映射并重排优先级。

---

## 二、现状定位

当前系统（v13.0）已构建了一个相对完整的多智能体任务执行系统，核心能力包括：

- **三种规划路径**：Simple（线性）→ Complex（DAG 并行）→ Emergent（v5 TODO）/ GoalDriven（v8 ReflAct）
- **SubAgent 协作**（v9）：depth=1 隔离 + 纯摘要返回 + Token 预算熔断
- **全链路 Tracing**（v7）：OTel 标准 Span 树 + Web Viewer
- **联网搜索 + Bailian MCP 出站**（v10/v11）：DDGS 兜底，Bailian MCP 优先
- **HITL 人机协作**（v13）：ask_user 工具 + 双门控（`interactive=False` 自动抑制）
- **Wave-1..7 SubAgent overhaul**：helper sharing、runtime system prompt、caller_tag 归因
- **评测体系**：12 基准任务 × 四维度加权评分（Planning 30% / Execution 40% / Efficiency 20% / Reflection 10%）

**已知短板**（成为 v14+ 的立项依据）：

- `memory/long_term.py:141` 仅做 `set(query.lower().split())` 关键词重叠检索，无向量、无时间索引、无巩固
- `llm/client.py` token 统计未对推理模型 thinking / reasoning_tokens 分桶
- `tools/shell_tool.py:130` Wave-5 沙箱绕过 bug 仍 pending（`CHANGELOG.md:83-85` 显式标记"待办，风险最高"）
- `tools/mcp_client.py` 仅是 Bailian 出站包装，不是通用 MCP Client / Server
- `config.py:44-45` `DAG_SERIAL_EXECUTION=true`，并行 DAG 红利未拿
- evaluation 仅 12 任务，无法支撑 v17+ 任何自动调优

---

## 三、最新研究 / 行业趋势的关键发现

| 趋势 | 关键来源 | 与当前系统的差距 |
|---|---|---|
| **Harness Engineering**（2026 核心范式） | OpenAI 工程博客 (2026-02) / Anthropic Harness 设计文档 / Martin Fowler 深度分析 / LangChain Deep Agents / Meta-Harness (Stanford, arXiv 2026) / DataFun Agentic AI Summit 2026 深圳 Harness 专场 | **Agent = Model + Harness** 已成行业共识。同模型换 Harness 可从 42%→78%；Can Bölük 实验仅改编辑格式（hashline）即将 Grok Code Fast 从 6.7%→68.3%（10 倍，零训练成本）；Meta-Harness 让 LLM 自优化 Harness 达 76.4% 通过率；我们已有 prompt_utils / tool_call_helpers / context / router 等 Harness 基建，但未以 Harness 为单位组织规划 |
| **Agentic Memory（3D 框架）** | "Memory in the Age of AI Agents" 综述（NUS/复旦/Stanford/Oxford 等 47 作者），A-Mem (NeurIPS 2025) | 已从"六层"模型演化为 **Forms × Functions × Dynamics 三维框架**；当前 LongTermMemory 仅关键词重叠，零向量零时间索引 |
| **Self-Evolving Agent** | "A Comprehensive Survey of Self-Evolving AI Agents"（清华/北大/MSRA 等）；STELLA 案例（HLE Biomedicine 14%→26%） | 当前完全无状态，每次任务零积累 |
| **MCP 已成行业标准** | Linux 基金会 AAIF 托管；最新规范 2025-11-25（Streamable HTTP + OIDC + outputSchema）；OpenAI Agents SDK 2026-04-15 大更新：原生沙箱 + MCP 集成 | 已有 Bailian 出站客户端，但缺通用 MCP Client（多服务、动态发现），且**未把自身暴露为 MCP Server** |
| **Workflow + Agentic Loop 双引擎** | CaibotStudio 企业 Agent 平台架构（ATA #11020642405） | 当前 simple/complex/emergent 三路由实际只是 1.5 个引擎（Workflow 退化为 simple plan），双引擎未显式拉出 |
| **Master-Worker via MCP** | Codex × CodeFuse（ATA #12020632801） | 我们 SubAgent v9 是 in-process 同构方案，未走 MCP 跨进程 |
| **Multi-Agent Orchestration** | OpenAI Agents SDK v0.14+（Agent/Handoff/Guardrail 三原语）；A2A Protocol v1 (Google) | 当前仅 depth=1 SubAgent（隔离式），无 Handoff（上下文传递式）、无 A2A 通信标准 |
| **Agentic RAG** | 2025 AI Agent 六大趋势之一 | 当前 KnowledgeRetriever 仅 TF-IDF + 余弦，无推理驱动检索 |
| **Reasoning Model 适配** | DeepSeek R1（Nature 2025 封面）/ GRPO；Claude Opus 4.5 interleaved thinking | ReActEngine 未针对长 CoT / thinking token 分桶 / interleaved 优化；`MAX_REACT_ITERATIONS` 在推理模型上会爆 |
| **Safety Guardrails + 红队基准** | **OWASP ASI 2026（Agentic Applications Top 10）** 正式发布（100+ 专家评审）；Microsoft MDASH 多 Agent 安全系统登顶 CyberGym (88.45%)；真实事故：Claude Opus 4.6 Agent 9 秒删除生产数据库 | 当前仅 ShellTool 黑名单 + sandbox（且 sandbox 有 Wave-5 bug），无系统性校验，无红队基准；v19 应以 OWASP ASI 2026 为主要参考而非仅 AgentDojo |
| **Skill 系统**（程序性记忆轻量化） | Claude Code Skills | 当前 LTM 存文本摘要，不存可复用"技能"或"工作流" |

---

## 四、v13.x 维护批次（评审新增，前置阻塞）

在启动 v14 之前必须先解决一批"已知 bug + 必前置基建"。本批次约 **1 周**，作为零号优先级。

| # | 工作项 | 说明 | 工期 |
|---|---|---|---|
| 1 | codemap.md / CHANGELOG.md 回填到 v13 | 两份文档当前停在 v9.1，新成员入项目读到的全是错的 | 0.5 天 |
| 2 | Wave-5 沙箱修复 | `tools/shell_tool.py:130` 的 `_run_shell` 是 `@staticmethod` 直接用 `config.SANDBOX_DIR`，完全绕过 `self._workdir`，是 SubAgent 沙箱隔离的结构性漏洞 | 2 天 |
| 3 | `LLMClient` 推理 token 分桶 | DeepSeek R1 用 `<think>...</think>` 标签、OpenAI o 系列用 `reasoning_tokens` 字段；当前不分桶导致 ReAct 迭代计数失真 | 2 天 |
| 4 | `DAG_SERIAL_EXECUTION` 默认值复盘 | `config.py:44-45` 当前默认串行，并行红利未拿；需要复盘历史是否有 bug 才回退到串行，写明并行恢复条件 | 1 天 |
| 5 | evaluation 扩样到 30+ 任务 | v17 自演化、v18 协作模式、v19 安全基准全部依赖更大样本量，**这是横切阻塞依赖**，不可推后 | 1 周（可与其他项并行） |
| 6 | 清理 v12 占位 | 文档中明确"v12 跳过，不再补"，避免后续混淆 | 0.5 小时 |

---

## 五、v14 — Reasoning Model 适配 + Harness 优化 + 任务恢复（P0，2-3 周）

**动机**：包含当前已存在的 bug（推理 token 未分桶），且 Harness 红利 > 模型红利已被实验证实（同模型换 Harness 可从 42% 提升到 78%）。优先级从原 P4 上调到 **P0**。

**核心设计**：

```
react/reasoning_engine.py（新增，与 ReActEngine 并行）

1. 双协议支持
   - DeepSeek R1：识别 <think>...</think> 标签，分离 thinking vs response
   - OpenAI o 系列：读取 reasoning_tokens 字段
   - 通过 model 类型自动选择协议

2. ReAct 迭代计数修正
   - thinking tokens 不计入 MAX_REACT_ITERATIONS
   - 仅 tool_calls + 最终输出计为一次迭代
   - 新增 MAX_THINKING_TOKENS 控制推理预算

3. Interleaved Thinking（Claude Opus 4.5）
   - 思考-工具-思考-工具的多轮模式
   - 当前 react/engine.py 是"一次 LLM 调用 = 一轮 ReAct"，需改为支持多轮
   - 不是"小优化"而是范式重写，需要回归 ContextManager._find_safe_split

4. reasoning_effort × ToolRouter 联动
   - 简单任务 → low reasoning + 快速工具
   - 复杂任务 → high reasoning + 多轮工具

5. 任务持久化与恢复（resume）— 评审新增
   - OrchestratorAgent 任务中断后可恢复（保存 ReAct 状态、tool_calls 历史、Memory snapshot）
   - 受 Qoder / 通义灵码 / Anthropic Harness 检查点机制启发
   - Anthropic 在 Harness 设计文档中明确强调：企业级 Harness 必须具备检查点机制和人工介入节点
   - LangGraph 已提供生产级持久化 + 流式 + Human-in-the-loop 作为参考
   - 接口：OrchestratorAgent.resume(task_id)
```

**Harness 优化子项**：

- `agents/prompt_utils.py` 的 `build_system_prompt` 抽离为"Harness 配置层"，支持按模型类型切换 prompt 风格
- `react/tool_call_helpers.py` 的 `truncate_for_llm` / `classify_result` 策略可配置（按工具、按场景）
- `context/manager.py` 引入 thinking-aware split，避免在 `<think>` 块中间切开

**前置依赖**：v13.x 维护批次第 3 项（token 分桶）必须先完成

**预估工作量**：2-3 周（原 roadmap 估 1-1.5 周严重低估了 interleaved thinking 范式重写）

---

## 六、v15 — Agentic Memory 重构（P0，5-6 周，依赖 v14）

**动机**：`memory/long_term.py:141` 仅关键词重叠检索是项目最薄弱环节。最新 Survey（"Memory in the Age of AI Agents"）给出了 3D 框架的清晰范式。

**核心设计（3D 框架替代原"六层"）**：

```
                  Forms                Functions             Dynamics
                  ─────                ─────────             ────────
                  Token-level    ×     Factual         ×     Formation
                  Parametric           Experiential          Evolution
                  Latent               Working               Retrieval

存储后端按 Forms 划分：
  Token-level    → pgvector / Milvus 向量数据库
  Parametric     → LoRA / Adapter（远期，本期不做）
  Latent         → KV-Cache（已有 ContextManager 雏形）

检索 API 按 Functions 划分：
  memory_search_factual(query)        — 事实/概念检索
  memory_search_experiential(query)   — 情节/经验检索（带时间索引）
  memory_get_working()                — 当前工作内存

生命周期按 Dynamics 调度：
  Formation  — STM → Episodic 自动巩固
  Evolution  — 时间衰减 × LRU 淘汰（重要性评分留到 v17）
  Retrieval  — 召回-排序两阶段
```

**关键设计原则**：

1. **继承而非另起**：必须接管或继承 `context/manager.py` 的 Working Memory 实现，禁止两套压缩并存
2. **Memory as Tool**：Agent 通过 `memory_store / memory_search / memory_consolidate` 工具主动管理，而非被动读写
3. **第一版只用最简因子**：时间衰减 + 上限淘汰，"重要性评分"留到 v17 自演化阶段做

**三阶段交付**：

| 阶段 | 内容 | 工期 |
|---|---|---|
| v15.1 检索基建 | pgvector 部署 + Forms × Functions API + 与 ContextManager 对接 | 3 周 |
| v15.2 Memory as Tool | 6 个工具暴露给 ReActEngine + caller_tag 集成 + TracingBridge 采样配置 | 1 周 |
| v15.3 巩固与遗忘 | STM → Episodic 自动巩固 + 时间衰减 + LRU；Skill 系统轻量实验 | 2 周 |

**开源框架选型参考**：

| 场景 | 推荐 | 理由 |
|---|---|---|
| 研究型、关联推理 | A-MEM (NeurIPS 2025) | Zettelkasten 关联推理，LoCoMo 基准领先 |
| 轻量嵌入式 | Mem0 | 简单易集成 |
| 生产级会话 | Zep | 长期会话管理 |
| OS 级 + 多 Agent | Letta (MemGPT 继任) | sleep-time compute + 原生多 Agent |
| 记忆操作系统 | MemOS (MemTensor) | 首个 LLM 记忆 OS 概念 |

**预估工作量**：5-6 周（原 roadmap 估 3-4 周偏乐观，主要低估 pgvector 部署、6 类 API 契约稳定、巩固机制原型）

---

## 七、v16 — MCP 全面适配（P1，2 周，独立）

**动机校正**：原 roadmap 写"当前是自定义 BaseTool ABC，与生态隔离"——半对。实际上 `tools/mcp_client.py` 已经基于 mcp SDK 的 Streamable HTTP 实现了**单服务出站**（Bailian），但缺：

1. 通用 MCP Client（多服务发现、动态接入、stdio + Streamable HTTP 双传输）
2. 把自身暴露为 **MCP Server**（评审新增）

**核心设计**：

```
tools/mcp_bridge.py（新增）

v16.1 通用 MCP Client（1 周）
├── 多服务连接池（stdio + Streamable HTTP）
├── MCPBridgeTool(BaseTool) — 自动桥接 MCP Server 暴露的 Tool
├── JSON Schema 转换（MCP Schema → OpenAI function calling）
├── outputSchema 强校验（2025-11-25 规范）
└── 动态工具发现与注册

v16.2 MCP Server 暴露（1 周，评审新增）
├── 把 BaseTool 列表暴露为 MCP Tools
├── 把 SubAgent 暴露为 MCP Prompts（可触发模板）
├── 把 Memory 检索暴露为 MCP Resources
└── 受益方：Claude Code / Codex / Cursor 可直接接入
```

**好处**：

- 不替换 BaseTool 体系，桥接模式最小侵入
- ToolRouter / ReActEngine / SubAgentTool 全部透明复用
- v16.2 极小工作量极高生态收益（评审新增）

**与 v18 的关系**：v16.2 的 capabilities 元数据可被 v18 的 A2A Agent Card 直接复用，避免两套能力声明

**预估工作量**：2 周

---

## 八、v17 — Self-Evolution（P1，2-3 周，依赖 v15 + v13.x evaluation 扩样）

**动机**：当前系统每次任务完全无状态。STELLA 案例（生物医学领域）验证从经验中学习可使准确率近翻倍（14% → 26%）。

**核心范围（评审新增显式限制）**：

> **只做 Reflexion 范式（prompt 注入），明确拒绝 RL 范式**。当前项目无 RL 基础设施，prompt 注入是安全可回退的最小可行方案；RL 范式有不可回退、协同进化等风险，不在 v17 范围内。

**核心设计**：

```
agents/experience_learner.py（新增）

1. 成功轨迹提炼 → Procedural Memory 模板
   - 任务完成后分析工具调用序列
   - 提取可复用决策模式存入 v15 Procedural Memory
   - 受 Claude Code Skills 启发，可考虑"文件夹 + 触发条件"轻量形态

2. 失败轨迹反思
   - 记录 (task_type, failure_reason, correction) 三元组
   - 下次类似任务自动注入"避坑指南"
   - 参考 Reflexion（语言强化学习）

3. 分类器阈值在线调整
   - 当前 agents/planner.py 硬编码 score ≤ -1 / ≥ 2
   - 基于真实任务成功率统计调整
   - 前置：v13.x evaluation 扩样到 30+ 任务（否则统计不显著）

4. 用户偏好学习
   - 从 HITL 交互中积累用户偏好
   - 代码风格、输出格式、关注领域等
```

**四维框架对照**（"A Comprehensive Survey of Self-Evolving AI Agents"）：

| 维度 | v17 实现 |
|---|---|
| What: Context | 经验教训注入到 prompt |
| What: Tool | 工具组合最佳实践积累 |
| What: Architecture | 分类器阈值调整 |
| When | Inter-test-time（任务完成后离线分析） |
| How | Reward-based（任务成功率作为信号） |
| Where | Domain-specific（先在 evaluation 集上闭环验证） |

**STELLA 范式参考**：四智能体协同（Manager / Dev / Critic / Tool Creator）+ 双重进化（模板库 + 工具海洋）；本项目 v17 第一版**只做模板库（Procedural Memory），不做工具创建**。

**预估工作量**：2-3 周

---

## 九、v18 — Multi-Agent + 双引擎显式化（P2，3-4 周，依赖 v15 + v16.2）

**动机**：当前 SubAgent 仅 depth=1 隔离式委派。OpenAI Agents SDK 已定义 Agent/Handoff/Guardrail 三原语，A2A 协议定义 Agent 间通信标准；CaibotStudio 企业实践证明 Workflow + Agentic Loop 双引擎应当并列。

**核心设计（融合企业实践 + 评审新增）**：

```
1. Workflow + Agentic Loop 双引擎显式化（CaibotStudio 范式，评审新增）
   - 当前 simple/complex/emergent 三路由实际上是 1.5 个引擎
   - simple 退化为 Workflow，emergent/goal-driven 是 Agentic Loop
   - 显式拆出 Workflow 引擎（DAG + 节点确定性执行）作为一档
   - 用户/上游可选"我要确定性 Workflow"还是"我要自主探索"

2. Handoff 模式（OpenAI Agents SDK 范式）
   - 与 SubAgent 隔离式互补：Handoff 是上下文传递
   - 适用专业化分工：搜索 Agent → 分析 Agent → 写作 Agent
   - 注意：会破坏 SubAgent 的 token 预算熔断与 caller_tag 闭环，需重新设计归因

3. SubAgent MCP 远端化（Codex × CodeFuse 范式，评审新增）
   - 当前 SubAgent 是 Python in-process
   - 引入"远端 SubAgent"模式：通过 MCP 调用本地 CodeFuse / 远端 Agent Server
   - 适用长任务跨进程隔离场景（避免单 Python 进程崩溃殃及全局）
   - 复用 v16 的 MCP Bridge

4. A2A Protocol 基础支持
   - Agent Card（JSON 能力声明，复用 v16.2 的 MCP capabilities）
   - Task Request/Response
   - 状态同步

5. 多 Agent 协作模式
   - 讨论（Discussion）：多 Agent 同问题不同角度
   - 辩论（Debate）：正反方交锋
   - 层级（Hierarchical）：Manager 分配 Worker
   - 对等（Peer）：共享记忆并行

6. evaluation 同步开发（评审新增）
   - 单 Agent 12 任务无法衡量协作收益
   - 需要新增"多 Agent 专属"评测子集
   - 与功能开发同步推进，不可推后
```

**三大协议关系（评审新增）**：

```
MCP (Anthropic) → 工具/数据源接入（"AI 的 USB-C"）      → v16
A2A (Google)    → Agent 间通信（"Agent 间的 HTTP"）     → v18.A2A
Workflow Engine → 确定性多步骤编排（"Agent 的 Airflow"） → v18.Workflow
```

**预估工作量**：3-4 周

---

## 十、v19 — Guardrails 安全体系（P2，2 周，依赖 v13.x 沙箱修复）

**动机**：OpenAI Agents SDK 已内置 input/output guardrail，当前系统仅 ShellTool 黑名单 + sandbox（且有 Wave-5 bug）。

**核心设计**：

```
guardrails/
├── input_guardrail.py  — 输入校验（PII 检测、越权检测）
├── output_guardrail.py — 输出校验（敏感信息过滤、质量检查）
└── tool_guardrail.py   — 工具调用校验（危险操作确认、权限检查）

集成点：
- OrchestratorAgent.run() 入口 → input_guardrail
- ReflectorAgent.reflect()    → output_guardrail
- ReActEngine tool_calls 前置 → tool_guardrail
- HITL 双门控与 tool_guardrail 协同（危险工具触发 ask_user）
```

**评审新增交付顺序（基准前置）**：

| 阶段 | 内容 | 说明 |
|---|---|---|
| v19.0 基准接入 | OWASP ASI 2026（Agentic Applications Top 10）作为主基准；AgentDojo / OS-Harm 作为补充 | **功能验收前置**，没基准的安全功能无法判断"做完没有"；OWASP ASI 2026 已于 2026 年 4 月正式发布，是首个面向自主智能体的权威安全标准 |
| v19.1 保守版 | PII 脱敏 + 工具白名单二次校验 | 误报率低的基础版本 |
| v19.2 进阶版 | Prompt injection 检测 | 仅在基准就绪后实施，避免"用 LLM 检测 LLM 攻击"的递归陷阱 |
| v19.3 协同版 | HITL × Guardrail 闭环 | 危险工具自动触发 ask_user 双确认 |

**前置依赖**：v13.x 维护批次第 2 项（Wave-5 沙箱修复）必须先完成——否则安全章节起步就有结构性漏洞

**预估工作量**：2 周

---

## 十一、整体路线图

```
v13.0 (当前)
 │
 ├── v13.x 维护批次 ──────────────── [1 周, 零号优先级]
 │   ├── codemap/CHANGELOG 回填
 │   ├── Wave-5 shell_tool.py:130 沙箱修复
 │   ├── LLMClient 推理 token 分桶
 │   ├── DAG_SERIAL_EXECUTION 默认值复盘
 │   ├── evaluation 扩样到 30+ 任务（横切前置）
 │   └── 清理 v12 占位
 │
 ├── v14 Reasoning Model + Harness 优化 ─── [2-3 周, P0, 独立]
 │   ├── ReActEngine 双协议（<think> / reasoning_tokens）
 │   ├── interleaved thinking（Claude Opus 4.5）
 │   ├── reasoning_effort × ToolRouter 联动
 │   ├── Harness 配置层抽离
 │   └── 任务持久化与恢复（resume）
 │
 ├── v15 Agentic Memory 重构 ──────────── [5-6 周, P0, 依赖 v14]
 │   ├── v15.1 检索基建（3 周, pgvector + Forms × Functions API）
 │   ├── v15.2 Memory as Tool（1 周）
 │   └── v15.3 巩固与遗忘（2 周, 含 Skill 实验）
 │
 ├── v16 MCP 全面适配 ─────────────────── [2 周, P1, 独立]
 │   ├── v16.1 通用 MCP Client
 │   └── v16.2 暴露自身为 MCP Server
 │
 ├── v17 Self-Evolution ──────────────── [2-3 周, P1, 依赖 v15 + v13.x 扩样]
 │   ├── Reflexion 范式（只做 prompt 注入，拒绝 RL）
 │   ├── 用户偏好抽取
 │   └── 分类器阈值在线调整
 │
 ├── v18 Multi-Agent + 双引擎 ─────────── [3-4 周, P2, 依赖 v15 + v16.2]
 │   ├── Workflow + Agentic Loop 双引擎显式化
 │   ├── Handoff 模式（与 SubAgent 互补）
 │   ├── SubAgent MCP 远端化
 │   ├── A2A Agent Card（复用 v16.2 capabilities）
 │   ├── 多 Agent 协作模式
 │   └── 多 Agent evaluation 同步开发
 │
 └── v19 Guardrails 安全体系 ──────────── [2 周, P2, 依赖 v13.x 沙箱修复]
     ├── v19.0 AgentDojo / OS-Harm 基准（验收前置）
     ├── v19.1 PII + 白名单二次校验
     ├── v19.2 Prompt injection 检测
     └── v19.3 HITL × Guardrail 闭环
```

**总工期估算**：17-20 周（v13.x 1 周 + v14 3 周 + v15 5-6 周 + v16-v19 9-10 周，其中 v16 / v17 / v18 / v19 之间可与 v14 / v15 部分并行）

**关键依赖**：

- **v13.x 是横切前置**：evaluation 扩样阻塞 v17 / v18 / v19 三个版本的自动调优能力
- **v14 → v15**：Memory 重构依赖推理 token 分桶（否则上下文统计失真）
- **v15 → v17 / v18**：Self-Evolution 与 Multi-Agent 共享记忆均强依赖 Memory
- **v16.2 → v18**：A2A Agent Card 复用 MCP capabilities，避免两套能力声明
- **v13.x → v19**：Guardrails 不能在沙箱已知漏洞上构建

---

## 十二、风险与依赖（评审新增）

| 风险 | 影响版本 | 缓解策略 |
|---|---|---|
| **Memory 与 ContextManager 双压缩冲突** | v15 | v15.1 必须接管或继承 `context/manager.py`，禁止两套压缩并存；接口层做兼容垫片 |
| **evaluation 样本量阻塞自动调优** | v17 / v18 / v19 | v13.x 扩样到 30+ 任务作横切前置；不允许在 12 任务下做统计推断 |
| **OTel tracing × Memory as Tool（span 爆炸）** | v15.2 / v18 | TracingBridge 引入"工具类型采样"，Memory 工具默认低采样率 |
| **Bilingual comment 审查瓶颈** | v15 / v18 大模块 | 预留 0.5 周注释审查 buffer；考虑写在 docstring 而非行内 |
| **HITL × Handoff 边界未决** | v18 | 设计阶段先答"被 Handoff 出去的子 Agent 能否问用户"；当前 AskUserTool 仅在 depth=0 可用，Handoff 后归属如何判定 |
| **A2A Agent Card 安全漏洞** | v18 | 2026-04 安全研究员发现 A2A 协议"名片交换"机制存在 3 行代码可利用漏洞，可泄露用户数据（如酒店订单）；v18 实现 A2A 时必须加入 Agent Card 完整性校验与签名机制 |
| **推理范式重写风险** | v14 | interleaved thinking 是范式重写而非小适配；保留 ReActEngine v1 作为 fallback，新 reasoning_engine 灰度切换 |
| **MCP outputSchema 强校验破坏现有工具** | v16.1 | 桥接层提供"宽松模式"开关，过渡期允许无 outputSchema 的工具继续工作 |

---

## 十三、与企业实践对照（评审新增）

| 企业实践 | 我们的现状 | 差距 / 借鉴价值 |
|---|---|---|
| **CaibotStudio**（ATA #11020642405）5 层架构 + 8 治理面 + 双引擎 | 3 层（agents/dag/react）+ 弱治理 + 1.5 引擎 | 我们处于早期阶段，不必照搬规模；但**双引擎显式化**值得借鉴（已纳入 v18） |
| **Codex × CodeFuse**（ATA #12020632801）Master-Worker via MCP | 仅 in-process SubAgent | 价值在跨进程隔离，长任务尤其重要（已纳入 v18 SubAgent MCP 远端化） |
| **Anthropic Claude Code Skills** | 无对应抽象 | "Skill = 文件夹 + 可声明触发条件"是轻量 Procedural Memory 的现成形态（v15.3 / v17 参考） |
| **腾讯云 Agent Memory** | 关键词重叠检索 | 四层渐进式架构（L0 原始对话→L1 原子记忆→L2 场景聚类→L3 用户画像）；接入后准确率提升 59%；**v15 可直接借鉴分层思路**，无需照搬其 pgvector 全栈 |
| **Qoder / 通义灵码** 任务恢复 | 无 | 长任务核心痛点是上下文压缩 + 任务恢复（已纳入 v14） |
| **OpenAI Agents SDK** Guardrail | 仅 ShellTool 黑名单（且有 Wave-5 bug） | 三原语（Agent/Handoff/Guardrail）已成事实标准；input/output guardrail 直接借鉴（v19） |

---

## 十四、参考资源

### 论文

| 论文 | 会议/来源 | 核心价值 |
|---|---|---|
| Memory in the Age of AI Agents: A Survey | arXiv 2025 (NUS/复旦/Stanford/Oxford 等 47 作者) | **3D 框架**（Forms × Functions × Dynamics）替代"六层"模型；7 大前沿；HuggingFace Daily Paper #1 |
| A-Mem: Agentic Memory for LLM Agents | NeurIPS 2025 (Rutgers/Ant Group) | Zettelkasten 原则的动态记忆组织；三层（Activation/Plaintext/Parameter）；LoCoMo 数据集领先 |
| From Human Memory to AI Memory: A Survey | arXiv 2025 | "对象-形式-时间"三维八象限分类 |
| A Comprehensive Survey of Self-Evolving AI Agents | arXiv 2025 (清华/北大/MSRA 等) | 四维框架（What/When/How/Where）+ 三定律（Endure/Excel/Evolve）+ 开源 EvoAgentX 框架 |
| STELLA: Self-Evolving LLM Agent for Biomedical Research | 2025 | 四智能体协同（Manager/Dev/Critic/Tool Creator）+ 双重进化；准确率 14% → 26% |
| DeepSeek-R1: Incentivizing Reasoning via RL | Nature 2025（封面文章，首个通过顶级期刊同行评审的 LLM） | RLVR / GRPO；无 SFT 的 RL 推理训练；论文 86 页全公开 |
| DeepSeek-V3.2 | 2025 | DSA（稀疏注意力）+ Scaling GRPO；MoE + MLA |
| Reflexion: Language Agents with Verbal Reinforcement Learning | NeurIPS 2023 | v17 prompt 注入范式的理论基础 |
| MEXTRA: Memory Extraction Attack | ACL 2025 | Agent 记忆隐私风险，v19 安全设计参考 |
| AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks | NeurIPS 2024 | v19 红队基准 |
| OS-Harm | 2025 | OS-level Agent 安全基准，v19 参考 |
| ReflAct | EMNLP 2025 | v8 GoalDriven 已参考 |
| MemOS (MemTensor) | 2025.7 | 首个 LLM 记忆操作系统概念 |
| Code LLM 全景综述（303 页） | 北航/阿里/字节 | Code Agent 全景指南 |
| LLMs 2025 年度报告 (Sebastian Raschka) | 2025 年终 | RLVR/GRPO/推理扩展/工具使用/架构岔路口 |
| Meta-Harness: End-to-End Optimization of Model Harnesses | arXiv 2026 (Stanford IRIS Lab) | LLM 自优化 Harness 达 76.4% 通过率，超越人工设计；v14 Harness 优化远期参考 |
| OWASP Top 10 for Agentic Applications (ASI 2026) | OWASP 官方 2026-04 | 首个面向自主智能体的权威安全标准；ASI01 目标劫持 / ASI02 工具滥用 / ASI06 记忆污染 / ASI07 不安全 Agent 通信 / ASI10 失控 Agent；v19 主基准 |
| Harness Engineering: Leveraging Codex in an Agent-First World | OpenAI 工程博客 2026-02 | 3 名工程师 + Codex 5 个月生成 100 万行代码零手写；Harness Engineering 范式的引爆点 |

### 行业框架

| 框架 | 核心特性 |
|---|---|
| OpenAI Agents SDK (v0.14.2+) | Agent/Handoff/Guardrail 三原语；MCP 内置；26079 GitHub Stars；100+ LLM 提供商；**2026-04-15 大更新：原生沙箱 + MCP 集成** |
| Anthropic Claude Code | Subagent 模式（v9 参考）；MCP 发起者；**Skill 系统**（v15.3 / v17 参考）；Harness 设计文档（检查点 + 人工介入） |
| A2A Protocol v1 (Google) | Agent 间通信开放标准；Agent Card；150+ 企业接入；**MAF 已支持 A2A v1**；⚠️ 2026-04 发现 Agent Card 名片交换机制安全漏洞 |
| MCP (Linux 基金会 AAIF) | 2025-11-25 规范；Streamable HTTP + OIDC + outputSchema；3000+ 工具生态；Windows 11 26H2 原生支持 |
| Letta (MemGPT 继任者) | OS 级记忆调度 + 原生多 Agent + sleep-time compute |
| EvoAgentX | 首个开源自进化 Agent 框架（清华/北大等） |
| LangGraph / CrewAI / AutoGen | 多 Agent 编排，图状态机，角色专业化 |
| Microsoft Agent Framework (MAF) | .NET 原生 + A2A v1 支持 |
| CaibotStudio（阿里内部） | 5 层 + 8 治理面 + Workflow + Agentic Loop 双引擎 |
| **LangChain Deep Agents** (2026-01) | 官方定义的 Agent Harness；Planning + 文件系统 + Sub-agent + LangGraph 持久化；8.8k+ stars；Deep Agents Deploy beta (2026-04)；Harrison Chase："2026 = Long-Horizon Agents 元年" |
| **OpenHarness** (HKUDS) | 纯 Python 轻量 Harness；3% 代码实现 80% 功能；43 工具 + Markdown 持久记忆 + 并行执行 |
| **DeerFlow 2.0** (ByteDance) | Super Agent Harness；47.3k Stars；从 Deep Research 升级为通用 Agent Harness |
| **Microsoft MDASH** | 多 Agent 安全系统；100+ 专业化 Agent 协作；登顶 CyberGym 安全基准 88.45%（超越 Anthropic Mythos 83.1%） |

### ATA 内部参考

| 文章 | 编号 | 与 roadmap 的关系 |
|---|---|---|
| CaibotStudio 企业级 Agent 平台架构 | ATA #11020642405 | v18 双引擎显式化的范式来源；安全治理 8 个面参考 |
| Codex × CodeFuse Master-Worker via MCP | ATA #12020632801 | v18 SubAgent MCP 远端化的范式来源 |
| Agent Memory 体系实战 | （ATA 检索）| v15 Memory as Tool 的工程实践参考 |
| Harness Engineering 中文讨论 | （ATA 检索）| v14 优先级上调到 P0 的依据 |
| AI 工程师空间 / 通义灵码 长任务实践 | （ATA 检索）| v14 任务恢复设计参考 |

### 技术博文

| 文章 | 来源 | 核心观点 |
|---|---|---|
| Agent 三重觉醒: Tool/Plan/Memory | CSDN | Tool→开放世界, Plan→深度推理, Memory→持续成长；MCP="AI 的 USB"；A2A=Agent 间 HTTP |
| LLM Agent 记忆分类及存储设计建议 | CSDN | 6 种记忆 × 存储组件选型 × 数据模型设计 |
| 5 款开源 Agent 记忆框架横评 | GitCode | A-MEM / Mem0 / Zep / Letta / MemOS |
| 自进化智能体如何实现自我进化 | CSDN | 四维框架详解 + TextGrad "文本反向传播" |
| 基于反馈循环的自我进化 AI 智能体 | 博客园/SegmentFault | OpenAI Cookbook 四步闭环 |
| MCP 协议深度解析: AI Agent 的"USB-C 接口" | CSDN | MCP 完整架构拆解 |
| MCP 协议工程实践 2026 | CSDN | 生产级 MCP Server 构建；Python MCP SDK 实战 |
| A2A/MCP/ACP 协议大战 | 知乎 | 三大协议对比 |
| 2026 AI Agent 十大趋势 | CSDN | 多模态/自主决策/端侧/具身/A2A 量产 |
| Harness Engineering 实证 | Can Bölük 实验 / OpenAI 工程博客 | 同模型换 Harness 42%→78%；仅改编辑格式(hashline) 6.7%→68.3%（10 倍，零训练成本） |
| 信息访问 vs. 推理能力: LLM Agent 性能归因 | 腾讯新闻 | Agent 性能提升的归因分析框架 |
| Harness Engineering: AI Agent 落地企业的工程化核心 | CSDN | Harness 五大维度完整架构；Anthropic 首创 → OpenAI 推广的溯源；88 个 AGENTS.md 分布式上下文 |
| "我"与 Harness Engineering: DeepBot 是怎么理解"控制"的 | 格灵深瞳 DeepBot | 多源组装 System Prompt / 分层工具体系 / 全局与局部记忆分治 / 多 Tab 上下文隔离 |
| Harness 工程必读，AI Agent 入门首选 | CSDN | 从 Prompt → Context → Harness 三次跃迁的完整知识体系；《Harness 工程》书评 |
| 最近 AI 圈爆火的 Harness Engineering | 苏三（CSDN） | Agent = Model + Harness 公式详解；Claude Code 51 万行源码六大模块还原；腾讯/阿里/字节大厂押注 |
| Prompt、Context、Harness: AI Agent 工程的三层架构解析 | DeepHub | 三者不是竞争而是分层；"多数 Agent 失败不是模型失败而是配置失败" |
| 红杉对话 LangChain 创始人: 2026 告别对话框步入 Long-Horizon Agents 元年 | 今日头条 | Harrison Chase: "2026 = Agent Harness 年"；Deep Agents = batteries-included Harness |
| 面向 AI 智能体的红队测试实战: 基于 OWASP ASI 2026 | 同花顺财经 | OWASP ASI 2026 十大风险详解 + 金融场景红队方法论全流程 |
| 2026 主流 Agent Memory 方案横评与选型指南 | SegmentFault | 腾讯云 Agent Memory 四层架构(L0-L3)；pgvector / Milvus / Zep / Mem0 / Letta 横评 |
| Agentic AI 时代，向量数据库成"必选项" | 钛媒体/网易 | Gartner 预测 2028 年生成式 AI 数据库支出达 2180 亿美元；Zilliz/Milvus 定位 Agentic AI 基础设施 |
| 当 PostgreSQL 遇见 AI，数据库的 AI 进化论 | 百家号/腾讯云 | pgvector 在 2023 年成为 AI 浪潮分水岭；Stack Overflow 连续 7 年开发者调研 PG 爆炸式增长 |
| Agent 的安全边界: 如何防止 AI 失控 | CSDN | 真实事故：Claude Opus 4.6 Agent 9 秒删除生产数据库；CLTR 记录近 700 起 AI 失控事件 |
| 2026 年 AI Agent 行业: OWASP Agentic Top 10 带来的警醒 | 知乎 | OWASP ASI 2026 正式发布；88% 组织已确认 AI Agent 安全事件；Gravitee 调研 919 位高管数据 |
| A2A v1 Is Here: Cross-Platform Agent Communication | Microsoft Learn | MAF 正式支持 A2A v1；.NET 原生跨平台 Agent 通信实现 |
| Google A2A 协议曝 3 行代码漏洞 | 网易 | Agent Card 名片交换机制安全漏洞；v18 A2A 实现必须加入完整性校验 |

---

**修订记录**：

- v1 (2026-05-21 初版)：六层记忆架构、v10-v15 提案、二次搜索 — 已作废
- v2 (2026-05-21 评审重构)：版本号校正为 v14-v19、3D 记忆框架、新增 Harness Engineering / 双引擎 / Master-Worker via MCP 等 9 项、加入 v13.x 维护批次、加入风险与企业实践对照
- v3 (2026-05-22 三次搜索定稿)：新增 18 篇参考资源；OWASP ASI 2026 升格为 v19 主基准；新增 Harness 三大实证（Meta-Harness 76.4% / hashline 10 倍提升 / OpenHarness 3% 代码 80% 功能）；新增 A2A 安全漏洞风险项；新增 LangChain Deep Agents / DeerFlow 2.0 / MDASH 框架；新增 Anthropic Harness 检查点机制参考；新增腾讯云 Agent Memory 四层架构参考
