"""
Evaluation Platform data models.
评测平台数据模型。

All records are plain Pydantic models persisted as JSON files by
``evalplatform.store.EvalPlatformStore``. BenchmarkTask is reused from
``evaluation.benchmark`` so generated eval sets run unmodified on the
existing EvaluationRunner / metrics / verifier stack.
所有记录均为 Pydantic 模型，由 EvalPlatformStore 以 JSON 文件持久化。
BenchmarkTask 直接复用 evaluation.benchmark，保证生成的评测集可以
原样跑在现有 EvaluationRunner / 指标 / verifier 体系上。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from evaluation.benchmark import BenchmarkTask


def new_id(prefix: str) -> str:
    """Short unique id with a type prefix. 带类型前缀的短唯一 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ======================================================================
# 1. Uploaded document / 上传文档
# ======================================================================

class DocumentRecord(BaseModel):
    """An uploaded source document. 一份上传的源文档。"""

    doc_id: str = Field(default_factory=lambda: new_id("doc"))
    filename: str = ""
    title: str = ""                       # 展示标题（默认取文件名）
    content: str = ""                     # 提取后的纯文本内容
    char_count: int = 0
    content_hash: str = ""                # sha256，用于重复上传去重
    created_at: float = Field(default_factory=time.time)


# ======================================================================
# 2. Generated eval set / 生成的评测集
# ======================================================================

class EvalSetStatus(str, Enum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class GeneratedEvalSet(BaseModel):
    """An eval set generated from a document. 从文档生成的评测集。"""

    evalset_id: str = Field(default_factory=lambda: new_id("es"))
    name: str = ""
    doc_id: str = ""
    doc_filename: str = ""
    target_goal: str = ""                 # 用户给定的目标任务描述（可空）
    status: EvalSetStatus = EvalSetStatus.GENERATING
    generator: str = "llm"                # llm | heuristic
    generation_model: str = ""            # 生成所用 LLM 模型名
    generation_error: str = ""            # 失败原因（status=failed 时）
    requested_num_tasks: int = 0
    tasks: list[BenchmarkTask] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


# ======================================================================
# 3. Evaluation run / 评测运行
# ======================================================================

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunProgress(BaseModel):
    """Coarse progress for polling. 供轮询的粗粒度进度。"""

    total_units: int = 0                  # 任务数 × 模式数
    completed_units: int = 0
    current_task_id: str = ""
    current_mode: str = ""


class EvalRunRecord(BaseModel):
    """One execution of an eval set. 评测集的一次执行。"""

    run_id: str = Field(default_factory=lambda: new_id("run"))
    evalset_id: str = ""
    evalset_name: str = ""
    doc_id: str = ""
    modes: list[str] = Field(default_factory=lambda: ["simple"])
    repeat: int = 1
    status: RunStatus = RunStatus.PENDING
    error: str = ""
    llm_model: str = ""
    progress: RunProgress = Field(default_factory=RunProgress)
    # AggregatedMetrics.model_dump() per mode（含逐任务 results，供报告钻取）
    metrics_by_mode: dict[str, Any] = Field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0
    created_at: float = Field(default_factory=time.time)


# ======================================================================
# 4. Single-run report / 单次评测报告
# ======================================================================

class EvalReport(BaseModel):
    """Structured report for one run (+ rendered markdown).
    单次运行的结构化报告（附渲染后的 markdown）。"""

    run_id: str = ""
    evalset_id: str = ""
    evalset_name: str = ""
    doc_filename: str = ""
    generated_at: float = Field(default_factory=time.time)
    # 每模式概要行：mode / tasks / success_rate / 各维度评分 / tokens / time
    mode_summaries: list[dict[str, Any]] = Field(default_factory=list)
    per_task_rows: list[dict[str, Any]] = Field(default_factory=list)
    failure_distribution: dict[str, int] = Field(default_factory=dict)
    weak_dimensions: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    markdown: str = ""


# ======================================================================
# 5. Aggregate analysis / 聚合分析
# ======================================================================

class Suggestion(BaseModel):
    """One actionable optimization suggestion. 一条可执行的优化建议。"""

    title: str = ""
    severity: str = "info"                # critical | warning | info
    evidence: str = ""                    # 触发该建议的数据证据
    action: str = ""                      # 建议采取的动作（含配置项/命令）


class AggregateAnalysis(BaseModel):
    """Cross-run aggregated analysis. 跨多次运行的聚合分析。"""

    analysis_id: str = Field(default_factory=lambda: new_id("ana"))
    run_ids: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    stats: dict[str, Any] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    llm_insight: str = ""                 # 可选 LLM 深度分析（无 LLM 时为空）
    markdown: str = ""
