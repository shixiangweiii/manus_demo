# Manus Demo 后续迭代路线图：v20 Agent Skills

> **生成日期**: 2026-06-01 初版
> **当前状态**: v14-v19 代码已全部完成；v20 Agent Skills 为下一阶段方向
> **数据来源**: agentskills.io Specification V1.0 + Anthropic 官方 skills 仓库 + SkillOpt 论文 + 公网最佳实践 + 本项目源码集成点分析
> **定位**: 个人学习 agent 架构设计与工程落地的自学 demo；v20 遵循开放标准，让 agent 获得可复用、可移植、可组合的专业领域能力

---

## 一、为什么要做 Agent Skills

### 1.1 行业标准已成

Agent Skills 由 Anthropic 于 2025年10月发起，2025年12月在 agentskills.io 开源 Specification V1.0，截至 2026 年初：

- **37+ 主流平台**已采纳：Claude Code、OpenAI Codex、Gemini CLI、Cursor、VS Code + GitHub Copilot、JetBrains Junie、OpenHands、Letta、Goose、Databricks、Snowflake、Roo Code、Kiro、Spring AI 等
- **85,000+** 公开可用的 Agent Skills
- Anthropic 官方 `anthropics/skills` 仓库 **142k+ stars**
- Linux 基金会已讨论纳入 AI & Data 基金会候选标准
- TechCrunch 称其为 **"AI 领域的 Dockerfile"**——可移植、可组合、可版本控制

**不支持的后果**：agent 系统在互操作性上掉队，无法利用日益丰富的 skill 生态，也无法将自身能力以标准格式暴露给其他 agent 客户端。

### 1.2 与本项目现有能力的倍增效应

| 现有特性 | + Agent Skills | 效应 |
|---------|---------------|------|
| v17 Self-Evolution | 经验蒸馏为 SKILL.md | 从 "memory 级文本注入" 升级为 "skill 级结构化知识"，可跨项目移植 |
| v19 Guardrails | Skill 来源信任模型 + 注入扫描 | 第三方 skill 安全沙箱 |
| v18 A2A | AgentCard.skills ← SKILL.md frontmatter | A2A 能力声明自动生成 |
| v16 MCP Bridge | Skill scripts 可暴露为 MCP Tool | 双向：MCP 工具也可被 skill allowed-tools 引用 |
| SPECIALIST_REGISTRY | Skill 补充 "怎么做"，Specialist 解决 "谁来做" | 互补而非替代 |

### 1.3 与旧 roadmap v15.5 的关系

旧 roadmap v15.5 "Skill 轻量实验"将 skill 定义为 "高频成功流程保存为 procedural note，文本触发规则"。新的 Agent Skills 规范远超这个设计：

| 维度 | v15.5 旧设计 | Agent Skills 规范 |
|------|-------------|-----------------|
| 格式 | procedural note（内存文本） | SKILL.md（YAML frontmatter + Markdown，文件系统级） |
| 发现 | 关键词匹配 | 渐进式披露（Discovery → Activation → Execution） |
| 资源 | 无 | scripts/、references/、assets/ 目录 |
| 可移植 | 项目内 | 跨 37+ 平台标准 |
| 工具绑定 | 无 | allowed-tools 预授权 |
| 安全 | 无 | 信任分级 + guardrail 集成 |

**结论**：v15.5 升级为 v20，按 Agent Skills 规范重新设计。

---

## 二、规范核心（agentskills.io Specification V1.0）

### 2.1 目录结构

```
skill-name/
├── SKILL.md          # 必需：元数据 + 指令
├── scripts/          # 可选：可执行代码
├── references/       # 可选：参考文档
├── assets/           # 可选：模板、资源
└── ...               # 其他自定义文件
```

### 2.2 SKILL.md 格式

```markdown
---
name: skill-name          # 必填，1-64字符，小写+数字+连字符
                          # 须与父目录名完全一致
                          # 不可以连字符开头/结尾，不可连续连字符
description: ...          # 必填，1-1024字符，描述做什么+何时用
                          # 这是 agent 判断是否激活 skill 的唯一依据
license: Apache-2.0       # 可选
compatibility: ...        # 可选，1-500字符，环境要求
metadata:                 # 可选，key-value
  author: org
  version: "1.0"
allowed-tools: Bash(git:*) Read  # 可选，实验性，预授权工具列表（空格分隔）
---

# Skill 指令正文
（建议 <500行, <5000 tokens）
```

### 2.3 三层渐进式披露（Progressive Disclosure）

| 层级 | 内容 | 加载时机 | Token 开销 |
|------|------|---------|-----------|
| **Discovery** | `name` + `description` | 启动时全量加载到 system prompt | ~100 tokens/skill |
| **Activation** | SKILL.md 完整正文 | 任务匹配时，LLM 通过 tool call 主动读取 | 建议 <5000 tokens |
| **Execution** | scripts/、references/、assets/ | 按需引用时加载 | 按实际使用 |

**关键设计**：Discovery 阶段不需要额外 LLM 调用做匹配——所有 skill 的 name+description 以结构化文本注入 system prompt，LLM 在 ReAct 循环中自行判断是否需要激活某个 skill 并读取其完整内容。

