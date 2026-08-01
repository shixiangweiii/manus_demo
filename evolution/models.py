"""
Self-Evolution models - Task outcome snapshot & evolution-record conventions.
自演化模型 —— 任务结果快照与演化记忆的约定常量。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from execution.models import StepResult

# ======================================================================
# Conventions / 约定常量
# ======================================================================

# 演化记忆统一来源标记，便于检索过滤与回滚（revoke）
# All learned records carry source=EVOLUTION_SOURCE for filtering and rollback.
EVOLUTION_SOURCE = "evolution"

# 成功经验记忆 tag（写入 PROCEDURAL）
EXPERIENCE_TAG = "evolution_experience"

# 失败教训记忆 tag（写入 EXPERIENTIAL）
FAILURE_LESSON_TAG = "failure_lesson"

# 用户偏好记忆 tag（写入 FACTUAL）
USER_PREFERENCE_TAG = "user_preference"

# Skill 自动蒸馏标记 tag（写入 PROCEDURAL，防重复蒸馏）
SKILL_DISTILL_TAG = "skill_distill"

# 记忆格式标记，写入 metadata.evolution_schema
EVOLUTION_SCHEMA = "experience-v1"


# ======================================================================
# Models
# ======================================================================

class TaskOutcome(BaseModel):
    """
    Factual snapshot of one completed task, fed to ExperienceLearner.
    一次任务结束时的事实快照，传给 ExperienceLearner 提炼经验/教训。
    """
    task: str = ""
    task_id: str = ""
    complexity: str = ""                # semantic engine name
    success: bool = False
    final_answer: str = ""
    reflection_feedback: str = ""       # 来自 Reflection.feedback（无则空）
    reflection_score: float = 0.0       # 来自 Reflection.score
    suggestions: list[str] = Field(default_factory=list)  # 来自 Reflection.suggestions
    trajectory: list[StepResult] = Field(default_factory=list)  # 执行轨迹
    # 偏好学习预留：HITL question/answer 对，当前不填充
    hitl_pairs: list[dict] = Field(default_factory=list)
