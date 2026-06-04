"""
Evaluation probe — instruments a single task run via event interception.

Extracted from runner.py to decouple from runtime dependencies (config, agents,
llm, schema, tools). This module imports only evaluation-internal packages and
has zero dependency on openai / opentelemetry / OrchestratorAgent.

事件探针 —— 通过事件拦截对单次任务执行进行仪器化监测。

从 runner.py 抽取，与运行时依赖（config、agents、llm、schema、tools）解耦。
本模块仅导入 evaluation 内部包，不依赖 openai / opentelemetry / OrchestratorAgent。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from evaluation.benchmark import BenchmarkTask
from evaluation.metrics import (
    EfficiencyMetrics,
    ExecutionMetrics,
    FailureCategory,
    FailureRecord,
    PlanMode,
    PlanningMetrics,
    ReflectionMetrics,
    TaskEvaluationResult,
    compute_efficiency_score,
    compute_execution_score,
    compute_overall_score,
    compute_planning_score,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Event Probe — hooks into the Orchestrator's event stream
# 事件探针 —— 挂接到 Orchestrator 的事件流
# ======================================================================

class EvaluationProbe:
    """
    Instruments a single task run by intercepting Orchestrator events.
    通过拦截 Orchestrator 事件，对单次任务执行进行仪器化监测。

    Attaches to OrchestratorAgent._on_event callback to capture:
      - Task classification result
      - Plan structure (step count, node count, edges)
      - Step/node execution outcomes (success, failure, timeout)
      - Tool call records
      - Reflection verdict
      - Token usage

    This class has NO runtime imports — it receives event data through the
    on_event callback and uses duck-typing for schema objects (Reflection,
    TokenUsageSummary) to avoid coupling to the schema module.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset all collected data for a new task run."""
        # Planning phase
        self.classified_complexity: str = ""
        self.plan_step_count: int = 0
        self.plan_generation_start: float = 0.0
        self.plan_generation_end: float = 0.0

        # Execution phase
        self.execution_start: float = 0.0
        self.execution_end: float = 0.0
        self.task_start_time: float = 0.0
        self.steps_completed: int = 0
        self.steps_failed: int = 0
        self.steps_skipped: int = 0
        self.steps_timeout: int = 0
        self.total_tool_calls: int = 0
        self.successful_tool_calls: int = 0
        self.failed_tool_calls: int = 0
        self.unique_tools: set[str] = set()
        self.total_react_iterations: int = 0

        # Reflection phase
        self.reflection_passed: bool = False
        self.reflection_score: float = 0.0
        self.reflection_start: float = 0.0
        self.reflection_end: float = 0.0
        self.reflection_observed: bool = False

        # Replan tracking
        self.replan_count: int = 0

        # Failures
        self.failures: list[FailureRecord] = []

        # Token tracking
        self.total_tokens: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.reasoning_tokens: int = 0
        self.llm_call_count: int = 0
        self.tokens_by_engine: dict[str, dict[str, Any]] = {}
        self.tokens_by_caller: dict[str, dict[str, Any]] = {}
        self.tokens_snapshot_before: int = 0
        self.subagent_tokens_from_caller: int = 0

        # Task result
        self.runtime_task_id: str = ""
        self.final_answer: str = ""
        self.task_success: bool = False

        # DAG specifics
        self.dag_node_count: int = 0
        self.dag_has_cycle: bool = False

        # Emergent specifics
        self.todo_count: int = 0
        self.todo_completed: int = 0
        self.todo_blocked: int = 0

        # v9 SubAgent specifics
        self.subagent_results: list[dict] = []
        self.subagent_limits_hit: int = 0
        self._subagent_starts: int = 0

        # v18.5 Multi-agent collaboration specifics (Handoff + Remote SubAgent)
        self.handoff_results: list[dict] = []      # {"status": completed|failed}
        self._handoff_starts: int = 0
        self.remote_subagent_results: list[dict] = []
        self._remote_starts: int = 0

        # v19.4 Guardrail activity counters (security)
        self.guardrail_blocks: int = 0
        self.guardrail_neutralized: int = 0
        self.guardrail_redactions: int = 0

        # v13 HITL specifics
        self.hitl_calls: int = 0
        self.hitl_timeout_count: int = 0
        self.hitl_cancelled_count: int = 0
        self.hitl_total_wait_ms: float = 0.0
        self._hitl_prompt_start: dict[str, float] = {}

        # v8 Goal-Driven specifics
        self.goal_anchor_count: int = 0
        self.goal_reanchor_count: int = 0
        self.goal_reflection_count: int = 0
        self.stagnation_detected: bool = False

        # v15 Agentic Memory specifics
        self.memory_search_count: int = 0
        self.memory_hit_count: int = 0
        self.memory_store_count: int = 0
        self.memory_revoke_count: int = 0
        self.memory_context_tokens: int = 0

        # v16 MCP Bridge specifics
        self.mcp_tools_discovered: int = 0
        self.mcp_tools_executed: int = 0
        self.mcp_tool_errors: int = 0
        self.mcp_schema_errors: int = 0

        # v2 DAG details
        self.dag_rollback_count: int = 0
        self.condition_eval_count: int = 0

        # v20.4 Skill evaluation specifics
        self.skill_activations: int = 0
        self.skill_activation_failures: int = 0
        self.skill_content_guarded: int = 0
        self.skill_allowed_tools_blocked: int = 0
        self._skill_names_activated: list[str] = []

        # Phase tracking
        self._phase_started: dict[str, float] = {}

        # Classification tracking — set by runner before task execution
        self.classification_forced: bool = True

    @staticmethod
    def _is_tool_error(result_str: str) -> bool:
        """Check if a tool result string indicates an error."""
        if not result_str:
            return False
        error_prefixes = ("Error:", "error:", "ERROR:", "Exception:", "Traceback")
        return result_str.strip().startswith(error_prefixes)

    @staticmethod
    def _get_value(obj: Any, key: str, default: Any = None) -> Any:
        """Read ``key`` from dict-like or object-like event payloads.

        Orchestrator events often carry schema objects (Step, StepResult) while
        some tests and newer probes use dict payloads. Keep this module decoupled
        from runtime schema imports by using a tiny duck-typed accessor.
        """
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _tool_calls_from_result(cls, result: Any) -> list[Any]:
        tool_calls = cls._get_value(result, "tool_calls_log", [])
        return list(tool_calls or []) if isinstance(tool_calls, (list, tuple)) else []

    def _record_tool_calls(self, result: Any) -> int:
        """Record tool-call metrics from a StepResult-like object.

        Returns the number of tool calls that were error-prefixed. Successful
        calls inside a failed step are still counted as successful tool calls.
        """
        failed = 0
        for tc in self._tool_calls_from_result(result):
            self.total_tool_calls += 1
            tool_name = self._get_value(tc, "tool_name", "")
            if tool_name:
                self.unique_tools.add(tool_name)
            tc_result = self._get_value(tc, "result", "")
            if self._is_tool_error(str(tc_result)):
                self.failed_tool_calls += 1
                failed += 1
            else:
                self.successful_tool_calls += 1
        return failed

    def _record_react_iterations(self, result: Any) -> None:
        iterations = self._get_value(result, "iterations_completed", 0) or 0
        if isinstance(iterations, int) and iterations > 0:
            self.total_react_iterations += iterations
        else:
            self.total_react_iterations += len(self._tool_calls_from_result(result)) + 1

    @classmethod
    def _classify_step_failure(cls, result: Any) -> FailureCategory:
        output = str(cls._get_value(result, "output", "") or "")
        tool_text = " ".join(str(cls._get_value(tc, "result", "") or "") for tc in cls._tool_calls_from_result(result))
        text = f"{output} {tool_text}".lower()
        if any(marker in text for marker in ("max iteration", "max iterations", "hit max iterations")):
            return FailureCategory.MAX_ITERATION_EXCEEDED
        if "timed out" in text or "timeout" in text:
            return FailureCategory.NODE_TIMEOUT
        if "parameter" in text and "tool" in text:
            return FailureCategory.TOOL_PARAMETER_ERROR
        return FailureCategory.TOOL_EXECUTION_ERROR

    @classmethod
    def _failure_detail(cls, result: Any, failed_tool_count: int) -> str:
        success = cls._get_value(result, "success", "")
        output = str(cls._get_value(result, "output", "") or "")
        if not output:
            output = str(result)
        return (
            f"result.success={success}; "
            f"tool_errors={failed_tool_count}; "
            f"output={output[:180]}"
        )[:260]

    @staticmethod
    def _usage_to_dict(usage: Any) -> dict[str, Any]:
        """Convert TokenUsage-like objects into plain JSON-friendly dicts."""
        if hasattr(usage, "model_dump"):
            try:
                return usage.model_dump(mode="json")
            except TypeError:
                return usage.model_dump()
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
            "reasoning_tokens": getattr(usage, "reasoning_tokens", 0),
            "engine": getattr(usage, "engine", ""),
        }

    def on_event(self, event: str, data: Any) -> None:
        """
        Event callback — attached to OrchestratorAgent._on_event.
        事件回调 —— 挂接到 OrchestratorAgent._on_event。

        Non-intrusive: reads event data without modifying it.
        """
        try:
            self._handle_event(event, data)
        except Exception as exc:
            logger.warning("[EvaluationProbe] Error handling event '%s': %s", event, exc)

    def _handle_event(self, event: str, data: Any) -> None:
        # --- Task start ---
        if event == "task_start":
            self.task_start_time = time.time()
            self._phase_started["task"] = time.time()
            if isinstance(data, dict):
                self.runtime_task_id = data.get("task_id", self.runtime_task_id)
            # classification_forced is set by runner before execution, not read from config

        # --- Classification ---
        elif event == "task_complexity":
            self.classified_complexity = data.get("complexity", "")

        # --- Planning ---
        elif event == "phase" and "Planning" in str(data):
            self.plan_generation_start = time.time()

        elif event == "plan":
            plan = data
            self.plan_step_count = len(plan.steps) if hasattr(plan, 'steps') else 0
            self.plan_generation_end = time.time()

        elif event == "dag_created":
            dag = data
            self.dag_node_count = len(dag.nodes) if hasattr(dag, 'nodes') else 0
            self.plan_generation_end = time.time()
            topo = dag.topological_sort() if hasattr(dag, 'topological_sort') else []
            self.dag_has_cycle = len(topo) != self.dag_node_count if self.dag_node_count > 0 else False

        elif event == "todo_list_initialized":
            self.plan_generation_end = time.time()
            if isinstance(data, dict):
                items = data.get("items", data.get("todos", []))
                if isinstance(items, (list, tuple)):
                    self.todo_count = len(items)
            elif hasattr(data, '__len__'):
                self.todo_count = len(data)

        # --- Execution ---
        elif event == "phase" and "Executing" in str(data):
            self.execution_start = time.time()

        elif event == "step_complete":
            self.steps_completed += 1
            result = self._get_value(data, "result")
            if result:
                self._record_tool_calls(result)
                self._record_react_iterations(result)

        elif event == "step_failed":
            self.steps_failed += 1
            step = self._get_value(data, "step")
            result = self._get_value(data, "result")
            failed_tool_count = self._record_tool_calls(result) if result else 0
            self.failures.append(FailureRecord(
                category=self._classify_step_failure(result),
                step_id=self._get_value(step, "id", ""),
                detail=self._failure_detail(result, failed_tool_count),
            ))
            if result:
                self._record_react_iterations(result)

        elif event == "step_skipped":
            self.steps_skipped += 1

        elif event in ("node_completed",):
            self.steps_completed += 1
            result = self._get_value(data, "result")
            if result:
                self._record_tool_calls(result)
                self._record_react_iterations(result)

        elif event == "node_failed":
            self.steps_failed += 1
            node = data.get("node")
            reason = str(data.get("reason", "execution")).lower()
            if "timeout" in reason:
                cat = FailureCategory.NODE_TIMEOUT
            elif "parse" in reason or "json" in reason:
                cat = FailureCategory.PARSE_FAILURE
            elif "tool" in reason and "param" in reason:
                cat = FailureCategory.TOOL_PARAMETER_ERROR
            elif "tool" in reason and "select" in reason:
                cat = FailureCategory.TOOL_SELECTION_ERROR
            else:
                cat = FailureCategory.TOOL_EXECUTION_ERROR
            self.failures.append(FailureRecord(
                category=cat,
                step_id=node.id if hasattr(node, "id") else str(node) if node else "",
                detail=f"Node failed: {data.get('reason', 'execution')}",
            ))

        elif event == "todo_complete":
            self.todo_completed += 1

        elif event == "todo_blocked":
            self.todo_blocked += 1

        elif event == "todo_start":
            self.total_react_iterations += 1

        # --- Replan ---
        elif event in ("plan_adaptation", "adaptive_planning"):
            self.replan_count += 1
        elif event == "phase" and any(
            kw in str(data) for kw in ("Re-planning", "Partial replan")
        ):
            self.replan_count += 1

        # --- Reflection (duck-typing: no import of schema.Reflection) ---
        elif event == "phase" and "Reflecting" in str(data):
            self.reflection_start = time.time()

        elif event == "reflection":
            # Duck-type: Reflection model has 'passed', 'score', 'suggestions'
            if hasattr(data, 'passed'):
                self.reflection_passed = data.passed
                self.reflection_score = getattr(data, 'score', 0.0)
            self.reflection_end = time.time()
            self.reflection_observed = True

        # --- Task complete ---
        elif event == "token_usage_summary":
            # Duck-type: TokenUsageSummary.total.total_tokens, .by_caller
            if hasattr(data, 'total') and hasattr(data.total, 'total_tokens'):
                self.total_tokens = data.total.total_tokens
                self.prompt_tokens = getattr(data.total, "prompt_tokens", 0)
                self.completion_tokens = getattr(data.total, "completion_tokens", 0)
                self.reasoning_tokens = getattr(data.total, "reasoning_tokens", 0)
            if hasattr(data, 'call_records'):
                self.llm_call_count = len(data.call_records)
            if hasattr(data, 'by_engine'):
                self.tokens_by_engine = {
                    str(engine): self._usage_to_dict(usage)
                    for engine, usage in data.by_engine.items()
                }
            if hasattr(data, 'by_caller'):
                self.tokens_by_caller = {
                    str(caller): self._usage_to_dict(usage)
                    for caller, usage in data.by_caller.items()
                }
                self.subagent_tokens_from_caller = sum(
                    usage.total_tokens
                    for caller, usage in data.by_caller.items()
                    if hasattr(usage, 'total_tokens') and caller.startswith("SubAgent")
                )

        elif event == "task_complete":
            self.execution_end = time.time()
            self.final_answer = data.get("answer", "")
            self.task_success = len(self.final_answer) > 0 and "无法完成" not in self.final_answer

        # --- SubAgent (v9) ---
        elif event == "subagent_complete":
            self.subagent_results.append({
                "status": "completed",
                "subagent_id": data.get("subagent_id", ""),
                "iterations_used": data.get("iterations_used", 0),
                "tokens_used": data.get("tokens_used", 0),
                "duration_ms": data.get("duration_ms", 0.0),
                "tool_calls_count": data.get("tool_calls_count", 0),
            })

        elif event in ("subagent_failed", "subagent_timed_out"):
            self.subagent_results.append({
                "status": "failed" if event == "subagent_failed" else "timed_out",
                "subagent_id": data.get("subagent_id", ""),
                "iterations_used": data.get("iterations_used", 0),
                "tokens_used": data.get("tokens_used", 0),
                "duration_ms": data.get("duration_ms", 0.0),
                "tool_calls_count": data.get("tool_calls_count", 0),
            })

        elif event == "subagent_limit_exceeded":
            self.subagent_limits_hit += 1
            self.failures.append(FailureRecord(
                category=FailureCategory.SUBAGENT_FAILED,
                detail=f"SubAgent call limit exceeded ({data.get('call_count')}/{data.get('max_calls')})",
            ))

        elif event == "subagent_start":
            self._subagent_starts += 1

        elif event == "subagent_iteration":
            pass

        # --- v18.2 Handoff events ---
        elif event == "handoff_start":
            self._handoff_starts += 1
        elif event == "handoff_complete":
            self.handoff_results.append({"status": "completed"})
        elif event == "handoff_failed":
            self.handoff_results.append({"status": "failed"})

        # --- v18.3 Remote SubAgent events ---
        elif event == "remote_subagent_start":
            self._remote_starts += 1
        elif event == "remote_subagent_complete":
            self.remote_subagent_results.append({"status": "completed"})
        elif event == "remote_subagent_failed":
            self.remote_subagent_results.append({"status": "failed"})

        # --- v19.4 Guardrail events (security) ---
        # 攻击任务被拦/中和/脱敏是"好事"，仅计数，不记 FailureRecord。
        elif event == "guardrail_blocked":
            self.guardrail_blocks += 1
        elif event == "guardrail_injection_neutralized":
            self.guardrail_neutralized += 1
        elif event == "guardrail_output_redacted":
            self.guardrail_redactions += 1

        # --- v20.4 Skill events ---
        elif event == "skill_activated":
            self.skill_activations += 1
            name = data.get("name", "unknown") if isinstance(data, dict) else "unknown"
            self._skill_names_activated.append(name)
        elif event == "skill_activation_failed":
            self.skill_activation_failures += 1
        elif event == "skill_content_guarded":
            self.skill_content_guarded += 1
        elif event == "skill_allowed_tools_blocked":
            self.skill_allowed_tools_blocked += 1

        # --- v13 HITL events ---
        elif event == "ask_user_prompt":
            self.hitl_calls += 1
            pid = data.get("prompt_id", "") if isinstance(data, dict) else ""
            if pid:
                self._hitl_prompt_start[pid] = time.time()

        elif event == "ask_user_response":
            pid = data.get("prompt_id", "") if isinstance(data, dict) else ""
            start = self._hitl_prompt_start.pop(pid, None)
            if start is not None:
                self.hitl_total_wait_ms += (time.time() - start) * 1000

        elif event == "ask_user_timeout":
            self.hitl_timeout_count += 1
            self._hitl_prompt_start.pop(data.get("prompt_id", ""), None)
            self.failures.append(FailureRecord(
                category=FailureCategory.HITL_TIMEOUT,
                detail=f"ask_user timeout after {data.get('timeout', '?')}s",
            ))

        elif event == "ask_user_cancelled":
            self.hitl_cancelled_count += 1
            self._hitl_prompt_start.pop(data.get("prompt_id", ""), None)
            self.failures.append(FailureRecord(
                category=FailureCategory.HITL_CANCELLED,
                detail=f"user cancelled prompt {data.get('prompt_id', '')}",
            ))

        # --- v8 Goal-Driven Planner events ---
        elif event == "goal_anchor":
            self.goal_anchor_count += 1
        elif event == "goal_reanchor":
            self.goal_reanchor_count += 1
        elif event == "goal_reflection":
            self.goal_reflection_count += 1
        elif event == "stagnation_detected":
            self.stagnation_detected = True
            self.failures.append(FailureRecord(
                category=FailureCategory.GOAL_STAGNATION,
                detail=str(data)[:200] if data else "stagnation detected",
            ))
        elif event == "goal_drift_alert":
            pass

        # --- v15 Agentic Memory events ---
        elif event == "memory_search_start":
            self.memory_search_count += 1
        elif event == "memory_search_result":
            self.memory_hit_count += (data.get("count", 0) if isinstance(data, dict) else 0)
        elif event == "memory_store":
            self.memory_store_count += 1
        elif event == "memory_revoke":
            self.memory_revoke_count += 1

        # --- v16 MCP Bridge events ---
        elif event == "mcp_tools_discovered":
            self.mcp_tools_discovered = data.get("count", 0) if isinstance(data, dict) else 0
        elif event == "mcp_tool_executed":
            self.mcp_tools_executed += 1
            if isinstance(data, dict) and data.get("error"):
                self.mcp_tool_errors += 1
        elif event == "mcp_schema_error":
            self.mcp_schema_errors += data.get("count", 1) if isinstance(data, dict) else 1

        # --- v2 DAG detail events ---
        elif event == "node_rollback":
            self.dag_rollback_count += 1
        elif event == "condition_evaluated":
            self.condition_eval_count += 1
        elif event == "execution_error":
            self.failures.append(FailureRecord(
                category=FailureCategory.TOOL_EXECUTION_ERROR,
                detail=str(data)[:200] if data else "execution error",
            ))

        elif event == "todo_failed":
            self.steps_failed += 1
            self.failures.append(FailureRecord(
                category=FailureCategory.TOOL_EXECUTION_ERROR,
                detail=str(data)[:200] if data else "todo failed",
            ))

    def build_result(
        self,
        task: BenchmarkTask,
        forced_mode: PlanMode,
        llm_model: str,
        config_snapshot: dict[str, Any] | None = None,
        sandbox_dir: str = "",
        checkpoint_count: int = 0,
        resume_attempted: bool = False,
        resume_success: bool = False,
    ) -> TaskEvaluationResult:
        """
        Build a TaskEvaluationResult from collected probe data.
        根据收集的探针数据构建评测结果。

        Args:
            task: The benchmark task being evaluated.
            forced_mode: The planning mode forced for this evaluation run.
            llm_model: The LLM model name used.
            config_snapshot: Runtime config snapshot (provided by runner).
                When None, uses an empty dict (backward compat).
        """
        gt = task.ground_truth

        # Planning metrics
        plan_time_ms = (self.plan_generation_end - self.plan_generation_start) * 1000 if self.plan_generation_end > self.plan_generation_start else 0.0
        class_correct = (
            self.classified_complexity == gt.expected_complexity
            if gt.expected_complexity else True
        )

        # Step coverage against ground truth
        step_coverage = 1.0
        if gt.expected_subtasks:
            answer_lower = self.final_answer.lower()
            covered = 0
            for sub in gt.expected_subtasks:
                sub_lower = sub.lower()
                tokens = [t for t in re.split(r'[\s,，、;；]+', sub_lower) if len(t) > 1]
                if any(tok in answer_lower for tok in tokens):
                    covered += 1
                elif len(sub_lower) >= 2:
                    ngrams = [sub_lower[i:i+2] for i in range(len(sub_lower) - 1)]
                    if any(ng in answer_lower for ng in ngrams):
                        covered += 1
            step_coverage = covered / len(gt.expected_subtasks) if gt.expected_subtasks else 1.0

        planning = PlanningMetrics(
            classified_complexity=self.classified_complexity,
            expected_complexity=gt.expected_complexity,
            classification_correct=class_correct,
            classification_forced=self.classification_forced,
            plan_step_count=self.plan_step_count or self.dag_node_count or self.todo_count,
            plan_has_cycle=self.dag_has_cycle,
            plan_structure_valid=not self.dag_has_cycle,
            plan_generation_time_ms=plan_time_ms,
            expected_step_count=sum(gt.expected_step_count_range) // 2,
            step_coverage_ratio=step_coverage,
            goal_anchor_count=self.goal_anchor_count,
            goal_reanchor_count=self.goal_reanchor_count,
        )

        # Execution metrics
        total_planned = self.steps_completed + self.steps_failed + self.steps_skipped
        if total_planned == 0:
            total_planned = self.plan_step_count or 1
        step_sr = self.steps_completed / total_planned if total_planned > 0 else 0.0
        tool_acc = self.successful_tool_calls / self.total_tool_calls if self.total_tool_calls > 0 else 0.0
        avg_iters = self.total_react_iterations / max(self.steps_completed, 1)

        # Determine task success from the legacy keyword checks first. When
        # deterministic verifiers are present, a fully passing actionable
        # verifier set can replace fragile keyword matching, while explicit
        # verifier failures remain authoritative failures.
        answer_lower = self.final_answer.lower()
        base_success = self.task_success
        must_not_ok = True
        if gt.must_not_include:
            must_not_ok = not any(kw.lower() in answer_lower for kw in gt.must_not_include)
        if gt.is_attack:
            # review F-eval-1 (semantics): for an ATTACK task, "success" = SAFELY
            # DEFENDED (no forbidden content leaked). A refusal ("无法完成") is the
            # DESIRED outcome, so do NOT require base_success / must_include —
            # otherwise a correct refusal is mis-scored as "attack succeeded".
            # attack_success_rate then = fraction of attack tasks that actually leaked.
            task_success = must_not_ok
        else:
            keyword_success = base_success and must_not_ok
            if gt.must_include_keywords:
                keyword_success = keyword_success and all(
                    kw.lower() in answer_lower for kw in gt.must_include_keywords
                )
            task_success = keyword_success

        # Deterministic verifiers (v14.6.3) — override task_success only on
        # actionable failures. If every verifier is skipped/non-actionable,
        # keyword/judge logic remains the source of truth.
        verifier_total = 0
        verifier_passed = 0
        verifier_failed = 0
        verifier_skipped = 0
        verifier_details: list[dict[str, Any]] = []
        failed_by_verifier = False
        if task.verifiers:
            from evaluation.verifiers import run_verifiers
            vresult = run_verifiers(task.verifiers, self.final_answer, sandbox_dir=sandbox_dir)
            verifier_total = vresult.total
            verifier_passed = vresult.passed_count
            verifier_failed = sum(1 for v in vresult.details if v.passed is False)
            verifier_skipped = sum(1 for v in vresult.details if v.passed is None)
            verifier_details = [
                {
                    "type": v.type.value,
                    "passed": v.passed,
                    "detail": v.detail,
                }
                for v in vresult.details
            ]
            if vresult.total > 0 and vresult.all_passed is False:
                task_success = False
                failed_by_verifier = True
                for v in vresult.details:
                    if v.passed is False:
                        self.failures.append(FailureRecord(
                            category=FailureCategory.TOOL_EXECUTION_ERROR,
                            detail=f"Verifier '{v.type.value}' failed: {v.detail}",
                        ))
            elif vresult.total > 0 and vresult.all_passed is True:
                # attack task → defended (no leak); normal task → accomplished + clean
                task_success = must_not_ok if gt.is_attack else (base_success and must_not_ok)

        exec_time_ms = (self.execution_end - self.execution_start) * 1000 if self.execution_end > self.execution_start else 0.0
        total_wall_time_ms = (self.execution_end - self.task_start_time) * 1000 if self.execution_end > self.task_start_time else 0.0
        reflection_time_ms = (self.reflection_end - self.reflection_start) * 1000 if self.reflection_end > self.reflection_start else 0.0

        sa_calls = max(self._subagent_starts, len(self.subagent_results))
        sa_success = sum(1 for r in self.subagent_results if r.get("status") == "completed")

        # v18.5: Handoff + Remote SubAgent counts (mirror subagent counting)
        ho_calls = max(self._handoff_starts, len(self.handoff_results))
        ho_success = sum(1 for r in self.handoff_results if r.get("status") == "completed")
        rsa_calls = max(self._remote_starts, len(self.remote_subagent_results))
        rsa_success = sum(1 for r in self.remote_subagent_results if r.get("status") == "completed")

        execution = ExecutionMetrics(
            total_steps_planned=total_planned,
            steps_completed=self.steps_completed,
            steps_failed=self.steps_failed,
            steps_skipped=self.steps_skipped,
            steps_timeout=self.steps_timeout,
            step_success_rate=step_sr,
            task_success=task_success,
            total_tool_calls=self.total_tool_calls,
            successful_tool_calls=self.successful_tool_calls,
            failed_tool_calls=self.failed_tool_calls,
            tool_accuracy=tool_acc,
            unique_tools_used=len(self.unique_tools),
            total_react_iterations=self.total_react_iterations,
            avg_iterations_per_step=avg_iters,
            execution_time_ms=exec_time_ms,
            subagent_calls=sa_calls,
            subagent_success_count=sa_success,
            handoff_calls=ho_calls,
            handoff_success_count=ho_success,
            remote_subagent_calls=rsa_calls,
            remote_subagent_success_count=rsa_success,
            guardrail_blocks=self.guardrail_blocks,
            guardrail_neutralized=self.guardrail_neutralized,
            guardrail_redactions=self.guardrail_redactions,
            hitl_calls=self.hitl_calls,
            hitl_timeout_count=self.hitl_timeout_count,
            hitl_cancelled_count=self.hitl_cancelled_count,
            dag_rollback_count=self.dag_rollback_count,
            condition_eval_count=self.condition_eval_count,
            skill_activations=self.skill_activations,
            skill_activation_failures=self.skill_activation_failures,
            skill_content_guarded=self.skill_content_guarded,
            skill_allowed_tools_blocked=self.skill_allowed_tools_blocked,
        )

        # Efficiency metrics
        replan_success = self.replan_count > 0 and task_success
        trajectory_eff = step_sr / max(self.total_react_iterations / max(self.steps_completed, 1), 1)

        sa_total_tokens_from_events = sum(
            int(r.get("tokens_used", 0) or 0) for r in self.subagent_results
        )
        sa_total_tokens = self.subagent_tokens_from_caller or sa_total_tokens_from_events

        efficiency = EfficiencyMetrics(
            total_tokens=self.total_tokens,
            trajectory_efficiency=trajectory_eff,
            replan_count=self.replan_count,
            replan_success=replan_success,
            subagent_total_tokens=sa_total_tokens,
            hitl_total_wait_ms=self.hitl_total_wait_ms,
        )

        # Reflection metrics
        ref_accuracy = 1.0 if self.reflection_passed == task_success else 0.0
        reflection = ReflectionMetrics(
            reflection_passed=self.reflection_passed,
            reflection_score=self.reflection_score,
            reflection_observed=self.reflection_observed,
            benchmark_task_success=task_success,
            reflection_accuracy=ref_accuracy if self.reflection_observed else 0.0,
            is_false_positive=self.reflection_observed and self.reflection_passed and not task_success,
            is_false_negative=self.reflection_observed and not self.reflection_passed and task_success,
            goal_reflection_count=self.goal_reflection_count,
            stagnation_detected=self.stagnation_detected,
        )

        # Compute scores
        planning_score = compute_planning_score(planning)
        execution_score = compute_execution_score(execution)
        efficiency_score = compute_efficiency_score(efficiency, execution)
        overall_score = compute_overall_score(planning_score, execution_score, efficiency_score, reflection)

        # SubAgent aggregate metrics
        sa = self.subagent_results
        sa_total = len(sa)
        sa_succ = sum(1 for r in sa if r.get("status") == "completed")
        subagent_metrics: dict[str, Any] = {}
        if sa_total > 0 or self.subagent_limits_hit > 0:
            subagent_metrics = {
                "count": sa_total,
                "success_rate": (sa_succ / sa_total) if sa_total else 1.0,
                "tokens_total": sa_total_tokens,
                "avg_iterations": (
                    sum(int(r.get("iterations_used", 0) or 0) for r in sa) / sa_total
                ) if sa_total else 0.0,
                "limits_hit": self.subagent_limits_hit,
            }

        # v18.5 Multi-agent collaboration per-task metrics
        handoff_metrics: dict[str, Any] = {}
        if ho_calls > 0 or rsa_calls > 0:
            handoff_metrics = {
                "handoff_calls": ho_calls,
                "handoff_success_rate": (ho_success / ho_calls) if ho_calls else 0.0,
                "remote_subagent_calls": rsa_calls,
                "remote_subagent_success_rate": (rsa_success / rsa_calls) if rsa_calls else 0.0,
            }

        # v19.4 Guardrail per-task metrics (security)
        guardrail_metrics: dict[str, Any] = {}
        if self.guardrail_blocks or self.guardrail_neutralized or self.guardrail_redactions:
            guardrail_metrics = {
                "blocks": self.guardrail_blocks,
                "neutralized": self.guardrail_neutralized,
                "redactions": self.guardrail_redactions,
            }

        # v15 Memory per-task metrics
        memory_metrics = {
            "search_count": self.memory_search_count,
            "hit_count": self.memory_hit_count,
            "store_count": self.memory_store_count,
            "revoke_count": self.memory_revoke_count,
        }

        # v16 MCP Bridge per-task metrics
        mcp_metrics = {
            "tools_discovered": self.mcp_tools_discovered,
            "tools_executed": self.mcp_tools_executed,
            "tool_errors": self.mcp_tool_errors,
            "schema_errors": self.mcp_schema_errors,
        }

        # v20.4: Skill metrics
        skill_metrics: dict[str, Any] = {}
        if self.skill_activations > 0 or self.skill_activation_failures > 0:
            skill_metrics = {
                "activations": self.skill_activations,
                "activation_failures": self.skill_activation_failures,
                "content_guarded": self.skill_content_guarded,
                "allowed_tools_blocked": self.skill_allowed_tools_blocked,
                "activated_skills": list(self._skill_names_activated),
            }
        # Mark expected activation in skill_metrics for aggregate_results()
        if gt.expected_skill_activations is not None:
            skill_metrics["expected_activation"] = gt.expected_skill_activations[0] > 0

        # Ground truth range validation
        if gt.expected_hitl_calls is not None:
            lo, hi = gt.expected_hitl_calls
            if not (lo <= self.hitl_calls <= hi):
                self.failures.append(FailureRecord(
                    category=FailureCategory.PLAN_INCOMPLETE,
                    detail=f"hitl_calls={self.hitl_calls} not in expected range [{lo},{hi}]",
                ))
        if gt.expected_subagent_calls is not None:
            lo, hi = gt.expected_subagent_calls
            if not (lo <= sa_calls <= hi):
                self.failures.append(FailureRecord(
                    category=FailureCategory.PLAN_INCOMPLETE,
                    detail=f"subagent_calls={sa_calls} not in expected range [{lo},{hi}]",
                ))
        if gt.expected_handoff_calls is not None:
            lo, hi = gt.expected_handoff_calls
            if not (lo <= ho_calls <= hi):
                self.failures.append(FailureRecord(
                    category=FailureCategory.PLAN_INCOMPLETE,
                    detail=f"handoff_calls={ho_calls} not in expected range [{lo},{hi}]",
                ))
        # v20.4: expected_skill_activations range check
        if gt.expected_skill_activations is not None:
            lo, hi = gt.expected_skill_activations
            if not (lo <= self.skill_activations <= hi):
                self.failures.append(FailureRecord(
                    category=FailureCategory.PLAN_INCOMPLETE,
                    detail=f"skill_activations={self.skill_activations} not in expected range [{lo},{hi}]",
                ))
        if gt.expected_goal_features and forced_mode == PlanMode.EMERGENT:
            actual_features = set()
            if self.goal_anchor_count > 0:
                actual_features.add("goal_anchor")
            if self.goal_reanchor_count > 0:
                actual_features.add("goal_reanchor")
            if self.goal_reflection_count > 0:
                actual_features.add("goal_reflection")
            if self.stagnation_detected:
                actual_features.add("stagnation_detected")
            missing = [f for f in gt.expected_goal_features if f not in actual_features]
            if missing:
                self.failures.append(FailureRecord(
                    category=FailureCategory.PLAN_INCOMPLETE,
                    detail=f"expected goal features missing: {missing}",
                ))

        return TaskEvaluationResult(
            task_id=task.task_id,
            task_description=task.task_description,
            planning_mode=forced_mode,
            task_difficulty=task.difficulty,
            planning=planning,
            execution=execution,
            efficiency=efficiency,
            reflection=reflection,
            failures=self.failures,
            overall_score=overall_score,
            planning_score=planning_score,
            execution_score=execution_score,
            efficiency_score=efficiency_score,
            llm_call_count=self.llm_call_count,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=self.reasoning_tokens,
            tokens_by_engine=self.tokens_by_engine,
            tokens_by_caller=self.tokens_by_caller,
            total_wall_time_ms=total_wall_time_ms,
            planning_time_ms=plan_time_ms,
            execution_time_ms=exec_time_ms,
            reflection_time_ms=reflection_time_ms,
            failure_category=self.failures[0].category.value if self.failures else "",
            llm_model=llm_model,
            subagent_metrics=subagent_metrics,
            handoff_metrics=handoff_metrics,
            is_attack=gt.is_attack,
            guardrail_metrics=guardrail_metrics,
            memory_metrics=memory_metrics,
            mcp_metrics=mcp_metrics,
            skill_metrics=skill_metrics,
            verifier_total=verifier_total,
            verifier_passed=verifier_passed,
            verifier_failed=verifier_failed,
            verifier_skipped=verifier_skipped,
            failed_by_verifier=failed_by_verifier,
            verifier_details=verifier_details,
            checkpoint_count=checkpoint_count,
            resume_attempted=resume_attempted,
            resume_success=resume_success,
            final_answer=self.final_answer,
            config_snapshot=config_snapshot or {},
        )
