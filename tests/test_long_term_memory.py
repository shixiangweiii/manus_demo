"""
Unit tests for LongTermMemory bilingual recall (P1 fix).
长期记忆中文双语召回单元测试（P1 修复）。
"""

import time

import pytest

from memory.long_term import LongTermMemory
from memory.text_utils import bilingual_tokenize
from schema import MemoryEntry


@pytest.fixture
def ltm(tmp_path):
    return LongTermMemory(memory_dir=str(tmp_path))


def _entry(task: str, summary: str = "") -> MemoryEntry:
    return MemoryEntry(
        task=task,
        summary=summary or task,
        learnings=[],
        timestamp=time.time(),
    )


class TestBilingualTokenize:
    def test_chinese_sentence_is_not_single_token(self):
        toks = bilingual_tokenize("列表推导式的语法")
        # bigrams, not one whole-sentence token
        assert len(toks) > 1
        assert "列表推导式的语法" not in toks

    def test_english_lowercased(self):
        toks = bilingual_tokenize("Python List")
        assert "python" in toks and "list" in toks


class TestChineseRecall:
    def test_related_chinese_task_is_recalled(self, ltm):
        ltm.store(_entry("Python中列表推导式的语法是什么？", "列表推导式用 [x for x in ...]"))
        results = ltm.search("Python字典推导式和列表推导式有什么区别？")
        assert len(results) > 0
        assert "列表推导式" in results[0].task

    def test_unrelated_chinese_task_not_recalled(self, ltm):
        ltm.store(_entry("如何配置 Nginx 反向代理", "server block 配置"))
        results = ltm.search("番茄炒蛋怎么做")
        assert len(results) == 0

    def test_english_recall_still_works(self, ltm):
        ltm.store(_entry("How to deploy FastAPI app", "use uvicorn"))
        results = ltm.search("FastAPI deployment guide")
        assert len(results) > 0
