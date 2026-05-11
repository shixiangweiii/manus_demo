# Manus Demo v7 技术升级方案

> **版本**: v7 Draft
> **日期**: 2026-05-11
> **依据**: ATA 文章最佳实践（Agent 架构、Claude Code、Harness Engineering、AGENTS.md、Hermes 自进化）
> **范围**: Context Engineering / Memory System / Multi-Agent / Tool Design / Harness Engineering
> **排除**: RL 训练闭环（不涉及模型训练）

---

## 1. 背景与目标

### 1.1 当前架构评估

Manus Demo v6 已有完整的混合规划系统，但在 ATA 文章实践中发现了以下工程层面的优化空间：

| 维度 | 当前状态 | ATA 最佳实践 | 差距 |
|------|---------|-------------|------|
| **上下文压缩** | 单一 LLM 摘要压缩 | 三层渐进式压缩（MicroCompact → SM Compact → Full LLM） | 压缩策略单一，无层级递进 |
| **Skills 机制** | 无，所有工具常驻上下文 | Skills 延迟加载，描述符 ~9 tokens | 上下文随工具数线性膨胀 |
| **记忆系统** | 关键词重叠度检索 | 四类分类 + LLM 语义检索 | 检索精度低，无分类 |
| **自进化** | 一次性任务执行 | 轨迹复盘 → 动态 Skill 生成 | 无经验沉淀能力 |
| **工具错误** | 简单字符串返回 | 结构化错误 + 修正建议 | Agent 难以自愈 |
| **协调者模式** | Orchestrator 部分路径直接执行 | 协调者绝对不写代码，纯委派 | 上下文污染 |
| **多 Agent 通信** | 共享 LLMClient，无协议 | JSONL 结构化协议 + Worktree 隔离 | 无隔离与协议 |
| **架构约束** | 无 | lint-arch 编码化约束 | 无机械执行层 |

### 1.2 升级目标

```
v7 核心目标：
1. Context 三层压缩 —— 降低 Token 消耗 40-60%，保护关键决策不丢失
2. Skills 延迟加载 —— 工具定义常驻改为按需加载，上下文减少 30-50%
3. 四类记忆 + 语义检索 —— 记忆召回准确率提升至 85%+
4. 动态 Skill 自进化 —— 轨迹复盘自动生成 Skills，重复错误率降低
5. ACI 工具错误结构化 —— 工具错误自愈率从 <20% 提升至 70%+
6. 纯协调者模式 —— Orchestrator 完全不执行，只做委派
7. 子 Agent 隔离协议 —— JSONL inbox + Worktree 隔离
```

---

## 2. 系统架构总览

### 2.1 整体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                         OrchestratorAgent                          │
│                     (纯协调者，零执行，全委派)                      │
└──────────────────────────────────────────────────────────────────┘
                    │                    │                    │
          ┌────────┴────────┐ ┌───────┴────────┐ ┌────────┴────────┐
          │  SubAgent (Simple) │ │ SubAgent (DAG)  │ │ SubAgent (Emergent)│
          │  ModelSelector     │ │  DAGExecutor     │ │  EmergentPlanner   │
          │  委派给Executor  │ │  Worktree隔离    │ │  Worktree隔离      │
          └───────────────────┘ └─────────────────┘ └─────────────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                   │
              ┌─────┴─────┐    ┌──────┴──────┐    ┌──────┴──────┐
              │ ContextMgr │    │   Memdir    │    │ SkillStore  │
              │ (三层压缩) │    │ (四类记忆)  │    │ (延迟加载) │
              └───────────┘    └─────────────┘    └─────────────┘
                                       │
                          ┌────────────┴────────────┐
                          │   EvolutionEngine       │
                          │  (轨迹记录 → Skill生成)  │
                          └─────────────────────────┘
```

### 2.2 模块依赖关系

```
context/
├── manager.py          # [改动] 三层压缩协调器
├── micro_compact.py    # [新增] Layer1: 规则驱动微压缩
├── sm_compact.py       # [新增] Layer2: 会话记忆复用压缩
└── llm_compact.py     # [新增] Layer3: LLM 结构化摘要

memory/
├── memdir.py           # [新增] 四类记忆目录（User/Feedback/Project/Reference）
├── semantic_retriever.py # [新增] LLM 语义检索器
├── long_term.py       # [改动] 对接 Memdir 四类格式
└── short_term.py       # [新增] 四类情景记忆写入

skills/
├── store.py            # [新增] Skill 仓库（.skills/ 目录）
├── loader.py          # [新增] Skill 延迟加载器
└── generator.py       # [新增] 轨迹复盘 → Skill 自动生成

evolution/
├── trajectory_logger.py # [新增] 完整轨迹记录器
├── reviewer.py         # [新增] 后台异步复盘 Agent
└── skill_updater.py   # [新增] Skill 动态更新

tools/
├── base.py             # [改动] 结构化错误返回
├── structured_error.py # [新增] 统一错误结构
└── [all tools]        # [改动] 错误返回改为结构化

