"""
Skill models - Data classes for skill metadata and definitions.
技能元数据与定义的数据类。

Skills are file-system declarative packages that extend the agent's behavior
at runtime. The LLM sees skill descriptions in its system prompt and decides
whether to activate one via the activate_skill tool.

技能是基于文件系统的声明式包，在运行时扩展智能体行为。
LLM 在系统提示词中看到技能描述，通过 activate_skill 工具决定是否激活。

Progressive disclosure tiers:
  1. Discovery — SkillMeta (name + description, ~100 tokens/skill in system prompt)
  2. Activation — SkillDef.full_content (via activate_skill tool, <5000 tokens)
  3. Execution — scripts/references/assets (on demand)

渐进式披露层级：
  1. 发现层 — SkillMeta（名称+描述，约 100 tokens/技能 在系统提示词中）
  2. 激活层 — SkillDef.full_content（通过 activate_skill 工具，<5000 tokens）
  3. 执行层 — scripts/references/assets（按需加载）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SkillTrustLevel(str, Enum):
    """Trust level for skill content security policy.
    技能内容安全策略的信任等级。

    Determines how the InputGuardrail processes SKILL.md body content:
    - PROJECT: Trusted — shipped with code, skip scanning entirely
    - USER: Semi-trusted — scan for injection but no boundary marking
    - THIRD_PARTY: Untrusted — scan + boundary marking + neutralize

    决定 InputGuardrail 如何处理 SKILL.md 正文内容：
    - PROJECT: 可信——随代码发布，跳过扫描
    - USER: 半可信——扫描注入但不加边界标记
    - THIRD_PARTY: 不可信——扫描 + 边界标记 + neutralize
    """
    PROJECT = "project"          # Trusted — shipped with code, no scanning
    USER = "user"                # Semi-trusted — scan but no boundary marking
    THIRD_PARTY = "third_party"  # Untrusted — scan + boundary + neutralize


@dataclass
class SkillMeta:
    """Lightweight metadata for skill discovery / progressive disclosure tier 1.
    用于技能发现的轻量元数据（渐进式披露第一层）。

    Approximately 100 tokens per skill when formatted in the system prompt.
    Each skill's name and description are shown to the LLM at startup so it
    can decide whether to activate a skill during the structured tool-calling loop.

    每个技能在系统提示词中约占 100 tokens。
    启动时向 LLM 展示每个技能的名称和描述，LLM 在结构化工具调用循环中自行决定
    是否激活某个技能。
    """
    # Required fields / 必填字段
    name: str                           # Unique skill identifier (kebab-case, 1-64 chars)
                                        # 唯一技能标识符（kebab-case，1-64 字符）
    description: str                    # One-line description for LLM decision-making (1-1024 chars)
                                        # 供 LLM 决策的一行描述（1-1024 字符）

    # Optional fields / 可选字段
    license: str = ""                   # "project" (trusted) | "user" (semi-trusted) | "third_party" (untrusted)
                                        # Empty = auto-detected by loader based on directory.
                                        # "project"（可信）| "user"（半可信）| "third_party"（不可信）
                                        # 空 = 由 loader 根据目录自动检测。
    metadata: dict[str, Any] = field(default_factory=dict) # Arbitrary key-value metadata
                                                            # 任意键值元数据
    allowed_tools: list[str] = field(default_factory=list) # Tool names this skill pre-authorizes;
                                                            # empty list = all tools allowed (default)
                                                            # 此技能预授权的工具名列表；
                                                            # 空列表 = 允许所有工具（默认）


# Reserved name prefixes that cannot be used for skill names (prevent confusion
# with built-in tools and meta-tools).
# 技能名称不可使用的保留前缀（避免与内置工具和元工具混淆）。
RESERVED_SKILL_PREFIXES = ("activate_", "memory_", "subagent_", "ask_", "guardrail_")

# Name validation pattern: lowercase, digits, hyphens only; 1-64 chars.
# 名称校验正则：仅小写字母、数字、连字符；1-64 字符。
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")


@dataclass
class SkillDef:
    """Full skill definition loaded on activation / progressive disclosure tier 2.
    激活时加载的完整技能定义（渐进式披露第二层）。

    This is returned by SkillActivationTool.execute() when the LLM activates
    a skill. The full_content field contains the SKILL.md body (everything
    after the YAML frontmatter) which provides detailed instructions,
    constraints, and workflow guidance.

    当 LLM 激活技能时，由 SkillActivationTool.execute() 返回。
    full_content 字段包含 SKILL.md 正文（YAML frontmatter 之后的所有内容），
    提供详细指令、约束和工作流引导。
    """
    meta: SkillMeta                     # Skill metadata (discovery tier)
                                        # 技能元数据（发现层）
    skill_dir: str                      # Absolute path to the skill directory
                                        # 技能目录的绝对路径
    full_content: str                   # Full SKILL.md body (after frontmatter)
                                        # 完整 SKILL.md 正文（frontmatter 之后）
    scripts: list[str] = field(default_factory=list)    # Relative paths to script files (.py, .sh, .js)
                                                         # 脚本文件的相对路径
    references: list[str] = field(default_factory=list) # Relative paths to reference docs (.md, .txt, .rst)
                                                         # 参考文档的相对路径
    assets: list[str] = field(default_factory=list)     # Relative paths to other assets
                                                         # 其他资源的相对路径
