"""
Tests for evalplatform document ingestion + store persistence.
评测平台文档接入与存储持久化测试。
"""

from __future__ import annotations

import pytest

from evalplatform.document import (
    DocumentIngestError,
    extract_text,
    ingest_document,
)
from evalplatform.models import (
    AggregateAnalysis,
    DocumentRecord,
    EvalReport,
    EvalSetStatus,
    GeneratedEvalSet,
    RunStatus,
    Suggestion,
)
from evalplatform.store import EvalPlatformStore
from tests.helpers.evalplatform_fixtures import SAMPLE_MD_DOC, make_run


@pytest.fixture
def store(tmp_path) -> EvalPlatformStore:
    return EvalPlatformStore(base_dir=str(tmp_path / "platform"))


# ======================================================================
# Document ingestion / 文档接入
# ======================================================================

class TestExtractText:
    def test_plain_text(self):
        assert extract_text("a.txt", "你好 world".encode("utf-8")) == "你好 world"

    def test_html_stripped(self):
        html = b"<html><script>evil()</script><body><h1>Title</h1><p>body text</p></body></html>"
        text = extract_text("page.html", html)
        assert "Title" in text and "body text" in text
        assert "evil" not in text and "<h1>" not in text

    def test_html_entities_decoded(self):
        # review V12: entities must be decoded, not leaked as &amp;/&lt; into tasks
        html = b"<body><p>Tom &amp; Jerry &lt;tag&gt; caf&#233;</p></body>"
        text = extract_text("page.html", html)
        assert "Tom & Jerry" in text
        assert "<tag>" in text
        assert "café" in text
        assert "&amp;" not in text and "&#233;" not in text

    def test_binary_extension_rejected(self):
        with pytest.raises(DocumentIngestError, match="二进制"):
            extract_text("report.pdf", b"%PDF-1.4")

    def test_empty_rejected(self):
        with pytest.raises(DocumentIngestError, match="为空"):
            extract_text("empty.txt", b"   ")

    def test_gbk_fallback(self):
        text = extract_text("gbk.txt", "中文内容".encode("gb18030"))
        assert "中文内容" in text

    def test_oversize_rejected(self):
        with pytest.raises(DocumentIngestError, match="过大"):
            extract_text("big.txt", b"x" * (6 * 1024 * 1024))


class TestIngestDocument:
    def test_ingest_and_dedup(self, store):
        doc1 = ingest_document("guide.md", SAMPLE_MD_DOC, store)
        assert doc1.char_count > 0
        assert doc1.filename == "guide.md"
        # 相同内容重复上传 → 返回已有记录
        doc2 = ingest_document("renamed.md", SAMPLE_MD_DOC, store)
        assert doc2.doc_id == doc1.doc_id
        assert len(store.list_documents()) == 1

    def test_title_defaults_to_stem(self, store):
        doc = ingest_document("some/path/guide.md", SAMPLE_MD_DOC, store)
        assert doc.title == "guide"
        assert doc.filename == "guide.md"


# ======================================================================
# Store roundtrips / 存储往返
# ======================================================================

class TestStore:
    def test_document_roundtrip(self, store):
        doc = DocumentRecord(filename="a.txt", content="hello", char_count=5)
        store.save_document(doc)
        loaded = store.get_document(doc.doc_id)
        assert loaded is not None and loaded.content == "hello"

    def test_evalset_roundtrip_with_tasks(self, store):
        from evalplatform.generator import generate_heuristic_tasks
        doc = DocumentRecord(filename="g.md", content=SAMPLE_MD_DOC, char_count=len(SAMPLE_MD_DOC))
        tasks = generate_heuristic_tasks(doc, 4)
        evalset = GeneratedEvalSet(name="es", doc_id=doc.doc_id, tasks=tasks, status=EvalSetStatus.READY)
        store.save_evalset(evalset)
        loaded = store.get_evalset(evalset.evalset_id)
        assert loaded is not None
        assert [t.task_id for t in loaded.tasks] == [t.task_id for t in tasks]
        # tuple 字段（step_count_range）在 JSON 往返后仍可用
        assert loaded.tasks[0].ground_truth.expected_step_count_range[0] >= 1

    def test_run_roundtrip_and_ordering(self, store):
        run_old = make_run("run_old", [True], started_at=100.0)
        run_new = make_run("run_new", [True, False], started_at=200.0)
        run_old.created_at, run_new.created_at = 100.0, 200.0
        store.save_run(run_old)
        store.save_run(run_new)
        runs = store.list_runs()
        assert [r.run_id for r in runs] == ["run_new", "run_old"]
        loaded = store.get_run("run_new")
        assert loaded is not None and loaded.status == RunStatus.COMPLETED
        assert loaded.metrics_by_mode["simple"]["total_tasks"] == 2

    def test_report_markdown_written(self, store):
        report = EvalReport(run_id="run_x", markdown="# 报告内容")
        store.save_report(report)
        assert store.get_report("run_x") is not None
        md_path = store.report_markdown_path("run_x")
        assert md_path.exists() and "报告内容" in md_path.read_text(encoding="utf-8")

    def test_analysis_roundtrip(self, store):
        analysis = AggregateAnalysis(
            run_ids=["r1"], markdown="# 分析",
            suggestions=[Suggestion(title="t", severity="info", evidence="e", action="a")],
        )
        store.save_analysis(analysis)
        loaded = store.get_analysis(analysis.analysis_id)
        assert loaded is not None and loaded.suggestions[0].title == "t"

    def test_missing_returns_none(self, store):
        assert store.get_document("nope") is None
        assert store.get_evalset("nope") is None
        assert store.get_run("nope") is None
        assert store.get_report("nope") is None
        assert store.get_analysis("nope") is None

    def test_path_traversal_id_is_neutralized(self, store, tmp_path):
        doc = DocumentRecord(filename="a.txt", content="x", char_count=1)
        store.save_document(doc)
        # 目录穿越形式的 id 被 basename 清洗，不会读到平台目录以外
        assert store.get_document("../../etc/passwd") is None