agents/
├── orchestrator.py     # [改动] 纯协调者，不执行
├── base.py            # [改动] 支持模型选择器
└── sub_agent.py       # [新增] 子 Agent 基类（隔离上下文）

subagent/
├── protocol.py         # [新增] JSONL inbox 协议
├── worktree.py         # [新增] Git Worktree 隔离
└── model_selector.py  # [新增] 任务复杂度 → 模型映射
```

---

## 3. Context Engineering：三层渐进式压缩

### 3.1 设计原理

ATA 文章（Claude Code / Agent 架构）指出：上下文按使用频率和稳定性分为多层，不同层用不同方式管理。压缩不是"全部摘要"，而是按层级选择保留策略。

### 3.2 三层压缩架构

```
上下文分层模型：
┌─────────────────────────────────────────────────────────┐
│ 常驻层：身份定义、项目约定、绝对禁止项（短、硬、可执行）  │
├─────────────────────────────────────────────────────────┤
│ 按需加载：Skills 描述符（~9 tokens）、领域知识          │
├─────────────────────────────────────────────────────────┤
│ 运行时注入：当前时间、用户偏好、工具调用结果              │
├─────────────────────────────────────────────────────────┤
│ 记忆层：MEMORY.md 跨会话经验（不直接进系统提示）        │
├─────────────────────────────────────────────────────────┤
│ 系统层：Hooks / 代码规则处理的确定性逻辑（不进上下文）   │
└─────────────────────────────────────────────────────────┘

三层压缩触发流程（AUTOCOMPACT_BUFFER_TOKENS = 13000）：
┌────────────────────────────────────┐
│ 上下文剩余空间 < 13000 tokens？    │
└────────────────┬─────────────────┘
                 ▼
     ┌────────────────────────────┐
     │ Layer1: MicroCompact        │
     │ 规则驱动，无 LLM 调用        │
     │ ① 工具输出按时间截断        │
     │ ② KV Cache 边界外压缩        │
     └────────────┬───────────────┘
                  ▼
         ┌───────────────────┐
         │ Token ≥ 10000     │ 否 → 直接返回
         │ 文本消息 ≥ 5 条？  │
         └────────┬──────────┘
                  ▼ 是
      ┌─────────────────────────┐
      │ Layer2: SM Compact      │
      │ 复用已有会话记忆摘要      │
      │ 零额外推理成本          │
      └────────────┬────────────┘
                   ▼
          ┌─────────────────┐
          │ 仍超限？        │ 否 → 返回
          └────────┬────────┘
                   ▼ 是
       ┌────────────────────────┐
       │ Layer3: Full LLM       │
       │ 调用 LLM 生成9段式摘要  │
       │ 保护头尾，中间压缩      │
       └────────────────────────┘
```

### 3.3 MicroCompact 实现（Layer 1）

**触发条件**：上下文超阈值，自动执行

**压缩策略**：
- 只压缩白名单工具（COMPACTABLE_TOOLS）：`Bash`、`Read`、`Grep`、`Glob`
- `Edit`、`Write` 等状态变更工具的输出完整保留
- 按时间截断旧工具输出（> 120 秒的工具结果截断）
- KV Cache 边界外的消息优先压缩

**伪代码**：
```python
# context/micro_compact.py
COMPACTABLE_TOOLS = {"bash", "read", "grep", "glob", "web_search"}
PROTECTED_TOOLS = {"edit", "write", "delete", "execute_code"}

def micro_compact(messages: list[dict]) -> list[dict]:
    """规则驱动，无 LLM 调用"""
    result = []
    for msg in reversed(messages):
        if msg["role"] == "tool":
            tool_name = msg.get("tool_call_id", "")  # 从 tool_calls_log 追溯
            if tool_name in PROTECTED_TOOLS:
                result.append(msg)
            elif tool_name in COMPACTABLE_TOOLS:
                # 按时间或 token 截断
                truncated = truncate_tool_output(msg, max_tokens=500)
                result.append(truncated)
            else:
                result.append(msg)
        else:
            result.append(msg)
    return list(reversed(result))
