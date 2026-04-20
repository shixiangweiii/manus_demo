# 与生产级 Agent 的自主规划差距分析

> 本文档将当前 Demo（v4：含混合规划路由）的自主规划能力与三个生产级 Agent（Manus、Claude Code、OpenClaw）进行逐维度对比，
> 识别关键差距并给出可落地的升级方向。

---

## 三种生产级 Agent 的规划哲学

在深入对比之前，先理解三个标杆产品截然不同的规划范式——它们代表了当前 Agent 规划的三条主流路线。

### Manus — 显式 Plan-and-Act + CodeAct

Manus 采用**显式双模型架构**（Explicit Dual-Model Architecture）：

- **Planner 模型**：使用 Claude 3.5 + Answer Set Programming，将复杂任务分解为结构化的高层计划
- **Executor 模型**：将计划翻译为环境级操作，核心是 **CodeAct** 范式——不通过 JSON function calling 调用工具，而是直接**生成可执行的 Python 代码**作为行动
- **自愈循环**（Self-healing Loop）：运行测试 → 分析失败 → 修补代码 → 再次迭代，最大限度减少人类干预

关键特征：**计划是显式的、结构化的，但行动是代码化的**。规划和执行是两个独立的模型/阶段。

### Claude Code — 隐式涌现规划

Claude Code 采用了与 Manus 截然相反的设计哲学——**极简主义**（Radical Simplicity）：

- **没有独立的 Planner 模块**，只有一个 `while(tool_use)` 主循环
- 规划通过 **TODO 列表**自然涌现——模型在工作过程中自发创建和更新 TODO
- 只有 **14 个工具**（文件读写、搜索、bash、git、web），没有复杂的工具路由
- 单线程、扁平消息历史、无 critic 模式、无角色扮演
- 上下文用到 ~92% 时触发压缩器，将关键信息转存为 Markdown 文件

关键特征：**没有显式计划，规划是工具使用过程中的涌现行为**。简单循环 + 强模型 = 高自主性。

### OpenClaw — 双 Agent 分工 + 心跳自治

OpenClaw 采用**双 Agent 分离架构**（Two-Agent Split Architecture）：

- **Planner/Initializer Agent**：负责任务分解和初始化
- **Executor Agent**：负责实际执行，通过 RPC 流式模型与运行时交互
- **心跳机制**（Heartbeat）：每 30 分钟自动检查待办工作和定时任务（`HEARTBEAT.md`）
- **六种输入类型**：消息、心跳、定时任务、钩子、Webhook、Agent 间通信
- 记忆系统基于本地 Markdown + 追加式 JSONL 日志

关键特征：**规划和执行显式分离，通过心跳实现长时自治**。强调安全、可审计、本地优先。

---

## 维度一：规划范式（Planning Paradigm）

### 当前 Demo

- **三层显式 DAG 规划**：Planner 单次 LLM 调用生成 Goal → SubGoal → Action 的嵌套 JSON
- v3 增加了超步间自适应（`adapt_plan`），每个 super-step 后 Planner 可调整待执行节点
- 规划输出是**完整的 DAG 拓扑**——所有节点、边、依赖关系在执行前确定

### 生产级 Agent

| Agent | 规划范式 |
|-------|---------|
| **Manus** | Planner 生成高层计划 → Executor 逐步执行，环境反馈可触发重规划。计划粒度灵活，不固定为三层 |
| **Claude Code** | **无显式计划**。模型在循环中自发创建 TODO 列表，边做边规划，计划是动态涌现的 |
| **OpenClaw** | Planner Agent 初始化任务分解 → Executor Agent 执行，串行优先的队列调度 |

### 差距分析

**差距 1：一次性全量规划 vs 渐进式规划**

Demo 的 Planner 试图在**第一次调用时就预测整个任务的完整拓扑**。这对 LLM 是一个很高的要求——它需要在没有任何执行反馈的情况下，预估所有子任务、依赖关系和条件分支。

