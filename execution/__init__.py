"""Action execution strategies."""

from importlib import import_module

_EXPORTS = {
    "ActionExecutor": ("execution.base", "ActionExecutor"),
    "ToolCallingActionExecutor": (
        "execution.tool_calling",
        "ToolCallingActionExecutor",
    ),
    "ReasoningAwareToolCallingActionExecutor": (
        "execution.reasoning_aware_tool_calling",
        "ReasoningAwareToolCallingActionExecutor",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
