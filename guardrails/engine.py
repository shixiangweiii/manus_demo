"""
GuardrailEngine (v19) - orchestrates the three guardrail layers + runtime hooks.
护栏引擎（v19）—— 编排三层护栏 + 运行时事件/确认钩子。

Module-level runtime hooks (mirroring agents.prompt_utils.set_hitl_runtime_enabled):
  - set_event_sink(cb)        : route guardrail events to UI/Tracing/Probe
  - set_confirm_callback(cb)  : async write-op confirmation (wired to ask_user)
  - current_guardrail()       : returns a GuardrailEngine when GUARDRAILS_ENABLED,
                                else None (zero overhead on the disabled path).

`current_guardrail()` reads LIVE config each call so evaluation variants that flip
config at runtime are honored; detection patterns are module-level constants.
current_guardrail() 每次读实时 config（兼容评测 variant 翻转），patterns 为模块级常量。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import config
from guardrails.input_guardrail import InputGuardrail
from guardrails.models import GuardrailAction, GuardrailDecision, GuardrailLayer
from guardrails.output_guardrail import OutputGuardrail
from guardrails.tool_guardrail import ToolGuardrail

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Module-level runtime hooks
# ----------------------------------------------------------------------
_event_sink: Callable[[str, Any], None] | None = None
_confirm_cb: Callable[[str, dict], Awaitable[bool]] | None = None


def set_event_sink(cb: Callable[[str, Any], None] | None) -> None:
    """Route guardrail events to a multicast sink (orchestrator._emit)."""
    global _event_sink
    _event_sink = cb


def set_confirm_callback(cb: Callable[[str, dict], Awaitable[bool]] | None) -> None:
    """Register an async write-op confirmation callback (interactive only)."""
    global _confirm_cb
    _confirm_cb = cb


def reset_guardrail_runtime() -> None:
    """Clear runtime hooks (called by orchestrator after a run to avoid leaks)."""
    global _event_sink, _confirm_cb
    _event_sink = None
    _confirm_cb = None


def _emit(event: str, data: Any) -> None:
    if _event_sink is None:
        return
    try:
        _event_sink(event, data)
    except Exception:
        logger.debug("[Guardrail] event sink failed for '%s'", event, exc_info=True)


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------
class GuardrailEngine:
    """Coordinates tool-input / input-context / output guardrails."""

    def __init__(self) -> None:
        self._tool = ToolGuardrail()
        self._input = InputGuardrail()
        self._output = OutputGuardrail()

    async def check_tool_input(self, tool_name: str, params: dict) -> GuardrailDecision:
        """19.1: validate a tool call before execution. Resolves CONFIRM here so
        callers only see ALLOW/BLOCK."""
        if not config.GUARDRAIL_TOOL_ENABLED:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)

        decision = self._tool.check(tool_name, params)

        if decision.action == GuardrailAction.CONFIRM:
            decision = await self._resolve_confirm(tool_name, params, decision)

        if decision.action == GuardrailAction.BLOCK:
            if config.GUARDRAIL_TOOL_MODE == "observe":
                _emit("guardrail_violation", {
                    "layer": "tool_input", "tool": tool_name,
                    "reason": decision.reason, "risk": decision.risk, "mode": "observe",
                })
                return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)
            _emit("guardrail_blocked", {
                "tool": tool_name, "reason": decision.reason, "risk": decision.risk,
            })
        return decision

    async def _resolve_confirm(self, tool_name: str, params: dict, decision: GuardrailDecision) -> GuardrailDecision:
        """Map a CONFIRM (write op) per GUARDRAIL_WRITE_CONFIRM."""
        mode = config.GUARDRAIL_WRITE_CONFIRM
        if mode == "allow":
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.TOOL_INPUT)
        if mode == "confirm" and _confirm_cb is not None:
            _emit("guardrail_write_confirm", {"tool": tool_name, "reason": decision.reason})
            try:
                approved = await _confirm_cb(tool_name, params)
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
            reason=decision.reason + " (write blocked; set GUARDRAIL_WRITE_CONFIRM=allow/confirm to permit)",
            risk=decision.risk,
        )

    def scan_tool_output(self, tool_name: str, result: str) -> GuardrailDecision:
        """19.2: neutralize injection in untrusted tool output."""
        if not config.GUARDRAIL_INPUT_ENABLED:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.INPUT_CONTEXT)
        decision = self._input.scan_tool_output(tool_name, result)
        if decision.action == GuardrailAction.NEUTRALIZE:
            _emit("guardrail_injection_neutralized", {
                "tool": tool_name, "reason": decision.reason, "risk": decision.risk,
            })
        return decision

    def scan_memory(self, content: str) -> GuardrailDecision:
        """19.2: detect injection in retrieved memory (poisoning defense)."""
        if not config.GUARDRAIL_INPUT_ENABLED:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.INPUT_CONTEXT)
        decision = self._input.scan_memory(content)
        if decision.action == GuardrailAction.NEUTRALIZE:
            _emit("guardrail_injection_neutralized", {
                "tool": "memory", "reason": decision.reason, "risk": decision.risk,
            })
        return decision

    def scan_final_output(self, text: str) -> GuardrailDecision:
        """19.3: redact PII/credentials from the final answer."""
        if not config.GUARDRAIL_OUTPUT_ENABLED:
            return GuardrailDecision(action=GuardrailAction.ALLOW, layer=GuardrailLayer.OUTPUT)
        decision = self._output.scan(text)
        if decision.action == GuardrailAction.REDACT:
            _emit("guardrail_output_redacted", {"reason": decision.reason, "risk": decision.risk})
        return decision


def current_guardrail() -> GuardrailEngine | None:
    """Return a GuardrailEngine when v19 is enabled, else None (zero overhead)."""
    if not config.GUARDRAILS_ENABLED:
        return None
    return GuardrailEngine()
