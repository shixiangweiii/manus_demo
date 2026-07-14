"""
File-based persistence for the evaluation platform.
评测平台的文件持久化。

Layout / 目录布局（EVAL_PLATFORM_DIR，默认 ~/.manus_demo/evalplatform）：
  documents/{doc_id}.json
  evalsets/{evalset_id}.json
  runs/{run_id}.json
  reports/{run_id}.json + {run_id}.md
  analyses/{analysis_id}.json + {analysis_id}.md

Atomic write follows the checkpoint convention (.tmp then os.replace) so a
crash never leaves a partial JSON file.
原子写沿用 checkpoint 约定（先写 .tmp 再 os.replace），崩溃不会留下半截文件。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

import config
from evalplatform.models import (
    AggregateAnalysis,
    DocumentRecord,
    EvalReport,
    EvalRunRecord,
    GeneratedEvalSet,
)

logger = logging.getLogger(__name__)

_M = TypeVar("_M", bound=BaseModel)

_SUBDIRS = ("documents", "evalsets", "runs", "reports", "analyses")


class EvalPlatformStore:
    """JSON-file store for documents / evalsets / runs / reports / analyses.
    文档、评测集、运行、报告、分析的 JSON 文件存储。"""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or config.EVAL_PLATFORM_DIR).expanduser()
        for sub in _SUBDIRS:
            (self.base_dir / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Generic helpers / 通用读写
    # ------------------------------------------------------------------

    def _path(self, sub: str, record_id: str, suffix: str = ".json") -> Path:
        # record_id 来自 API 路径参数，做基本清洗防目录穿越
        safe = os.path.basename(record_id)
        return self.base_dir / sub / f"{safe}{suffix}"

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """.tmp + os.replace 原子写；写失败清理残留 .tmp（对齐 checkpoint 约定）。
        Atomic write; clean the stray .tmp on failure (checkpoint convention)."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            if tmp.exists():
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise

    def _save(self, sub: str, record_id: str, model: BaseModel) -> None:
        self._atomic_write(self._path(sub, record_id), model.model_dump_json(indent=2))

    def _load(self, sub: str, record_id: str, cls: type[_M]) -> _M | None:
        path = self._path(sub, record_id)
        if not path.exists():
            return None
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[EvalPlatformStore] Failed to load %s: %s", path, exc)
            return None

    def _list(self, sub: str, cls: type[_M], sort_key: str) -> list[_M]:
        records: list[_M] = []
        for path in (self.base_dir / sub).glob("*.json"):
            try:
                records.append(cls.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning("[EvalPlatformStore] Skipping unreadable %s: %s", path, exc)
        records.sort(key=lambda r: getattr(r, sort_key, 0), reverse=True)
        return records

    # ------------------------------------------------------------------
    # Documents / 文档
    # ------------------------------------------------------------------

    def save_document(self, doc: DocumentRecord) -> None:
        self._save("documents", doc.doc_id, doc)

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        return self._load("documents", doc_id, DocumentRecord)

    def list_documents(self) -> list[DocumentRecord]:
        return self._list("documents", DocumentRecord, "created_at")

    def find_document_by_hash(self, content_hash: str) -> DocumentRecord | None:
        """重复上传去重：按内容哈希查找已有文档。"""
        for doc in self.list_documents():
            if doc.content_hash and doc.content_hash == content_hash:
                return doc
        return None

    # ------------------------------------------------------------------
    # Eval sets / 评测集
    # ------------------------------------------------------------------

    def save_evalset(self, evalset: GeneratedEvalSet) -> None:
        self._save("evalsets", evalset.evalset_id, evalset)

    def get_evalset(self, evalset_id: str) -> GeneratedEvalSet | None:
        return self._load("evalsets", evalset_id, GeneratedEvalSet)

    def list_evalsets(self) -> list[GeneratedEvalSet]:
        return self._list("evalsets", GeneratedEvalSet, "created_at")

    # ------------------------------------------------------------------
    # Runs / 运行
    # ------------------------------------------------------------------

    def save_run(self, run: EvalRunRecord) -> None:
        self._save("runs", run.run_id, run)

    def get_run(self, run_id: str) -> EvalRunRecord | None:
        return self._load("runs", run_id, EvalRunRecord)

    def list_runs(self) -> list[EvalRunRecord]:
        return self._list("runs", EvalRunRecord, "created_at")

    # ------------------------------------------------------------------
    # Reports / 报告（report_id == run_id，一次运行一份报告）
    # ------------------------------------------------------------------

    def save_report(self, report: EvalReport) -> None:
        # 先写 .md 再写 .json：读取方（API/页面/CLI）都以 .json 为权威入口，
        # 崩溃窗口只会留下孤儿 .md，不会出现"新 json 配旧 md"的错配
        # .md first, .json last: readers gate on .json, so a crash between
        # the two writes leaves an orphan .md instead of a mismatched pair.
        self._atomic_write(self._path("reports", report.run_id, suffix=".md"), report.markdown)
        self._save("reports", report.run_id, report)

    def get_report(self, run_id: str) -> EvalReport | None:
        return self._load("reports", run_id, EvalReport)

    def list_reports(self) -> list[EvalReport]:
        return self._list("reports", EvalReport, "generated_at")

    def report_markdown_path(self, run_id: str) -> Path:
        return self._path("reports", run_id, suffix=".md")

    # ------------------------------------------------------------------
    # Analyses / 聚合分析
    # ------------------------------------------------------------------

    def save_analysis(self, analysis: AggregateAnalysis) -> None:
        # 同 save_report：.md 在前，权威 .json 在后
        self._atomic_write(
            self._path("analyses", analysis.analysis_id, suffix=".md"), analysis.markdown,
        )
        self._save("analyses", analysis.analysis_id, analysis)

    def get_analysis(self, analysis_id: str) -> AggregateAnalysis | None:
        return self._load("analyses", analysis_id, AggregateAnalysis)

    def list_analyses(self) -> list[AggregateAnalysis]:
        return self._list("analyses", AggregateAnalysis, "created_at")
