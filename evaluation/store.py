"""File-backed persistence for the local evaluation platform."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from core.settings import get_settings
from evaluation.models import AggregateAnalysis, DocumentRecord, EvalRunRecord, GeneratedEvalSet

ModelT = TypeVar("ModelT", bound=BaseModel)
logger = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class EvaluationStore:
    def __init__(self, root: str | Path | None = None) -> None:
        default = get_settings().evaluation.output_dir
        self.root = Path(root or default).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("documents", "evalsets", "runs", "analyses"):
            (self.root / name).mkdir(exist_ok=True)

    def _path(self, kind: str, item_id: str) -> Path:
        if kind not in {"documents", "evalsets", "runs", "analyses"}:
            raise ValueError(f"Unknown evaluation record kind: {kind}")
        if not _SAFE_ID.fullmatch(item_id):
            raise ValueError(f"Invalid evaluation record id: {item_id!r}")
        return self.root / kind / f"{item_id}.json"

    def _save(self, kind: str, item_id: str, model: BaseModel) -> None:
        path = self._path(kind, item_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _load(self, kind: str, item_id: str, model_type: type[ModelT]) -> ModelT | None:
        try:
            path = self._path(kind, item_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def _list(self, kind: str, model_type: type[ModelT]) -> list[ModelT]:
        items = []
        for path in (self.root / kind).glob("*.json"):
            try:
                items.append(model_type.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                logger.warning("Skipping invalid evaluation record: %s", path, exc_info=True)
        return sorted(items, key=lambda item: getattr(item, "created_at", 0), reverse=True)

    def save_document(self, item: DocumentRecord) -> None:
        self._save("documents", item.doc_id, item)

    def get_document(self, item_id: str) -> DocumentRecord | None:
        return self._load("documents", item_id, DocumentRecord)

    def list_documents(self) -> list[DocumentRecord]:
        return self._list("documents", DocumentRecord)

    def save_evalset(self, item: GeneratedEvalSet) -> None:
        self._save("evalsets", item.evalset_id, item)

    def get_evalset(self, item_id: str) -> GeneratedEvalSet | None:
        return self._load("evalsets", item_id, GeneratedEvalSet)

    def list_evalsets(self) -> list[GeneratedEvalSet]:
        return self._list("evalsets", GeneratedEvalSet)

    def save_run(self, item: EvalRunRecord) -> None:
        self._save("runs", item.run_id, item)

    def get_run(self, item_id: str) -> EvalRunRecord | None:
        return self._load("runs", item_id, EvalRunRecord)

    def list_runs(self) -> list[EvalRunRecord]:
        return self._list("runs", EvalRunRecord)

    def save_analysis(self, item: AggregateAnalysis) -> None:
        self._save("analyses", item.analysis_id, item)

    def get_analysis(self, item_id: str) -> AggregateAnalysis | None:
        return self._load("analyses", item_id, AggregateAnalysis)

    def list_analyses(self) -> list[AggregateAnalysis]:
        return self._list("analyses", AggregateAnalysis)
