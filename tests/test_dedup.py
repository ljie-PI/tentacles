from tentacles.dedup import dedup_articles, title_similarity
from tentacles.models import Article

import pytest


def make_article(title: str, url: str = "https://example.com/a") -> Article:
    return Article(source="test", url=f"{url}?u={abs(hash(title))}", title=title,
                   summary="some summary")


@pytest.mark.parametrize("a,b", [
    ("OpenAI 发布 GPT-5 新模型", "OpenAI 发布 GPT-5 新模型!"),
])
def test_title_similarity(a, b):
    score = title_similarity(a, b)
    assert score > 0.9
    assert score <= 1.0


def test_normalize_url_strips_tracking():
    a = Article(source="t", url="https://example.com/x?utm_source=foo&id=2#top", title="T")
    assert a.url == "https://example.com/x?id=2"


def test_fingerprint_same_title_different_url():
    a = Article(source="t", url="https://a.com/1", title="标题 ABC", summary="内容")
    b = Article(source="u", url="https://b.com/2", title="标题 ABC", summary="内容")
    assert a.fingerprint == b.fingerprint


def test_dedup_keeps_first_of_similar_titles():
    articles = [
        make_article(" officials report major earthquake in Japan"),
        make_article("Officials report major earthquake in Japan"),
        make_article("Khởi động lễ hội mùa xuân"),  # 无关文章应保留
    ]
    kept = dedup_articles(articles)
    assert len(kept) == 2
    assert kept[0].title == articles[0].title
