"""
Workflow spec loader - Load a WorkflowSpec from a JSON file.
工作流定义加载器——从 JSON 文件加载 WorkflowSpec。
"""

from __future__ import annotations

import json

from workflow.models import WorkflowSpec


def load_workflow_spec(path: str) -> WorkflowSpec:
    """Load and validate a WorkflowSpec from a JSON file.
    从 JSON 文件加载并校验 WorkflowSpec。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return WorkflowSpec.model_validate(data)
