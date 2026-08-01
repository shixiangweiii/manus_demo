"""
OutputGuardrail - redact PII / credentials from the final answer.
输出护栏——对最终答案中的 PII / 凭证脱敏。

Reuses credential patterns so redaction is consistent with the shared secret
handling policy used by runtime traces.
"""

from __future__ import annotations

from guardrails.models import GuardrailAction, GuardrailDecision, GuardrailLayer
from guardrails.patterns import CREDENTIAL_PATTERNS

_PLACEHOLDER = "[REDACTED]"


class OutputGuardrail:
    """Scan + redact sensitive content in the final answer. / 输出脱敏。"""

    def __init__(self, mode: str = "redact") -> None:
        self._mode = mode

    def scan(self, text: str) -> GuardrailDecision:
        if not isinstance(text, str) or not text:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.OUTPUT)

        redacted = text
        hit_count = 0
        first_hit = ""
        for pat in CREDENTIAL_PATTERNS:
            new_text, n = pat.subn(_PLACEHOLDER, redacted)
            if n:
                if not first_hit:
                    first_hit = pat.pattern
                hit_count += n
                redacted = new_text

        if hit_count == 0:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.OUTPUT)

        if self._mode == "observe":
            return GuardrailDecision(
                action=GuardrailAction.ALLOW, layer=GuardrailLayer.OUTPUT,
                reason=f"{hit_count} sensitive match(es) (observe only)", risk="ASI05",
            )

        return GuardrailDecision(
            action=GuardrailAction.REDACT,
            layer=GuardrailLayer.OUTPUT,
            reason=f"redacted {hit_count} sensitive match(es)",
            risk="ASI05",
            transformed_text=redacted,
        )
