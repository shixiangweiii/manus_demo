"""
Knowledge Retriever - Simple TF-IDF keyword-based retrieval over local documents.
知识检索器 —— 基于 TF-IDF 关键词的本地文档检索。

Loads text/markdown files from the knowledge docs directory, splits them into
chunks, and provides keyword-based search to inject relevant context into
agent prompts. Pure Python implementation with no external ML dependencies.
从知识文档目录加载文本/Markdown 文件，切分为块，
提供关键词搜索以将相关上下文注入到智能体 prompt 中。
纯 Python 实现，无需外部 ML 依赖。
"""

from __future__ import annotations

import logging
import math
import os
import re
from typing import Any

import config

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """
    Lightweight knowledge retrieval using TF-IDF scoring.
    使用 TF-IDF 评分的轻量级知识检索器。

    Documents are loaded from the configured docs directory, split into
    fixed-size chunks, and indexed using an inverted TF-IDF index.
    从配置的文档目录加载文档，切分为固定大小的块，
    使用倒排 TF-IDF 索引进行高效检索。
    """

    def __init__(self, docs_dir: str | None = None, chunk_size: int | None = None):
        self.docs_dir = docs_dir or config.KNOWLEDGE_DOCS_DIR  # 知识文档目录
        self.chunk_size = chunk_size or config.KNOWLEDGE_CHUNK_SIZE  # 每块最大字符数
        self.top_k = config.KNOWLEDGE_TOP_K  # 默认返回的最相关块数量

        self._chunks: list[dict[str, Any]] = []     # 所有文档块 {text, source, index}
        self._idf: dict[str, float] = {}             # 词的逆文档频率 IDF
        self._tf_idf: list[dict[str, float]] = []   # 每个块的 TF-IDF 向量

        self._build_index()  # 构造时自动建立索引

    # ------------------------------------------------------------------
    # Index building
    # 索引构建
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """
        Load documents, chunk them, and compute TF-IDF.
        加载文档，切分为块，计算 TF-IDF 索引。
        """
        if not os.path.isdir(self.docs_dir):
            logger.warning("Knowledge docs dir not found: %s", self.docs_dir)
            return

        # Load and chunk documents（加载并切分文档）
        for filename in sorted(os.listdir(self.docs_dir)):
            if not filename.endswith((".txt", ".md")):
                continue  # 只处理文本和 Markdown 文件
            filepath = os.path.join(self.docs_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
            chunks = self._split_text(text, self.chunk_size)
            for i, chunk in enumerate(chunks):
                self._chunks.append({
                    "text": chunk,
                    "source": filename,  # 记录来源文件名，用于引用说明
                    "index": i,
                })

        if not self._chunks:
            logger.info("No knowledge documents found in %s", self.docs_dir)
            return

        # Compute IDF（计算 IDF：log((总文档数+1) / (含该词的文档数+1)) + 1，加 1 做平滑）
        n_docs = len(self._chunks)
        doc_freq: dict[str, int] = {}  # 每个词出现在多少个文档块中
        for chunk in self._chunks:
            words = set(self._tokenize(chunk["text"]))  # 用集合去重，每块中每个词只计一次
            for w in words:
                doc_freq[w] = doc_freq.get(w, 0) + 1

        self._idf = {
            w: math.log((n_docs + 1) / (df + 1)) + 1
            for w, df in doc_freq.items()
        }

        # Compute TF-IDF vectors for each chunk（为每个块计算 TF-IDF 向量）
        for chunk in self._chunks:
            tf = self._compute_tf(chunk["text"])
            tfidf = {w: freq * self._idf.get(w, 1.0) for w, freq in tf.items()}
            self._tf_idf.append(tfidf)

        logger.info(
            "Knowledge index built: %d chunks from %d files",
            len(self._chunks),
            len(set(c["source"] for c in self._chunks)),
        )

    # ------------------------------------------------------------------
    # Search
    # 搜索
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """
        Retrieve the top-K most relevant chunks for a query.
        检索与查询最相关的 top-K 个文档块。

        Returns a list of dicts: {text, source, score}.
        返回字典列表：{text（文本内容）, source（来源文件）, score（相关性分数）}。
        """
        if not self._chunks:
            return []

        top_k = top_k or self.top_k
        # 计算查询向量的 TF-IDF 表示
        query_tf = self._compute_tf(query)
        query_vec = {w: freq * self._idf.get(w, 1.0) for w, freq in query_tf.items()}

        # 与每个文档块计算余弦相似度
        scores: list[tuple[float, int]] = []
        for idx, chunk_vec in enumerate(self._tf_idf):
            score = self._cosine_similarity(query_vec, chunk_vec)
            if score > 0:
                scores.append((score, idx))

        scores.sort(key=lambda x: x[0], reverse=True)  # 按相似度降序排列

        results = []
        for score, idx in scores[:top_k]:
            results.append({
                "text": self._chunks[idx]["text"],
                "source": self._chunks[idx]["source"],
                "score": round(score, 4),
            })
        return results

    def format_results(self, results: list[dict[str, Any]]) -> str:
        """
        Format search results into a context string for the LLM.
        将搜索结果格式化为供 LLM 使用的上下文字符串。
        """
        if not results:
            return "No relevant knowledge found."
        parts = []
        for i, r in enumerate(results, 1):
            parts.append(
                f"[Knowledge {i}] (source: {r['source']}, relevance: {r['score']})\n"
                f"{r['text']}"
            )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Text processing helpers
    # 文本处理辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """
        Simple word tokenization: lowercase, alphanumeric only.
        简单词分割：转小写，只保留字母和数字。
        注意：此方法仅适用于英文内容；中文等语言需要专用分词器。
        """
        return re.findall(r"[a-z0-9]+", text.lower())

    @staticmethod
    def _compute_tf(text: str) -> dict[str, float]:
        """
        Compute term frequency (normalized).
        计算词频（归一化）：词频 / 最高词频，使 TF 值在 [0,1] 之间。
        """
        words = re.findall(r"[a-z0-9]+", text.lower())
        if not words:
            return {}
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        max_freq = max(freq.values())
        return {w: c / max_freq for w, c in freq.items()}  # 归一化

    @staticmethod
    def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
        """
        Cosine similarity between two sparse vectors.
        计算两个稀疏向量的余弦相似度。
        余弦相似度 = 点积 / (向量A的模 * 向量B的模)。
        只计算共同词（稀疏向量优化），未出现的词贡献为 0。
        """
        common = set(a.keys()) & set(b.keys())
        if not common:
            return 0.0
        dot = sum(a[k] * b[k] for k in common)           # 点积（只对共同词）
        norm_a = math.sqrt(sum(v * v for v in a.values()))  # 向量 A 的 L2 范数
        norm_b = math.sqrt(sum(v * v for v in b.values()))  # 向量 B 的 L2 范数
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _split_text(text: str, chunk_size: int) -> list[str]:
        """
        Split text into chunks at paragraph boundaries.
        按段落边界切分文本为块，尽量不截断段落以保持语义完整性。
        若当前块加下一段落超过 chunk_size，则将当前块存储并开始新块。
        """
        paragraphs = re.split(r"\n\s*\n", text.strip())  # 按空行分段
        chunks: list[str] = []
        current = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) + 1 > chunk_size and current:
                chunks.append(current.strip())  # 当前块已满，保存并开始新块
                current = para
            else:
                current = f"{current}\n{para}" if current else para
        if current.strip():
            chunks.append(current.strip())
        return chunks if chunks else [text.strip()] if text.strip() else []
