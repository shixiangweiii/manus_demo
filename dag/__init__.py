"""Dependency graph structures and execution."""

from importlib import import_module

_EXPORTS = {
    "TaskDAG": ("dag.graph", "TaskDAG"),
    "NodeStateMachine": ("dag.state_machine", "NodeStateMachine"),
    "DAGExecutor": ("dag.executor", "DAGExecutor"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
