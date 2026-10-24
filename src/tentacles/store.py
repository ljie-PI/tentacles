"""SQLite storage for fetched articles."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tentacles.models import Article

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    published   TEXT,
    fetched_at  TEXT NOT NULL,
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_fingerprint ON articles (fingerprint);
CREATE INDEX IF NOT EXISTS idx_articles_fetched_at ON articles (fetched_at);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def upsert(self, articles: list[Article]) -> tuple[int, int]:
        """写入文章，返回 (新增, 重复) 数量。"""
        new = dup = 0
        for a in articles:
            cur = self._conn.execute(
                "SELECT 1 FROM articles WHERE url = ? OR fingerprint = ?",
                (a.url, a.fingerprint),
            )
            if cur.fetchone():
                dup += 1
                continue
            self._conn.execute(
                "INSERT INTO articles (id, url, source, title, summary, published, fetched_at, fingerprint)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (a.id, a.url, a.source, a.title, a.summary,
                 a.published.isoformat() if a.published else None,
                 a.fetched_at.isoformat(), a.fingerprint),
            )
            new += 1
        self._conn.commit()
        return new, dup

    def rows_to_articles(self, rows) -> list[Article]:
        out = []
        for r in rows:
            out.append(Article(
                id=r["id"], url=r["url"], source=r["source"], title=r["title"],
                summary=r["summary"],
                published=datetime.fromisoformat(r["published"]) if r["published"] else None,
                fetched_at=datetime.fromisoformat(r["fetched_at"]),
            ))
        return out

    def articles_fetched_since(self, since: datetime) -> list[Article]:
        rows = self._conn.execute(
            "SELECT * FROM articles WHERE fetched_at >= ? ORDER BY fetched_at DESC",
            (since.isoformat(),),
        ).fetchall()
        return self.rows_to_articles(rows)

    def articles_last_n_hours(self, hours: int = 24) -> list[Article]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return self.articles_fetched_since(cutoff)

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
