"""SQLite storage. Schema versions are tracked with PRAGMA user_version; each
entry in MIGRATIONS upgrades the schema by one version."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from time import monotonic, sleep
from dataclasses import dataclass, field
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from tentacles.dedup import split_similar
from tentacles.models import Item, item_id
from tentacles.errors import Conflict
from tentacles.personas.models import FeedbackView, PersonaEdit, PersonaView, edit_keywords, keywords
from tentacles.timeutil import from_iso, to_iso, to_utc
from tentacles.tasks import Claim, Task, TaskState
from tentacles.pipelines.models import Evaluation, PipelineQuery, ReportView

log = logging.getLogger(__name__)


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
    pipeline_id: str | None = None
    passed: bool | None = None


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


def _migrate_v3(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE task_state (
        kind TEXT NOT NULL, target TEXT NOT NULL, first_seen_at TEXT NOT NULL,
        last_run_at TEXT, last_status TEXT, last_error TEXT, token TEXT,
        lease_until TEXT, started_at TEXT, run_id INTEGER,
        PRIMARY KEY (kind, target)
    )""")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE persona_versions (
        name TEXT NOT NULL, version INTEGER NOT NULL, profile TEXT NOT NULL,
        tags TEXT NOT NULL, include_keywords TEXT NOT NULL, exclude_keywords TEXT NOT NULL,
        source TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
        tag_status TEXT NOT NULL, tag_error TEXT, PRIMARY KEY (name, version)
    )""")


def _migrate_v5(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE evaluations (
        pipeline_id TEXT NOT NULL, item_id TEXT NOT NULL, persona_version INTEGER NOT NULL,
        passed INTEGER NOT NULL, score REAL NOT NULL, tags TEXT NOT NULL, reason TEXT NOT NULL,
        stage TEXT NOT NULL, stage_scores TEXT NOT NULL, evaluated_at TEXT NOT NULL, reported_at TEXT,
        PRIMARY KEY (pipeline_id, item_id, persona_version)
    )""")
    conn.execute('CREATE INDEX idx_evaluations_pending ON evaluations'
                 ' (pipeline_id, persona_version, passed, reported_at, score DESC)')
    conn.execute("""CREATE TABLE reports (
        id TEXT PRIMARY KEY, pipeline_id TEXT NOT NULL, persona_version INTEGER NOT NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL, markdown TEXT NOT NULL, item_ids TEXT NOT NULL,
        model TEXT, usage TEXT NOT NULL, cost REAL, path TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    conn.execute('CREATE INDEX idx_reports_time ON reports (created_at DESC, id DESC)')
    conn.execute('CREATE INDEX idx_reports_pipeline ON reports (pipeline_id, created_at DESC, id DESC)')


def _migrate_v6(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE pipeline_consumption (
        pipeline_id TEXT NOT NULL, item_id TEXT NOT NULL, consumed_at TEXT NOT NULL,
        report_id TEXT, PRIMARY KEY (pipeline_id, item_id)
    )""")
    conn.execute('INSERT INTO pipeline_consumption (pipeline_id, item_id, consumed_at)'
                 ' SELECT pipeline_id, item_id, MIN(reported_at) FROM evaluations'
                 ' WHERE reported_at IS NOT NULL GROUP BY pipeline_id, item_id')
    # A pre-ledger explicit re-evaluation may have deleted its reported marker.
    # Reports still contain the immutable material IDs, so recover those too.
    for row in conn.execute('SELECT id, pipeline_id, item_ids, created_at FROM reports'
                            ' ORDER BY created_at, id').fetchall():
        for item_id in json.loads(row['item_ids']):
            conn.execute('INSERT INTO pipeline_consumption VALUES (?, ?, ?, ?)'
                ' ON CONFLICT (pipeline_id, item_id) DO UPDATE SET'
                ' consumed_at = MIN(consumed_at, excluded.consumed_at),'
                ' report_id = COALESCE(report_id, excluded.report_id)',
                (row['pipeline_id'], item_id, row['created_at'], row['id']))
    conn.execute('UPDATE evaluations SET reported_at = (SELECT consumed_at FROM pipeline_consumption c'
                 ' WHERE c.pipeline_id = evaluations.pipeline_id AND c.item_id = evaluations.item_id)'
                 ' WHERE EXISTS (SELECT 1 FROM pipeline_consumption c WHERE c.pipeline_id = evaluations.pipeline_id'
                 ' AND c.item_id = evaluations.item_id)')


