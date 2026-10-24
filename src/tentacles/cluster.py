"""Group deduplicated articles into events (simple greedy clustering)."""

from __future__ import annotations

from dataclasses import dataclass, field

from tentacles.dedup import title_similarity
from tentacles.models import Article

MAJOR_EVENT_SIZE = 3        # 同一事件文章数 >= 此值时视为重大事件，触发深度报道
SIMILARITY_THRESHOLD = 0.30  # 标题相似度阈值，低于它不合并


@dataclass
class Cluster:
    representative: Article
    members: list[Article] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.members = [self.representative]
        self.sources = {self.representative.source}

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def is_major(self) -> bool:
        return self.size >= MAJOR_EVENT_SIZE

    def title(self) -> str:
        return self.representative.title


def cluster_articles(articles: list[Article],
                     threshold: float = SIMILARITY_THRESHOLD) -> list[Cluster]:
    """贪心聚类：按来源不同优先，标题与已有点相似即归入该事件。"""
    clusters: list[Cluster] = []
    for article in articles:
        best, best_score = None, 0.0
        for cluster in clusters:
            score = title_similarity(article.title, cluster.representative.title)
            if score > best_score:
                best, best_score = cluster, score
        if best is not None and best_score >= threshold:
            best.members.append(article)
            best.sources.add(article.source)
        else:
            clusters.append(Cluster(representative=article))
    clusters.sort(key=lambda c: (-c.size, -len(c.sources)))
    return clusters