### 2.4 Skill 搜索目录约定

| 平台 | 项目级 | 用户级（全局） |
|------|--------|-------------|
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Cursor | `.cursor/skills/`、`.claude/skills/`、`.codex/skills/` | `~/.cursor/skills/`、`~/.claude/skills/` |
| Codex | `.codex/skills/` | `~/.codex/skills/` |
| Kiro | `.kiro/skills/` | `~/.kiro/skills/` |
| 通用 | `.agents/skills/` | — |

本项目采用 `.agents/skills/` 作为项目级目录（与 VS Code / OpenAI Codex 兼容），`~/.manus_demo/skills/` 作为用户级目录。

---

## 三、二次反思：初版分析的修正

在生成本 roadmap 前对初版分析进行了深度复核，以下是关键修正：

### 3.1 修正：Skill 匹配不需要额外 LLM 调用

**初版判断**："Skill 匹配（task→skill 激活）难度中等，需要 LLM 调用"

**修正**：Agent Skills 规范的核心设计是 **LLM 自行决定是否激活 skill**。所有 skill 的 name+description 在启动时注入 system prompt，LLM 在 ReAct 循环中通过 tool call 读取完整 SKILL.md。不需要复用 `classify_task` 或新增 LLM 匹配调用。实现复杂度从 "中" 降为 "低"。

### 3.2 修正：Skill 与 Specialist 的关系是互补，非替代

**初版判断**："SpecialistSpec 与 Skill 的关系需要厘清"

**修正**：Specialist 解决 **"谁来做"**（agent 角色 + 工具集），Skill 解决 **"怎么做"**（工作流知识 + 领域专家指令）。一个 Specialist 可以使用多个 Skill，一个 Skill 也可以跨 Specialist 复用。两者不在同一抽象层。

- Specialist = `SpecialistSpec(name, description, system_prompt, default_tools)` → 初始化一个独立 ReAct 循环
- Skill = `SkillDef(name, description, instructions, allowed_tools, ...)` → 注入到已有 ReAct 循环的 system prompt

### 3.3 修正：Skill 不应存储在 AgenticMemory 中

**初版判断**："可以把 skill 存储为 MemoryKind.SKILL 的 AgenticMemoryRecord"

**修正**：Skill 和 Memory 是不同的生命周期：

| 维度 | AgenticMemory | Agent Skill |
|------|-------------|------------|
| 来源 | 运行时动态学习 | 文件系统声明式定义 |
| 生命周期 | 可 revoke、可 consolidate | 版本控制，git 管理 |
| 存储 | JSON / SQLite | 文件系统（SKILL.md + 目录） |
| 发现 | 语义搜索 | 目录扫描 + 渐进式披露 |
| 安全 | v19 注入扫描 | 信任分级 + allowed-tools |

将 Skill 塞进 AgenticMemory 会混淆两个不同的概念。但 v17 Self-Evolution **蒸馏产出的 skill** 可以写入 `.agents/skills/` 目录，而不是 AgenticMemory。

### 3.4 修正：allowed-tools 与 Guardrails 的优先级

**初版未充分考虑**：Skill 的 `allowed-tools` 字段是"预授权"，意味着 agent 调用这些工具时可以跳过用户确认。这与 v19 的 `GUARDRAIL_WRITE_CONFIRM`（block/confirm/allow）有直接冲突。

**规则**：**Guardrails 优先于 allowed-tools**。即使 skill 预授权了 `file_ops write`，如果 `GUARDRAIL_WRITE_CONFIRM=block`，仍然 block。allowed-tools 只影响 "工具是否出现在 agent 可选列表中"，不覆盖安全策略。

### 3.5 新增风险：DeepSeek 对 Skill 激活的可靠性

初版只提了 "模型兼容性需验证"，但根据项目已有的评测经验（F-eval-2：emergent TODO-init returns 0 with DeepSeek），DeepSeek 在遵循结构化指令方面可能不如 Claude。需要：

- Skill 的 description 必须极其明确（比规范建议的更具体）
- 评测中增加 skill_activation_rate 指标
- 如果 activation rate 过低，考虑 fallback：在 `_gather_context()` 中做关键词预匹配，将匹配 skill 的完整指令直接注入 context（跳过渐进式披露的第二层）

---

## 四、v20 分阶段设计

### 总体架构

```
OrchestratorAgent.run(task)
  │
  ├── __init__: SkillLoader.discover() → SkillRegistry (name+description 列表)
  │
  ├── _gather_context(task):
  │     ├── [现有] AgenticMemory search
  │     ├── [现有] ExperienceLearner hints
  │     ├── [现有] Knowledge search
  │     └── [新增] skill_descriptions = SkillRegistry.format_descriptions()
  │           → 注入 combined_context 作为 "=== Available Skills ===" 段
  │
  ├── classify_task → route
  │
  ├── [所有执行路径]:
  │     build_system_prompt(..., inject_skill_guidance=True)
  │       → 包含 "=== Available Skills ===" 段（Discovery 层）
  │       → 包含 skill_activation_tool 提示
  │     ReActEngine.execute(system_hint=composed_prompt)
  │       → LLM 自行决定是否调用 skill_activation_tool
  │       → skill_activation_tool 读取完整 SKILL.md → 注入消息
  │
  └── [可选] Skill 内的 allowed-tools → 过滤当前 ReAct 循环可用工具
```

