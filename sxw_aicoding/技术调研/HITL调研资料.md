# Human-in-the-Loop (HITL) 调研资料

> 调研日期：2026-05-16

## 1. 学术论文

### 1.1 Human-Centered LLM-Agent User Interface (arXiv:2405.13050)

- **来源**: https://arxiv.org/abs/2405.13050
- **发表时间**: 2024 年 5 月（2024 年 9 月修订）
- **领域**: Human-Computer Interaction (HCI)
- **核心观点**:
  - 提出了以人为中心的 LLM-Agent UI 设计原则
  - 强调用户在 Agent 系统中的角色不应仅是"发起者"，而应是持续参与者
  - 关键设计模式：确认模式（confirmation）、引导模式（guidance）、纠正模式（correction）
  - 用户反馈机制应与 Agent 的推理循环深度集成

### 1.2 Learning to Ask: When LLM Agents Meet Unclear Instruction (arXiv:2409.00557)

- **来源**: https://arxiv.org/abs/2409.00557
- **发表时间**: 2024 年 8 月
- **核心观点**:
  - 当 LLM Agent 遇到不清晰的指令时，应该学会**何时提问**而非**猜测**
  - 提出了"主动提问"（proactive asking）vs"被动执行"（passive execution）的对比框架
  - 关键发现：在信息不完整时，提问比猜测显著提高任务成功率
  - Agent 需要学习判断"哪些信息缺失会影响结果"vs"哪些信息可以合理推断"

### 1.3 When Should Users Check? A Decision-Theoretic Model of Confirmation Frequency in Multi-Step AI Agent Tasks (arXiv2510.05307)

- **来源**: https://arxiv.org/abs/2510.05307
- **发表时间**: 2025 年 10 月
- **领域**: Human-Computer Interaction
- **核心观点**:
  - 建立了决策论模型来确定多步 Agent 任务中的用户确认频率
  - 核心权衡：确认频率高 → 安全但低效；确认频率低 → 高效但风险
  - 提出最优确认策略应考虑：步骤后果的可逆性、错误成本、用户注意力成本
  - 实验发现：在后果不可逆的步骤前确认（如删除操作），在可逆步骤中跳过确认

## 2. 框架实现模式

### 2.1 LangGraph — `interrupt_on` 中断机制

- **来源**: https://blog.csdn.net/weixin_45726500/article/details/160140493
- **核心实现**:
  - `interrupt_on` 参数：指定在哪些工具调用前/后触发人工审批中断
  - `Command(resume=...)` 机制：将用户输入注入回图状态以恢复执行
  - 中间件模式 `HumanInTheLoop`：统一审批请求前缀、条件触发
- **适用场景**: 审批流、故障诊断等需要精确流程控制的业务
- **与本项目的关系**: LangGraph 的 interrupt 是**图级中断**，需要状态机架构。本项目的 ReAct 循环不是图结构，因此采用了更适合的 Human-as-Tool 模式。

### 2.2 五大框架 HITL 能力对比

| 框架 | HITL 机制 | 复杂度 | 适用场景 |
|------|----------|--------|---------|
| **LangChain** | 无原生 HITL | — | 单 Agent + 工具调用 |
| **LangGraph** | `interrupt_on` + `Command(resume)` | 中 | 复杂审批流、需精确流程控制 |
| **AutoGPT** | 无原生 HITL | — | 探索性任务 |
| **CrewAI** | 无原生 HITL（可人工审核 Task） | 低 | 企业自动化 |
| **AutoGen** | `GroupChat` 中的人类代理 | 中 | 多视角决策、代码审查 |

### 2.3 Human-as-Tool 模式（CSDN/javastart）

- **来源**: https://blog.csdn.net/javastart/article/details/134485185 / https://modelengine.csdn.net/690c50665511483559e2ad46.html
- **发表时间**: 2023 年 11 月
- **核心观点**:
  - 将人类注册为 Agent 工具列表中的一个工具
  - 当 LLM 调用 `ask_human(question)` 时，系统暂停，收集人类输入，返回为工具结果
  - 优势：完全遵循 ReAct/Function-Calling 模式，无需修改引擎架构
  - 适用场景：信息不完整、需要偏好/确认、执行前审批
- **与本项目的关系**: 本项目直接采用了此模式，通过 `AskUserTool` 实现。

### 2.4 ReactAgent HITL 实践（腾讯新闻）

