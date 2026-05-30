"""
Guardrail models (v19) - decisions and actions for the security guardrail layers.
护栏模型（v19）—— 安全护栏三层的决策与动作。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class GuardrailAction(str, Enum):
    """What a guardrail decided to do. / 护栏决策动作。"""
    ALLOW = "allow"            # pass through unchanged
    BLOCK = "block"            # deny the tool call / operation
    NEUTRALIZE = "neutralize"  # wrap/strip untrusted content (input layer)
    REDACT = "redact"          # mask sensitive content (output layer)
    CONFIRM = "confirm"        # requires explicit user confirmation (write ops)


class GuardrailLayer(str, Enum):
    """Which layer produced the decision. / 决策来自哪一层。"""
    TOOL_INPUT = "tool_input"      # 19.1 before tool execution
    INPUT_CONTEXT = "input_context"  # 19.2 tool output / retrieved memory
    OUTPUT = "output"              # 19.3 final answer


class GuardrailDecision(BaseModel):
    """Result of a guardrail check. / 一次护栏检查的结果。"""
    action: GuardrailAction = GuardrailAction.ALLOW
    layer: GuardrailLayer = GuardrailLayer.TOOL_INPUT
    reason: str = ""
    risk: str = ""                       # OWASP ASI id, e.g. "ASI01"
    # For NEUTRALIZE/REDACT: the transformed text to use instead of the original.
    # None means "no transformation" (caller keeps the original).
    transformed_text: str | None = None

    @property
    def changed(self) -> bool:
        return self.transformed_text is not None