### v20.1 — SkillLoader + SkillRegistry + Discovery 层（P0，1 周）

**目标**：实现 Agent Skills 规范的 Discovery 阶段——启动时扫描 skill 目录、解析 YAML frontmatter、注册到内存、注入 system prompt。

#### 新增文件

```
skills/
├── __init__.py
├── loader.py          # SkillLoader: 目录扫描 + YAML 解析 + 验证
├── registry.py        # SkillRegistry: 内存注册表 + 查询 + 格式化
├── models.py          # SkillDef / SkillMeta 数据模型
└── activation.py      # SkillActivationTool: BaseTool 子类，读取完整 SKILL.md
```

#### SkillDef 数据模型

```python
@dataclass
class SkillMeta:
    """YAML frontmatter 解析结果 / YAML 前置元数据"""
    name: str                           # 必填，1-64字符
    description: str                    # 必填，1-1024字符
    license: str = ""                   # 可选
    compatibility: str = ""             # 可选
    metadata: dict[str, str] = field(default_factory=dict)  # 可选
    allowed_tools: list[str] = field(default_factory=list)   # 可选，从空格分隔字符串解析

@dataclass
class SkillDef:
    """完整的 Skill 定义 / 完整 Skill 定义"""
    meta: SkillMeta
    skill_dir: str                      # skill 目录绝对路径
    full_content: str = ""              # SKILL.md 完整正文（Activation 时加载）
    scripts: list[str] = field(default_factory=list)   # scripts/ 下的文件列表
    references: list[str] = field(default_factory=list) # references/ 下的文件列表
    assets: list[str] = field(default_factory=list)     # assets/ 下的文件列表
```

#### SkillLoader 核心逻辑

```python
class SkillLoader:
    """扫描 skill 目录，解析 SKILL.md frontmatter / 扫描 skill 目录，解析 SKILL.md 前置元数据"""

    def discover(self, skill_dirs: list[str]) -> list[SkillDef]:
        """
        扫描所有 skill 目录，解析每个子目录中的 SKILL.md。
        遵循 agentskills.io V1.0 规范：
        - SKILL.md 必须存在
        - name 必须与目录名一致
        - name 必须满足小写+数字+连字符约束
        - 解析失败的 skill 记录警告并跳过（不阻断启动）
        """

    def _parse_frontmatter(self, content: str) -> tuple[SkillMeta, str]:
        """解析 YAML frontmatter，返回 (meta, body) / 解析 YAML 前置元数据"""

    def _validate_name(self, name: str, dir_name: str) -> bool:
        """验证 name 字段格式 / 验证 name 字段格式"""
```

#### SkillRegistry

```python
class SkillRegistry:
    """内存注册表 / 内存注册表"""

    def register(self, skill: SkillDef) -> None: ...
    def get(self, name: str) -> SkillDef | None: ...
    def list_all(self) -> list[SkillMeta]: ...
    def format_descriptions(self) -> str:
        """
        格式化为 system prompt 可注入的文本（Discovery 层）。
        格式：
        === Available Skills ===
        - skill-name: <description>
        - another-skill: <description>
        Use the 'activate_skill' tool to load a skill's full instructions when needed.
        """

    def load_full_content(self, name: str) -> str:
        """加载 SKILL.md 完整正文（Activation 层）/ 加载完整正文"""
```

#### SkillActivationTool（BaseTool 子类）

```python
class SkillActivationTool(BaseTool):
    """
    让 ReAct 循环中的 LLM 主动激活一个 skill。
    / 让 ReAct 循环中的 LLM 主动激活一个 skill。

    name: "activate_skill"
    description: "Load the full instructions of a skill by name. Use this when a task matches one of the available skills."
    parameters: { skill_name: string (required) }
    """
    def __init__(self, registry: SkillRegistry): ...
    async def execute(self, skill_name: str) -> str:
        """
        返回 SKILL.md 完整正文。
        如果 skill 不存在，返回 Error: Skill '{name}' not found.
        """
```

#### prompt_utils.py 集成

在 `build_system_prompt()` 中新增：

```python
def build_system_prompt(
    base_prompt: str,
    inject_context: bool = True,
    inject_subagent_guidance: bool = True,
    inject_location_guidance: bool = True,
    inject_search_guidance: bool = True,
    inject_hitl_guidance: bool = True,
    inject_skill_guidance: bool = True,    # 新增
    skill_descriptions: str = "",           # 新增：SkillRegistry.format_descriptions() 输出
) -> str:
    parts = [base_prompt]
    # ... 现有段 ...
    if inject_skill_guidance and skill_descriptions:
        parts.append(skill_descriptions)
    return "".join(parts)
```

#### config.py 新增

