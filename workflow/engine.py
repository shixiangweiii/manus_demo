"""Deterministic tool-workflow implementation with no per-step LLM call."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from tools.base import BaseTool
from react.tool_call_helpers import classify_result
from workflow.models import WorkflowResult, WorkflowSpec, WorkflowStep

logger = logging.getLogger(__name__)

# ${step_id} 模板占位符（step_id 为字母数字下划线连字符）
_TEMPLATE_RE = re.compile(r"\$\{([A-Za-z0-9_\-]+)\}")


class WorkflowEngine:
    """
    Executes a WorkflowSpec deterministically over a fixed tool set.
    在固定工具集上确定性执行 WorkflowSpec。
    """

    def __init__(
        self,
        tools: dict[str, BaseTool] | list[BaseTool],
        on_event: Callable[[str, Any], None] | None = None,
        guardrail: Any | None = None,
    ):
        if isinstance(tools, dict):
            self.tools = tools
        else:
            self.tools = {t.name: t for t in tools}
        self._on_event = on_event or (lambda *_: None)
        self._guardrail = guardrail

    def _emit(self, event: str, data: Any = None) -> None:
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("[WorkflowEngine] event callback failed for '%s'", event, exc_info=True)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def execute(self, spec: WorkflowSpec) -> WorkflowResult:
        """Execute the workflow deterministically and return a WorkflowResult."""
        result = WorkflowResult()
        self._emit("workflow_start", {"name": spec.name, "steps": len(spec.steps)})

        # Validate + topological order (missing deps / cycles → fail)
        try:
            order = self._topo_order(spec)
        except ValueError as exc:
            result.error = str(exc)
            self._emit("workflow_failed", {"name": spec.name, "error": str(exc)})
            return result

        guardrail = self._guardrail
        _GAction = None
        if guardrail is not None:
            try:
                from guardrails.models import GuardrailAction as _GAction
            except Exception as exc:
                raise RuntimeError("Guardrail is configured but unavailable") from exc

        step_results: dict[str, str] = {}
        last_output = ""
        for step in order:
            self._emit("workflow_step_start", {"id": step.id, "tool": step.tool})
            call_id = f"workflow:{step.id}"
            self._emit(
                "tool_started",
                {
                    "tool": step.tool,
                    "parameters": BaseTool._sanitize_params(step.params),
                    "action_id": step.id,
                    "call_id": call_id,
                },
            )

            tool = self.tools.get(step.tool)
            if tool is None:
                msg = f"Error: unknown tool '{step.tool}' in step '{step.id}'"
                logger.warning("[WorkflowEngine] %s", msg)
                step_results[step.id] = msg
                result.step_parameters[step.id] = BaseTool._sanitize_params(step.params)
                result.failed_step = step.id
                result.error = msg
                result.step_results = step_results
                self._emit("tool_completed", {
                    "tool": step.tool, "success": False, "result": msg,
                    "action_id": step.id, "call_id": call_id,
                })
                self._emit("workflow_step_failed", {"id": step.id, "error": msg})
                self._emit("workflow_failed", {"name": spec.name, "error": msg})
                return result

            try:
                resolved = self._resolve_params(step.params, step_results)
                result.step_parameters[step.id] = BaseTool._sanitize_params(resolved)
                # Tool-input guardrail: block dangerous parameters and gated writes
                # gating BEFORE execution (CONFIRM resolved internally → ALLOW/BLOCK).
                if guardrail is not None:
                    try:
                        decision = await guardrail.check_tool_input(step.tool, resolved)
                    except Exception as exc:
                        raise RuntimeError(f"guardrail input check failed: {exc}") from exc
                    if decision is not None and decision.action == _GAction.BLOCK:
                        msg = f"Error: [GUARDRAIL BLOCKED] {decision.reason}"
                        logger.warning("[WorkflowEngine] step '%s' blocked: %s", step.id, decision.reason)
                        result.failed_step = step.id
                        result.error = msg
                        step_results[step.id] = msg
                        result.step_results = step_results
                        self._emit("tool_completed", {
                            "tool": step.tool, "success": False, "result": msg,
                            "action_id": step.id, "call_id": call_id,
                        })
                        self._emit("workflow_step_failed", {"id": step.id, "error": msg})
                        self._emit("workflow_failed", {"name": spec.name, "error": msg})
                        return result
                output = await tool.traced_execute(**resolved)
            except Exception as exc:  # tool raised
                msg = f"Error: tool '{step.tool}' raised: {exc}"
                logger.error("[WorkflowEngine] step '%s' %s", step.id, msg, exc_info=True)
                result.failed_step = step.id
                result.error = msg
                step_results[step.id] = msg
                result.step_results = step_results
                self._emit("tool_completed", {
                    "tool": step.tool, "success": False, "result": msg,
                    "action_id": step.id, "call_id": call_id,
                })
                self._emit("workflow_step_failed", {"id": step.id, "error": msg})
                self._emit("workflow_failed", {"name": spec.name, "error": msg})
                return result

            step_results[step.id] = output
            # fail-fast on Error:-prefixed tool result (BaseTool error convention)
            is_error, _ = classify_result(output)
            if is_error:
                logger.warning("[WorkflowEngine] step '%s' returned error: %s", step.id, output[:200])
                result.failed_step = step.id
                result.error = output
                result.step_results = step_results
                self._emit("tool_completed", {
                    "tool": step.tool, "success": False, "result": output[:1000],
                    "action_id": step.id, "call_id": call_id,
                })
                self._emit("workflow_step_failed", {"id": step.id, "error": output[:300]})
                self._emit("workflow_failed", {"name": spec.name, "error": output[:300]})
                return result

            # Tool-output guardrail: neutralize injection in untrusted output
            # BEFORE it flows into downstream ${step_id} templating / final output.
            if guardrail is not None:
                try:
                    scan = guardrail.scan_tool_output(step.tool, output)
                    if scan.transformed_text is not None:
                        output = scan.transformed_text
                        step_results[step.id] = output
                except Exception as exc:
                    msg = f"Error: guardrail output check failed: {exc}"
                    result.failed_step = step.id
                    result.error = msg
                    step_results[step.id] = msg
                    result.step_results = step_results
                    self._emit("tool_completed", {
                        "tool": step.tool, "success": False, "result": msg,
                        "action_id": step.id, "call_id": call_id,
                    })
                    self._emit("workflow_step_failed", {"id": step.id, "error": msg})
                    self._emit("workflow_failed", {"name": spec.name, "error": msg})
                    return result

            last_output = output
            self._emit("tool_completed", {
                "tool": step.tool, "success": True, "result": str(output)[:1000],
                "action_id": step.id, "call_id": call_id,
            })
            self._emit("workflow_step_complete", {"id": step.id, "output_preview": str(output)[:200]})

        # The specification was validated before execution, so an explicit
        # final step is guaranteed to exist.
        if spec.final_step:
            final_output = step_results[spec.final_step]
        else:
            final_output = last_output

        result.success = True
        result.step_results = step_results
        result.final_output = final_output
        self._emit("workflow_complete", {"name": spec.name, "steps": len(step_results)})
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_params(self, params: dict[str, Any], step_results: dict[str, str]) -> dict[str, Any]:
        """Recursively resolve templates and reject unavailable step results."""

        def resolve(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if not isinstance(value, str):
                return value

            missing = [
                match.group(1)
                for match in _TEMPLATE_RE.finditer(value)
                if match.group(1) not in step_results
            ]
            if missing:
                names = ", ".join(sorted(set(missing)))
                raise ValueError(f"unresolved workflow result(s): {names}")
            return _TEMPLATE_RE.sub(
                lambda match: step_results[match.group(1)],
                value,
            )

        return resolve(params)

    @staticmethod
    def _topo_order(spec: WorkflowSpec) -> list[WorkflowStep]:
        """Kahn topological sort over depends_on; raises ValueError on missing dep / cycle.
        基于 depends_on 的 Kahn 拓扑排序；缺失依赖或存在环时抛 ValueError。"""
        if not spec.steps:
            raise ValueError("Workflow must contain at least one step")

        by_id: dict[str, WorkflowStep] = {}
        for s in spec.steps:
            if not s.id.strip():
                raise ValueError("Workflow step id must not be empty")
            if s.id in by_id:
                raise ValueError(f"Duplicate step id '{s.id}'")
            by_id[s.id] = s

        if spec.final_step and spec.final_step not in by_id:
            raise ValueError(f"Workflow final_step '{spec.final_step}' does not exist")

        # validate deps exist
        indegree: dict[str, int] = {s.id: 0 for s in spec.steps}
        adjacency: dict[str, list[str]] = {s.id: [] for s in spec.steps}
        for s in spec.steps:
            for dep in s.depends_on:
                if dep not in by_id:
                    raise ValueError(f"Step '{s.id}' depends on unknown step '{dep}'")
                adjacency[dep].append(s.id)
                indegree[s.id] += 1

        def template_refs(value: Any) -> set[str]:
            if isinstance(value, dict):
                return set().union(*(template_refs(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(template_refs(item) for item in value))
            if isinstance(value, str):
                return {match.group(1) for match in _TEMPLATE_RE.finditer(value)}
            return set()

        def depends_transitively(step_id: str, target: str, seen: set[str]) -> bool:
            if step_id in seen:
                return False
            seen.add(step_id)
            direct = by_id[step_id].depends_on
            return target in direct or any(
                depends_transitively(dep, target, seen) for dep in direct
            )

        for step in spec.steps:
            for ref in template_refs(step.params):
                if ref not in by_id:
                    raise ValueError(
                        f"Step '{step.id}' references unknown result '{ref}'"
                    )
                if not depends_transitively(step.id, ref, set()):
                    raise ValueError(
                        f"Step '{step.id}' must depend on referenced result '{ref}'"
                    )

        # Kahn — preserve declaration order among ready nodes for determinism
        ready = [s.id for s in spec.steps if indegree[s.id] == 0]
        order: list[WorkflowStep] = []
        while ready:
            nid = ready.pop(0)
            order.append(by_id[nid])
            for nxt in adjacency[nid]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)

        if len(order) != len(spec.steps):
            raise ValueError("Workflow has a dependency cycle")
        return order
