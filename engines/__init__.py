"""The three explicit task engines exposed by the runtime."""

from importlib import import_module

_EXPORTS = {
    "TaskEngine": ("engines.base", "TaskEngine"),
    "PlanAndExecuteEngine": ("engines.base", "PlanAndExecuteEngine"),
    "AgentLoopEngine": ("engines.agent_loop", "AgentLoopEngine"),
    "DagPlanAndExecuteEngine": ("engines.dag", "DagPlanAndExecuteEngine"),
    "SequentialPlanAndExecuteEngine": (
        "engines.sequential",
        "SequentialPlanAndExecuteEngine",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