```python
# --- Skills (v20) ---
SKILLS_ENABLED = os.getenv("SKILLS_ENABLED", "false").lower() == "true"
SKILLS_DIRS = os.getenv("SKILLS_DIRS", "")  # 逗号分隔的自定义目录
SKILLS_PROJECT_DIR = os.getenv("SKILLS_PROJECT_DIR", ".agents/skills")
SKILLS_USER_DIR = os.path.expanduser(os.getenv("SKILLS_USER_DIR", "~/.manus_demo/skills"))
SKILLS_MAX_ACTIVATIONS_PER_TASK = int(os.getenv("SKILLS_MAX_ACTIVATIONS_PER_TASK", "3"))
SKILLS_MAX_CONTENT_TOKENS = int(os.getenv("SKILLS_MAX_CONTENT_TOKENS", "5000"))
```

#### 事件

| 事件 | 数据 | 说明 |
|------|------|------|
| `skills_discovered` | `{count, names}` | 启动时 skill 发现结果 |
| `skill_activated` | `{name, content_length}` | skill 被激活 |
| `skill_activation_failed` | `{name, error}` | 激活失败 |

#### 验收标准

- [ ] `SkillLoader.discover()` 扫描 `.agents/skills/` 和 `~/.manus_demo/skills/`，正确解析 SKILL.md frontmatter
- [ ] name 不符合规范（大写、连续连字符、与目录名不一致）的 skill 被跳过并记录警告
- [ ] `SkillRegistry.format_descriptions()` 输出格式正确
- [ ] `SkillActivationTool` 被注册到 ReActEngine 的工具列表
- [ ] `SKILLS_ENABLED=false` 时零行为变更（不扫描目录、不注册工具、不注入 prompt）
- [ ] `python3 -m py_compile skills/loader.py skills/registry.py skills/models.py skills/activation.py` 通过

---

### v20.2 — Prompt 注入 + allowed-tools 过滤（P0，1 周）

**目标**：完成 Discovery→Activation 的端到端流程；实现 allowed-tools 对 ReAct 循环工具列表的过滤。

#### OrchestratorAgent 集成

在 `__init__` 中：

```python
# v20: Skill discovery / v20: Skill 发现
self._skill_registry = SkillRegistry()
if config.SKILLS_ENABLED:
    loader = SkillLoader()
    dirs = [config.SKILLS_PROJECT_DIR, config.SKILLS_USER_DIR]
    if config.SKILLS_DIRS:
        dirs.extend(config.SKILLS_DIRS.split(","))
    skills = loader.discover(dirs)
    for s in skills:
        self._skill_registry.register(s)
    self._emit("skills_discovered", {"count": len(skills), "names": [s.meta.name for s in skills]})

    # 注册 SkillActivationTool / 注册 SkillActivationTool
    self._skill_activation_tool = SkillActivationTool(self._skill_registry)
    # 注入到 tool list / 注入到工具列表
```

在 `_gather_context()` 中（新增段）：

```python
# v20: inject skill descriptions (Discovery layer)
# v20: 注入 skill 描述（Discovery 层）
if self._skill_registry.list_all():
    skill_desc = self._skill_registry.format_descriptions()
    combined += f"{skill_desc}\n\n"
```

在所有 `build_system_prompt()` 调用点传入 `skill_descriptions`：

```python
system_prompt = build_system_prompt(
    base_prompt=self.system_prompt,
    inject_skill_guidance=True,
    skill_descriptions=self._skill_registry.format_descriptions(),
)
```

#### allowed-tools 过滤

**设计决策**：allowed-tools 在 Skill **激活后**生效，过滤当前 ReAct 循环的可用工具列表。

```python
# 在 SkillActivationTool.execute() 中 / 在 SkillActivationTool.execute() 中
async def execute(self, skill_name: str) -> str:
    skill = self._registry.get(skill_name)
    if not skill:
        return f"Error: Skill '{skill_name}' not found."

    content = self._registry.load_full_content(skill_name)

    # 如果 skill 定义了 allowed-tools，通知 ReActEngine 过滤工具
    if skill.meta.allowed_tools:
        self._tool_filter_callback(skill.meta.allowed_tools)

    return content
```

**与 Specialist 的工具过滤复用**：`SpecialistAgent.__init__` 已有按 `default_tools` 过滤工具的逻辑（`specialist.py:116-129`），allowed-tools 过滤遵循同一模式。

**Guardrails 优先级规则**：

```
ToolGuardrail > allowed-tools > 默认工具集
```

即使 skill 预授权了 `file_ops write`，如果 `GUARDRAIL_WRITE_CONFIRM=block`，仍然 block。allowed-tools 只控制 "工具是否出现在 agent 可选列表中"，不覆盖安全策略。

#### 验收标准

- [ ] `SKILLS_ENABLED=true` + 存在 `.agents/skills/demo-skill/SKILL.md` 时，system prompt 包含 skill description
- [ ] LLM 可以通过 `activate_skill` tool call 读取完整 SKILL.md
- [ ] 激活 skill 后，allowed-tools 中列出的工具在 ReAct 循环中可用，未列出的工具被隐藏
- [ ] Guardrails 规则（tool-input block、write-confirm）优先于 allowed-tools 预授权
- [ ] 单个 task 最多激活 `SKILLS_MAX_ACTIVATIONS_PER_TASK` 个 skill
- [ ] skill 内容超过 `SKILLS_MAX_CONTENT_TOKENS` 时截断并提示

---

