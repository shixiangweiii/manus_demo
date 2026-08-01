"""Action execution strategies."""

from importlib import import_module

_EXPORTS = {
    "ActionExecutor": ("execution.base", "ActionExecutor"),
    "ReactActionExecutor": ("execution.react", "ReactActionExecutor"),
    "ThinkingAwareActionExecutor": ("execution.thinking", "ThinkingAwareActionExecutor"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
