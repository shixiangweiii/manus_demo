"""Local conversational and structured memory implementations."""

from importlib import import_module

_EXPORTS = {
    "ShortTermMemory": ("memory.short_term", "ShortTermMemory"),
    "LongTermMemory": ("memory.long_term", "LongTermMemory"),
    "AgenticMemoryRecord": ("memory.models", "AgenticMemoryRecord"),
    "MemoryKind": ("memory.models", "MemoryKind"),
    "MemoryStatus": ("memory.models", "MemoryStatus"),
    "MemorySearchQuery": ("memory.models", "MemorySearchQuery"),
    "MemorySearchResult": ("memory.models", "MemorySearchResult"),
    "AgenticMemoryStore": ("memory.agentic_store", "AgenticMemoryStore"),
    "AgenticMemoryService": ("memory.service", "AgenticMemoryService"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    return getattr(import_module(module_name), attribute)
