"""
Guardrails (v19) - minimal agent security guardrails.
安全护栏（v19）—— 最小可用的 agent 安全护栏。

Three layers (all opt-in via GUARDRAILS_ENABLED, default off):
  19.1 ToolGuardrail   — dangerous tool params / path traversal / write-op gating
  19.2 InputGuardrail  — neutralize indirect prompt injection in untrusted output/memory
  19.3 OutputGuardrail — redact PII / credentials from the final answer

OWASP Agentic Top 10 (ASI) taxonomy; see sxw_aicoding/security/owasp-asi-threat-matrix.md.
"""

from guardrails.engine import (
    GuardrailEngine,
    current_guardrail,
    reset_guardrail_runtime,
    set_confirm_callback,
    set_event_sink,
)
from guardrails.models import GuardrailAction, GuardrailDecision, GuardrailLayer

__all__ = [
    "GuardrailEngine",
    "current_guardrail",
    "set_event_sink",
    "set_confirm_callback",
    "reset_guardrail_runtime",
    "GuardrailAction",
    "GuardrailDecision",
    "GuardrailLayer",
]
