"""Engine-local ``todo_write`` tool used by the task-level agent loop."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from tools.base import BaseTool


_TODO_STATUSES = frozenset({"pending", "in_progress", "completed"})


class TodoWriteTool(BaseTool):
    """Replace one agent loop's TODO state with a complete snapshot.

    The state belongs to this tool instance.  ``AgentLoop`` creates a private
    instance for every engine instance, so TODOs never leak between tasks or
    between the planning engines retained elsewhere in the repository.
    """

    def __init__(
        self,
        on_event: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._todos: list[dict[str, str]] = []
        self._last_submitted: list[dict[str, str]] = []
        self._update_count = 0
        self._on_event = on_event

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def description(self) -> str:
        return (
            "Replace your task TODO list with the complete current snapshot. "
            "Send every TODO on each call, keep statuses clear, and mark items "
            "completed as work finishes."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "The complete TODO snapshot, not a patch.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "active_form": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": sorted(_TODO_STATUSES),
                            },
                        },
                        "required": ["content", "active_form", "status"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["todos"],
            "additionalProperties": False,
        }

    @property
    def snapshot(self) -> list[dict[str, str]]:
        """Return a copy of the current full snapshot."""
        return deepcopy(self._todos)

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "todos": self.snapshot,
            "last_submitted": deepcopy(self._last_submitted),
            "update_count": self._update_count,
        }

    async def execute(self, **kwargs: Any) -> str:
        unknown_top_level = set(kwargs) - {"todos"}
        if unknown_top_level:
            return (
                "Error: unsupported fields: "
                f"{', '.join(sorted(unknown_top_level))}"
            )
        raw_todos = kwargs.get("todos")
        if not isinstance(raw_todos, list):
            return "Error: todos must be an array"

        normalized: list[dict[str, str]] = []
        for index, raw in enumerate(raw_todos):
            if not isinstance(raw, dict):
                return f"Error: todos[{index}] must be an object"
            unknown = set(raw) - {"content", "active_form", "status"}
            if unknown:
                return (
                    f"Error: todos[{index}] has unsupported fields: "
                    f"{', '.join(sorted(unknown))}"
                )
            content = raw.get("content")
            active_form = raw.get("active_form")
            status = raw.get("status")
            if not isinstance(content, str) or not content.strip():
                return f"Error: todos[{index}].content must be a non-empty string"
            if not isinstance(active_form, str) or not active_form.strip():
                return f"Error: todos[{index}].active_form must be a non-empty string"
            if not isinstance(status, str) or status not in _TODO_STATUSES:
                return (
                    f"Error: todos[{index}].status must be one of "
                    "pending, in_progress, completed"
                )
            normalized.append(
                {
                    "content": content.strip(),
                    "active_form": active_form.strip(),
                    "status": status,
                }
            )

        self._todos = normalized
        self._last_submitted = deepcopy(normalized)
        self._update_count += 1
        snapshot = self.snapshot
        if self._on_event is not None:
            counts = {
                status: sum(item["status"] == status for item in snapshot)
                for status in sorted(_TODO_STATUSES)
            }
            self._on_event(
                "todo_updated",
                {
                    "todos": snapshot,
                    "counts": {"total": len(snapshot), **counts},
                    "update_count": self._update_count,
                    "all_completed": bool(snapshot)
                    and all(item["status"] == "completed" for item in snapshot),
                },
            )
        return f"TODO snapshot updated ({len(snapshot)} items)."

    def reset_task_state(self) -> None:
        """Clear task-local display state before reusing the owning AgentLoop."""
        self._todos = []
        self._last_submitted = []
        self._update_count = 0
