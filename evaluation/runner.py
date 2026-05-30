"""
Evaluation runner — orchestrates benchmark execution and metric collection.

Hooks into the existing OrchestratorAgent pipeline via event listeners,
collecting per-phase metrics without modifying the core execution path.
Then computes evaluation results using the metrics module.

评测执行器 —— 编排基准任务执行并收集指标。

通过事件监听器挂接到现有 OrchestratorAgent 流水线，
在不修改核心执行路径的前提下收集各阶段指标。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import re
import shutil
import tempfile
import uuid
from typing import Any, TYPE_CHECKING

import config
from evaluation.benchmark import BenchmarkTask, get_benchmark_tasks
from evaluation.metrics import (
    AggregatedMetrics,
    ExecutionMetrics,
    EfficiencyMetrics,
    FailureCategory,
    FailureRecord,
    PlanMode,
    PlanningMetrics,
    ReflectionMetrics,
    TaskDifficulty,
    TaskEvaluationResult,
    aggregate_results,
    compute_efficiency_score,
    compute_execution_score,
    compute_overall_score,
    compute_planning_score,
)
from evaluation.probe import EvaluationProbe  # noqa: F401 — backward-compat re-export

if TYPE_CHECKING:
    from llm.client import LLMClient
    from tools.base import BaseTool

logger = logging.getLogger(__name__)
OrchestratorAgent: Any | None = None


# ======================================================================
# Evaluation Runner
# ======================================================================

class EvaluationRunner:
    """
    Runs evaluation benchmarks across one or all planning modes.
    对一个或全部规划模式运行评测基准。

    For each benchmark task × planning mode, the runner:
      1. Creates a fresh OrchestratorAgent
      2. Attaches an EvaluationProbe to capture events
      3. Forces the specified planning mode via config override
      4. Executes the task
      5. Collects metrics from the probe
      6. Returns TaskEvaluationResult
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        tools: list[BaseTool] | None = None,
    ):
        self.llm_client = llm_client
        self.tools = tools

    async def evaluate_task(
        self,
        task: BenchmarkTask,
        mode: PlanMode,
        _shared_memory_dir: str | None = None,
    ) -> TaskEvaluationResult:
        """
        Evaluate a single benchmark task with a specific planning mode.
        以指定规划模式评测单个基准任务。

        v8: tags drive feature activation —
          - "hitl"        → HITL_ENABLED=true + interactive=True + SimulatedUser intercept
          - "subagent"    → SUBAGENT_ENABLED=true
          - "goal_driven" → ENABLE_GOAL_DRIVEN_PLANNER=true
        After the run, all overridden config values are restored.
        """
        # Import tools lazily to avoid circular imports
        if self.tools is None:
            from tools.code_executor import CodeExecutorTool
            from tools.fetch_url import FetchUrlTool
            from tools.file_ops import FileOpsTool
            from tools.shell_tool import ShellTool
            from tools.web_search import WebSearchTool
            from tools.user_location import UserLocationTool
            self.tools = [
                WebSearchTool(), FetchUrlTool(), UserLocationTool(),
                CodeExecutorTool(), FileOpsTool(), ShellTool(),
            ]

        if self.llm_client is None:
            from llm.client import LLMClient
            self.llm_client = LLMClient()

        probe = EvaluationProbe()
        llm_client = self.llm_client  # Use shared client

        # Set classification_forced from runner (knows the mode), not from config
        probe.classification_forced = (mode.value != "auto")

        # v8: capture original env for full restoration
        original_plan_mode = config.PLAN_MODE
        original_emergent = config.EMERGENT_PLANNING_ENABLED
        original_hitl = config.HITL_ENABLED
        original_subagent = config.SUBAGENT_ENABLED
        original_goal_driven = config.ENABLE_GOAL_DRIVEN_PLANNER
        original_handoff = config.HANDOFF_ENABLED
        original_handoff_ask_user = config.HANDOFF_ALLOW_ASK_USER
        original_agentic_memory = config.AGENTIC_MEMORY_ENABLED
        original_memory_tools = config.MEMORY_TOOLS_ENABLED
        original_memory_dir = config.MEMORY_DIR
        original_mcp_bridge = config.MCP_BRIDGE_ENABLED

        config.PLAN_MODE = mode.value
        if mode == PlanMode.EMERGENT:
            config.EMERGENT_PLANNING_ENABLED = True

        # v8: feature activation by task tag
        tags_lower = [t.lower() for t in task.tags]
        is_hitl_task = "hitl" in tags_lower
        is_subagent_task = "subagent" in tags_lower
        is_goal_driven_task = "goal_driven" in tags_lower
        is_resume_task = "resume" in tags_lower
        is_memory_task = "memory" in tags_lower
        is_handoff_task = "handoff" in tags_lower
        is_mcp_task = "mcp" in tags_lower

        # v15: Memory tasks get isolated MEMORY_DIR to avoid polluting user's real memory
        _memory_tmpdir: str | None = None
        if is_memory_task:
            config.AGENTIC_MEMORY_ENABLED = True
            config.MEMORY_TOOLS_ENABLED = True
            if _shared_memory_dir:
                config.MEMORY_DIR = _shared_memory_dir
            else:
                _memory_tmpdir = tempfile.mkdtemp(prefix="manus_eval_memory_")
                config.MEMORY_DIR = _memory_tmpdir

        if is_hitl_task:
            config.HITL_ENABLED = True
        if is_subagent_task:
            config.SUBAGENT_ENABLED = True
        if is_goal_driven_task:
            config.ENABLE_GOAL_DRIVEN_PLANNER = True
        # v18.5: handoff-tagged tasks enable Handoff; handoff+hitl tasks must opt
        # the specialist into ask_user explicitly (roadmap §10 requirement).
        if is_handoff_task:
            config.HANDOFF_ENABLED = True
            if is_hitl_task:
                config.HANDOFF_ALLOW_ASK_USER = True
        # v16: mcp-tagged tasks enable the MCP Bridge client (a configured server is
        # still required for discovery to yield tools; variants may also flip the flag).
        if is_mcp_task:
            config.MCP_BRIDGE_ENABLED = True

        # v8: SimulatedUser for HITL tasks — intercepts ask_user_prompt event and
        # resolves the Future with a scripted response BEFORE the probe records it.
        sim_user = None
        if is_hitl_task:
            from evaluation.user_simulator import SimulatedUser
            sim_user = SimulatedUser(task.ground_truth.simulated_responses or [])

        def event_callback(event: str, data: Any) -> None:
            # HITL intercept: must resolve future BEFORE delegating to probe
            if sim_user is not None and event == "ask_user_prompt":
                try:
                    fut = data.get("response_future") if isinstance(data, dict) else None
                    if fut is not None and not fut.done():
                        question = data.get("question", "") if isinstance(data, dict) else ""
                        response = sim_user.respond(question)
                        fut.set_result(response)
                except Exception as exc:
                    logger.warning("[EvalRunner] SimulatedUser intercept failed: %s", exc)
            probe.on_event(event, data)

        # v16: discover MCP bridge tools per task so events reach THIS task's probe
        # (avoids a cached on_event pointing at a stale probe). Discovery yields
        # tools only when a server is configured; otherwise it emits count=0.
        task_tools = self.tools
        if config.MCP_BRIDGE_ENABLED:
            try:
                from tools.mcp.discovery import discover_mcp_bridge_tools
                mcp_tools = await discover_mcp_bridge_tools(on_event=event_callback)
                if mcp_tools:
                    task_tools = list(self.tools) + mcp_tools
            except Exception as exc:
                logger.warning("[EvalRunner] MCP discovery failed: %s", exc)

        from checkpoint.store import TaskStateStore
        with tempfile.TemporaryDirectory(prefix="manus_eval_checkpoints_") as checkpoint_dir:
            task_state_store = TaskStateStore(checkpoint_dir=checkpoint_dir)
            resume_attempted = False
            resume_seed_task_id = ""

            try:
                global OrchestratorAgent
                if OrchestratorAgent is None:
                    from agents.orchestrator import OrchestratorAgent as _OrchestratorAgent
                    OrchestratorAgent = _OrchestratorAgent

                orchestrator = OrchestratorAgent(
                    llm_client=llm_client,
                    tools=task_tools,
                    on_event=event_callback,
                    interactive=is_hitl_task,
                    task_state_store=task_state_store,
                )

                logger.info(
                    "[EvalRunner] Running task '%s' with mode '%s' (hitl=%s subagent=%s goal_driven=%s)",
                    task.task_id, mode.value, is_hitl_task, is_subagent_task, is_goal_driven_task,
                )

                if is_resume_task:
                    resume_attempted = True
                    resume_seed_task_id = self._seed_resume_checkpoint(task, task_state_store)
                    await orchestrator.resume(resume_seed_task_id)
                else:
                    await orchestrator.run(task.task_description)

            except Exception as exc:
                logger.error(
                    "[EvalRunner] Task '%s' crashed with mode '%s': %s",
                    task.task_id, mode.value, exc,
                )
                probe.failures.append(FailureRecord(
                    category=FailureCategory.LLM_CALL_FAILURE,
                    detail=str(exc)[:300],
                ))
            finally:
                config.PLAN_MODE = original_plan_mode
                config.EMERGENT_PLANNING_ENABLED = original_emergent
                config.HITL_ENABLED = original_hitl
                config.SUBAGENT_ENABLED = original_subagent
                config.ENABLE_GOAL_DRIVEN_PLANNER = original_goal_driven
                config.HANDOFF_ENABLED = original_handoff
                config.HANDOFF_ALLOW_ASK_USER = original_handoff_ask_user
                config.AGENTIC_MEMORY_ENABLED = original_agentic_memory
                config.MEMORY_TOOLS_ENABLED = original_memory_tools
                config.MEMORY_DIR = original_memory_dir
                config.MCP_BRIDGE_ENABLED = original_mcp_bridge
                if _memory_tmpdir:
                    shutil.rmtree(_memory_tmpdir, ignore_errors=True)

            checkpoint_task_id = probe.runtime_task_id or resume_seed_task_id
            checkpoint_count = (
                task_state_store.count_checkpoints(checkpoint_task_id)
                if checkpoint_task_id else 0
            )

            # Build config snapshot from runner (has config imported)
            config_snapshot = {
                "plan_mode": mode.value,
                "max_react_iterations": getattr(config, 'MAX_REACT_ITERATIONS', None),
                "max_parallel_nodes": getattr(config, 'MAX_PARALLEL_NODES', None),
                "max_replan_attempts": getattr(config, 'MAX_REPLAN_ATTEMPTS', None),
                "emergent_planning_enabled": getattr(config, 'EMERGENT_PLANNING_ENABLED', None),
                "adaptive_planning_enabled": getattr(config, 'ADAPTIVE_PLANNING_ENABLED', None),
                "enable_reasoning_engine": getattr(config, 'ENABLE_REASONING_ENGINE', None),
                "reasoning_effort": getattr(config, 'REASONING_EFFORT', None),
                "max_thinking_tokens": getattr(config, 'MAX_THINKING_TOKENS', None),
                "max_thinking_rounds": getattr(config, 'MAX_THINKING_ROUNDS', None),
                "dag_serial_execution": getattr(config, 'DAG_SERIAL_EXECUTION', None),
                "subagent_enabled": getattr(config, 'SUBAGENT_ENABLED', None),
                "enable_goal_driven_planner": getattr(config, 'ENABLE_GOAL_DRIVEN_PLANNER', None),
            }

            result = probe.build_result(
                task=task,
                forced_mode=mode,
                llm_model=self.llm_client.model if self.llm_client else "unknown",
                config_snapshot=config_snapshot,
                sandbox_dir=getattr(config, "SANDBOX_DIR", ""),
                checkpoint_count=checkpoint_count,
                resume_attempted=resume_attempted,
                resume_success=resume_attempted and probe.task_success,
            )

            # v8: LLM-as-Judge fallback when keyword check fails
            await self._maybe_apply_llm_judge(task, result)
            if resume_attempted:
                result.resume_success = result.execution.task_success

            return result

    def _seed_resume_checkpoint(
        self,
        task: BenchmarkTask,
        task_state_store: Any,
    ) -> str:
        """Create a minimal simple-path checkpoint so evaluation can exercise resume()."""
        from checkpoint.models import SimplePathState, TaskCheckpoint
        from schema import Plan, ReasoningEffort, Step, TaskRunState

        task_id = f"evalrsm_{uuid.uuid4().hex[:8]}"
        now = time.time()
        plan = Plan(
            task=task.task_description,
            steps=[Step(id=1, description=task.task_description)],
        )
        checkpoint = TaskCheckpoint(
            task_id=task_id,
            task=task.task_description,
            context="",
            complexity="simple",
            effort=ReasoningEffort.MEDIUM.value,
            state=TaskRunState.RUNNING,
            created_at=now,
            updated_at=now,
            simple_state=SimplePathState(
                plan=plan.model_dump(),
                all_results=[],
                current_step_index=0,
            ),
            resume_metadata={"boundary": "evaluation_resume_seed"},
        )
        task_state_store.save(checkpoint)
        return task_id

    async def _maybe_apply_llm_judge(
        self,
        task: BenchmarkTask,
        result: TaskEvaluationResult,
    ) -> None:
        """
        v8: LLM-as-Judge fallback. When task is marked failed solely due to
        missed must_include_keywords (but otherwise has a non-empty answer),
        invoke an LLM judge to assess semantic correctness against
        success_criteria. If judge passes with high confidence, flip task_success
        to True and record the override on the result.
        """
        gt = task.ground_truth
        if result.execution.task_success:
            return
        if result.failed_by_verifier:
            return
        if not gt.must_include_keywords or not gt.success_criteria:
            return
        if not result.final_answer:
            return

        answer_lower = result.final_answer.lower()

        if "无法完成" in result.final_answer or "could not complete" in answer_lower:
            return

        if gt.must_not_include and any(
            kw.lower() in answer_lower for kw in gt.must_not_include
        ):
            return

        missed = [
            kw for kw in gt.must_include_keywords
            if kw.lower() not in answer_lower
        ]
        if not missed:
            return

        from evaluation.llm_judge import judge_answer_quality
        try:
            passed, conf, reasoning = await judge_answer_quality(
                self.llm_client,
                task.task_description,
                result.final_answer,
                gt.success_criteria,
                missed,
            )
            if passed and conf >= 0.7:
                result.execution.task_success = True
                result.judge_overrode = True
                result.judge_reasoning = reasoning
                from evaluation.metrics import (
                    compute_execution_score, compute_overall_score,
                )
                result.execution_score = compute_execution_score(result.execution)
                result.overall_score = compute_overall_score(
                    result.planning_score,
                    result.execution_score,
                    result.efficiency_score,
                    result.reflection,
                )
                logger.info(
                    "[LLMJudge] Override accepted for %s: %s (confidence=%.2f)",
                    task.task_id, reasoning[:120], conf,
                )
            else:
                if reasoning.startswith("[judge error]"):
                    result.judge_reasoning = reasoning
                else:
                    result.judge_reasoning = f"[declined, conf={conf:.2f}] {reasoning}"
        except Exception as exc:
            logger.warning("[LLMJudge] Judge call failed for %s: %s", task.task_id, exc)
            result.judge_reasoning = f"[judge error] {exc}"

    async def evaluate_mode(
        self,
        mode: PlanMode,
        tasks: list[BenchmarkTask] | None = None,
        repeat: int = 1,
    ) -> AggregatedMetrics:
        """
        Evaluate all (or filtered) benchmark tasks with a specific planning mode.
        以指定规划模式评测全部（或筛选后的）基准任务。
        """
        if tasks is None:
            tasks = get_benchmark_tasks()

        # v15: shared MEMORY_DIR for all memory tasks in this mode run
        _shared_memory_dir: str | None = None
        if any("memory" in t.tags for t in tasks):
            _shared_memory_dir = tempfile.mkdtemp(prefix="manus_eval_memory_")

        results: list[TaskEvaluationResult] = []
        try:
            for task in tasks:
                if repeat <= 1:
                    result = await self.evaluate_task(task, mode, _shared_memory_dir=_shared_memory_dir)
                    result.trial_count = 1
                    result.trial_results = [result.execution.task_success]
                    results.append(result)
                    logger.info(
                        "[EvalRunner] Task '%s' (%s): score=%.3f success=%s",
                        task.task_id, mode.value, result.overall_score, result.execution.task_success,
                    )
                else:
                    trial_results: list[bool] = []
                    last_result: TaskEvaluationResult | None = None
                    for k_idx in range(repeat):
                        r = await self.evaluate_task(task, mode, _shared_memory_dir=_shared_memory_dir)
                        trial_results.append(r.execution.task_success)
                        last_result = r
                        logger.info(
                            "[EvalRunner] Task '%s' (%s) trial %d/%d: success=%s",
                            task.task_id, mode.value, k_idx + 1, repeat,
                            r.execution.task_success,
                        )
                    assert last_result is not None
                    last_result.trial_count = repeat
                    last_result.trial_results = trial_results
                    last_result.pass_at_k = sum(trial_results) / repeat
                    results.append(last_result)
                    logger.info(
                        "[EvalRunner] Task '%s' (%s) pass@%d=%.2f (%d/%d successes)",
                        task.task_id, mode.value, repeat,
                        last_result.pass_at_k, sum(trial_results), repeat,
                    )
        finally:
            if _shared_memory_dir:
                shutil.rmtree(_shared_memory_dir, ignore_errors=True)

        return aggregate_results(results)

    async def evaluate_all_modes(
        self,
        tasks: list[BenchmarkTask] | None = None,
        modes: list[PlanMode] | None = None,
        repeat: int = 1,
    ) -> dict[PlanMode, AggregatedMetrics]:
        """
        Evaluate all benchmark tasks across all planning modes.
        以全部规划模式评测全部基准任务，返回各模式的聚合指标。
        """
        if tasks is None:
            tasks = get_benchmark_tasks()
        if modes is None:
            modes = list(PlanMode)

        all_metrics: dict[PlanMode, AggregatedMetrics] = {}
        for mode in modes:
            logger.info("[EvalRunner] ===== Evaluating mode: %s (repeat=%d) =====", mode.value, repeat)
            metrics = await self.evaluate_mode(mode, tasks, repeat=repeat)
            all_metrics[mode] = metrics

        return all_metrics
