"""Task orchestration engines selected by runtime policy."""

from importlib import import_module

_EXPORTS = {
    "TaskEngine": ("engines.base", "TaskEngine"),
    "DagEngine": ("engines.dag_engine", "DagEngine"),
    "GoalEngine": ("engines.goal", "GoalEngine"),
    "SequentialPlanEngine": ("engines.sequential", "SequentialPlanEngine"),
    "TodoEngine": ("engines.todo", "TodoEngine"),
    "WorkflowEngine": ("engines.workflow", "WorkflowEngine"),
    "EffortPolicy": ("engines.selector", "EffortPolicy"),
    "EngineSelector": ("engines.selector", "EngineSelector"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