```

### 3.4 SM Compact 实现（Layer 2）

**触发条件**：`token ≥ 10000 AND 文本消息数 ≥ 5`

**核心思路**：不要重复造轮子，直接复用已生成的会话记忆摘要

**策略**：
- 查询 `memory/sessions/` 中是否有对应的会话摘要
- 用摘要替换旧消息，不产生新的 LLM 调用
- 严格保留最近 4 轮消息（近因效应）
- 单次最大压缩 40000 tokens

### 3.5 Full LLM Compact 实现（Layer 3）

**触发条件**：Layer1 + Layer2 仍超限

**9 段式结构化摘要模板**（来自 ATA Claude Code 文章）：
```
1. Primary Request and Intent        # 原始任务目标
2. Key Technical Concepts            # 关键技术概念
3. Files and Code Sections          # 已修改的文件
4. Errors and fixes                 # 错误及修复
5. Problem Solving                  # 问题解决过程
6. All user messages                # 所有用户消息
7. Pending Tasks                    # 未完成的 TODO
8. Current Work                     # 当前工作
9. Optional Next Step               # 下一步（可选）
```

**头尾保护策略**：
- 头部保护区：系统指令、第一条用户消息、第一条助手回复、第一轮工具交互 —— 绝对不压缩
- 尾部保护区：最后 4 轮对话 —— 绝对不压缩
- 中间压缩区：工具调用历史、试错过程 —— 用摘要替换

**LLM 摘要 Prompt 示例**：
```
在生成摘要前，先在 <analysis> 标签内进行逻辑推演，
然后在 <summary> 标签内输出结构化摘要。
禁止在此阶段调用任何工具（工具调用将被拒绝）。
```

### 3.4 与现有 ContextManager 的关系

现有 `context/manager.py` 的 `compress_if_needed()` 改造为三层压缩的协调器：

```python
# context/manager.py 改动
class ContextManager:
    def __init__(self, ...):
        # 新增配置
        self.micro_threshold = config.MICRO_COMPACT_THRESHOLD  # 默认: 0.7
        self.sm_threshold = config.SM_COMPACT_THRESHOLD  # 默认: 10000 tokens
        self.llm_threshold = config.LLM_COMPACT_THRESHOLD  # 默认: 13000 tokens

    async def compress_if_needed(self, messages, llm_client) -> list[dict]:
        # Layer 1: MicroCompact（规则，检查 cache 边界）
        messages = self._micro_compact(messages)

        # Layer 2: SM Compact（检查是否有可用摘要）
        if self._should_sm_compact(messages):
            messages = await self._sm_compact(messages, llm_client)

        # Layer 3: Full LLM Compact（最后手段）
        if self.estimate_messages_tokens(messages) > self.llm_threshold:
            messages = await self._full_llm_compact(messages, llm_client)

        return messages
```

---

## 4. Skills 延迟加载机制

### 4.1 设计原理

ATA 文章指出：Skills 描述符常驻上下文（~9 tokens），完整内容按需注入（trigger 时才加载）。描述符格式为 `Use when / Don't use when + 反例`，准确率从 73% 提升到 85%。

### 4.2 目录结构

```
.skets/                           # Skill 仓库（项目级）
├── deploy/
│   ├── SKILL.md                  # Skill 描述符（~9 tokens）
│   └── steps.md                  # 详细步骤（按需加载）
├── code-review/
│   ├── SKILL.md
│   └── checklist.md
├── git-workflow/
│   ├── SKILL.md
│   └── conventions.md
└── __init__.py

~/.manus_demo/skills/             # 全局 Skill 目录（用户级）
```

### 4.3 Skill 描述符格式

```markdown
# deploy/SKILL.md

## Skill Descriptor（约 9 tokens）
Use when deploying to production or rolling back.
Don't use when writing code, doing code review.

## Use Cases
- 部署应用到生产环境
- 回滚到上一稳定版本
- 检查部署状态

## Don't Use When
- 只需要编译代码
- 只需要写测试
- 只做代码审查

## Output
- 部署成功/失败报告
- 版本号
- 回滚操作确认

## Example
Deploy: make deploy VERSION=1.2.3
Rollback: make rollback
```

### 4.4 Skills 加载器实现

```python
# skills/loader.py
class SkillLoader:
    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self._descriptor_cache: dict[str, str] = {}  # name -> descriptor

    def get_skill_descriptor(self, name: str) -> str | None:
        """加载 Skill 描述符（常驻，快速）"""
        if name not in self._descriptor_cache:
            path = os.path.join(self.skills_dir, name, "SKILL.md")
            if os.path.exists(path):
                self._descriptor_cache[name] = self._read_first_block(path)
        return self._descriptor_cache.get(name)

    def load_skill_content(self, name: str) -> str:
        """按需加载 Skill 完整内容（慢速）"""
        path = os.path.join(self.skills_dir, name, "SKILL.md")
        return open(path).read()  # 完整内容

    def match_skill(self, task: str, available_skills: list[str]) -> str | None:
        """匹配最合适的 Skill（用 LLM 或规则）"""
        # 规则匹配：扫描描述符中的 Use when
        # 优先选最具体的匹配（最长 Use when 描述）
        best_match = None
        best_score = 0
        for skill in available_skills:
            desc = self.get_skill_descriptor(skill)
            if desc:
                score = self._compute_match_score(task, desc)
                if score > best_score:
                    best_score = score
                    best_match = skill
        return best_match if best_score > 0 else None
```

### 4.5 Skill 注册到工具系统