### v20.3 — Guardrails 扩展：Skill 安全沙箱（P0，1 周）

**目标**：为第三方 Skill 建立安全边界，防止注入攻击和越权操作。

#### 信任分级

| 来源 | 信任等级 | 行为 |
|------|---------|------|
| `.agents/skills/`（项目级，git 管理） | **可信** | SKILL.md 正文直接注入，不标记 UNTRUSTED |
| `~/.manus_demo/skills/`（用户级） | **半可信** | SKILL.md 正文注入前经 InputGuardrail 扫描，不标记 UNTRUSTED 但检测注入指令 |
| 第三方安装（未来） | **不可信** | SKILL.md 正文标记 `[UNTRUSTED SKILL OUTPUT …]` boundary，neutralize 模式处理 |

#### InputGuardrail 扩展

在 `guardrails/input_guardrail.py` 中新增：

```python
def scan_skill_content(self, content: str, trust_level: str) -> GuardrailDecision:
    """
    扫描 SKILL.md 正文中的注入指令。
    / 扫描 SKILL.md 正文中的注入指令。

    - 可信级别：跳过扫描
    - 半可信级别：扫描但不标记 boundary
    - 不可信级别：扫描 + 标记 [UNTRUSTED SKILL OUTPUT] boundary + neutralize
    """
```

#### ToolGuardrail 扩展

在 `guardrails/tool_guardrail.py` 中新增：

```python
def check_skill_allowed_tools(self, skill_name: str, tool_name: str) -> GuardrailDecision:
    """
    检查 skill 预授权的工具是否与 ToolGuardrail 规则冲突。
    / 检查 skill 预授权工具与 ToolGuardrail 规则是否冲突。

    规则：Guardrails 优先于 allowed-tools。
    如果 ToolGuardrail block 了某个工具，即使 skill 预授权也仍然 block。
    """
```

#### 验收标准

- [ ] 项目级 skill 的正文不经 UNTRUSTED 标记直接注入
- [ ] 用户级 skill 的正文经 InputGuardrail 扫描后再注入
- [ ] Skill 预授权的危险工具（如 `execute_shell`）仍受 ToolGuardrail 规则约束
- [ ] SKILL.md 中包含间接注入指令（如 "Ignore previous instructions"）的不可信 skill 被 neutralize
- [ ] v19 red-team 评测用例中新增 skill 注入 attack vector

---

### v20.4 — Skill 评测 + Checkpoint 持久化（P1，1 周）

**目标**：建立 skill 激活和输出质量的评测框架；支持 skill 激活状态的 checkpoint 持久化。

#### 评测指标

| 指标 | 含义 | 目标 |
|------|------|------|
| `skill_activation_rate` | 应激活 skill 的任务中，skill 实际被激活的比例 | ≥ 0.7 |
| `skill_false_activation_rate` | 不应激活 skill 的任务中，skill 被误激活的比例 | ≤ 0.2 |
| `skill_output_quality` | skill 激活后任务的成功率 vs 无 skill baseline | delta > 0 |
| `skill_token_overhead` | skill 机制引入的额外 token 消耗 | < 15% |

#### Benchmark 扩展

新增 `skill` tag 的评测任务，分为三类：

| 类别 | 数量 | 示例 |
|------|------|------|
| **应激活** | 6-8 | "用 PDF skill 提取表单字段"、"用 data-analysis skill 分析 CSV" |
| **不应激活** | 4-6 | "写一个 fibonacci 函数"（无对应 skill）、"搜索天气"（不匹配 data-analysis） |
| **安全种子** | 3-4 | 含注入指令的 SKILL.md、预授权危险工具的 skill |

#### Checkpoint 扩展

在 `checkpoint/models.py` 的各 PathState 中新增：

```python
# v20: active skills at checkpoint time / v20: checkpoint 时的活跃 skill
active_skills: list[str] = field(default_factory=list)
```

恢复时，自动重新激活 checkpoint 中记录的 skill。

#### 验收标准

- [ ] `python -m evaluation.eval_cli --suite skill` 可运行 skill 评测
- [ ] 报告包含 skill_activation_rate、skill_false_activation_rate、skill_output_quality
- [ ] Skill 激活状态在 checkpoint 中正确保存和恢复
- [ ] 与 v14.6 baseline 对比时，skill 开启后的成功率有明确 delta

---

### v20.5 — Self-Evolution → SKILL.md 自动蒸馏（P1，2 周）

**目标**：让 v17 Self-Evolution 的 `ExperienceLearner` 从成功/失败轨迹中自动蒸馏 SKILL.md 格式的技能文件。

#### 设计

```
任务完成 → ExperienceLearner._learn_from_task()
  │
  ├── [现有] 成功 → PROCEDURAL memory (AgenticMemory)
  ├── [现有] 失败 → EXPERIENTIAL memory (AgenticMemory)
  └── [新增] 高频成功模式 → 检查是否值得蒸馏为 Skill
        │
        ├── 计数：同类任务成功 N 次 (N ≥ 3)
        ├── 提取：共性步骤 → SKILL.md 正文
        ├── 生成：YAML frontmatter (name, description, allowed-tools)
        ├── 写入：.agents/skills/auto-{name}/SKILL.md
        └── 通知：emit skill_auto_created 事件
```