生产级 Agent 的做法截然不同：
- Claude Code 根本不做前期规划，完全靠运行时涌现
- Manus 的 Planner 只生成高层计划骨架，细节在执行阶段逐步展开
- OpenClaw 的 Planner 也是粗粒度初始化，细节由 Executor 在运行时决定

**核心问题**：LLM 的规划能力与任务的前期信息量成正比。执行前知道的信息最少，反而要做出最详细的计划——这是一个根本性的矛盾。

**升级方向**：
- 引入**渐进式规划**（Progressive Planning）：第一次只规划高层 SubGoal，每个 SubGoal 开始执行时再展开为具体 Action
- 或者采用 Claude Code 风格的**隐式规划**：去掉独立的 Planner，让 Executor 在 ReAct 循环中通过 TODO 列表自行规划

---

## 维度二：代码即行动（Code-as-Action / CodeAct）

### 当前 Demo

- 使用标准的 **OpenAI function calling** 调用预定义工具
- 每次只能调用一个工具，传递 JSON 参数
- 工具集是**静态定义**的：`web_search`、`execute_python`、`file_ops`
- `execute_python` 工具可以运行任意 Python 代码，但需要 LLM 在 JSON 参数中输出完整代码字符串

### 生产级 Agent

| Agent | 行动方式 |
|-------|---------|
| **Manus** | **CodeAct**——LLM 直接生成 Python 代码作为行动，利用变量、循环、条件、库生态，比 function calling 表达力强得多 |
| **Claude Code** | 14 个工具中包含 **bash** 工具，可执行任意 shell 命令；file_edit 支持 diff-based 精确编辑 |
| **OpenClaw** | Shell 访问 + 文件系统操作 + 浏览器自动化，与运行用户同权限 |

### 差距分析

**差距 2：function calling 的表达力天花板**

当前 Demo 的工具调用模式：

```json
{"name": "execute_python", "arguments": {"code": "print('hello')"}}
```

Manus 的 CodeAct 模式：

```python
import requests
data = requests.get("https://api.example.com/data").json()
filtered = [item for item in data if item["score"] > 0.8]
with open("results.csv", "w") as f:
    for item in filtered:
        f.write(f"{item['name']},{item['score']}\n")
print(f"Saved {len(filtered)} items")
```

区别在于：
- **function calling**：每次只能做一件事，工具之间不能直接传递数据，中间状态需要通过 LLM 重新推理
- **CodeAct**：一段代码可以完成多个步骤、自由组合数据、使用任意第三方库，表达力与人类编程等价

**升级方向**：
- 短期：增加 **shell 命令执行工具**，让 LLM 可以运行任意 bash 命令
- 长期：考虑引入 CodeAct 模式——LLM 输出的不是工具调用 JSON，而是直接可执行的 Python 代码块

---

## 维度三：上下文管理（Context Management）

### 当前 Demo

- `ContextManager` 基于粗略的 token 估算（~1 token / 3 chars）
- 超限时通过 LLM 摘要压缩旧消息
- 保留策略：始终保留 system prompt + 最近 6 条消息，压缩中间部分
- 每个 ACTION 节点的 ReAct 循环独立维护自己的消息历史

### 生产级 Agent

| Agent | 上下文策略 |
|-------|-----------|
| **Manus** | 扩展上下文窗口（v1.5/1.6）+ **外部记忆模块**处理超出 LLM 窗口的长期状态 |
| **Claude Code** | ~92% 窗口使用率时触发**压缩器**（Compressor），关键信息转存为 **Markdown 文件**作为外部记忆 |
| **OpenClaw** | 持久化本地 Markdown + **追加式 JSONL 日志**，创建持久、可审计的状态，不依赖云服务 |

### 差距分析

**差距 3：节点间的信息孤岛**

Demo 中每个 ACTION 节点的 ReAct 循环是独立的。节点 A 搜索到的详细结果，传递给节点 B 时只有 `DAGState.node_results` 中的一个字符串摘要。原始的搜索结果、中间推理、工具调用记录都丢失了。