```python
# tools/base.py 或新文件 tools/skill_adapter.py
class SkillTool(BaseTool):
    """将 Skill 包装为工具，供 Agent 通过 function calling 调用"""

    def __init__(self, skill_loader: SkillLoader, skill_name: str):
        self.skill_loader = skill_loader
        self.skill_name = skill_name

    @property
    def description(self) -> str:
        # 返回 Skill 描述符（约 9 tokens）
        return self.skill_loader.get_skill_descriptor(self.skill_name)

    async def execute(self, **kwargs) -> str:
        # 按需加载完整内容并执行
        content = self.skill_loader.load_skill_content(self.skill_name)
        return content
```

---

## 5. 记忆系统：四类 Memdir + 语义检索

### 5.1 设计原理

ATA 文章（Claude Code）：记忆分四类 —— User（用户偏好）/ Feedback（纠错记录）/ Project（项目信息）/ Reference（通用知识）。检索时用 LLM 当"图书管理员"，限制最多返回 5 条。

### 5.2 记忆分类结构

```
MEMORY.md 格式（按 # 分类）：

#user
- 用户使用 Python + TypeScript
- 偏好简洁的代码风格
- 每次任务后需要总结报告

#feedback
- 2026-05-10: web_search 工具超时导致任务失败，切换到 execute_python 后成功
- 2026-05-09: DAG 规划在多并行场景下有状态不一致问题

#project
- 当前项目是 Manus Demo 多智能体系统
- 使用 DeepSeek 作为 LLM 提供者
- 三层规划路径：simple / complex / emergent

#reference
- 上下文压缩使用三层机制：MicroCompact → SM Compact → Full LLM
- 工具错误使用结构化返回格式
```

### 5.3 四类记忆语义检索器

```python
# memory/semantic_retriever.py
class SemanticRetriever:
    """LLM 参与的语义检索，限制最多 5 条结果"""

    def __init__(self, llm_client: LLMClient, max_results: int = 5):
        self.llm_client = llm_client
        self.max_results = max_results

    async def retrieve(self, query: str, memories: list[MemoryEntry]) -> list[MemoryEntry]:
        """
        用 LLM 判断每条记忆与 query 的相关性，
        返回最多 5 条最相关的。
        """
        if not memories:
            return []

        # 批量让 LLM 评分（避免逐条调用）
        scored = await self._batch_score(query, memories)
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:self.max_results]]

    async def _batch_score(self, query: str, entries: list[MemoryEntry]) -> list[tuple[float, MemoryEntry]]:
        """用 LLM 批量评估相关性"""
        prompt = f"""Query: {query}

Given the query, rate each memory's relevance from 0-10.
Return JSON array: [{{"id": 0, "score": 8.5, "reason": "..."}}]

Memories:
{chr(10).join(self._format_entry(i, e) for i, e in enumerate(entries))}"""

        response = await self.llm_client.chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        # 解析响应并映射回 entry
        ...
```

### 5.4 与现有 LongTermMemory 的关系

`memory/long_term.py` 改造：

```python
# memory/long_term.py 改动
class LongTermMemory:
    def __init__(self, ...):
        # 新增
        self.semantic_retriever = SemanticRetriever(llm_client, max_results=5)

    def search(self, query: str, top_k: int = 3) -> list[MemoryEntry]:
        """两层检索：关键词粗筛 + LLM 语义精排"""
        # Step 1: 关键词粗筛
        candidates = self._keyword_filter(query, top_k=20)
        # Step 2: LLM 语义精排
        ranked = await self.semantic_retriever.retrieve(query, candidates)
        return ranked[:top_k]

    def _keyword_filter(self, query: str, top_k: int) -> list[MemoryEntry]:
        """现有关键词重叠逻辑，作为粗筛层"""
        ...
```

---

## 6. 自进化：轨迹记录 → 动态 Skill 生成

### 6.1 设计原理

ATA 文章（Hermes）：任务完成后，后台异步启动审查 Agent，复盘三个维度：
1. 记忆审查 —— 值得长期保留的经验
2. 技能审查 —— 值得沉淀为 Skill 的任务模式
3. 综合审查 —— 有哪些可以改进的地方

### 6.2 轨迹记录器

```python
# evolution/trajectory_logger.py
class TrajectoryLogger:
    """
    记录完整执行轨迹到 JSONL 文件，
    供后续复盘和 Skill 生成使用。
    """
    def __init__(self, trace_dir: str = ".manus_demo/trajectories"):
        self.trace_dir = trace_dir
        os.makedirs(trace_dir, exist_ok=True)

    def save(self, task_id: str, trajectory: TrajectoryData) -> None:
        """追加写入 JSONL，崩溃可恢复"""
        filepath = os.path.join(self.trace_dir, f"{task_id}.jsonl")
        with open(filepath, "a") as f:
            f.write(trajectory.model_dump_json() + "\n")

@dataclass
class TrajectoryData:
    task_id: str
    task: str
    mode: str  # simple / complex / emergent
    messages: list[dict]  # 完整消息历史
    tool_calls: list[ToolCallRecord]
    start_time: str
    end_time: str
    success: bool
    error: str | None = None
```

### 6.3 后台复盘审查 Agent

