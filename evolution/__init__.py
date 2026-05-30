"""
Self-Evolution (v17) - Experience learning & failure reflection.
自演化（v17）—— 经验学习与失败反思。

第一版只做 v17.1 经验学习 + v17.2 失败反思：
  - 任务结束后从轨迹/反思中提炼经验（成功）或失败教训（失败）
  - 写入 v15 Agentic Memory（可回滚、带来源与置信度）
  - 下次相似任务自动检索注入"避坑提示"

不做：RL、模型参数更新、自动改源码、自动生成工具。
"""

from evolution.learner import ExperienceLearner
from evolution.models import (
    EVOLUTION_SOURCE,
    EVOLUTION_VERSION,
    EXPERIENCE_TAG,
    FAILURE_LESSON_TAG,
    TaskOutcome,
)

__all__ = [
    "ExperienceLearner",
    "TaskOutcome",
    "EVOLUTION_SOURCE",
    "EVOLUTION_VERSION",
    "EXPERIENCE_TAG",
    "FAILURE_LESSON_TAG",
]
