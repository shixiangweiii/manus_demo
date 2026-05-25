"""
Task state persistence store (v14.5).

Handles saving/loading/deleting checkpoint files on disk.
Each checkpoint is a single JSON file named {task_id}_{timestamp}.json.
Atomic write via .tmp + os.rename prevents partial writes on crash.

任务状态持久化存储。
每个 checkpoint 是一个 JSON 文件，命名格式 {task_id}_{timestamp}.json。
通过 .tmp + os.rename 实现原子写入，防止崩溃时写入不完整文件。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from pydantic import ValidationError

import config
from checkpoint.models import (
    CHECKPOINT_VERSION,
    CheckpointCorruptedError,
    CheckpointValidationError,
    CheckpointVersionMismatchError,
    TaskCheckpoint,
    TaskCheckpointSummary,
    TaskRunState,
)

logger = logging.getLogger(__name__)


class TaskStateStore:
    """Persistent store for task checkpoints.
    任务 checkpoint 持久化存储。"""

    def __init__(self, checkpoint_dir: str | None = None):
        self._dir = checkpoint_dir or config.CHECKPOINT_DIR
        os.makedirs(self._dir, exist_ok=True)

    def save(self, checkpoint: TaskCheckpoint) -> str:
        """Save checkpoint to disk. Returns file path.
        Atomic write: .tmp then os.rename.
        保存 checkpoint 到磁盘，返回文件路径。原子写入。"""
        timestamp = int(time.time() * 1000)
        filename = f"{checkpoint.task_id}_{timestamp}.json"
        filepath = os.path.join(self._dir, filename)
        tmp_path = filepath + ".tmp"

        data = checkpoint.model_dump(mode="json")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, filepath)
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        self._prune_old_checkpoints(checkpoint.task_id)
        logger.debug("[TaskStateStore] Saved checkpoint %s", filepath)
        return filepath

    def load(self, task_id: str) -> TaskCheckpoint | None:
        """Load the latest checkpoint for task_id.
        加载指定 task_id 的最新 checkpoint。"""
        latest_path = self._latest_file_for(task_id)
        if latest_path is None:
            return None
        return self._load_from_path(latest_path)

    def list_tasks(self) -> list[TaskCheckpointSummary]:
        """List all checkpointed tasks with summary info.
        列出所有 checkpointed 任务的摘要信息。"""
        summaries: list[TaskCheckpointSummary] = []
        seen_task_ids: set[str] = set()

        for filepath in sorted(Path(self._dir).glob("*.json"), reverse=True):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            task_id = data.get("task_id", "")
            if not task_id or task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)

            summaries.append(TaskCheckpointSummary(
                task_id=task_id,
                task=data.get("task", ""),
                complexity=data.get("complexity", ""),
                state=TaskRunState(data.get("state", "running")),
                updated_at=data.get("updated_at", 0.0),
                file_path=str(filepath),
            ))

        return summaries

    def mark_completed(self, task_id: str) -> None:
        """Update the latest checkpoint state to COMPLETED and save.
        将最新 checkpoint 状态更新为 COMPLETED。"""
        checkpoint = self.load(task_id)
        if checkpoint is None:
            logger.warning("[TaskStateStore] No checkpoint found for task_id=%s", task_id)
            return
        checkpoint.state = TaskRunState.COMPLETED
        checkpoint.updated_at = time.time()
        self.save(checkpoint)

    def mark_failed(self, task_id: str) -> None:
        """Update the latest checkpoint state to FAILED and save.
        将最新 checkpoint 状态更新为 FAILED。"""
        checkpoint = self.load(task_id)
        if checkpoint is None:
            return
        checkpoint.state = TaskRunState.FAILED
        checkpoint.updated_at = time.time()
        self.save(checkpoint)

    def delete(self, task_id: str) -> None:
        """Remove all checkpoint files for a task_id.
        删除指定 task_id 的所有 checkpoint 文件。"""
        for filepath in Path(self._dir).glob(f"{task_id}_*.json"):
            filepath.unlink(missing_ok=True)
        logger.debug("[TaskStateStore] Deleted checkpoints for task_id=%s", task_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _latest_file_for(self, task_id: str) -> str | None:
        """Find the most recent checkpoint file for a task_id.
        查找指定 task_id 的最新 checkpoint 文件路径。"""
        candidates = sorted(
            Path(self._dir).glob(f"{task_id}_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(candidates[0]) if candidates else None

    def _load_from_path(self, path: str) -> TaskCheckpoint:
        """Load and validate a checkpoint from a specific file path.
        从指定文件路径加载并验证 checkpoint。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise CheckpointCorruptedError(f"Corrupted checkpoint file {path}: {exc}") from exc

        version = data.get("checkpoint_version", 0)
        if version > CHECKPOINT_VERSION:
            raise CheckpointVersionMismatchError(
                f"Checkpoint version {version} is newer than supported {CHECKPOINT_VERSION}: {path}"
            )

        try:
            return TaskCheckpoint(**data)
        except ValidationError as exc:
            raise CheckpointValidationError(f"Invalid checkpoint schema in {path}: {exc}") from exc

    def _prune_old_checkpoints(self, task_id: str) -> None:
        """Keep only the latest N checkpoint files per task.
        每个任务只保留最近的 N 个 checkpoint 文件。"""
        max_per_task = config.CHECKPOINT_MAX_PER_TASK
        files = sorted(
            Path(self._dir).glob(f"{task_id}_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_file in files[max_per_task:]:
            old_file.unlink(missing_ok=True)