```python
# evolution/reviewer.py
class BackgroundReviewer:
    """
    后台异步启动的轻量审查 Agent，
    用户无感知，不阻塞主流程。
    """
    PROMPTS = {
        "memory": """从以下轨迹中提取值得长期记忆的经验：
        - 用户偏好
        - 有效的工具使用模式
        - 关键架构决策""",

        "skill": """分析以下轨迹，判断是否有值得沉淀为可复用 Skill 的任务模式：
        - 是否是通用任务流程？
        - 是否有可模板化的步骤？
        - 是否在类似场景中可重复使用？""",

        "feedback": """从以下失败轨迹中提取改进建议：
        - 哪里犯了错误？
        - 错误的原因是什么？
        - 如何避免同类错误？"""
    }

    async def review(self, trajectory: TrajectoryData) -> ReviewResult:
        """后台异步执行，返回 ReviewResult"""
        ...
```

### 6.4 Skill 自动生成

```python
# skills/generator.py
class SkillGenerator:
    """
    基于复盘结果自动生成或更新 Skill。
    """
    async def generate_from_review(self, review: ReviewResult) -> Skill | None:
        """如果审查判定值得生成 Skill，则创建"""
        if not review.worth_creating_skill:
            return None

        skill_name = self._extract_skill_name(review)
        skill_path = os.path.join(".skills", skill_name)
        os.makedirs(skill_path, exist_ok=True)

        # 写 SKILL.md
        descriptor = self._build_descriptor(review)  # Use when / Don't use when
        steps = self._build_steps(review)  # 详细步骤

        with open(f"{skill_path}/SKILL.md", "w") as f:
            f.write(f"# {skill_name}\n\n## Skill Descriptor\n{descriptor}\n\n## Steps\n{steps}")

        return Skill(name=skill_name, path=skill_path)
```

### 6.5 Skill 自进化流程

```
任务完成
    │
    ▼
TrajectoryLogger.save() ──→ JSONL 文件持久化
    │
    ▼
spawn_background_review() ──→ 后台异步，不阻塞用户
    │
    ├─→ Memory Reviewer ──→ 写入 MEMORY.md (#user / #feedback / #project / #reference)
    │
    ├─→ Skill Reviewer ──→ SkillGenerator.generate_from_review()
    │                         │
    │                         ▼
    │                     .skills/{skill_name}/SKILL.md
    │
    └─→ Feedback Reviewer ──→ 更新 ToolRouter 统计，优化下次路由
```

---

## 7. 工具设计：ACI 原则 + 结构化错误

### 7.1 设计原理

ATA 文章（Agent 架构）：工具设计三个原则：
1. **粒度对应 Agent 目标**，而非底层 API 操作
2. **错误结构化**，包含 `error_code` + `suggestion` + `fix_steps`
3. **描述说明 Use when / Don't use when** + 反例

### 7.2 统一错误结构

```python
# tools/structured_error.py
@dataclass
class ToolError:
    error_code: str
    message: str
    suggestion: str | None = None
    tool_name: str = ""
    context: dict | None = None

    def to_string(self) -> str:
        parts = [f"[{self.error_code}] {self.message}"]
        if self.suggestion:
            parts.append(f"Suggestion: {self.suggestion}")
        return "\n".join(parts)

# 错误代码表
TOOL_ERROR_CODES = {
    "FILE_NOT_FOUND": "文件不存在",
    "PERMISSION_DENIED": "权限拒绝",
    "INVALID_PARAMS": "参数格式错误",
    "EXECUTION_FAILED": "执行失败",
    "TIMEOUT": "执行超时",
    "NETWORK_ERROR": "网络错误",
    "RATE_LIMIT": "请求限流",
}
```

### 7.3 工具错误改造示例

**Before（当前 `file_ops.py`）**：
```python
def _read_file(self, filename: str) -> str:
    if not os.path.exists(path):
        return f"Error: File not found: {filename}"  # 无结构，无建议
```

**After（改造后）**：
```python
def _read_file(self, filename: str) -> str:
    if not filename:
        return ToolError(
            error_code="INVALID_PARAMS",
            message="filename is required for read operation",
            suggestion="Provide the filename parameter, e.g. filename='data.txt'",
            tool_name="file_ops",
        ).to_string()

    if not os.path.exists(path):
        return ToolError(
            error_code="FILE_NOT_FOUND",
            message=f"File not found: {filename}",
            suggestion=f"Use action='list' to see available files, then retry with correct filename",
            tool_name="file_ops",
            context={"attempted_path": filename},
        ).to_string()
```

### 7.4 工具描述符改造

**Before（当前 `file_ops.py`）**：
```python
@property
def description(self) -> str:
    return "Perform file operations: read, write, or list files..."
```

**After（改造后）**：
```python
# 约 9 tokens，符合 Skills 描述符规范
DESCRIPTION = """Use when reading project files, writing code/config, or listing directory contents.
Don't use when executing shell commands, searching file content, or running tests.

Actions:
- read: Read file content (filename required)
- write: Create or overwrite file (filename + content required)
- list: List files in sandbox

Output: Formatted file content or operation status."""
```

