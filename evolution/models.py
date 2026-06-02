"""
Self-Evolution models - Task outcome snapshot & evolution-record conventions.
自演化模型 —— 任务结果快照与演化记忆的约定常量。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schema import StepResult

# ======================================================================
# Conventions / 约定常量
# ======================================================================

# 演化记忆统一来源标记，便于检索过滤与回滚（revoke）
# All v17-learned records carry source=EVOLUTION_SOURCE for filtering/rollback.
EVOLUTION_SOURCE = "evolution"

# 成功经验记忆 tag（写入 PROCEDURAL）
EXPERIENCE_TAG = "evolution_experience"

# 失败教训记忆 tag（写入 EXPERIENTIAL）
FAILURE_LESSON_TAG = "failure_lesson"

# 用户偏好记忆 tag（v17.4，写入 FACTUAL）
USER_PREFERENCE_TAG = "user_preference"

# v20.5 Skill 自动蒸馏标记 tag（写入 PROCEDURAL，防重复蒸馏）
SKILL_DISTILL_TAG = "skill_distill"

# 版本标记，写入 metadata.evolution_version
EVOLUTION_VERSION = "v20.5"


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
    complexity: str = ""                # simple / complex / emergent / goal_driven
    success: bool = False
    final_answer: str = ""
    reflection_feedback: str = ""       # 来自 Reflection.feedback（无则空）
    reflection_score: float = 0.0       # 来自 Reflection.score
    suggestions: list[str] = Field(default_factory=list)  # 来自 Reflection.suggestions
    trajectory: list[StepResult] = Field(default_factory=list)  # 执行轨迹
    # v17.4 偏好学习预留：HITL question/answer 对，本版不填充
    hitl_pairs: list[dict] = Field(default_factory=list)
