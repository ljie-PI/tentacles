"""Same-story deduplication: URL/fingerprint handled in the store, here we
collapse near-identical titles that came from different outlets."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from tentacles.models import Article

_PUNCT = re.compile(r"[\s\W]+", re.UNICODE)

# 英文高频虚词，对判断"是否同一事件"没有区分度
_EN_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "then", "as", "at", "by",
    "for", "in", "of", "on", "to", "with", "from", "is", "are", "was",
    "were", "be", "been", "it", "its", "this", "that", "these", "those",
    "we", "our", "us", "you", "your", "they", "their", "them", "he", "she",
    "his", "her", "what", "which", "who", "whom", "how", "why", "not",
    "no", "new", "here", "there", "will", "can", "may", "more", "most",
    "after", "before", "over", "all", "say", "says", "said",
}


def _tokenize(title: str) -> set[str]:
    """简单中英混合分词：英文按词（滤掉虚词）、中文按字 bigram。"""
    text = _PUNCT.sub(" ", title.casefold()).strip()
    tokens: set[str] = set()
    for word in text.split():
        if word.isascii():
            if len(word) >= 2 and word not in _EN_STOP:
                tokens.add(word)
        else:  # 中文，取相邻双字组合
            tokens.update(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


def title_similarity(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    if jaccard == 0.0:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    return 0.7 * jaccard + 0.3 * ratio


def dedup_articles(articles: list[Article], threshold: float = 0.72) -> list[Article]:
    """保留每次出现的第一条，丢弃与其后出现标题高度相似的文章。"""
    kept: list[Article] = []
    titles: list[str] = []
    for article in articles:
        if any(title_similarity(article.title, seen) >= threshold for seen in titles):
            continue
        kept.append(article)
        titles.append(article.title)
    return kept
