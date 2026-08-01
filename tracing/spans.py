"""Semantic tracing constants used by instrumentation and local viewers."""


class AttrKey:
    """OpenTelemetry attribute names shared by LLM and decorator tracing."""

    GEN_AI_USAGE_REASONING_TOKENS = "gen_ai.usage.reasoning_tokens"
    GEN_AI_RESPONSE_THINKING_CONTENT = "gen_ai.response.thinking_content"
    LATENCY_MS = "latency_ms"


SPAN_ICONS: dict[str, str] = {
    "agent": "🎯",
    "engine": "⚡",
    "planner": "📋",
    "dag": "🔄",
    "node": "🎯",
    "react": "💭",
    "thinking": "🧠",
    "llm": "🤖",
    "tool": "🔧",
    "reflector": "🪞",
    "memory": "🧠",
    "knowledge": "📚",
    "todo": "📝",
    "subagent": "🤖",
    "workflow": "⚙",
    "step": "👣",
    "hitl": "🙋",
}

DEFAULT_SPAN_ICON = "📌"
