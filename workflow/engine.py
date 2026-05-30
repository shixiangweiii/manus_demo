"""
WorkflowEngine (v18.1) - Deterministic tool-workflow executor (no per-step LLM).
工作流引擎（v18.1）—— 确定性工具工作流执行器（每步无 LLM 推理）。

This is the deterministic engine of the explicit dual-engine architecture:
  - OrchestratorAgent.run(task)          → autonomous agentic loop (LLM-driven)
  - WorkflowEngine.execute(spec)         → deterministic tool DAG (this module)

确定性双引擎中的"确定性"一侧：拓扑序执行工具步骤，支持 ${step_id} 参数模板，
工具失败即 fail-fast，全程不调用 LLM。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from tools.base import BaseTool
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
    ):
        if isinstance(tools, dict):
            self.tools = tools
        else:
            self.tools = {t.name: t for t in tools}
        self._on_event = on_event or (lambda *_: None)

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

        step_results: dict[str, str] = {}
        last_output = ""
        for step in order:
            self._emit("workflow_step_start", {"id": step.id, "tool": step.tool})

            tool = self.tools.get(step.tool)
            if tool is None:
                msg = f"Unknown tool '{step.tool}' in step '{step.id}'"
                logger.warning("[WorkflowEngine] %s", msg)
                result.failed_step = step.id
                result.error = msg
                result.step_results = step_results
                self._emit("workflow_step_failed", {"id": step.id, "error": msg})
                self._emit("workflow_failed", {"name": spec.name, "error": msg})
                return result

            try:
                resolved = self._resolve_params(step.params, step_results)
                output = await tool.traced_execute(**resolved)
            except Exception as exc:  # tool raised
                msg = f"Tool '{step.tool}' raised: {exc}"
                logger.error("[WorkflowEngine] step '%s' %s", step.id, msg, exc_info=True)
                result.failed_step = step.id
                result.error = msg
                result.step_results = step_results
                self._emit("workflow_step_failed", {"id": step.id, "error": msg})
                self._emit("workflow_failed", {"name": spec.name, "error": msg})
                return result

            step_results[step.id] = output
            # fail-fast on Error:-prefixed tool result (BaseTool error convention)
            if isinstance(output, str) and output.startswith("Error:"):
                logger.warning("[WorkflowEngine] step '%s' returned error: %s", step.id, output[:200])
                result.failed_step = step.id
                result.error = output
                result.step_results = step_results
                self._emit("workflow_step_failed", {"id": step.id, "error": output[:300]})
                self._emit("workflow_failed", {"name": spec.name, "error": output[:300]})
                return result

            last_output = output
            self._emit("workflow_step_complete", {"id": step.id, "output_preview": str(output)[:200]})

        # Final output: explicit final_step if set & present, else last step output
        if spec.final_step and spec.final_step in step_results:
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
        """Substitute ${step_id} in string param values with prior step outputs.
        把字符串参数值里的 ${step_id} 替换为前序步骤输出；非字符串原样保留。"""
        resolved: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, str):
                resolved[key] = _TEMPLATE_RE.sub(
                    lambda m: step_results.get(m.group(1), m.group(0)), value
                )
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def _topo_order(spec: WorkflowSpec) -> list[WorkflowStep]:
        """Kahn topological sort over depends_on; raises ValueError on missing dep / cycle.
        基于 depends_on 的 Kahn 拓扑排序；缺失依赖或存在环时抛 ValueError。"""
        by_id: dict[str, WorkflowStep] = {}
        for s in spec.steps:
            if s.id in by_id:
                raise ValueError(f"Duplicate step id '{s.id}'")
            by_id[s.id] = s

        # validate deps exist
        indegree: dict[str, int] = {s.id: 0 for s in spec.steps}
        adjacency: dict[str, list[str]] = {s.id: [] for s in spec.steps}
        for s in spec.steps:
            for dep in s.depends_on:
                if dep not in by_id:
                    raise ValueError(f"Step '{s.id}' depends on unknown step '{dep}'")
                adjacency[dep].append(s.id)
                indegree[s.id] += 1

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