Claude Code 的做法完全不同——它维护**一个扁平的全局消息历史**，所有工具调用和结果都在同一个上下文中，后续推理可以引用之前任意步骤的详细输出。

**差距 4：没有持久化外部记忆**

当 DAG 执行到后半段时，前面节点的详细结果可能已经被压缩丢失。生产级 Agent 会将关键中间结果写入文件系统（Markdown/JSONL），需要时重新读取，而不是全部依赖 LLM 上下文窗口。

**升级方向**：
- 引入**执行期工作日志**（Working Memory File）：每个节点完成后将结果追加写入一个共享的 Markdown 文件，后续节点可以通过 file_ops 工具按需读取
- 考虑将 DAGState 的 `node_results` 从内存字典改为文件持久化，解决大任务的上下文溢出问题
- 参考 Claude Code 的做法：在上下文压缩时，将关键事实转存到文件而不是直接丢弃

---

## 维度四：工具生态（Tool Ecosystem）

### 当前 Demo

- 3 个工具：`web_search`（mock）、`execute_python`（subprocess 沙箱）、`file_ops`（沙箱目录读写）
- 工具集**编译时静态注册**，无法运行时动态发现或加载
- web_search 是 mock 的，没有真实网络能力

### 生产级 Agent

| Agent | 工具能力 |
|-------|---------|
| **Manus** | 云端 Linux 沙箱、真实 Shell、浏览器自动化（Playwright）、真实 Web 搜索、文件系统、CodeAct 直接调库 |
| **Claude Code** | 14 个精选工具：bash、file read/write/edit、glob、grep、git、web search/fetch |
| **OpenClaw** | Shell 访问、文件系统、浏览器自动化、API 调用，与运行用户同权限 |

### 差距分析

**差距 5：缺乏真实环境交互**

Demo 最大的能力缺口是**无法与真实世界交互**：
- 搜索是 mock 的，Agent 无法获取实时信息
- 没有 shell 命令执行，无法安装依赖、运行项目、操作 git
- 没有浏览器能力，无法抓取网页、填写表单
- 文件操作仅限于沙箱目录的简单读写，没有 diff-based 编辑

**差距 6：工具粒度不当**

Claude Code 的工具设计哲学值得学习——**少而精**。它只有 14 个工具，但每个都经过精心设计：
- `bash`：一个工具覆盖几乎所有系统操作
- `file_edit`：支持精确的 diff-based 编辑，而不是全量覆写
- `grep`/`glob`：专门的搜索工具，比通用 file_read 高效得多

对比之下，Demo 的 `file_ops` 把读、写、列出三个操作合成一个工具，而缺少 shell、grep、git 等关键能力。

**升级方向**：
- P0：接入**真实 Web 搜索 API**（Tavily/SerpAPI/DuckDuckGo），保留 mock fallback
- P0：新增 **shell 命令执行工具**（`asyncio.create_subprocess_exec`，支持超时和输出捕获）
- P1：新增**网页内容抓取**（httpx + markdownify）
- P1：文件操作增加 **diff-based 编辑**模式
- P2：考虑 MCP 协议支持动态工具发现

---

## 维度五：错误恢复与自愈（Error Recovery & Self-Healing）

### 当前 Demo

- **节点级重试**：ReAct 循环内重复调用工具（最多 `MAX_REACT_ITERATIONS` 次）
- **v3 工具路由**：连续失败 2 次后建议切换替代工具
- **局部重规划**：Reflector 评估失败后，仅重建失败子树
- **回滚机制**：ROLLBACK 边触发清理操作

### 生产级 Agent

| Agent | 自愈策略 |
|-------|---------|
| **Manus** | **自愈循环**：运行测试 → 分析失败 → 修补代码 → 重新测试，闭环迭代直到通过 |
| **Claude Code** | 每次迭代的**全部历史**都在上下文中，失败信息自然传递给下一次推理，模型自行调整策略 |
| **OpenClaw** | 证据优先日志（Evidence-first Logging）+ 最小权限 + 心跳监督限制爆炸半径 |

