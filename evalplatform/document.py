"""
Document ingestion for the evaluation platform.
评测平台的文档接入。

Accepts text-based documents (txt/md/rst/json/yaml/py/csv/log/html …),
extracts plain text, and stores a DocumentRecord. Binary formats (pdf/docx)
are rejected with a clear error instead of producing garbage tasks.
接收文本类文档并提取纯文本入库；二进制格式（pdf/docx）明确报错，
避免生成垃圾评测任务。
"""

from __future__ import annotations

import hashlib
import html as html_stdlib
import re
from pathlib import Path

from evalplatform.models import DocumentRecord
from evalplatform.store import EvalPlatformStore

# 支持的文本扩展名（无扩展名时也尝试按文本解码）
SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".csv", ".tsv", ".log", ".html", ".htm", ".xml",
}
_BINARY_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".png", ".jpg", ".jpeg"}

_MAX_DOC_BYTES = 5 * 1024 * 1024  # 5MB 上限，本地调试平台足够


class DocumentIngestError(ValueError):
    """Raised when an uploaded document cannot be ingested. 文档无法接入时抛出。"""


def _strip_html(text: str) -> str:
    """
    HTML → text. Reuses BeautifulSoup (already a project dependency, same as
    tools/local_web_parser.py); falls back to regex + html.unescape when bs4
    is unavailable. Both paths decode HTML entities.
    HTML 转文本。复用 BeautifulSoup（项目既有依赖，与 tools/local_web_parser.py
    一致）；bs4 不可用时回退正则 + html.unescape。两条路径都解码 HTML 实体。
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        extracted = soup.get_text(" ")  # get_text 已解码实体 / entities decoded
    except Exception:
        extracted = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        extracted = re.sub(r"(?s)<[^>]+>", " ", extracted)
        extracted = html_stdlib.unescape(extracted)
    extracted = re.sub(r"[ \t]+", " ", extracted)
    return re.sub(r"\n{3,}", "\n\n", extracted).strip()


def extract_text(filename: str, raw: bytes) -> str:
    """Decode raw bytes into plain text. 将原始字节解码为纯文本。"""
    if len(raw) > _MAX_DOC_BYTES:
        raise DocumentIngestError(f"文档过大（>{_MAX_DOC_BYTES // (1024 * 1024)}MB）：{filename}")

    ext = Path(filename).suffix.lower()
    if ext in _BINARY_EXTENSIONS:
        raise DocumentIngestError(
            f"暂不支持二进制格式 {ext}，请先转换为 txt/md 后上传：{filename}"
        )

    text = ""
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not text.strip():
        raise DocumentIngestError(f"文档内容为空或无法解码为文本：{filename}")

    if ext in (".html", ".htm", ".xml"):
        text = _strip_html(text)
    return text.strip()


def ingest_document(
    filename: str,
    raw: bytes | str,
    store: EvalPlatformStore,
    title: str = "",
) -> DocumentRecord:
    """
    Extract text, dedup by content hash, persist and return the record.
    提取文本、按内容哈希去重、持久化并返回文档记录。
    """
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    text = extract_text(filename, data)

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = store.find_document_by_hash(content_hash)
    if existing is not None:
        return existing

    doc = DocumentRecord(
        filename=Path(filename).name,
        title=title or Path(filename).stem,
        content=text,
        char_count=len(text),
        content_hash=content_hash,
    )
    store.save_document(doc)
    return doc