---

## 8. 多 Agent：纯协调者 + 子 Agent 隔离协议

### 8.1 设计原理

ATA 文章（Agent 架构）：
- **协调者绝对不写代码**，只做规划、委派、汇总
- 子 Agent 独立 context，跑完只回摘要，不污染主 Agent 上下文
- 多 Agent 通信用结构化 JSONL 协议，append-only，崩溃可恢复

### 8.2 Orchestrator 纯协调者改造

**当前问题**（`orchestrator.py`）：
- `_execute_and_reflect_simple()` 直接调用 `ExecutorAgent`，不是纯委派
- `Orchestrator` 本身持有 `llm_client` 和 `tools`，有直接执行能力

**改造后**：
```python
# agents/orchestrator.py
async def _execute_and_reflect_simple(self, task: str, plan: Plan, context: str) -> str:
    """
    纯委派：Orchestrator 不执行，只创建子 Agent 并汇总结果。
    """
    # 创建独立 ExecutorAgent 实例（隔离 context）
    executor = SubAgentExecutor(
        llm_client=self.llm_client,  # 共享 LLMClient，但 context 独立
        tools=self.tools,
        context_manager=self.context_manager,
    )

    # 执行计划（子 Agent 内部处理 ReAct 循环）
    results = await executor.execute_plan(plan, context)

    # 汇总结果（Orchestrator 只做聚合）
    return self._aggregate_results(results)
```

### 8.3 子 Agent 基类（隔离上下文）

```python
# agents/sub_agent.py
class SubAgent(BaseAgent):
    """
    子 Agent 基类，提供独立 context 的执行单元。

    关键特性：
    - 独立 message history，不污染父 Agent
    - 可选 Git Worktree 隔离（复杂任务用）
    - 执行完只回传摘要，不回传完整 context
    """
    def __init__(self, parent_agent: "OrchestratorAgent", ...):
        self.parent = parent_agent
        self.messages: list[dict] = []  # 独立上下文

    async def execute(self, task: str, context: str = "") -> str:
        """执行任务，返回摘要（而非完整 context）"""
        ...

    def get_summary(self) -> str:
        """返回执行摘要，供父 Agent 聚合"""
        return summarize(self.messages)  # 调用 LLM 摘要
```

### 8.4 JSONL 通信协议

```python
# subagent/protocol.py
class JSONLProtocol:
    """
    Agent 间结构化通信协议。
    append-only，崩溃可恢复。
    """
    def __init__(self, team_dir: str = ".manus_demo/team"):
        self.team_dir = team_dir
        self.inbox_dir = os.path.join(team_dir, "inbox")
        os.makedirs(self.inbox_dir, exist_ok=True)

    def send(self, to_agent: str, message: AgentMessage) -> None:
        """发送消息到目标 Agent 的 inbox"""
        filepath = os.path.join(self.inbox_dir, f"{to_agent}.jsonl")
        with open(filepath, "a") as f:
            f.write(message.model_dump_json() + "\n")

    def receive(self, agent_id: str) -> list[AgentMessage]:
        """读取并解析收到的消息（按 status 过滤）"""
        filepath = os.path.join(self.inbox_dir, f"{agent_id}.jsonl")
        if not os.path.exists(filepath):
            return []

        messages = []
        pending = []
        with open(filepath) as f:
            for line in f:
                msg = AgentMessage.model_validate_json(line)
                if msg.status == "pending":
                    pending.append(msg)
        # 删除已读消息，重写 pending
        self._rewrite_inbox(agent_id, pending)
        return messages

@dataclass
class AgentMessage:
    request_id: str
    from_agent: str
    to_agent: str
    content: dict  # 结构化内容
    status: str = "pending"  # pending / approved / rejected
    timestamp: str = ""
```

### 8.5 Git Worktree 隔离

```python
# subagent/worktree.py
class WorktreeManager:
    """
    为复杂任务创建隔离的 Git Worktree，
    成功则合并，失败则丢弃，不污染主分支。
    """
    def __init__(self, base_repo: str):
        self.base_repo = base_repo

    async def create_worktree(self, task_id: str) -> str:
        """创建独立工作树"""
        worktree_path = f".manus_demo/worktrees/{task_id}"
        branch_name = f"agent/{task_id}"

        await run_shell(
            f"git worktree add {worktree_path} -b {branch_name}",
            cwd=self.base_repo,
        )
        return worktree_path

    async def merge(self, task_id: str) -> None:
        """合并到主分支"""
        worktree_path = f".manus_demo/worktrees/{task_id}"
        await run_shell(f"git checkout main", cwd=worktree_path)
        await run_shell(f"git merge agent/{task_id}", cwd=worktree_path)
        await self.cleanup(task_id)

    async def cleanup(self, task_id: str) -> None:
        """丢弃工作树"""
        await run_shell(f"git worktree remove {task_id}", cwd=self.base_repo)
```

