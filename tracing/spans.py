"""Semantic tracing constants used by instrumentation and local viewers."""


class AttrKey:
    """OpenTelemetry attribute names shared by LLM and decorator tracing."""

    GEN_AI_USAGE_REASONING_TOKENS = "gen_ai.usage.reasoning_tokens"
    GEN_AI_RESPONSE_REASONING_CONTENT = "gen_ai.response.reasoning_content"
    LATENCY_MS = "latency_ms"


SPAN_ICONS: dict[str, str] = {
    "agent": "🎯",
    "engine": "⚡",
    "planner": "📋",
    "dag": "🔄",
    "node": "🎯",
    "llm": "🤖",
    "tool": "🔧",
    "reflector": "🪞",
    "memory": "🧠",
    "knowledge": "📚",
    "agent_loop": "🔁",
    "subagent": "🤖",
    "step": "👣",
    "hitl": "🙋",
}

DEFAULT_SPAN_ICON = "📌"
