"""Retained planner and peripheral agent implementations."""

from importlib import import_module

_EXPORTS = {
    "PlannerAgent": ("agents.planner", "PlannerAgent"),
    "ReflectorAgent": ("agents.reflector", "ReflectorAgent"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