### 差距分析

**差距 7：缺乏基于证据的自愈**

Demo 的错误恢复是**结构化的**（重规划失败子树），但不是**基于证据的**。当一个节点失败时：

- Demo 做法：Reflector 判定失败 → Planner 重新生成子树 → 用新节点替换
- Manus 做法：保留完整的错误日志 → Agent 分析具体失败原因 → 针对性修补 → 验证修补结果

差别在于：Demo 的重规划是"重新开始"，Manus 的自愈是"在失败基础上改进"。

**差距 8：缺少验证-修复闭环**

Manus 的自愈循环有一个关键特征：**每次修复后都会运行验证**（测试/检查），确认修复有效才继续前进。Demo 的局部重规划没有这个验证环节——新子树生成后直接执行，不会验证重规划方案本身的合理性。

**升级方向**：
- 失败节点保留**完整的错误上下文**（工具调用记录、错误消息、堆栈跟踪），传递给重规划的 Planner
- 重规划后增加**方案验证步骤**：让 Reflector 先评估新计划的合理性，再交给 Executor 执行
- 引入**测试驱动修复**：对于代码类任务，失败后自动运行相关测试，将测试输出作为修复依据

---

## 维度六：Human-in-the-Loop

### 当前 Demo

- **完全自主执行**，没有任何人类干预点
- 用户提交任务后只能等待最终结果
- 无法在执行中途修正方向、确认关键决策或审批高风险操作

### 生产级 Agent

| Agent | 人机交互 |
|-------|---------|
| **Manus** | 审批机制——高风险操作前暂停等待确认 |
| **Claude Code** | 默认在每次工具调用前**请求用户确认**，可设置为自动模式跳过；支持执行中途人类修正 |
| **OpenClaw** | 配置化的权限层级，`HEARTBEAT.md` 定义自治范围 |

### 差距分析

**差距 9：全有或全无的自治**

Demo 目前是"全自动"的——要么完全自主执行，要么不执行。缺乏中间态：

- 不能在 DAG 生成后让用户审批计划再执行
- 不能对危险操作（删除文件、执行代码）设置审批门
- 不能在执行中途接受用户的修正输入
- 不能让用户在自适应规划时参与决策

Claude Code 的设计特别精巧：**默认交互式**（每步确认），用户可以根据信任度逐步放开到自动模式。这种"渐进信任"比 Demo 的"全有或全无"更符合实际使用场景。

**升级方向**：
- 在 `TaskNode` 上增加 `requires_approval: bool` 属性
- DAG 生成后暂停，展示计划并等待用户确认
- 高风险节点（`risk_level = "high"`）执行前自动暂停等待审批
- 自适应规划提出 REMOVE/ADD 操作时，可选地征求用户意见

---

## 维度七：长时自治与状态持久化（Long-Running Autonomy）

### 当前 Demo

- **单次会话、单次任务**——用户输入一个任务，系统执行完毕，结束
- 长期记忆仅记录任务摘要和关键学习，不保留执行细节
- 没有定时任务、后台运行或跨会话续作能力
- 如果执行中断（进程崩溃），所有进度丢失

### 生产级 Agent

| Agent | 长时自治 |
|-------|---------|
| **Manus** | 云端持续运行，支持多步骤长任务，外部记忆模块维持超出上下文窗口的长期状态 |
| **Claude Code** | 上下文压缩 + Markdown 外部记忆，支持长时间自主会话；`relay race baton handoff` 模式跨会话传递状态 |
| **OpenClaw** | **心跳机制**每 30 分钟自动唤醒检查待办；JSONL 日志持久化全部状态；支持 cron 定时任务 |

### 差距分析

**差距 10：无法从中断恢复**

