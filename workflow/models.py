"""
Workflow models - Declarative deterministic tool-workflow specs.
工作流模型——声明式确定性工具工作流定义。

A WorkflowSpec is a DAG of tool steps executed deterministically WITHOUT any
per-step LLM reasoning — this is the "workflow" half of the explicit
workflow-vs-agent distinction.
WorkflowSpec 是工具步骤的 DAG，确定性执行、每步无 LLM 推理 —— 与自主 agentic
loop 形成显式区分。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkflowStep(BaseModel):
    """A single deterministic tool step. / 单个确定性工具步骤。"""
    id: str = Field(description="Unique step id (referenced by ${id} templating)")
    tool: str = Field(description="Tool name to invoke (must exist in the tool set)")
    params: dict[str, Any] = Field(default_factory=dict, description="Tool params; string values may contain ${dep_id} templates")
    depends_on: list[str] = Field(default_factory=list, description="Step ids this step depends on")


class WorkflowSpec(BaseModel):
    """A deterministic workflow: a DAG of tool steps. / 确定性工作流：工具步骤 DAG。"""
    name: str = Field(default="workflow")
    description: str = ""
    steps: list[WorkflowStep] = Field(default_factory=list)
    # Optional: which step's output is the final result; defaults to last completed step.
    final_step: str = ""


class WorkflowResult(BaseModel):
    """Outcome of a workflow run. / 工作流执行结果。"""
    success: bool = False
    step_results: dict[str, str] = Field(default_factory=dict)
    step_parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    final_output: str = ""
    failed_step: str = ""
    error: str = ""
