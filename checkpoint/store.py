"""Atomic local persistence for semantic runtime checkpoints."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from pydantic import ValidationError

from checkpoint.models import (
    CheckpointCorruptedError,
    RuntimeCheckpoint,
    RuntimeCheckpointSummary,
)
from core.settings import get_settings


logger = logging.getLogger(__name__)


class RuntimeCheckpointStore:
    """Store one latest checkpoint per task and ignore legacy checkpoint files."""

    def __init__(self, checkpoint_dir: str | None = None) -> None:
        configured = checkpoint_dir or get_settings().paths.checkpoint_dir
        self.directory = Path(configured).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: RuntimeCheckpoint) -> Path:
        path = self._path(checkpoint.task_id)
        temporary = path.with_suffix(path.suffix + ".tmp")
        checkpoint.updated_at = time.time()
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(
                    checkpoint.model_dump(mode="json"),
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def load(self, task_id: str) -> RuntimeCheckpoint | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return RuntimeCheckpoint.model_validate(json.load(handle))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise CheckpointCorruptedError(f"Invalid runtime checkpoint {path}: {exc}") from exc

    def list_tasks(self) -> list[RuntimeCheckpointSummary]:
        summaries: list[RuntimeCheckpointSummary] = []
        for path in self.directory.glob("*.runtime.json"):
            try:
                checkpoint = self.load(path.name.removesuffix(".runtime.json"))
            except (CheckpointCorruptedError, ValueError) as exc:
                logger.warning("Skipping invalid runtime checkpoint: %s (%s)", path, exc)
                continue
            if checkpoint is None:
                continue
            summaries.append(
                RuntimeCheckpointSummary(
                    task_id=checkpoint.task_id,
                    task=checkpoint.task,
                    engine=checkpoint.engine,
                    executor=checkpoint.executor,
                    effort=checkpoint.effort,
                    state=checkpoint.state,
                    updated_at=checkpoint.updated_at,
                    file_path=str(path),
                )
            )
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def delete(self, task_id: str) -> None:
        self._path(task_id).unlink(missing_ok=True)

    def _path(self, task_id: str) -> Path:
        safe_id = "".join(char for char in task_id if char.isalnum() or char in "-_")
        if not safe_id or safe_id != task_id:
            raise ValueError("task_id may contain only letters, numbers, '-' and '_'")
        return self.directory / f"{safe_id}.runtime.json"