Demo 有 Checkpoint 机制（每个 super-step 后快照），但这些快照只存在内存中，进程退出就丢失。生产级 Agent 将状态持久化到磁盘，可以从任意 checkpoint 恢复执行。

**差距 11：缺乏后台自治能力**

OpenClaw 的心跳机制是一个很有启发性的设计——Agent 不是等用户来触发，而是**自己定期醒来检查有没有待办工作**。这使得 Agent 可以：
- 监控长时间运行的任务
- 定时执行例行工作
- 在用户不在线时继续推进项目

**升级方向**：
- 将 Checkpoint 持久化到磁盘（JSON 文件），支持进程重启后恢复
- 增加**会话续作**能力：`python main.py --resume <checkpoint_file>`
- 长期考虑：引入心跳机制实现后台自治

---

## 维度八：可观测性与调试（Observability & Debugging）

### 当前 Demo

- Rich CLI 实时展示 DAG 状态、super-step 进度、工具调用
- v3 增加了 `plan_adaptation` 事件展示
- 缺乏结构化的执行追踪（Trace）

### 生产级 Agent

| Agent | 可观测性 |
|-------|---------|
| **Manus** | 详细的结构化日志，支持多步骤执行链路追踪 |
| **Claude Code** | 扁平消息历史天然可追溯，上下文压缩时生成完整摘要日志 |
| **OpenClaw** | **追加式 JSONL 日志**记录全部交互，创建持久、可审计的完整执行轨迹 |

### 差距分析

**差距 12：缺乏结构化执行轨迹**

Demo 的 Rich UI 只是实时展示，没有持久化的结构化 Trace。执行结束后，无法回溯：
- 每个节点调用了哪些工具，传了什么参数，返回了什么
- 每次 LLM 调用消耗了多少 token，花了多少时间
- 自适应规划做了哪些调整，基于什么推理

OpenClaw 的 JSONL 追加日志是一个很好的参考——简单、持久、可审计。

**升级方向**：
- 每次执行生成一个 JSONL Trace 文件，记录所有事件（LLM 调用、工具执行、状态变更、自适应决策）
- 统计 Token 用量和估算成本
- 支持 Trace 文件回放和可视化

---

## 维度九：规划质量保障（Plan Quality Assurance）

### 当前 Demo

- 依赖 LLM 单次调用的 JSON 输出质量
- `_parse_dag()` 有降级处理（LLM 输出不合规时创建单节点兜底）
- 通过拓扑排序验证 DAG 无环
- 没有对计划本身的质量评估

### 生产级 Agent

| Agent | 质量保障 |
|-------|---------|
| **Manus** | Planner 使用 **Answer Set Programming** 进行形式化约束求解，确保计划逻辑一致 |
| **Claude Code** | 无需保障——没有显式计划，每一步都是基于当前完整上下文的最优决策 |
| **OpenClaw** | 双 Agent 分离 + 串行优先队列，Planner 输出经过 Executor 的二次验证 |

### 差距分析

**差距 13：规划的"一锤子买卖"**

Demo 的 Planner 用一次 LLM 调用生成完整的 DAG，这个过程没有任何验证或迭代：
- 没有检查生成的 Action 是否真的可以被现有工具执行
- 没有检查 exit criteria 是否可验证
- 没有让 LLM 自我审查计划的合理性
- 依赖关系可能不合理（LLM 有时会生成多余的串行依赖，降低并行度）

Manus 的做法更稳健——结合 Answer Set Programming 进行形式化验证，确保计划在逻辑上是可行的。

**升级方向**：
- 计划生成后增加**自审环节**：让 LLM 审查自己的计划（"Review this plan for issues"），修正后再执行
- 验证 Action 描述是否匹配可用工具能力（如 Action 要求"发送邮件"但没有邮件工具）
- 检测不必要的串行依赖，自动优化并行度

---

## 维度十：模型能力依赖（Model Capability Dependency）

### 当前 Demo

