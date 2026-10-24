from tentacles.cluster import MAJOR_EVENT_SIZE, cluster_articles
from tentacles.models import Article


def make_article(title: str, source: str) -> Article:
    return Article(source=source, url="https://s/" + title, title=title)


def test_same_event_groups_together():
    articles = [
        make_article("央行宣布下调存款利率", "a"),
        make_article("央行宣布下调存款利率，年内第三次", "b"),
        make_article("央行下调存款利率0.25个百分点", "c"),
        make_article("新款折叠手机发布评测", "a"),
    ]
    clusters = cluster_articles(articles)
    assert any(c.size >= 2 for c in clusters)
    assert all("利率" in c.representative.title
               for c in clusters if c.size >= 2)


def test_major_event_sorted_first():
    articles = (
        [make_article("央行宣布降息", s) for s in ("a", "b", "c", "d")]
        + [make_article("今日天气", "a")]
    )
    clusters = cluster_articles(articles)
    assert clusters[0].is_major
    assert clusters[0].size >= MAJOR_EVENT_SIZE
    assert clusters[0].sources == {"a", "b", "c", "d"}
