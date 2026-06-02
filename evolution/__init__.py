"""
Self-Evolution (v17+v20.5) - Experience learning, failure reflection, and skill distillation.
自演化（v17+v20.5）—— 经验学习、失败反思与技能蒸馏。

v17: 经验学习 + 失败反思 + 偏好学习
v20.5: 高频成功模式 → SKILL.md 自动蒸馏（SkillAutoDistiller）
"""

from evolution.learner import ExperienceLearner
from evolution.skill_distiller import SkillAutoDistiller
from evolution.models import (
    EVOLUTION_SOURCE,
    EVOLUTION_VERSION,
    EXPERIENCE_TAG,
    FAILURE_LESSON_TAG,
    SKILL_DISTILL_TAG,
    TaskOutcome,
)

__all__ = [
    "ExperienceLearner",
    "SkillAutoDistiller",
    "TaskOutcome",
    "EVOLUTION_SOURCE",
    "EVOLUTION_VERSION",
    "EXPERIENCE_TAG",
    "FAILURE_LESSON_TAG",
    "SKILL_DISTILL_TAG",
]