#### SkillAutoDistiller

```python
class SkillAutoDistiller:
    """
    从成功轨迹中蒸馏 SKILL.md / 从成功轨迹中蒸馏 SKILL.md
    遵循 "经验 → 知识 → 可复用技能" 的升级路径
    """

    def should_distill(self, task_pattern: str, success_count: int) -> bool:
        """判断是否值得蒸馏 / 判断是否值得蒸馏"""

    async def distill(self, trajectories: list[TaskTrajectory], task_pattern: str) -> SkillDef | None:
        """
        从轨迹中提取共性步骤，生成 SKILL.md。
        / 从轨迹中提取共性步骤，生成 SKILL.md。

        使用 LLM 辅助提取（SELF_EVOLUTION_LLM_EXTRACTION=true）或
        确定性提取（默认，从 step descriptions 中提取高频模式）。
        """

    def _write_skill_file(self, skill: SkillDef) -> str:
        """写入 .agents/skills/auto-{name}/SKILL.md / 写入 skill 文件"""
```

#### 与 Self-Evolution 的集成

在 `ExperienceLearner._learn_from_task()` 中新增：

```python
# v20.5: try auto-distill skill from accumulated success
# v20.5: 尝试从积累的成功经验中自动蒸馏 skill
if self._skill_distiller is not None:
    pattern = self._classify_task_pattern(task)
    if self._skill_distiller.should_distill(pattern, self._success_counts.get(pattern, 0)):
        skill = await self._skill_distiller.distill(self._recent_trajectories, pattern)
        if skill:
            self._emit("skill_auto_created", {"name": skill.meta.name})
```

#### 安全约束

- 自动蒸馏的 skill 写入 `.agents/skills/auto-{name}/`，trust_level = **半可信**（用户级目录）
- 不自动设置 `allowed-tools`——蒸馏的 skill 只提供指令，不预授权工具
- 蒸馏的 skill 必须经用户审核后才能移到 `.agents/skills/`（项目级，可信）
- 遵循 v17 原则：**禁止静默自改**——蒸馏结果写入文件，但不自动激活，需用户确认

#### 验收标准

- [ ] 同类任务成功 3 次后，自动蒸馏出 SKILL.md 到 `.agents/skills/auto-{name}/`
- [ ] 蒸馏的 SKILL.md 包含正确的 YAML frontmatter
- [ ] 蒸馏的 skill 不自动激活，需用户确认或手动移动到项目级目录
- [ ] `SELF_EVOLUTION_ENABLED=true` + `SKILLS_ENABLED=true` 时才生效
- [ ] 蒸馏失败不影响正常任务执行

---

### v20.6 — Skill 优化闭环（P2，2-3 周，远期）

**目标**：参考微软 SkillOpt 研究，建立 skill description 和内容的自动优化闭环。

#### 背景

SkillOpt（微软 + 上交 + 同济 + 复旦，2026年5月）将 SKILL.md 当作 "可训练的外部状态"，在文本空间做优化：

- 引入深度学习训练纪律：batch size、学习率（文本更新步长 Lt）、验证门控、拒绝编辑缓冲
- 52 个评测单元全胜：GPT-5.5 平均 +23.5 分
- 跨模型/跨环境/跨基准迁移全部正向
- 最终 Skill 仅 379-1995 tokens，部署零推理成本

#### 轻量版设计（不需要独立优化器模型）

利用项目已有的 evaluation + self-evolution 闭环：

```
1. 评估：在 skill 评测集上运行当前 skill
2. 诊断：分析激活失败案例（false negative / false positive）
3. 修订：基于失败案例优化 description（可 LLM 辅助或手动）
4. 验证：在 held-out 验证集上确认改善
5. 部署：验证通过则更新 SKILL.md
```

#### 验收标准

- [ ] `python -m skills.optimize --skill {name}` 可运行优化闭环
- [ ] 优化后的 skill description trigger rate 高于优化前
- [ ] 优化过程使用 train/validation split，防止 overfitting
- [ ] 每次优化生成 diff 报告，需用户确认后才写入

---

## 五、与现有代码的集成映射

### 5.1 关键集成点

| 集成点 | 文件:行 | 集成方式 |
|--------|---------|---------|
| System prompt 组装 | `agents/prompt_utils.py:188-231` | 新增 `inject_skill_guidance` 参数 + `skill_descriptions` 参数 |
| ReAct 引擎 system_hint | `react/engine.py:226-228` | skill 描述通过 system_hint 注入，无需改 ReActEngine |
| Orchestrator 初始化 | `agents/orchestrator.py:106-311` | 新增 SkillLoader + SkillActivationTool 注册，遵循现有 tool 注册模式 |
| Orchestrator 上下文收集 | `agents/orchestrator.py:509-557` | `_gather_context()` 新增 "=== Available Skills ===" 段 |
| Orchestrator 运行 | `agents/orchestrator.py:345-454` | 无需改动——skill 注入在 system prompt 层面完成 |
| 工具注册 | `main.py:工具注册段` | 新增 `SkillActivationTool` 注册，遵循 SubAgent/HITL/Handoff 的模式 |
| 工具过滤 | `agents/specialist.py:116-129` | allowed-tools 过滤复用 SpecialistAgent 的工具过滤模式 |
| Guardrails chokepoint | `react/engine_helpers.execute_tool_calls` | 已有 check_tool_input + scan_tool_output，skill 场景自动覆盖 |
| Guardrail 输出红action | `agents/orchestrator.py:_apply_output_guardrail` | 已覆盖，无需改动 |
| 配置 | `config.py` | 新增 `SKILLS_*` 变量组，遵循 `{FEATURE}_ENABLED` 命名模式 |
| Checkpoint | `checkpoint/models.py` | 各 PathState 新增 `active_skills: list[str]` 字段 |
| 事件 | `main.py:on_event` | 新增 `skills_discovered` / `skill_activated` / `skill_activation_failed` 渲染 |
| A2A AgentCard | `tools/mcp/server.py:205-206` | `AgentSkill(name=..., description=...)` 可从 SkillRegistry 生成 |

