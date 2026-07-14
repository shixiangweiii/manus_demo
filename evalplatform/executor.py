"""
Eval set executor — run a generated eval set through the existing
EvaluationRunner and persist progress + aggregated metrics.
评测集执行器 —— 复用 evaluation.runner.EvaluationRunner 执行生成的评测集，
持久化进度与聚合指标。

Delegates the task×repeat loop to ``EvaluationRunner.evaluate_mode`` (with
v21 progress hooks) so tag-driven feature activation, verifiers, LLM-judge
fallback, pass@k and scoring behave exactly like ``python -m evaluation.eval_cli``.
任务×重试循环直接委托给 EvaluationRunner.evaluate_mode（v21 进度钩子），
特性激活、verifier、兜底裁判、pass@k 与评分和 eval_cli 完全一致。

Sandbox isolation (review V1 fix): each run overrides config.SANDBOX_DIR to a
fresh temp dir (tools bind it lazily at first evaluate_task; verifiers read it
live), so file_exists/file_contains can never pass on stale files from a
previous run of the same eval set.
沙箱隔离（评审 V1 修复）：每次运行把 config.SANDBOX_DIR 覆盖为全新临时目录
（工具在首次 evaluate_task 时才惰性绑定；verifier 实时读取），杜绝上一次运行
残留文件让 file 验证器误判成功。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from typing import Any, Callable

import config
from evaluation.metrics import AggregatedMetrics, PlanMode
from evalplatform.models import EvalRunRecord, GeneratedEvalSet, RunStatus

logger = logging.getLogger(__name__)

# 进度回调：每完成一个 (task, mode, trial) 单元调用一次，参数为最新 run 记录
ProgressCallback = Callable[[EvalRunRecord], None]

# repeat 上限（server / CLI / executor 共用，评审 V6 修复：三处口径一致）
MAX_REPEAT = 5


def clamp_repeat(value: Any) -> int:
    """Clamp repeat to [1, MAX_REPEAT]. 钳制 repeat 到 [1, MAX_REPEAT]。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 1
    return max(1, min(MAX_REPEAT, v))


def parse_modes(modes: list[str] | None) -> list[PlanMode]:
    """Parse/validate mode strings; default to simple. 解析模式串，默认 simple。"""
    if not modes:
        return [PlanMode.SIMPLE]
    parsed: list[PlanMode] = []
    for m in modes:
        try:
            mode = PlanMode(str(m).strip().lower())
        except ValueError:
            raise ValueError(f"未知的规划模式：{m}（可选 simple / complex / emergent）")
        if mode not in parsed:
            parsed.append(mode)
    return parsed


class EvalSetExecutor:
    """Execute a GeneratedEvalSet and produce an EvalRunRecord.
    执行评测集并产出运行记录。"""

    def __init__(self, llm_client: Any | None = None, tools: list[Any] | None = None):
        # 延迟导入 EvaluationRunner，避免平台离线路径（store/报告浏览）拉起 LLM 依赖
        from evaluation.runner import EvaluationRunner
        self._runner = EvaluationRunner(llm_client=llm_client, tools=tools)
        # 未显式注入工具时由本执行器拥有 —— 沙箱切换时可安全重建
        # Tools are owned by this executor when not injected — safe to rebuild
        # on sandbox switch (FileOpsTool/ShellTool capture SANDBOX_DIR at __init__).
        self._owns_tools = tools is None

    async def execute(
        self,
        evalset: GeneratedEvalSet,
        run: EvalRunRecord,
        on_progress: ProgressCallback | None = None,
    ) -> EvalRunRecord:
        """
        Run all tasks × modes. Individual task crashes are already absorbed
        by EvaluationRunner (recorded as failures); only infrastructure-level
        errors mark the whole run FAILED.
        运行全部 任务 × 模式。单任务崩溃由 EvaluationRunner 吸收为失败记录，
        仅基础设施级错误会把整个 run 标记为 FAILED。
        """
        modes = parse_modes(run.modes)
        tasks = evalset.tasks
        if not tasks:
            run.status = RunStatus.FAILED
            run.error = "评测集中没有任务"
            return run

        run.repeat = clamp_repeat(run.repeat)
        run.status = RunStatus.RUNNING
        run.started_at = time.time()
        run.progress.total_units = len(tasks) * len(modes) * run.repeat
        run.progress.completed_units = 0
        if on_progress:
            on_progress(run)

        def _on_unit_start(task: Any, mode: PlanMode) -> None:
            run.progress.current_task_id = task.task_id
            run.progress.current_mode = mode.value
            if on_progress:
                on_progress(run)

        def _on_unit_done(task: Any, mode: PlanMode, result: Any) -> None:
            run.progress.completed_units += 1
            if on_progress:
                on_progress(run)
            logger.info(
                "[EvalSetExecutor] %s/%s (%s): success=%s score=%.3f",
                run.run_id, task.task_id, mode.value,
                result.execution.task_success, result.overall_score,
            )

        # 每次运行一个全新沙箱；结束后恢复并清理（工具惰性绑定发生在首次
        # evaluate_task，因此必须在任何 evaluate 调用前覆盖并重建工具集）
        original_sandbox = config.SANDBOX_DIR
        run_sandbox = tempfile.mkdtemp(prefix="manus_evalplatform_sandbox_")
        config.SANDBOX_DIR = run_sandbox
        if self._owns_tools:
            self._runner.tools = None  # 让 runner 在新沙箱下重建默认工具

        try:
            metrics_by_mode: dict[str, Any] = {}
            for mode in modes:
                aggregated: AggregatedMetrics = await self._runner.evaluate_mode(
                    mode,
                    tasks,
                    repeat=run.repeat,
                    on_unit_start=_on_unit_start,
                    on_unit_done=_on_unit_done,
                )
                aggregated.planning_mode = mode
                metrics_by_mode[mode.value] = aggregated.model_dump(mode="json")

            run.metrics_by_mode = metrics_by_mode
            run.status = RunStatus.COMPLETED
            run.llm_model = getattr(self._runner.llm_client, "model", "") or ""
        except Exception as exc:
            logger.error("[EvalSetExecutor] run %s failed: %s", run.run_id, exc, exc_info=True)
            run.status = RunStatus.FAILED
            run.error = str(exc)[:500]
        finally:
            config.SANDBOX_DIR = original_sandbox
            if self._owns_tools:
                self._runner.tools = None  # 防止旧沙箱工具泄漏到下一次 execute
            shutil.rmtree(run_sandbox, ignore_errors=True)
            run.finished_at = time.time()
            run.progress.current_task_id = ""
            run.progress.current_mode = ""
            if on_progress:
                on_progress(run)
        return run
