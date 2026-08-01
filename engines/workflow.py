"""Explicit deterministic workflow engine adapter."""

from __future__ import annotations

import time

from core.models import (
    ActionResult,
    EngineKind,
    ExecutorKind,
    TaskRequest,
    ToolInvocation,
)
from engines.base import TaskEngine
from workflow.engine import WorkflowEngine as DeterministicWorkflowEngine
from workflow.models import WorkflowSpec


class WorkflowEngine(TaskEngine):
    kind = EngineKind.WORKFLOW

    def __init__(self, *args, tools, guardrail=None, **kwargs) -> None:
        super().__init__(*args, tools=tools, **kwargs)
        # Workflow invokes tools directly; an action executor is not applicable.
        self.executor.kind = ExecutorKind.AUTO
        self._tools = tools
        self._guardrail = guardrail

    async def run(self, request: TaskRequest):
        started_at = time.time()
        self.events.emit("engine_started", {"engine": self.kind.value})
        raw_spec = request.metadata.get("workflow_spec")
        if raw_spec is None:
            raise ValueError("TaskRequest.metadata.workflow_spec is required")
        spec = raw_spec if isinstance(raw_spec, WorkflowSpec) else WorkflowSpec.model_validate(raw_spec)
        runner = DeterministicWorkflowEngine(
            self._tools,
            on_event=self.events.legacy_callback,
            guardrail=self._guardrail,
        )
        workflow_result = await runner.execute(spec)
        self.executor.results = [
            ActionResult(
                action_id=step_id,
                success=not (workflow_result.failed_step == step_id),
                output=output,
                tool_calls=[
                    ToolInvocation(
                        tool_name=next(
                            (step.tool for step in spec.steps if step.id == step_id),
                            "",
                        ),
                        parameters=workflow_result.step_parameters.get(step_id, {}),
                        result=output,
                    )
                ],
                error=output if workflow_result.failed_step == step_id else None,
            )
            for step_id, output in workflow_result.step_results.items()
        ]
        result = self.result(
            request,
            answer=workflow_result.final_output or workflow_result.error,
            success=workflow_result.success,
            started_at=started_at,
            metadata={"workflow": workflow_result.model_dump()},
        )
        self.emit_completed(result)
        return result