### 5.2 不需要改动的模块

| 模块 | 原因 |
|------|------|
| `dag/` | DAG 执行路径的 system prompt 已通过 `_gather_context()` + `build_system_prompt()` 覆盖 |
| `memory/` | Skill 不存储在 AgenticMemory 中，两者独立 |
| `llm/` | LLMClient 无需感知 skill |
| `tracing/` | Skill activation 作为普通 tool call 自动被 tracing 覆盖 |
| `context/manager.py` | Context compression 已能处理 skill 注入的内容 |
| `evolution/` | v20.5 扩展 ExperienceLearner，但不改核心逻辑 |

---

## 六、示例 Skill

### 6.1 项目级 Skill：Python 代码审查

```
.agents/skills/python-code-review/
├── SKILL.md
└── references/
    └── style-guide.md
```

```markdown
---
name: python-code-review
description: >
  Review Python code for bugs, style issues, and security vulnerabilities.
  Use when asked to review, audit, or check Python code, or when the user
  mentions code quality, linting, or best practices.
metadata:
  author: manus-demo
  version: "1.0"
allowed-tools: execute_python file_ops
---

# Python Code Review Skill

## Review checklist
1. Check for common bugs (off-by-one, None checks, mutable defaults)
2. Verify error handling (bare except, missing finally)
3. Look for security issues (SQL injection, hardcoded secrets)
4. Check style (PEP 8, naming conventions)
5. Verify type hints completeness

## Gotchas
- This project uses Python 3.11+ — match patterns are available
- All async functions must use `async def`, not `@asyncio.coroutine`
- Error strings use `Error:` prefix convention for tool results

See [style guide](references/style-guide.md) for project-specific conventions.
```

### 6.2 自动蒸馏 Skill 示例

```
.agents/skills/auto-web-research/
└── SKILL.md
```

```markdown
---
name: auto-web-research
description: >
  Research a topic using web search and URL fetching. Use when asked to
  investigate, research, or find information about a topic that requires
  up-to-date web sources.
metadata:
  author: auto-distilled
  version: "1.0"
  source: self-evolution
---

# Web Research Skill

## Workflow
1. Start with web_search to find relevant sources
2. Use fetch_url to read top 2-3 results
3. Cross-reference information across sources
4. Synthesize findings with citations

## Gotchas
- Always prefer web_search over execute_python for information retrieval
- Bailian MCP search has higher quality for Chinese content
- DDGS fallback is used when Bailian is unavailable
- Truncate long web content to avoid context overflow
```

---

## 七、整体路线图

```text
v14-v19 [已完成]
 │
 └── v20 Agent Skills [P0, 6-9 周]
      │
      ├── v20.1 SkillLoader + SkillRegistry + Discovery 层 [P0, 1 周]
      │   ├── SkillDef / SkillMeta 数据模型
      │   ├── SkillLoader: 目录扫描 + YAML frontmatter 解析 + 验证
      │   ├── SkillRegistry: 内存注册表 + 格式化输出
      │   ├── SkillActivationTool: BaseTool 子类
      │   ├── prompt_utils.py 新增 inject_skill_guidance
      │   ├── config.py 新增 SKILLS_* 变量
      │   └── SKILLS_ENABLED=false 零行为变更
      │
      ├── v20.2 Prompt 注入 + allowed-tools 过滤 [P0, 1 周]
      │   ├── OrchestratorAgent._gather_context() 集成
      │   ├── build_system_prompt() 传入 skill_descriptions
      │   ├── allowed-tools 过滤（复用 SpecialistAgent 模式）
      │   ├── Guardrails 优先于 allowed-tools 规则
      │   └── SKILLS_MAX_ACTIVATIONS_PER_TASK 限制
      │
      ├── v20.3 Guardrails 扩展：Skill 安全沙箱 [P0, 1 周]
      │   ├── 信任分级（项目级可信 / 用户级半可信 / 第三方不可信）
      │   ├── InputGuardrail.scan_skill_content()
      │   ├── ToolGuardrail.check_skill_allowed_tools()
      │   └── v19 red-team 评测新增 skill attack vector
      │
      ├── v20.4 Skill 评测 + Checkpoint 持久化 [P1, 1 周]
      │   ├── skill_activation_rate / skill_false_activation_rate 指标
      │   ├── skill 评测任务集（应激活 / 不应激活 / 安全种子）
      │   ├── Checkpoint PathState 新增 active_skills 字段
      │   └── 与 v14.6 baseline 对比
      │
      ├── v20.5 Self-Evolution → SKILL.md 自动蒸馏 [P1, 2 周]
      │   ├── SkillAutoDistiller: 轨迹 → SKILL.md
      │   ├── 高频成功模式检测 (N ≥ 3)
      │   ├── 写入 .agents/skills/auto-{name}/
      │   ├── 半可信级别 + 用户审核确认
      │   └── 遵循 "禁止静默自改" 原则
      │
      └── v20.6 Skill 优化闭环 [P2, 2-3 周, 远期]
          ├── 评估 → 诊断 → 修订 → 验证 → 部署 闭环
          ├── description 优化（减少 false negative/positive）
          ├── train/validation split 防 overfitting
          ├── diff 报告 + 用户确认
          └── 参考 SkillOpt（轻量版，不需独立优化器模型）
```