### 8.6 模型选择器

```python
# subagent/model_selector.py
class ModelSelector:
    """
    根据任务复杂度选择合适的模型：
    - 简单任务用轻量模型（快、便宜）
    - 复杂任务用旗舰模型（高质量）
    - 交叉 review 用不同架构的模型
    """
    MODEL_MAP = {
        "quick": "deepseek-chat",        # 简单执行类
        "reasoning": "deepseek-reasoner", # 深度推理类
        "review": "claude-sonnet-4-6",   # 交叉 review（不同架构）
    }

    def select(self, task_type: str, context: str) -> str:
        if "search" in task_type or "find" in task_type:
            return self.MODEL_MAP["quick"]
        elif "refactor" in task_type or "design" in task_type:
            return self.MODEL_MAP["reasoning"]
        elif "review" in task_type:
            return self.MODEL_MAP["review"]
        else:
            return "deepseek-chat"  # 默认
```

---

## 9. Harness Engineering：架构约束编码化

### 9.1 设计原理

ATA 文章（Qoder / AGENTS.md）：
- AGENTS.md 是"地图"，不是"手册"（约 100-200 行，详细内容链接到 docs/）
- 约束必须编码进 lint 脚本，机械执行，不靠 Agent"记住"
- 验证流水线：build → lint-arch → test → verify

### 9.2 分层架构约束（对标 DAG 三层结构）

```bash
# scripts/lint-dag-arch.sh
#!/bin/bash
# 检查 DAG 节点的依赖方向是否符合 GOAL→SUBGOAL→ACTION 三层

# Layer 0: entity/ → 只允许依赖 common
# Layer 1: repository/ → 只允许依赖 entity, common
# Layer 2: core/ → 横切关注点，不允许依赖业务包
# Layer 3: config/ → 允许依赖 core, service
# Layer 4: service/ → 业务核心层
# Layer 5: controller/ → 只允许依赖 service, core, common

# 扫描所有 Python 文件的 import 语句
# 违反依赖方向则报错，输出：WHAT + WHY + HOW
```

### 9.3 预验证机制

ATA 文章（Qoder）：在写代码**前**先问"这样做合法吗"，比写完**后**检查效率高 5 倍。

```python
# tools/pre_verify.py
class PreVerifier:
    """
    在执行高风险操作前，验证合法性。
    触发场景：创建新文件、添加跨层 import。
    """
    def verify_action(self, action: str, params: dict) -> VerificationResult:
        if action == "create_file":
            path = params.get("path", "")
            # 检查是否违反分层架构
            if self._violates_arch_layer(path):
                return VerificationResult(
                    valid=False,
                    error_code="ARCH_LAYER_VIOLATION",
                    message=f"Creating {path} violates architecture layer rules",
                    fix=f"Place files in correct layer: {self._get_allowed_layers(path)}",
                )
        return VerificationResult(valid=True)
```

### 9.4 AGENTS.md 改造（"地图"模式）

**改造前**：CLAUDE.md 内容较多，偏向"手册"

**改造后**（约 150 行，链接到详细文档）：

```markdown
# Manus Demo Agent Guide

## 项目概述
Manus Demo 是一个多智能体 AI 系统，支持三种规划范式...

## 快速命令
make test        # 运行测试
make eval        # 评测
make lint-arch   # 架构约束检查
→ 详见 docs/development.md

## 架构约束
- 规划路由：simple / complex / emergent 三条路径
- DAG 层级：GOAL → SUBGOAL → ACTION，禁止跨层依赖
- 工具选择：失败 2 次后触发 ToolRouter 建议切换
→ 详见 docs/architecture.md

## 工具约定
- 工具错误必须包含 error_code + suggestion
- 新增工具需更新 schema.py 和 tools/__init__.py
→ 详见 docs/tool-design.md

## 验证闭环
改完代码不算完，跑通测试才算完：
1. make lint-arch  # 架构检查
2. make test       # 功能测试
3. make eval       # 评测验证
→ 详见 docs/verification.md

## 文档导航
- [架构文档](docs/architecture.md)
- [开发指南](docs/development.md)
- [评测指南](docs/evaluation-guide.md)
- [Emergent 规划设计](docs/emergent-planning.md)
```

---

## 10. 配置项变更

### 10.1 新增配置

