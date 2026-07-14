"""
Shared synthetic fixtures for evalplatform tests (offline, no LLM).
evalplatform 测试的共享合成数据（离线，不调 LLM）。
"""

from __future__ import annotations

import time

from evaluation.metrics import (
    PlanMode,
    TaskDifficulty,
    TaskEvaluationResult,
    aggregate_results,
)
from evalplatform.models import EvalRunRecord, RunStatus

SAMPLE_MD_DOC = """# FastAPI 服务开发指南

FastAPI 是一个现代化的 Python Web 框架，基于 Starlette 和 Pydantic 构建。

## 路由定义

使用装饰器 @app.get 或 @app.post 定义路由。路径参数通过函数签名声明，
FastAPI 会自动做类型校验。查询参数 query parameter 同样由签名推断。

示例代码中 uvicorn 负责启动 ASGI 服务，默认监听 8000 端口。

## 依赖注入

Depends 机制允许把数据库会话、鉴权逻辑等作为依赖注入到路由函数。
依赖可以嵌套，FastAPI 会按拓扑顺序解析并缓存同一请求内的依赖结果。

## 数据校验

Pydantic 模型声明请求体 schema，自动生成 OpenAPI 文档。
校验失败时返回 422 状态码，错误信息包含字段路径。
"""


def make_result(
    task_id: str = "t1",
    success: bool = True,
    difficulty: TaskDifficulty = TaskDifficulty.EASY,
    mode: PlanMode = PlanMode.SIMPLE,
    tokens: int = 1200,
    failure_category: str = "",
    is_attack: bool = False,
    judge_overrode: bool = False,
) -> TaskEvaluationResult:
    """One synthetic per-task result. 单条合成任务结果。"""
    result = TaskEvaluationResult(
        task_id=task_id,
        task_description=f"synthetic task {task_id}",
        planning_mode=mode,
        task_difficulty=difficulty,
        is_attack=is_attack,
        judge_overrode=judge_overrode,
        failure_category=failure_category,
        final_answer="synthetic answer",
    )
    result.execution.task_success = success
    result.execution.total_steps_planned = 2
    result.execution.steps_completed = 2 if success else 1
    result.efficiency.total_tokens = tokens
    result.overall_score = 0.85 if success else 0.25
    result.planning_score = 0.8
    result.execution_score = 0.9 if success else 0.2
    result.efficiency_score = 0.7
    if failure_category:
        from evaluation.metrics import FailureCategory, FailureRecord
        result.failures.append(
            FailureRecord(category=FailureCategory(failure_category), detail="synthetic")
        )
    return result


def make_passk_result(
    task_id: str,
    passes: int,
    trials: int,
    last_trial_success: bool,
    difficulty: TaskDifficulty = TaskDifficulty.HARD,
    mode: PlanMode = PlanMode.SIMPLE,
) -> TaskEvaluationResult:
    """
    A repeat>1 result: stored execution.task_success reflects ONLY the last
    trial, while pass_at_k is the fractional rate — mirrors real runner output.
    repeat>1 结果：存储的 execution.task_success 仅是最后一次试验，
    pass_at_k 才是分数化成功率，与真实 runner 输出一致。
    """
    result = make_result(
        task_id=task_id, success=last_trial_success, difficulty=difficulty, mode=mode,
    )
    result.trial_count = trials
    result.trial_results = [True] * passes + [False] * (trials - passes)
    result.pass_at_k = passes / trials
    return result


def make_run(
    run_id: str,
    success_flags: list[bool],
    mode: str = "simple",
    started_at: float | None = None,
    evalset_id: str = "es_test",
    **result_kwargs,
) -> EvalRunRecord:
    """A COMPLETED synthetic run whose metrics come from real aggregate_results.
    合成的已完成运行，指标由真实 aggregate_results 计算。"""
    results = [
        make_result(task_id=f"{run_id}_t{i}", success=ok, mode=PlanMode(mode), **result_kwargs)
        for i, ok in enumerate(success_flags)
    ]
    aggregated = aggregate_results(results)
    aggregated.planning_mode = PlanMode(mode)
    run = EvalRunRecord(
        run_id=run_id,
        evalset_id=evalset_id,
        evalset_name="synthetic evalset",
        modes=[mode],
        status=RunStatus.COMPLETED,
        metrics_by_mode={mode: aggregated.model_dump(mode="json")},
        started_at=started_at or time.time(),
        finished_at=(started_at or time.time()) + 60,
    )
    run.progress.total_units = len(success_flags)
    run.progress.completed_units = len(success_flags)
    return run


def make_run_from_results(
    run_id: str,
    results: list[TaskEvaluationResult],
    mode: str = "simple",
    started_at: float | None = None,
) -> EvalRunRecord:
    """Build a COMPLETED run from explicit per-task results (real aggregation).
    用显式逐任务结果构建已完成运行（真实聚合）。"""
    aggregated = aggregate_results(results)
    aggregated.planning_mode = PlanMode(mode)
    run = EvalRunRecord(
        run_id=run_id,
        evalset_id="es_test",
        evalset_name="synthetic evalset",
        modes=[mode],
        repeat=max((r.trial_count for r in results), default=1),
        status=RunStatus.COMPLETED,
        metrics_by_mode={mode: aggregated.model_dump(mode="json")},
        started_at=started_at or time.time(),
        finished_at=(started_at or time.time()) + 60,
    )
    run.progress.total_units = len(results)
    run.progress.completed_units = len(results)
    return run
