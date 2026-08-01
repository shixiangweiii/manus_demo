"""Text-document ingestion for generated evaluation sets."""

from __future__ import annotations

import hashlib
from pathlib import Path

from evaluation.models import DocumentRecord


class DocumentIngestError(ValueError):
    pass


def ingest_document(
    filename: str,
    content: str,
    *,
    max_chars: int,
) -> DocumentRecord:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".md", ".json", ".toml", ".py"}:
        raise DocumentIngestError("仅支持本地文本、Markdown、JSON、TOML 和 Python 文件")
    text = content.strip()
    if not text:
        raise DocumentIngestError("文档内容为空")
    if len(text) > max_chars:
        text = text[:max_chars]
    return DocumentRecord(
        filename=Path(filename).name,
        title=Path(filename).stem,
        content=text,
        char_count=len(text),
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