```python
# config.py 新增

# ---- Context 三层压缩 ----
MICRO_COMPACT_THRESHOLD = 0.7        # Layer1 触发阈值（上下文满 70%）
SM_COMPACT_THRESHOLD = 10000        # Layer2 触发阈值（10000 tokens）
LLM_COMPACT_THRESHOLD = 13000       # Layer3 触发阈值（13000 tokens）
SM_COMPACT_MAX_TOKENS = 40000      # Layer2 单次最大压缩量
LLM_COMPACT_PROTECT_TURNS = 4       # 尾部保护轮数

# ---- Skills 延迟加载 ----
SKILLS_ENABLED = True               # 是否启用 Skills 机制
SKILLS_DIR = ".skills"              # 项目级 Skill 目录
GLOBAL_SKILLS_DIR = "~/.manus_demo/skills"  # 全局 Skill 目录

# ---- 四类记忆 ----
MEMDIR_ENABLED = True               # 是否启用 Memdir 四类记忆
SEMANTIC_RETRIEVAL_ENABLED = True    # 是否启用 LLM 语义检索
SEMANTIC_TOP_K = 5                 # 语义检索最多返回 5 条

# ---- 自进化 ----
EVOLUTION_ENABLED = True            # 是否启用轨迹记录
TRAJECTORY_DIR = ".manus_demo/trajectories"  # 轨迹存储目录
BACKGROUND_REVIEW_ENABLED = True    # 是否启用后台复盘
SKILL_AUTO_GENERATE = True          # 是否自动生成 Skill

# ---- 多 Agent 隔离 ----
SUBAGENT_ISOLATION = True           # 子 Agent 是否隔离执行
WORKTREE_ENABLED = True             # 是否启用 Worktree 隔离
MODEL_SELECTOR_ENABLED = True        # 是否启用模型选择器
JSONL_PROTOCOL_ENABLED = True       # 是否启用 JSONL 通信协议

# ---- 工具结构化错误 ----
STRUCTURED_ERROR_ENABLED = True      # 工具错误返回结构化
```

---

## 11. 实施路线图

### Phase 1：Context + Skills（1-2 周）

```
改动文件：
- context/manager.py         # 改为三层压缩协调器
- context/micro_compact.py   # [新增]
- context/sm_compact.py       # [新增]
- context/llm_compact.py      # [新增]
- skills/store.py             # [新增]
- skills/loader.py            # [新增]
- tools/base.py              # 描述符格式改造
- tools/file_ops.py          # 结构化错误改造（试点）
```

**验收标准**：
- 上下文 Token 消耗降低 30-50%（同等任务）
- Skill 机制下工具描述符 < 50 tokens / 个

### Phase 2：Memory + Evolution（2-3 周）

```
改动文件：
- memory/memdir.py           # [新增]
- memory/semantic_retriever.py # [新增]
- memory/long_term.py        # 对接 Memdir 格式
- evolution/trajectory_logger.py # [新增]
- evolution/reviewer.py       # [新增]
- skills/generator.py        # [新增]
```

**验收标准**：
- 重复任务错误率降低 40%
- 相似任务召回准确率 > 80%

### Phase 3：Multi-Agent 隔离（2-3 周）

```
改动文件：
- agents/orchestrator.py     # 纯协调者改造
- agents/sub_agent.py        # [新增]
- agents/base.py            # 支持模型选择
- subagent/protocol.py       # [新增]
- subagent/worktree.py       # [新增]
- subagent/model_selector.py # [新增]
```

**验收标准**：
- 子 Agent 执行后主 Agent 上下文不膨胀
- Worktree 隔离任务成功合并率 > 90%

### Phase 4：Harness + 工具改造（1-2 周）

```
改动文件：
- scripts/lint-dag-arch.sh   # [新增]
- tools/structured_error.py  # [新增]
- tools/*.py                 # 全面结构化错误改造
- CLAUDE.md                  # 改为"地图"模式
- docs/architecture.md       # 新增架构约束文档
```

**验收标准**：
- `make lint-arch` 能检测跨层依赖违规
- 所有工具错误包含 error_code + suggestion

---

## 12. 风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| 三层压缩丢失关键决策 | 明确压缩保留优先级：架构决策 > 文件变更 > 验证状态 > 工具输出 |
| Skills 路由失准 | Skill 描述符强制使用 Use when / Don't use when + 反例 |
| 后台复盘消耗资源 | 完全异步，不阻塞主流程；限制并发复盘数 |
| Worktree 合并冲突 | 复杂任务先用 Worktree isolation，成功后再合并 |
| LLM 语义检索成本 | 先用关键词粗筛（top 20），再 LLM 精排（top 5） |

---

## 13. 附录：ATA 文章索引

| 文章 | 关键参考点 |
|------|-----------|
| [你不知道的 Agent：原理、架构与工程实践](./article_11020600930.md) | Agent Loop / 上下文分层 / 工具设计 ACI / 多 Agent 组织 |
| [深度解析 Claude Code 在 Prompt/Context/Harness](./article_11020605711.md) | 三层压缩 / Memdir / Skills 延迟加载 / 系统提示组装 |
| [Qoder 工程实践：Harness Engineering](./article_11020601776.md) | AGENTS.md 地图理念 / 架构约束编码 / 预验证 |
| [深度解析 Hermes Agent 自进化](./article_11020604988.md) | 轨迹复盘 / Skill 动态生成 / 自进化闭环 |
| [AGENTS.md 实践指南](./article_11020618417.md) | 仓库聚合 / 验证闭环 / 分层依赖检查 |
