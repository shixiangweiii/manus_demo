"""Native tool-calling action-loop implementations."""

from importlib import import_module

_EXPORTS = {
    "ToolCallingLoop": ("tool_calling.loop", "ToolCallingLoop"),
    "ReasoningAwareToolCallingLoop": (
        "tool_calling.reasoning_aware_loop",
        "ReasoningAwareToolCallingLoop",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