---

## 八、关键风险与缓解

| 风险 | 影响版本 | 严重性 | 缓解 |
|------|---------|--------|------|
| 第三方 Skill 注入攻击 | v20.3 | 🔴 高 | v20.3 信任分级 + v19 Guardrails 扫描 + allowed-tools 不覆盖安全策略 |
| Skill 激活误判（DeepSeek 可靠性） | v20.2 | 🟡 中 | description 极度明确 + v20.4 评测 activation_rate + fallback: 关键词预匹配直接注入 |
| 上下文膨胀（多 skill 同时激活） | v20.2 | 🟡 中 | SKILLS_MAX_ACTIVATIONS_PER_TASK=3 + SKILLS_MAX_CONTENT_TOKENS=5000 + 渐进式披露 |
| Skill 与 Specialist 职责混淆 | v20.2 | 🟢 低 | 明确文档：Specialist="谁来做"，Skill="怎么做"；代码层面两者独立 |
| allowed-tools 与 Guardrails 冲突 | v20.2 | 🟡 中 | 明确优先级：Guardrails > allowed-tools > 默认工具集 |
| 自动蒸馏的 skill 质量不稳定 | v20.5 | 🟡 中 | 半可信级别 + 用户审核 + 不自动激活 + 遵循 "禁止静默自改" |
| Spec V1.0 → V2.0 breaking change | 全部 | 🟢 低 | 严格遵循 agentskills.io 规范；frontmatter 向后兼容；预留 metadata 扩展字段 |
| Skill 目录不存在时启动报错 | v20.1 | 🟢 低 | 目录不存在时优雅跳过（info 级日志），不报错 |

---

## 九、参考资源

### 规范 / 官方

| 资源 | 链接 | 用途 |
|------|------|------|
| Agent Skills Specification V1.0 | https://agentskills.io/specification | 核心规范 |
| Agent Skills Quickstart | https://agentskills.io/skill-creation/quickstart | 入门 |
| Agent Skills Best Practices | https://agentskills.io/skill-creation/best-practices | Skill 编写最佳实践 |
| Optimizing Skill Descriptions | https://agentskills.io/skill-creation/optimizing-descriptions | Description 优化方法论 |
| Using Scripts in Skills | https://agentskills.io/skill-creation/using-scripts | 脚本打包 |
| Anthropic 官方 Skills 仓库 | https://github.com/anthropics/skills | 142k+ stars 示例仓库 |
| Skills Reference Validator | https://github.com/agentskills/agentskills/tree/main/skills-ref | `skills-ref validate` 校验工具 |
| Anthropic "Equipping agents" 博文 | https://anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills | 设计哲学 |

### 前沿研究

| 资源 | 链接 | 用途 |
|------|------|------|
| SkillOpt（微软 + 上交 + 同济 + 复旦） | https://github.com/microsoft/SkillOpt | Skill 自动优化 |
| SkillOpt 论文 | https://arxiv.org/pdf/2605.23904 | 文本空间优化方法论 |
| "技能包：下一个共享单元还是定时炸弹？" | 网易 163.com | 第三方 skill 安全风险分析 |

### 本项目关联

| 资源 | 链接/文件 | 关系 |
|------|----------|------|
| v19 Guardrails | `guardrails/` | v20.3 扩展 skill 信任分级 |
| v17 Self-Evolution | `evolution/` | v20.5 蒸馏 skill |
| v18 A2A | `a2a/models.py:AgentSkill` | AgentCard.skills ← SkillRegistry |
| v16 MCP Bridge | `tools/mcp/` | Skill scripts → MCP Tool（远期） |
| Specialist Registry | `agents/specialist.py` | allowed-tools 过滤复用 |
| Evaluation | `evaluation/` | v20.4 skill 评测框架 |

---

## 十、修订记录

- v1 (2026-06-01 初版)：基于 agentskills.io V1.0 规范 + 公网最佳实践 + 项目源码集成点分析，经二次反思复核后生成。关键修正：Skill 匹配不需额外 LLM 调用、Skill 与 Specialist 互补非替代、Skill 不存储在 AgenticMemory 中、Guardrails 优先于 allowed-tools。
