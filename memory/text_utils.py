"""
Shared text utilities for memory retrieval.
记忆检索共享文本工具。

Bilingual tokenizer used by both LongTermMemory (legacy) and AgenticMemoryStore
so Chinese keyword recall behaves identically across both stores.
双语分词器，供 LongTermMemory（旧版）和 AgenticMemoryStore 共用，
确保中文关键词召回在两套存储中行为一致。
"""

from __future__ import annotations

import re


def bilingual_tokenize(text: str) -> set[str]:
    """
    Bilingual tokenizer: English word tokens + Chinese character bigrams.
    双语分词：英文按空格/单词分词，中文按字符 bigram 分词。

    Chinese text has no whitespace word boundaries, so a plain ``str.split()``
    turns a whole sentence into a single token and any two distinct sentences
    never overlap. Bigrams give partial-overlap matching for CJK.
    中文文本没有空格词边界，单纯 ``str.split()`` 会把整句变成一个 token，
    任意两句都无法重叠；bigram 为 CJK 提供部分重叠匹配能力。
    """
    tokens: set[str] = set()
    # English tokens / 英文分词
    en_words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    tokens.update(en_words)

    # Chinese bigrams / 中文 bigram
    cn_chars = re.findall(r"[一-鿿]", text)
    for i in range(len(cn_chars) - 1):
        tokens.add(f"{cn_chars[i]}{cn_chars[i + 1]}")

    return tokens
