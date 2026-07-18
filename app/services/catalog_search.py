"""Catalog Hybrid 搜索：官方 BM25 排序加中文片段补充召回。"""

import re
from collections.abc import Sequence

from fastmcp.server.transforms.search.base import _extract_searchable_text
from fastmcp.server.transforms.search.bm25 import BM25SearchTransform
from fastmcp.tools import Tool

_CHINESE_RUN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_MAX_CHINESE_NGRAM = 6
_GENERIC_CHINESE_FRAGMENTS = {
    "一个",
    "一下",
    "使用",
    "可以",
    "工具",
    "帮我",
    "想要",
    "查找",
    "查询",
    "获取",
    "进行",
    "需要",
}


def _chinese_ngrams(text: str) -> set[str]:
    """提取长度至少为 2 的中文字符片段，长片段用于提高精确度。"""
    ngrams = set()
    for run in _CHINESE_RUN_PATTERN.findall(text):
        max_size = min(len(run), _MAX_CHINESE_NGRAM)
        for size in range(2, max_size + 1):
            for start in range(len(run) - size + 1):
                ngrams.add(run[start : start + size])
    return ngrams


def _chinese_match_score(query: str, tool: Tool) -> int:
    """按查询与工具中文 n-gram 重叠计算确定性匹配分数。"""
    query_ngrams = _chinese_ngrams(query)
    if not query_ngrams:
        return 0

    text_ngrams = _chinese_ngrams(_extract_searchable_text(tool))
    matches = {
        fragment
        for fragment in query_ngrams & text_ngrams
        if fragment not in _GENERIC_CHINESE_FRAGMENTS
    }
    return sum(len(fragment) ** 2 for fragment in matches)


class HybridBM25SearchTransform(BM25SearchTransform):
    """保留官方 BM25 排序，并用中文片段匹配补充零分候选。"""

    async def _search(
        self,
        tools: Sequence[Tool],
        query: str,
    ) -> Sequence[Tool]:
        bm25_results = list(await super()._search(tools, query))
        selected_names = {tool.name for tool in bm25_results}

        chinese_matches = []
        for position, tool in enumerate(tools):
            if tool.name in selected_names:
                continue
            score = _chinese_match_score(query, tool)
            if score > 0:
                chinese_matches.append((score, position, tool))

        chinese_matches.sort(key=lambda item: (-item[0], item[1]))
        remaining = self._max_results - len(bm25_results)
        if remaining > 0:
            bm25_results.extend(tool for _, _, tool in chinese_matches[:remaining])
        return bm25_results[: self._max_results]