- **单模型、同模型**：Planner、Executor、Reflector 都使用同一个 LLM
- 所有 LLM 交互都走 JSON mode（`think_json`），依赖模型的 JSON 输出能力
- 规划质量完全取决于单个模型的推理能力

### 生产级 Agent

| Agent | 模型策略 |
|-------|---------|
| **Manus** | **多模型路由**：Planner 用强推理模型（Claude 3.5），Knowledge Agent 用 Qwen-72B，不同任务不同模型 |
| **Claude Code** | 单模型（Claude），但模型本身极强，且架构设计使得对模型能力的依赖最小化 |
| **OpenClaw** | 支持多 Provider（Anthropic/OpenAI/xAI），可灵活切换 |

### 差距分析

**差距 14：没有模型路由，规划质量受限于最弱环节**

Demo 用同一个模型做所有事。但不同任务对模型的能力要求不同：
- 规划需要强推理能力（适合 Claude / DeepSeek-R1）
- 简单工具调用只需要基本的 function calling 能力（用轻量模型即可，省成本）
- 代码生成需要代码专长模型

用同一个模型意味着：要么用强模型（成本高），要么用弱模型（规划质量差）。

**升级方向**：
- 引入 `llm/router.py`，按任务类型路由到不同模型
- Planner 用强推理模型，Executor 的简单工具调用用轻量模型
- 支持多 Provider fallback（主模型超时/限流时自动切换）

---

## 总结：差距优先级排序

按对"自主规划"能力的影响程度排序：

| 优先级 | 差距 | 核心问题 | 对标 |
|--------|------|---------|------|
| **P0** | 一次性全量规划 | LLM 在信息最少时做最详细的计划 | Manus 的渐进展开 / Claude Code 的隐式涌现 |
| **P0** | 缺乏真实工具 | Agent 无法与真实世界交互 | 三者都有真实 shell + web + 文件 |
| **P1** | 节点间信息孤岛 | 中间结果无法在节点间充分传递 | Claude Code 的全局扁平历史 |
| **P1** | 缺少 Human-in-the-Loop | 无法在关键决策点引入人类判断 | Claude Code 的渐进信任模型 |
| **P1** | 缺乏自愈闭环 | 错误恢复是"重新开始"而非"针对性修复" | Manus 的测试驱动自愈 |
| **P2** | 无持久化外部记忆 | 长任务的上下文会丢失 | Claude Code 的 Markdown 外部记忆 |
| **P2** | 规划质量无保障 | 一次 LLM 调用定成败 | Manus 的 ASP 形式化验证 |
| **P2** | 单模型无路由 | 成本和质量无法兼得 | Manus 的多模型路由 |
| **P3** | 无结构化 Trace | 执行过程不可审计 | OpenClaw 的 JSONL 日志 |
| **P3** | 无长时自治 | 单次任务、无法后台运行 | OpenClaw 的心跳机制 |

---

## 一个核心洞察

对比三个生产级 Agent 会发现一个有趣的分歧：

- **Manus** 走的是**结构化规划**路线——越来越精确的计划 + 形式化验证
- **Claude Code** 走的是**去规划化**路线——简单循环 + 强模型，让规划自然涌现

Demo 目前的架构（显式 DAG 规划）更接近 Manus 路线。如果继续沿这条路线升级，重点是**提高规划质量**（渐进展开、形式化验证、自审环节）。如果想探索 Claude Code 路线，则需要**大幅简化架构**——去掉 Planner，让 Executor 在一个简单循环中通过 TODO 列表自行涌现计划。

两条路线没有绝对的优劣：
- **结构化规划**更可解释、可审计、可控制，适合企业级场景
- **隐式涌现**更灵活、更适应未知任务，但高度依赖模型能力

作为教学 Demo，建议**继续走结构化规划路线**（因为显式 DAG 更容易理解和调试），同时吸收 Claude Code 的部分设计智慧（扁平上下文、简单工具集、渐进信任）。