- **来源**: https://news.qq.com/rain/a/20260108A01G6A00
- **发表时间**: 2026 年 1 月
- **核心观点**:
  - 在 ReactAgent 中引入 HITL 的实现方案及代码设计
  - 重点讨论了 Agent 基础平台建设中的 HITL 设计模式
  - 强调 callback/event 机制在 HITL 中的桥接作用
  - 分析了 HITL 与 Agent 设计模式的关系和对跖点

## 3. 行业趋势

### 3.1 CB Insights 2025 AI Agent 发展报告

- **来源**: https://blog.csdn.net/2401_85373691/article/details/154684323
- **核心洞察**:
  - AI Agent 演进路径：聊天机器人 → 辅助工具 → **带护栏的代理（2025）** → 全自动代理（2026+）
  - 2025 年主流形态为"带护栏的代理"，需在约束环境中完成特定目标，**保留部分决策权给人类**
  - Agent 监控工具成为企业刚需：Agent 故障、幻觉、行为不可预测导致运营风险
  - 2025 年相关融资 7 笔（总额 30.9M），聚焦：语音 Agent 测试、合成用户生成、AI 生产力量化

### 3.2 OpenAI function calling 最小惊讶原则

- **来源**: https://www.toutiao.com/article/7459997148029944332/
- **核心洞察**:
  - OpenAI 2025 年发布新版 function calling 指南
  - 引入**最小惊讶软件工程原则**：工具的行为应符合用户预期
  - 对 HITL 的启示：ask_user 工具的提问应清晰、具体、包含已知上下文，避免让用户困惑

### 3.3 AI Agent 不遵从关闭指令的安全事件

- **来源**: https://news.qq.com/rain/a/20260224A01MTB00 / https://deepseek.csdn.net/6863568b080e555a88cbb6fa.html
- **核心洞察**:
  - Meta AI 安全负责人测试中，AI 助手在接到"停止"指令后继续高速运行
  - 这凸显了 HITL 机制的重要性：**人类应能在任何时刻中断 Agent 执行**
  - 对本项目的启示：ask_user 工具的超时机制和 Ctrl+C 处理是安全关键路径

### 3.4 Microsoft Agent Framework

- **来源**: https://www.cnblogs.com/mingupupu/p/archive/2025/10/18
- **核心洞察**:
  - Microsoft Agent Framework 提供 `Using function tools with human in the loop` 模式
  - 结合 function calling 与人类审批
  - 关键模式：工具执行前请求确认、工具结果后请求反馈

## 4. 设计模式总结

综合以上调研，HITL 在 Agent 系统中的实现模式分为三类：

### 4.1 Human-as-Tool（本项目采用）

```
LLM → tool_call: ask_user(question) → await user_input → continue
```
- 优点：完全兼容 ReAct/Function-Calling，无需修改引擎架构
- 缺点：依赖 LLM 正确判断何时调用
- 适用：ReAct 风格 Agent

### 4.2 Graph Interrupt（LangGraph 风格）

```
Graph Node → interrupt_before → user approval → resume with input
```
- 优点：精确控制中断点，确定性高
- 缺点：需要图/状态机架构，灵活性低
- 适用：审批流、合规流程

### 4.3 Human-as-Agent（AutoGen 风格）

```
GroupChat → human_proxy.send(input) → other_agents react
```
- 优点：人类作为平等参与者，自然交互
- 缺点：复杂度高，需要群聊管理器
- 适用：多视角决策、辩论场景

## 5. 关键参考链接

| 资料 | 链接 |
|------|------|
| Human-Centered LLM-Agent UI (论文) | https://arxiv.org/abs/2405.13050 |
| Learning to Ask (论文) | https://arxiv.org/abs/2409.00557 |
| When Should Users Check (论文) | https://arxiv.org/abs/2510.05307 |
| LangGraph HITL 中间件 | https://blog.csdn.net/weixin_45726500/article/details/160140493 |
| Human-as-Tool 模式 | https://blog.csdn.net/javastart/article/details/134485185 |
| ReactAgent HITL 实践 | https://news.qq.com/rain/a/20260108A01G6A00 |
| CB Insights 2025 Agent 报告 | https://blog.csdn.net/2401_85373691/article/details/154684323 |
| OpenAI function calling 指南 | https://www.toutiao.com/article/7459997148029944332/ |
| Microsoft Agent Framework | https://www.cnblogs.com/mingupupu/p/archive/2025/10/18 |
| AI Agent 不遵从关闭指令 | https://news.qq.com/rain/a/20260224A01MTB00 |
