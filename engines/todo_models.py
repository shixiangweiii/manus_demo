"""Dynamic TODO planning models."""

from __future__ import annotations

import time
from enum import Enum

from pydantic import BaseModel, Field


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class TodoItem(BaseModel):
    id: int
    description: str
    status: TodoStatus = TodoStatus.PENDING
    dependencies: list[int] = Field(default_factory=list)
    result: str | None = None
    retry_count: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TodoList(BaseModel):
    task: str
    todos: dict[int, TodoItem] = Field(default_factory=dict)
    next_id: int = 1

    def _has_cycle(self) -> bool:
        dependents: dict[int, list[int]] = {item_id: [] for item_id in self.todos}
        indegree: dict[int, int] = {}
        for item_id, todo in self.todos.items():
            valid_dependencies = [value for value in todo.dependencies if value in self.todos]
            indegree[item_id] = len(valid_dependencies)
            for dependency_id in valid_dependencies:
                dependents[dependency_id].append(item_id)
        ready = [item_id for item_id, degree in indegree.items() if degree == 0]
        visited = 0
        while ready:
            item_id = ready.pop(0)
            visited += 1
            for dependent_id in dependents.get(item_id, []):
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    ready.append(dependent_id)
        return visited != len(self.todos)

    def add_todo(self, description: str, dependencies: list[int] | None = None) -> TodoItem:
        todo = TodoItem(
            id=self.next_id,
            description=description,
            dependencies=dependencies or [],
        )
        self.todos[todo.id] = todo
        if self._has_cycle():
            del self.todos[todo.id]
            raise ValueError(f"Cannot add TODO {todo.id}: dependency cycle")
        self.next_id += 1
        return todo

    def get_pending_todos(self) -> list[TodoItem]:
        return [
            todo
            for todo in self.todos.values()
            if todo.status in {TodoStatus.PENDING, TodoStatus.IN_PROGRESS}
        ]

    def get_ready_todos(self) -> list[TodoItem]:
        ready = []
        for todo in self.todos.values():
            if todo.status != TodoStatus.PENDING:
                continue
            if not all(dependency_id in self.todos for dependency_id in todo.dependencies):
                continue
            if all(
                self.todos[dependency_id].status == TodoStatus.COMPLETED
                for dependency_id in todo.dependencies
            ):
                ready.append(todo)
        return ready

    def mark_completed(self, todo_id: int, result: str) -> None:
        if todo_id in self.todos:
            self.todos[todo_id].status = TodoStatus.COMPLETED
            self.todos[todo_id].result = result
            self.todos[todo_id].updated_at = time.time()

    def mark_in_progress(self, todo_id: int) -> None:
        self._set_status(todo_id, TodoStatus.IN_PROGRESS)

    def mark_pending(self, todo_id: int) -> None:
        self._set_status(todo_id, TodoStatus.PENDING)

    def mark_blocked(self, todo_id: int) -> None:
        self._set_status(todo_id, TodoStatus.BLOCKED)

    def _set_status(self, todo_id: int, status: TodoStatus) -> None:
        if todo_id in self.todos:
            self.todos[todo_id].status = status
            self.todos[todo_id].updated_at = time.time()

    def is_complete(self) -> bool:
        return all(todo.status == TodoStatus.COMPLETED for todo in self.todos.values())

    def has_pending(self) -> bool:
        return any(
            todo.status in {TodoStatus.PENDING, TodoStatus.IN_PROGRESS}
            for todo in self.todos.values()
        )
