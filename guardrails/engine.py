"""Explicit, runtime-owned security guardrails."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from core.settings import CapabilitySettings, get_settings
from guardrails.input_guardrail import InputGuardrail
from guardrails.models import GuardrailAction, GuardrailDecision, GuardrailLayer
from guardrails.output_guardrail import OutputGuardrail
from guardrails.tool_guardrail import ToolGuardrail

logger = logging.getLogger(__name__)

class GuardrailEngine:
    """Coordinates tool-input / input-context / output guardrails."""

    def __init__(
        self,
        settings: CapabilitySettings | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None,
        sandbox_dir: str | None = None,
        shell_mode: str = "restricted",
    ) -> None:
        self.settings = settings or get_settings().capabilities
        self._on_event = on_event or (lambda *_: None)
        self._confirm_callback = confirm_callback
        configured_sandbox = sandbox_dir or get_settings().paths.sandbox_dir
        self._tool = ToolGuardrail(configured_sandbox, shell_mode=shell_mode)
        self._input = InputGuardrail(
            mode=self.settings.guardrail_input_mode,
            mcp_prefix=self.settings.mcp_bridge_tool_prefix,
        )
        self._output = OutputGuardrail(mode=self.settings.guardrail_output_mode)

    async def check_tool_input(self, tool_name: str, params: dict) -> GuardrailDecision:
        """19.1: validate a tool call before execution. Resolves CONFIRM here so
        callers only see ALLOW/BLOCK."""
        if not self.settings.guardrail_tool_enabled:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)

        decision = self._tool.check(tool_name, params)

        if decision.action == GuardrailAction.CONFIRM:
            decision = await self._resolve_confirm(tool_name, params, decision)

        if decision.action == GuardrailAction.BLOCK:
            if self.settings.guardrail_tool_mode == "observe":
                self._emit("guardrail_violation", {
                    "layer": "tool_input", "tool": tool_name,
                    "reason": decision.reason, "risk": decision.risk, "mode": "observe",
                })
                return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)
            self._emit("guardrail_blocked", {
                "tool": tool_name, "reason": decision.reason, "risk": decision.risk,
            })
        return decision

    async def _resolve_confirm(self, tool_name: str, params: dict, decision: GuardrailDecision) -> GuardrailDecision:
        """Resolve a write confirmation using structured runtime settings."""
        mode = self.settings.guardrail_write_confirm
        if mode == "allow":
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)
        if mode == "confirm" and self._confirm_callback is not None:
            self._emit("guardrail_write_confirm", {"tool": tool_name, "reason": decision.reason})
            try:
                approved = await self._confirm_callback(tool_name, params)
            except Exception:
                logger.debug("[Guardrail] confirm callback failed", exc_info=True)
                approved = False
            if approved:
                return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)
            return GuardrailDecision(
                action=GuardrailAction.BLOCK, layer=GuardrailLayer.TOOL_INPUT,
                reason="write operation declined by user", risk=decision.risk,
            )
        # mode == "block", or "confirm" with no callback (non-interactive) → fail-safe block
        return GuardrailDecision(
            action=GuardrailAction.BLOCK, layer=GuardrailLayer.TOOL_INPUT,
            reason=decision.reason + " (write blocked; configure guardrail_write_confirm to permit)",
            risk=decision.risk,
        )

    def scan_tool_output(self, tool_name: str, result: str) -> GuardrailDecision:
        """19.2: neutralize injection in untrusted tool output."""
        if not self.settings.guardrail_input_enabled:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.INPUT_CONTEXT)
        decision = self._input.scan_tool_output(tool_name, result)
        if decision.action == GuardrailAction.NEUTRALIZE:
            self._emit("guardrail_injection_neutralized", {
                "tool": tool_name, "reason": decision.reason, "risk": decision.risk,
            })
        return decision

    def scan_memory(self, content: str) -> GuardrailDecision:
        """19.2: detect injection in retrieved memory (poisoning defense)."""
        if not self.settings.guardrail_input_enabled:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.INPUT_CONTEXT)
        decision = self._input.scan_memory(content)
        if decision.action == GuardrailAction.NEUTRALIZE:
            self._emit("guardrail_injection_neutralized", {
                "tool": "memory", "reason": decision.reason, "risk": decision.risk,
            })
        return decision

    def scan_final_output(self, text: str) -> GuardrailDecision:
        """19.3: redact PII/credentials from the final answer."""
        if not self.settings.guardrail_output_enabled:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.OUTPUT)
        decision = self._output.scan(text)
        if decision.action == GuardrailAction.REDACT:
            self._emit("guardrail_output_redacted", {"reason": decision.reason, "risk": decision.risk})
        return decision

    def scan_skill_content(self, content: str, trust_level: str) -> GuardrailDecision:
        return self._input.scan_skill_content(content, trust_level)

    def _emit(self, event: str, data: Any) -> None:
        try:
            self._on_event(event, data)
        except Exception:
            logger.debug("Guardrail event subscriber failed: %s", event, exc_info=True)


def current_guardrail() -> GuardrailEngine | None:
    """Compatibility helper for retained callers outside the unified runtime."""
    settings = get_settings()
    if not settings.capabilities.guardrails:
        return None
    return GuardrailEngine(
        settings.capabilities,
        sandbox_dir=settings.paths.sandbox_dir,
        shell_mode=settings.tools.shell_mode,
    )
