"""
Declarative agent skill discovery, registration, and activation.
声明式智能体技能发现、注册与激活。

Skills are file-system declarative packages (a directory with a SKILL.md file)
that extend the agent's behavior at runtime. The LLM sees skill descriptions in
its system prompt and decides whether to activate one via the activate_skill tool.

技能是基于文件系统的声明式包（含 SKILL.md 的目录），在运行时扩展智能体行为。
LLM 在系统提示词中看到技能描述，通过 activate_skill 工具决定是否激活。

Progressive disclosure:
  1. Discovery — name + description loaded into system prompt (~100 tokens/skill)
  2. Activation — full SKILL.md body loaded via activate_skill tool (<5000 tokens)
  3. Execution — scripts/references/assets loaded on demand (future)

渐进式披露：
  1. 发现层 — 名称+描述加载到系统提示词（约 100 tokens/技能）
  2. 激活层 — 通过 activate_skill 工具加载完整 SKILL.md 正文（<5000 tokens）
  3. 执行层 — 按需加载 scripts/references/assets（未来实现）
"""

from skills.models import SkillMeta, SkillDef, SkillTrustLevel
from skills.loader import SkillLoader
from skills.registry import SkillRegistry
from skills.activation import SkillActivationTool
from skills.optimizer import SkillOptimizer, SkillEvalCase, SkillOptimizationReport

__all__ = [
    "SkillMeta", "SkillDef", "SkillTrustLevel",
    "SkillLoader", "SkillRegistry", "SkillActivationTool",
    "SkillOptimizer", "SkillEvalCase", "SkillOptimizationReport",
]