def _migrate_v7(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE pipeline_schedule_state (
        kind TEXT NOT NULL CHECK (kind IN ('filter', 'pipeline')), target TEXT NOT NULL,
        active INTEGER NOT NULL CHECK (active IN (0, 1)), PRIMARY KEY (kind, target)
    )""")


def _migrate_v8(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE feedback (
        id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, persona TEXT NOT NULL,
        rating INTEGER NOT NULL CHECK (rating IN (-1, 1)), note TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute('CREATE INDEX feedback_persona_time ON feedback (persona, created_at, id)')


def _migrate_v9(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE items ADD COLUMN identity_policy TEXT NOT NULL DEFAULT 'standard'"
                 " CHECK (identity_policy IN ('standard', 'revision'))")
    conn.execute("CREATE INDEX idx_items_article_url ON items (url)"
                 " WHERE url != '' AND identity_policy = 'standard'")
    conn.execute("CREATE INDEX idx_items_article_fp ON items (fingerprint)"
                 " WHERE kind != 'email' AND identity_policy = 'standard'")


def _migrate_v10(conn: sqlite3.Connection) -> None:
    # The cursor is only a 500-id cache; observations survive eviction and
    # article deduplication, so old links never trigger detail downloads again.
    conn.execute("""CREATE TABLE source_seen_ids (
        source_id TEXT NOT NULL, external_id TEXT NOT NULL,
        PRIMARY KEY (source_id, external_id)
    )""")


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_migrate_v1, _migrate_v2, _migrate_v3, _migrate_v4, _migrate_v5, _migrate_v6, _migrate_v7, _migrate_v8, _migrate_v9, _migrate_v10]

_ITEM_COLUMNS = ("id, source_id, kind, external_id, url, title, author, summary, content,"
                 " published, fetched_at, fingerprint, meta, identity_policy")
_EVAL_COLUMNS = ('pipeline_id', 'item_id', 'persona_version', 'passed', 'score', 'tags',
                 'reason', 'stage', 'stage_scores', 'evaluated_at', 'reported_at')
_EVAL_PROJECTION = ', '.join(f'e.{column} AS eval_{column}' for column in _EVAL_COLUMNS)
_SORT_TIME = "COALESCE(published, fetched_at)"


class Store:
    def __init__(self, path: str | Path, *, snapshot: bool = False):
        self.path = Path(path)
        if not snapshot:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(':memory:' if snapshot else self.path, check_same_thread=False)
        try:
            if snapshot and self.path.exists():
                # Copy through SQLite's backup API, including live WAL data. All
                # migrations/seeds/task initialization then affect memory only.
                deadline = monotonic() + 5

                def progress(status: int, remaining: int, total: int) -> None:
                    if monotonic() >= deadline:
                        raise sqlite3.OperationalError('database snapshot timeout')

                with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
                    source.backup(self._conn, pages=256, progress=progress)
            self._conn.row_factory = sqlite3.Row
            self._conn.create_function("casefold", 1, str.casefold, deterministic=True)
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._enable_wal()
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    def _enable_wal(self) -> None:
        """Retry initial mode-switch races within a five-second IO budget.

        A transient lock upgrade can fail without invoking SQLite's busy handler.
        The monotonic resource budget is independent of the domain clock.
        """
        deadline = monotonic() + 5
        last_error: sqlite3.OperationalError | None = None
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                if last_error is not None:
                    raise last_error
                raise sqlite3.OperationalError('database initialization lock timeout')
            # A retry must not start another full five-second native wait.
            self._conn.execute(f'PRAGMA busy_timeout = {max(1, int(remaining * 1000))}')
            try:
                self._conn.execute('PRAGMA journal_mode = WAL')
            except sqlite3.OperationalError as exc:
                # Error names are available on newer Python; 3.10 needs text.
                name = str(getattr(exc, 'sqlite_errorname', '') or '')
                message = str(exc).casefold()
                transient = name.startswith(('SQLITE_BUSY', 'SQLITE_LOCKED')) or 'locked' in message or 'busy' in message
                remaining = deadline - monotonic()
                if not transient or remaining <= 0:
                    raise
                last_error = exc
                sleep(min(0.01, remaining))
            else:
                self._conn.execute('PRAGMA busy_timeout = 5000')
                return

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

    # ---- pipeline evaluations ----------------------------------------------

    def filter_candidates(self, q: PipelineQuery, *, reevaluate: bool = False,
                          run_id: int | None = None) -> list[Item]:
        placeholders = ','.join('?' for _ in q.sources)
        with self._lock, self._conn:
            if reevaluate:
                assert run_id is not None
                self._conn.execute('BEGIN IMMEDIATE')
                self._require_running(run_id)
                self._conn.execute('DELETE FROM evaluations WHERE pipeline_id = ? AND persona_version = ?'
                    f' AND item_id IN (SELECT id FROM items WHERE source_id IN ({placeholders})'
                    ' AND fetched_at >= ? AND fetched_at <= ?)',
                    [q.pipeline_id, q.persona_version, *q.sources, to_iso(q.since), to_iso(q.until)])
            rows = self._conn.execute(
                f"SELECT {_ITEM_COLUMNS} FROM items WHERE source_id IN ({placeholders})"
                " AND fetched_at >= ? AND fetched_at <= ? AND NOT EXISTS"
                " (SELECT 1 FROM evaluations e WHERE e.item_id = items.id AND e.pipeline_id = ?"
                " AND e.persona_version = ?) ORDER BY fetched_at DESC, id LIMIT ?",
                [*q.sources, to_iso(q.since), to_iso(q.until), q.pipeline_id, q.persona_version, q.limit]).fetchall()
        return [_row_to_item(row) for row in rows]

    @staticmethod
    def _pending_from(q: PipelineQuery) -> tuple[str, list[Any]]:
        placeholders = ','.join('?' for _ in q.sources)
        sql = (' FROM items i JOIN evaluations e ON e.item_id = i.id'
               f' WHERE e.pipeline_id = ? AND e.persona_version = ? AND i.source_id IN ({placeholders})'
               ' AND i.fetched_at >= ? AND i.fetched_at <= ? AND e.passed = 1 AND e.reported_at IS NULL'
               ' AND NOT EXISTS (SELECT 1 FROM pipeline_consumption c'
               ' WHERE c.pipeline_id = e.pipeline_id AND c.item_id = e.item_id)')
        return sql, [q.pipeline_id, q.persona_version, *q.sources, to_iso(q.since), to_iso(q.until)]

    def pending_items(self, q: PipelineQuery) -> list[tuple[Item, Evaluation]]:
        sql, args = self._pending_from(q)
        with self._lock:
            rows = self._conn.execute(f'SELECT i.*, {_EVAL_PROJECTION}' + sql +
                ' ORDER BY e.score DESC, i.fetched_at DESC, i.id LIMIT ?', [*args, q.limit]).fetchall()
        return [(_row_to_item(row), _row_to_evaluation(row, 'eval_')) for row in rows]

    def pending_count(self, q: PipelineQuery) -> int:
        sql, args = self._pending_from(q)
        with self._lock:
            return self._conn.execute('SELECT COUNT(*)' + sql, args).fetchone()[0]

    def pipeline_ids(self) -> set[str]:
        with self._lock:
            return {row[0] for row in self._conn.execute(
                'SELECT DISTINCT pipeline_id FROM evaluations UNION SELECT DISTINCT pipeline_id FROM reports'
                ' UNION SELECT DISTINCT pipeline_id FROM pipeline_consumption')}

    def _check_pending(self, evaluations: list[Evaluation]) -> None:
        for evaluation in evaluations:
            row = self._conn.execute(
                'SELECT * FROM evaluations WHERE pipeline_id = ? AND item_id = ? AND persona_version = ?'
                ' AND NOT EXISTS (SELECT 1 FROM pipeline_consumption c'
                ' WHERE c.pipeline_id = evaluations.pipeline_id AND c.item_id = evaluations.item_id)',
                (evaluation.pipeline_id, evaluation.item_id, evaluation.persona_version)).fetchone()
            if row is None or _row_to_evaluation(row) != evaluation or evaluation.reported_at is not None:
                raise Conflict('evaluations changed while preparing the report')

    def _consume(self, evaluations: list[Evaluation], now: datetime, report_id: str | None = None) -> None:
        self._conn.executemany('INSERT INTO pipeline_consumption VALUES (?, ?, ?, ?)',
            [(e.pipeline_id, e.item_id, to_iso(now), report_id) for e in evaluations])
        self._conn.executemany('UPDATE evaluations SET reported_at = ? WHERE pipeline_id = ? AND item_id = ?',
            [(to_iso(now), e.pipeline_id, e.item_id) for e in evaluations])

    def save_report(self, report: ReportView, evaluations: list[Evaluation], run_id: int,
                    stats: dict[str, Any], cost: float | None = None) -> None:
        """Publish one exclusive file and its database record/consumption together.

        A failed SQL commit removes only the file created by this operation.
        Worker IO is drained by the caller before cancellation may close Store.
        """
        path, created = Path(report.path), False
        try:
            with self._lock, self._conn:
                self._conn.execute('BEGIN IMMEDIATE')
                self._require_running(run_id)
                self._check_pending(evaluations)
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, 'x', encoding='utf-8',
                          opener=lambda target, flags: os.open(target, flags, 0o600)) as handle:
                    created = True
                    handle.write(report.markdown)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._conn.execute('INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (report.id, report.pipeline_id, report.persona_version, report.kind, report.title,
                     report.markdown, json.dumps(report.item_ids), report.model,
                     report.usage.model_dump_json(), report.cost, report.path, to_iso(report.created_at)))
                self._consume(evaluations, report.created_at, report.id)
                self._finish_run(run_id, status='ok', now=report.created_at, stats=stats, cost=cost if cost is not None else report.cost)
                self._complete_task_run(run_id, 'ok', None)
        except BaseException:
            if created:
                try:
                    path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    log.warning('could not remove uncommitted report file (%s)', type(cleanup_error).__name__)
                    # The original failure remains visible; never remove an unowned file.
            raise

    def consume_without_report(self, evaluations: list[Evaluation], run_id: int, now: datetime,
                               stats: dict[str, Any], cost: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            self._require_running(run_id)
            self._check_pending(evaluations)
            self._consume(evaluations, now)
            self._finish_run(run_id, status='ok', now=now, stats=stats, cost=cost)
            self._complete_task_run(run_id, 'ok', None)

    def list_reports(self, pipeline_id: str | None, limit: int, offset: int) -> tuple[list[ReportView], int]:
        condition = 'pipeline_id = ?' if pipeline_id is not None else '1 = 1'
        args = [pipeline_id] if pipeline_id is not None else []
        with self._lock:
            total = self._conn.execute('SELECT COUNT(*) FROM reports WHERE ' + condition, args).fetchone()[0]
            rows = self._conn.execute('SELECT * FROM reports WHERE ' + condition +
                ' ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        return [_row_to_report(row) for row in rows], total

    def get_report(self, report_id: str) -> ReportView | None:
        with self._lock:
            row = self._conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
        return _row_to_report(row) if row else None

    def save_evaluations(self, evaluations: list[Evaluation], run_id: int, now: datetime,
                         stats: dict[str, Any], cost: float | None = None, error: str | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            self._require_running(run_id)
            for evaluation in evaluations:
                self._conn.execute(
                    'INSERT OR IGNORE INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,'
                    ' (SELECT consumed_at FROM pipeline_consumption WHERE pipeline_id = ? AND item_id = ?))',
                    (evaluation.pipeline_id, evaluation.item_id, evaluation.persona_version,
                     evaluation.passed, evaluation.score, json.dumps(evaluation.tags, ensure_ascii=False),
                     evaluation.reason, evaluation.stage,
                     json.dumps([s.model_dump() for s in evaluation.stage_scores], ensure_ascii=False),
                     to_iso(evaluation.evaluated_at), evaluation.pipeline_id, evaluation.item_id))
            status = 'failed' if error is not None else 'ok'
            self._finish_run(run_id, status=status, now=now, stats=stats, cost=cost, error=error)
            self._complete_task_run(run_id, status, error)

    def item_evaluations(self, item_id: str, pipeline_id: str | None = None) -> list[Evaluation]:
        where = 'e.item_id = ?'
        args = [item_id]
        if pipeline_id is not None:
            where += ' AND e.pipeline_id = ?'
            args.append(pipeline_id)
        with self._lock:
            rows = self._conn.execute(
                'SELECT e.* FROM evaluations e WHERE ' + where +
                ' AND e.persona_version = (SELECT MAX(newer.persona_version) FROM evaluations newer'
                ' WHERE newer.pipeline_id = e.pipeline_id AND newer.item_id = e.item_id)'
                ' ORDER BY e.pipeline_id', args).fetchall()
        return [_row_to_evaluation(row) for row in rows]

    def submit_feedback(self, item_id: str, persona: str, rating: int, note: str,
                        now: datetime) -> FeedbackView:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                'INSERT INTO feedback (item_id, persona, rating, note, created_at) VALUES (?, ?, ?, ?, ?)',
                (item_id, persona, rating, note, to_iso(now)))
            return FeedbackView.model_validate(dict(id=cursor.lastrowid, item_id=item_id, persona=persona,
                                                    rating=rating, note=note, created_at=to_utc(now)))

    def eligible_feedback(self, persona: str, since: datetime, until: datetime, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.id, f.item_id, f.rating, substr(f.note, 1, 2000) AS note, f.created_at,"
                " substr(i.title, 1, 500) AS title, substr(i.summary, 1, 2000) AS summary"
                " FROM feedback f JOIN items i ON i.id = f.item_id"
                " WHERE f.persona = ? AND f.created_at >= ? AND f.created_at <= ?"
                " AND f.created_at > COALESCE((SELECT created_at FROM persona_versions"
                " WHERE name = ? AND source = 'llm' ORDER BY version DESC LIMIT 1), '')"
                " ORDER BY f.created_at DESC, f.id DESC LIMIT ?",
                (persona, to_iso(since), to_iso(until), persona, limit)).fetchall()
        return [dict(row) for row in rows]

    # ---- versioned personas ------------------------------------------------

    def seed_personas(self, seeds: list[tuple[str, Callable[[], PersonaView]]]) -> None:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            for name, load in seeds:
                if self.get_persona(name) is None:
                    self._insert_persona(load())

    def get_persona(self, name: str, version: int | None = None) -> PersonaView | None:
        with self._lock:
            if version is None:
                row = self._conn.execute(
                    "SELECT * FROM persona_versions WHERE name = ? ORDER BY version DESC LIMIT 1",
                    (name,)).fetchone()
            else:
                row = self._conn.execute("SELECT * FROM persona_versions WHERE name = ? AND version = ?",
                                         (name, version)).fetchone()
        return _row_to_persona(row) if row else None

    def list_personas(self) -> list[PersonaView]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.* FROM persona_versions p JOIN"
                " (SELECT name, MAX(version) AS version FROM persona_versions GROUP BY name) latest"
                " USING (name, version) ORDER BY name").fetchall()
        return [_row_to_persona(row) for row in rows]

    def persona_history(self, name: str) -> list[PersonaView]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM persona_versions WHERE name = ? ORDER BY version DESC",
                                      (name,)).fetchall()
        return [_row_to_persona(row) for row in rows]

    def save_persona(self, edit: PersonaEdit, now: datetime, run_id: int) -> PersonaView:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            old = self.get_persona(edit.name)
            version = old.version if old else 0
            if edit.expected_version is not None and edit.expected_version != version:
                raise Conflict('persona changed while preparing the edit; refresh and retry')
            view = PersonaView(
                name=edit.name, version=version + 1,
                profile=edit.profile if edit.profile is not None else old.profile if old else '',
                tags=edit.tags if edit.tags is not None else old.tags if old else [],
                include=keywords(edit.include_replace) if edit.include_replace is not None else
                        edit_keywords(old.include if old else [], edit.include_add, edit.include_remove),
                exclude=keywords(edit.exclude_replace) if edit.exclude_replace is not None else
                        edit_keywords(old.exclude if old else [], edit.exclude_add, edit.exclude_remove),
                source=edit.source, note=edit.note, created_at=to_utc(now),
                tag_status=edit.tag_status, tag_error=edit.tag_error)
            self._insert_persona(view)
            self._finish_run(run_id, status='ok', now=now, stats={
                'version': view.version, 'tag_status': view.tag_status, 'tag_error': view.tag_error, **edit.run_stats},
                             cost=edit.cost, error=edit.tag_error)
            return view

    def _insert_persona(self, view: PersonaView) -> None:
        self._conn.execute(
            "INSERT INTO persona_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (view.name, view.version, view.profile,
             json.dumps([tag.model_dump() for tag in view.tags], ensure_ascii=False),
             json.dumps(view.include, ensure_ascii=False), json.dumps(view.exclude, ensure_ascii=False),
             view.source, view.note, to_iso(view.created_at), view.tag_status, view.tag_error))

    # ---- scheduler tasks ---------------------------------------------------

    def register_tasks(self, tasks: list[Task], now: datetime) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO task_state (kind, target, first_seen_at) VALUES (?, ?, ?)",
                [(task.kind, task.target, to_iso(now)) for task in tasks])
            for task in tasks:
                self._reconcile_completed_task(task)

    def sync_pipeline_schedules(self, tasks: list[Task], now: datetime) -> None:
        """Enroll scheduled pipeline roles without confusing them with manual locks.

        A never-run cron job begins at first activation, not when its reusable
        manual lock happened to be registered. Executed tasks retain progress.
        Only supplied identities are touched; a history-only config is harmless.
        """
        if not tasks:
            return
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            for task in tasks:
                self._conn.execute('INSERT OR IGNORE INTO task_state (kind, target, first_seen_at) VALUES (?, ?, ?)',
                                   (task.kind, task.target, to_iso(now)))
                state = self._reconcile_completed_task(task)
                previous = self._conn.execute('SELECT active FROM pipeline_schedule_state WHERE kind = ? AND target = ?',
                                              (task.kind, task.target)).fetchone()
                was_active = bool(previous['active']) if previous is not None else False
                if task.enabled and not was_active and state.last_run_at is None and state.token is None:
                    self._conn.execute('UPDATE task_state SET first_seen_at = ? WHERE kind = ? AND target = ?',
                                       (to_iso(now), task.kind, task.target))
                self._conn.execute('INSERT INTO pipeline_schedule_state VALUES (?, ?, ?)'
                    ' ON CONFLICT (kind, target) DO UPDATE SET active = excluded.active'
                    ' WHERE active != excluded.active', (task.kind, task.target, task.enabled))

    def get_task_state(self, task: Task) -> TaskState:
        with self._lock:
            row = self._conn.execute("SELECT * FROM task_state WHERE kind = ? AND target = ?",
                                     (task.kind, task.target)).fetchone()
        assert row is not None, "task must be registered before use"
        return TaskState(from_iso(row['first_seen_at']), from_iso(row['last_run_at']),
                         row['last_status'], row['last_error'], row['token'],
                         from_iso(row['lease_until']), from_iso(row['started_at']), row['run_id'])

    def claim_task(self, task: Task, token: str, now: datetime, *, force: bool = False,
                   lease: timedelta = timedelta(hours=1)) -> Claim:
        """Serialize the due check, lease acquisition and run creation across processes."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            state = self._reconcile_completed_task(task)
            if state.running(now):
                return Claim('busy')
            # An abandoned run is retried once its lease expires, even for a long interval.
            if not force and state.token is None and state.next_run(task) > now:
                return Claim('not_due')
            if state.run_id is not None and state.token is not None:
                self._finish_run(state.run_id, status='failed', now=now, error='task lease expired')
                self._complete_task_run(state.run_id, 'failed', 'task lease expired')
            cur = self._conn.execute(
                "INSERT INTO runs (kind, target, status, started_at) VALUES (?, ?, 'running', ?)",
                (task.kind, task.target, to_iso(now)))
            run_id = cur.lastrowid
            self._conn.execute(
                "UPDATE task_state SET token = ?, lease_until = ?, started_at = ?, run_id = ?"
                " WHERE kind = ? AND target = ?",
                (token, to_iso(now + lease), to_iso(now), run_id, task.kind, task.target))
            return Claim('ready', run_id)

    def finish_task(self, task: Task, token: str, now: datetime) -> None:
        """The owner alone releases a lease. A committed successful run stays successful."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            state = self.get_task_state(task)
            if state.token != token:
                return
            run = self._conn.execute("SELECT status, error FROM runs WHERE id = ?",
                                     (state.run_id,)).fetchone()
            status, error = run['status'], run['error']
            if status == 'running':
                status, error = 'failed', 'task cancelled before completion'
                assert state.run_id is not None
                self._finish_run(state.run_id, status=status, now=now, error=error)
                if task.kind == 'fetch':
                    self._conn.execute(
                        "INSERT INTO source_state (source_id, last_fetch_at, last_status, last_error)"
                        " VALUES (?, ?, 'error', ?) ON CONFLICT (source_id) DO UPDATE SET"
                        " last_fetch_at = excluded.last_fetch_at, last_status = excluded.last_status,"
                        " last_error = excluded.last_error",
                        (task.target, to_iso(state.started_at), error))
            assert state.run_id is not None
            self._complete_task_run(state.run_id, status, error)

    def _complete_task_run(self, run_id: int, status: str, error: str | None) -> None:
        """Advance scheduling alongside run/cursor completion in the caller's transaction."""
        self._conn.execute(
            "UPDATE task_state SET last_run_at = started_at, last_status = ?, last_error = ?,"
            " token = NULL, lease_until = NULL, started_at = NULL, run_id = NULL WHERE run_id = ?",
            ('ok' if status == 'ok' else 'error', error, run_id))

    def _reconcile_completed_task(self, task: Task) -> TaskState:
        state = self.get_task_state(task)
        if state.token is not None and state.run_id is not None:
            run = self._conn.execute("SELECT status, error FROM runs WHERE id = ?",
                                     (state.run_id,)).fetchone()
            if run is not None and run['status'] != 'running':
                # Repair a terminal run left with a lease by an interrupted older writer.
                self._complete_task_run(state.run_id, run['status'], run['error'])
                state = self.get_task_state(task)
        return state

    # ---- items -------------------------------------------------------------

    def seen_links(self, source_id: str, urls: list[str]) -> set[str]:
        known: set[str] = set()
        with self._lock:
            for offset in range(0, len(urls), 500):
                chunk = urls[offset:offset + 500]
                placeholders = ','.join('?' for _ in chunk)
                for table in ('source_seen_ids', 'suppressed_items'):
                    rows = self._conn.execute(f'SELECT external_id FROM {table}'
                        f' WHERE source_id = ? AND external_id IN ({placeholders})', [source_id, *chunk]).fetchall()
                    known.update(row[0] for row in rows)
                rows = self._conn.execute(f"SELECT url FROM items WHERE identity_policy = 'standard'"
                    f" AND url != '' AND url IN ({placeholders})", chunk).fetchall()
                known.update(row[0] for row in rows)
        return known

    def upsert_items(self, items: list[Item], *, source_id: str,
                     title_since: datetime | None, run_id: int | None = None) -> tuple[list[str], int]:
        """Atomically deduplicate and insert a source's batch, including its counts.

        The write lock precedes all duplicate reads, protecting both concurrent
        worker threads and independent processes sharing the same database.
        """
        fetched = len(items)
        new: list[str] = []
        counts: dict[str, int] = {}
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            self._require_running(run_id)
            self._conn.executemany('INSERT OR IGNORE INTO source_seen_ids VALUES (?, ?)',
                [(source_id, it.external_id) for it in items if it.kind == 'web' and it.identity_policy == 'standard'])
            suppressed: list[Item] = []
            if items and title_since is not None:
                candidates = [it for it in items if not self._is_duplicate(it)]
                if candidates:
                    ordinary = [it for it in candidates if it.identity_policy == 'standard']
                    kept, suppressed = split_similar(ordinary, self._recent_titles(
                        exclude_source=source_id, since=title_since))
                    allowed = {it.id for it in kept}
                    items = [it for it in candidates if it.identity_policy == 'revision' or it.id in allowed]
                else:
                    items = []
            for it in items:
                if self._is_duplicate(it):
                    continue
                self._conn.execute(
                    f"INSERT INTO items ({_ITEM_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (it.id, it.source_id, it.kind, it.external_id, it.url, it.title, it.author,
                     it.summary, it.content, to_iso(it.published), to_iso(it.fetched_at),
                     it.fingerprint, json.dumps(it.meta, ensure_ascii=False), it.identity_policy),
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
    _DUP_BY_URL = "SELECT 1 FROM items WHERE url = ? AND url != '' AND identity_policy = 'standard'"
    _DUP_BY_FP = "SELECT 1 FROM items WHERE fingerprint = ? AND kind != 'email' AND identity_policy = 'standard'"

    def _is_duplicate(self, it: Item) -> bool:
        c = self._conn
        if it.identity_policy == 'standard' and c.execute(self._SUPPRESSED, (it.source_id, it.external_id)).fetchone():
            return True
        if c.execute(self._DUP_BY_ID, (it.source_id, it.external_id)).fetchone():
            return True
        if it.identity_policy == 'revision':
            return False
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
                "SELECT title FROM items WHERE source_id != ? AND kind != 'email' AND identity_policy = 'standard' AND fetched_at >= ?",
                (exclude_source, to_iso(since))).fetchall()
        return [r[0] for r in rows]

    def get_item(self, item_id_: str) -> Item | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_ITEM_COLUMNS} FROM items WHERE id = ?",
                                     (item_id_,)).fetchone()
        return _row_to_item(row) if row else None

    def list_items(self, q: ItemQuery) -> tuple[list[tuple[Item, Evaluation | None]], int]:
        where = ["1 = 1"]
        args: list[Any] = []
        joined = ''
        extra_columns = ''
        if q.pipeline_id is not None:
            joined = (' JOIN evaluations e ON e.item_id = items.id AND e.pipeline_id = ?'
                      ' AND e.persona_version = (SELECT MAX(n.persona_version) FROM evaluations n'
                      ' WHERE n.pipeline_id = e.pipeline_id AND n.item_id = e.item_id)')
            args.append(q.pipeline_id)
            extra_columns = ', ' + _EVAL_PROJECTION
            if q.passed is not None:
                where.append('e.passed = ?')
                args.append(q.passed)
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
            total = self._conn.execute(f"SELECT COUNT(*) FROM items{joined} WHERE {cond}", args).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {_ITEM_COLUMNS}{extra_columns} FROM items{joined} WHERE {cond}"
                f" ORDER BY {_SORT_TIME} DESC, id LIMIT ? OFFSET ?",
                [*args, q.limit, q.offset],
            ).fetchall()
        return [(_row_to_item(r), _row_to_evaluation(r, "eval_")
                 if q.pipeline_id is not None else None) for r in rows], total

    # ---- source state -----------------------------------------------------

    def get_source_state(self, source_id: str) -> SourceState:
        with self._lock:
            row = self._conn.execute("SELECT * FROM source_state WHERE source_id = ?",
                                     (source_id,)).fetchone()
        if not row:
            return SourceState(source_id)
        return SourceState(source_id, json.loads(row["cursor"]), from_iso(row["last_fetch_at"]),
                           row["last_status"], row["last_error"], row["total_items"])

    def save_fetch_success(self, st: SourceState, run_id: int, *, now: datetime,
                           stats: dict[str, Any]) -> None:
        """Advance the cursor and finish its run together, or neither on failure."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            self._require_running(run_id)
            self._conn.execute(
                "INSERT INTO source_state (source_id, cursor, last_fetch_at, last_status,"
                " last_error, total_items) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (source_id) DO UPDATE SET cursor = excluded.cursor,"
                " last_fetch_at = excluded.last_fetch_at, last_status = excluded.last_status,"
                " last_error = excluded.last_error",
                (st.source_id, json.dumps(st.cursor, ensure_ascii=False), to_iso(st.last_fetch_at),
                 st.last_status, st.last_error, st.total_items),
            )
            self._finish_run(run_id, status="ok", now=now, stats=stats)
            self._complete_task_run(run_id, 'ok', None)

    def save_fetch_failure(self, source_id: str, run_id: int | None, *, started_at: datetime,
                           finished_at: datetime, error: str) -> None:
        """Record failure without overwriting a cursor committed by another fetch."""
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            if run_id is not None and not self._run_is_running(run_id):
                return  # an expired owner must not overwrite the replacement's source state
            self._conn.execute(
                "INSERT INTO source_state (source_id, last_fetch_at, last_status, last_error)"
                " VALUES (?, ?, 'error', ?) ON CONFLICT (source_id) DO UPDATE SET"
                " last_fetch_at = excluded.last_fetch_at, last_status = excluded.last_status,"
                " last_error = excluded.last_error", (source_id, to_iso(started_at), error))
            if run_id is not None:
                self._finish_run(run_id, status="failed", now=finished_at, error=error)
                self._complete_task_run(run_id, 'failed', error)

    # ---- runs ---------------------------------------------------------------

    def start_run(self, kind: str, target: str, now: datetime) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs (kind, target, status, started_at) VALUES (?, ?, 'running', ?)",
                (kind, target, to_iso(now)))
        assert cur.lastrowid is not None
        return cur.lastrowid

    def finish_run(self, run_id: int, *, status: str, now: datetime, error: str | None = None,
                   stats: dict[str, Any] | None = None, cost: float | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            previous = self._conn.execute('SELECT status FROM runs WHERE id = ?', (run_id,)).fetchone()
            self._finish_run(run_id, status=status, now=now, error=error, stats=stats, cost=cost)
            if previous is not None and previous['status'] == status == 'failed' and stats is not None:
                # A late response may reveal billed usage after lease reclamation.
                # Update that old run's metrics only, never its status or replacement task.
                self._conn.execute('UPDATE runs SET stats = ?, cost = COALESCE(?, cost) WHERE id = ?',
                                   (json.dumps(stats, ensure_ascii=False), cost, run_id))
            actual = self._conn.execute('SELECT status, error FROM runs WHERE id = ?', (run_id,)).fetchone()
            if actual is not None and actual['status'] != 'running':
                self._complete_task_run(run_id, actual['status'], actual['error'])

    def _run_is_running(self, run_id: int) -> bool:
        row = self._conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row is not None and row['status'] == 'running'

    def _require_running(self, run_id: int | None) -> None:
        if run_id is not None and not self._run_is_running(run_id):
            raise Conflict('run no longer owns its task lease')

    def _finish_run(self, run_id: int, *, status: str, now: datetime, stats: dict[str, Any] | None = None,
                    cost: float | None = None, error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, finished_at = ?, stats = ?, cost = ?, error = ? WHERE id = ? AND status = 'running'",
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
        meta=json.loads(r["meta"]), identity_policy=r["identity_policy"],
    )


def _row_to_persona(row: sqlite3.Row) -> PersonaView:
    return PersonaView(name=row['name'], version=row['version'], profile=row['profile'],
                       tags=json.loads(row['tags']), include=json.loads(row['include_keywords']),
                       exclude=json.loads(row['exclude_keywords']), source=row['source'], note=row['note'],
                       created_at=from_iso(row['created_at']), tag_status=row['tag_status'],
                       tag_error=row['tag_error'])


def _row_to_evaluation(row: sqlite3.Row, prefix: str = '') -> Evaluation:
    data = {column: row[prefix + column] for column in _EVAL_COLUMNS}
    data['tags'] = json.loads(data['tags'])
    data['stage_scores'] = json.loads(data['stage_scores'])
    data['evaluated_at'], data['reported_at'] = from_iso(data['evaluated_at']), from_iso(data['reported_at'])
    return Evaluation.model_validate(data)


def _row_to_report(row: sqlite3.Row) -> ReportView:
    return ReportView(id=row['id'], pipeline_id=row['pipeline_id'], persona_version=row['persona_version'],
        kind=row['kind'], title=row['title'], markdown=row['markdown'], item_ids=json.loads(row['item_ids']),
        model=row['model'], usage=json.loads(row['usage']), cost=row['cost'], path=row['path'],
        created_at=from_iso(row['created_at']))
