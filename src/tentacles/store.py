"""SQLite storage. Schema versions are tracked with PRAGMA user_version; each
entry in MIGRATIONS upgrades the schema by one version."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from tentacles.dedup import split_similar
from tentacles.models import Item, item_id
from tentacles.timeutil import from_iso, to_iso


@dataclass
class SourceState:
    source_id: str
    cursor: dict[str, Any] = field(default_factory=dict)
    last_fetch_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    total_items: int = 0


@dataclass
class RunRecord:
    id: int
    kind: str
    target: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    stats: dict[str, Any]
    cost: float | None
    error: str | None


@dataclass
class ItemQuery:
    source_ids: list[str] | None = None
    kinds: list[str] | None = None
    since: datetime | None = None
    until: datetime | None = None
    search: str | None = None
    limit: int = 50
    offset: int = 0


_V1 = """
CREATE TABLE items (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL, external_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', published TEXT,
    fetched_at TEXT NOT NULL, fingerprint TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
    UNIQUE (source_id, external_id)
);
CREATE INDEX idx_items_url ON items (url) WHERE url != '';
CREATE INDEX idx_items_fp ON items (fingerprint);
CREATE INDEX idx_items_time ON items (COALESCE(published, fetched_at));
CREATE INDEX idx_items_fetched ON items (fetched_at);
CREATE TABLE source_state (
    source_id TEXT PRIMARY KEY, cursor TEXT NOT NULL DEFAULT '{}', last_fetch_at TEXT,
    last_status TEXT, last_error TEXT, total_items INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, target TEXT NOT NULL,
    status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    stats TEXT NOT NULL DEFAULT '{}', cost REAL, error TEXT
);
"""


# 旧版 feeds.yaml 中的中文源名 -> 新配置里的信息源 id
_LEGACY_SOURCE_IDS = {"少数派": "sspai", "BBC中文": "bbc-zh", "界面新闻": "jiemian"}


def _migrate_v1(conn: sqlite3.Connection) -> None:
    for stmt in filter(str.strip, _V1.split(";")):
        conn.execute(stmt)
    legacy = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'articles'").fetchone()
    if not legacy:
        return
    # 旧版 RSS 文章表一次性导入为条目；旧表保留不删
    for r in conn.execute("SELECT url, source, title, summary, published, fetched_at, fingerprint "
                          "FROM articles").fetchall():
        source_id = _LEGACY_SOURCE_IDS.get(r[1], r[1])
        conn.execute(
            "INSERT OR IGNORE INTO items (id, source_id, kind, external_id, url, title, summary,"
            " published, fetched_at, fingerprint) VALUES (?, ?, 'rss', ?, ?, ?, ?, ?, ?, ?)",
            (item_id(source_id, r[0]), source_id, r[0], r[0], r[2], r[3] or "",
             _legacy_time(r[4]), _legacy_time(r[5]), r[6]),
        )
    conn.execute("INSERT INTO source_state (source_id, total_items)"
                 " SELECT source_id, COUNT(*) FROM items GROUP BY source_id")


def _legacy_time(s: str | None) -> str | None:
    return to_iso(from_iso(s)) if s else None


def _migrate_v2(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE suppressed_items (source_id TEXT NOT NULL, external_id TEXT NOT NULL,"
                 " PRIMARY KEY (source_id, external_id))")
    # Older skeleton builds kept a bounded list in each source's cursor.
    for row in conn.execute("SELECT source_id, cursor FROM source_state").fetchall():
        cursor = json.loads(row[1])
        suppressed = cursor.pop("_suppressed", [])
        conn.executemany("INSERT OR IGNORE INTO suppressed_items VALUES (?, ?)",
                         [(row[0], external_id) for external_id in suppressed])
        conn.execute("UPDATE source_state SET cursor = ?, total_items ="
                     " (SELECT COUNT(*) FROM items WHERE source_id = ?) WHERE source_id = ?",
                     (json.dumps(cursor, ensure_ascii=False), row[0], row[0]))


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migrate_v1, _migrate_v2]

_ITEM_COLUMNS = ("id, source_id, kind, external_id, url, title, author, summary, content,"
                 " published, fetched_at, fingerprint, meta")
_SORT_TIME = "COALESCE(published, fetched_at)"


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        try:
            self._conn.row_factory = sqlite3.Row
            self._conn.create_function("casefold", 1, str.casefold, deterministic=True)
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    def _migrate(self) -> None:
        """Each migration (DDL included) runs in one explicit transaction, so a
        failure leaves the schema at the previous version and is retryable."""
        with self._lock:
            while True:
                # IMMEDIATE 先拿写锁再读版本：多个进程同时打开新库时只有一个执行迁移
                self._conn.execute("BEGIN IMMEDIATE")
                version = self._conn.execute("PRAGMA user_version").fetchone()[0]
                if version >= len(MIGRATIONS):
                    self._conn.rollback()
                    return
                try:
                    MIGRATIONS[version](self._conn)
                    self._conn.execute(f"PRAGMA user_version = {version + 1}")
                except BaseException:
                    self._conn.rollback()
                    raise
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- items -------------------------------------------------------------

    def upsert_items(self, items: list[Item], *, source_id: str,
                     title_since: datetime | None) -> tuple[list[str], int]:
        """Atomically deduplicate and insert a source's batch, including its counts.

        The write lock precedes all duplicate reads, protecting both concurrent
        worker threads and independent processes sharing the same database.
        """
        fetched = len(items)
        new: list[str] = []
        counts: dict[str, int] = {}
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            suppressed: list[Item] = []
            if items and title_since is not None:
                candidates = [it for it in items if not self._is_duplicate(it)]
                if candidates:
                    items, suppressed = split_similar(candidates, self._recent_titles(
                        exclude_source=source_id, since=title_since))
                else:
                    items = []
            for it in items:
                if self._is_duplicate(it):
                    continue
                self._conn.execute(
                    f"INSERT INTO items ({_ITEM_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (it.id, it.source_id, it.kind, it.external_id, it.url, it.title, it.author,
                     it.summary, it.content, to_iso(it.published), to_iso(it.fetched_at),
                     it.fingerprint, json.dumps(it.meta, ensure_ascii=False)),
                )
                new.append(it.id)
                counts[it.source_id] = counts.get(it.source_id, 0) + 1
            self._conn.executemany(
                "INSERT INTO source_state (source_id, total_items) VALUES (?, ?)"
                " ON CONFLICT (source_id) DO UPDATE SET total_items = total_items + excluded.total_items",
                list(counts.items()))
            self._conn.executemany("INSERT OR IGNORE INTO suppressed_items VALUES (?, ?)",
                                   [(it.source_id, it.external_id) for it in suppressed])
        return new, fetched - len(new)

    # 三条规则各走自己的索引；合成一个 OR 查询会退化为全表扫描
    _SUPPRESSED = "SELECT 1 FROM suppressed_items WHERE source_id = ? AND external_id = ?"
    _DUP_BY_ID = "SELECT 1 FROM items WHERE source_id = ? AND external_id = ?"
    _DUP_BY_URL = "SELECT 1 FROM items WHERE url = ? AND url != ''"
    _DUP_BY_FP = "SELECT 1 FROM items WHERE fingerprint = ? AND kind != 'email'"

    def _is_duplicate(self, it: Item) -> bool:
        c = self._conn
        if c.execute(self._SUPPRESSED, (it.source_id, it.external_id)).fetchone():
            return True
        if c.execute(self._DUP_BY_ID, (it.source_id, it.external_id)).fetchone():
            return True
        if it.url and c.execute(self._DUP_BY_URL, (it.url,)).fetchone():
            return True
        return it.kind != "email" and bool(c.execute(self._DUP_BY_FP, (it.fingerprint,)).fetchone())

    def source_ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT DISTINCT source_id FROM items")}

    def _recent_titles(self, *, exclude_source: str, since: datetime) -> list[str]:
        """Titles of non-email items other sources stored since `since`."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT title FROM items WHERE source_id != ? AND kind != 'email' AND fetched_at >= ?",
                (exclude_source, to_iso(since))).fetchall()
        return [r[0] for r in rows]

    def get_item(self, item_id_: str) -> Item | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_ITEM_COLUMNS} FROM items WHERE id = ?",
                                     (item_id_,)).fetchone()
        return _row_to_item(row) if row else None

    def list_items(self, q: ItemQuery) -> tuple[list[Item], int]:
        where, args = ["1 = 1"], []
        if q.source_ids:
            where.append(f"source_id IN ({','.join('?' * len(q.source_ids))})")
            args += q.source_ids
        if q.kinds:
            where.append(f"kind IN ({','.join('?' * len(q.kinds))})")
            args += q.kinds
        if q.since:
            where.append(f"{_SORT_TIME} >= ?")
            args.append(to_iso(q.since))
        if q.until:
            where.append(f"{_SORT_TIME} < ?")
            args.append(to_iso(q.until))
        if q.search:
            where.append("(instr(casefold(title), ?) > 0 OR instr(casefold(summary), ?) > 0"
                         " OR instr(casefold(author), ?) > 0)")
            args += [q.search.casefold()] * 3
        cond = " AND ".join(where)
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM items WHERE {cond}", args).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_ITEM_COLUMNS} FROM items WHERE {cond}"
                f" ORDER BY {_SORT_TIME} DESC, id LIMIT ? OFFSET ?",
                [*args, q.limit, q.offset],
            ).fetchall()
        return [_row_to_item(r) for r in rows], total

    # ---- source state -----------------------------------------------------

    def get_source_state(self, source_id: str) -> SourceState:
        with self._lock:
            row = self._conn.execute("SELECT * FROM source_state WHERE source_id = ?",
                                     (source_id,)).fetchone()
        if not row:
            return SourceState(source_id)
        return SourceState(source_id, json.loads(row["cursor"]), from_iso(row["last_fetch_at"]),
                           row["last_status"], row["last_error"], row["total_items"])

    def save_source_state(self, st: SourceState) -> None:
        """Saves cursor and status; insertion updates counts in its own transaction."""
        with self._lock, self._conn:
            self._save_source_state(st)

    def _save_source_state(self, st: SourceState) -> None:
        self._conn.execute(
            "INSERT INTO source_state (source_id, cursor, last_fetch_at, last_status,"
            " last_error, total_items) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (source_id) DO UPDATE SET cursor = excluded.cursor,"
            " last_fetch_at = excluded.last_fetch_at, last_status = excluded.last_status,"
            " last_error = excluded.last_error",
            (st.source_id, json.dumps(st.cursor, ensure_ascii=False), to_iso(st.last_fetch_at),
             st.last_status, st.last_error, st.total_items),
        )

    def save_fetch_success(self, st: SourceState, run_id: int, *, now: datetime,
                           stats: dict[str, Any]) -> None:
        """Advance the cursor and finish its run together, or neither on failure."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            self._save_source_state(st)
            self._finish_run(run_id, status="ok", now=now, stats=stats)

    # ---- runs ---------------------------------------------------------------

    def start_run(self, kind: str, target: str, now: datetime) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (kind, target, status, started_at) VALUES (?, ?, 'running', ?)",
                (kind, target, to_iso(now)))
        assert cur.lastrowid is not None
        return cur.lastrowid

    def finish_run(self, run_id: int, *, status: str, now: datetime, stats: dict[str, Any] | None = None,
                   cost: float | None = None, error: str | None = None) -> None:
        with self._lock, self._conn:
            self._finish_run(run_id, status=status, now=now, stats=stats, cost=cost, error=error)

    def _finish_run(self, run_id: int, *, status: str, now: datetime, stats: dict[str, Any] | None = None,
                    cost: float | None = None, error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, finished_at = ?, stats = ?, cost = ?, error = ? WHERE id = ?",
            (status, to_iso(now), json.dumps(stats or {}, ensure_ascii=False), cost, error, run_id))

    def list_runs(self, kind: str | None = None, target: str | None = None,
                  limit: int = 50) -> list[RunRecord]:
        where, args = ["1 = 1"], []
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if target:
            where.append("target = ?")
            args.append(target)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM runs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                [*args, limit]).fetchall()
        return [RunRecord(r["id"], r["kind"], r["target"], r["status"], from_iso(r["started_at"]),
                          from_iso(r["finished_at"]), json.loads(r["stats"]), r["cost"], r["error"])
                for r in rows]


def _row_to_item(r: sqlite3.Row) -> Item:
    return Item(
        id=r["id"], source_id=r["source_id"], kind=r["kind"], external_id=r["external_id"],
        url=r["url"], title=r["title"], author=r["author"], summary=r["summary"],
        content=r["content"], published=from_iso(r["published"]),
        fetched_at=from_iso(r["fetched_at"]), fingerprint=r["fingerprint"],
        meta=json.loads(r["meta"]),
    )
