"""Dependency-graph planning and adaptive mutation models."""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class NodeType(str, Enum):
    GOAL = "goal"
    SUBGOAL = "subgoal"
    ACTION = "action"


class NodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


class EdgeType(str, Enum):
    DEPENDENCY = "dependency"
    CONDITIONAL = "conditional"
    ROLLBACK = "rollback"


class ExitCriteria(BaseModel):
    description: str
    validation_prompt: str = ""
    required: bool = True


class RiskAssessment(BaseModel):
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    risk_level: str = "low"
    fallback_strategy: str = ""


class TaskNode(BaseModel):
    id: str
    node_type: NodeType
    description: str
    exit_criteria: ExitCriteria = Field(
        default_factory=lambda: ExitCriteria(description="Step completed successfully")
    )
    risk: RiskAssessment = Field(default_factory=RiskAssessment)
    status: NodeStatus = NodeStatus.PENDING
    result: str | None = None
    parent_id: str | None = None
    rollback_action: str | None = None


class TaskEdge(BaseModel):
    source: str
    target: str
    edge_type: EdgeType = EdgeType.DEPENDENCY
    condition: str | None = None


class DAGState(BaseModel):
    task: str
    context: str = ""
    node_results: dict[str, str] = Field(default_factory=dict)

    def get_node_context(self, node_id: str, dependency_ids: list[str]) -> str:
        parts = [self.context] if self.context else []
        for dependency_id in dependency_ids:
            if dependency_id in self.node_results:
                parts.append(
                    f"[Result of {dependency_id}]:\n{self.node_results[dependency_id]}"
                )
        return "\n\n".join(parts)

    def merge_result(self, node_id: str, output: str) -> None:
        if node_id in self.node_results:
            logger.debug("Overwriting DAG result for node %s", node_id)
        self.node_results[node_id] = output


class AdaptAction(str, Enum):
    KEEP = "keep"
    MODIFY = "modify"
    REMOVE = "remove"
    ADD = "add"


class PlanAdaptation(BaseModel):
    action: AdaptAction
    target_node_id: str
    reason: str = ""
    new_description: str = ""
    new_exit_criteria: str = ""
    parent_node_id: str = ""
    dependencies: list[str] = Field(default_factory=list)


class AdaptationResult(BaseModel):
    should_adapt: bool
    reasoning: str = ""
    adaptations: list[PlanAdaptation] = Field(default_factory=list)
