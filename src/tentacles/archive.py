"""按自然年冷归档：两阶段搬迁、跨库只读归并与 `storage` 运维操作。

热库始终是唯一的活跃数据库：跨月聚类窗口、反馈回溯与外键完整性都留在原地。
早于热库保留期的条目按发布时间的自然年搬进 `data/archive/tentacles-YYYY.db`
（各自带 `items_fts`），热库只留下 `archived_identities` 去重墓碑。
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Callable, Iterator, Sequence
from zoneinfo import ZoneInfo

from tentacles.config import AppConfig
from tentacles.errors import ConfigError
from tentacles.models import Item
from tentacles.pipelines.models import Evaluation
from tentacles.schemas import (ArchiveExempt, ArchiveSnapshot, ArchiveYear, ReindexedDatabase,
                               StorageArchive, StorageReindex, StorageStatus)
from tentacles.store import ArchiveCopy, ItemHit, ItemQuery, Store
from tentacles.timeutil import parse_duration, parse_since, utcnow

# 近期活动保护窗口：即使早于热库保留期，最近 N 天更新过的条目也留在热库
ACTIVITY_DAYS = 30
ARCHIVE_PREFIX = 'tentacles-'
# SQLite 的 LIMIT/OFFSET 只接受 64 位整数；超出它的分页窗口本页必然为空
_WINDOW_LIMIT = 2 ** 63 - 1


def archive_path(archive_dir: Path, year: int) -> Path:
    """`data/archive/tentacles-2025.db`。"""
    return archive_dir / f'{ARCHIVE_PREFIX}{year}.db'


def archive_years(archive_dir: Path) -> list[int]:
    """已有归档库的自然年，新的在前；不是 `tentacles-<年>.db` 的文件一律忽略。"""
    if not archive_dir.is_dir():
        return []
    years = [int(suffix) for path in archive_dir.glob(f'{ARCHIVE_PREFIX}*.db')
             if (suffix := path.stem.removeprefix(ARCHIVE_PREFIX)).isdigit()]
    return sorted(years, reverse=True)


def open_archive(path: Path, *, write: bool = False) -> Store:
    """打开一个年度冷库：默认只读 URI，写模式仅用于建库、补写与索引重建。"""
    if not write and not path.exists():
        raise ConfigError(f'archive database {path.name} does not exist')
    try:
        return Store(path, read_only=not write)
    except sqlite3.Error:
        raise ConfigError(f'cannot read archive database {path.name}') from None


@contextmanager
def cold_writer(path: Path) -> Iterator[Store]:
    """写一个年度冷库，并在关闭前把它从 WAL 模式里拆出来。"""
    store = open_archive(path, write=True)
    try:
        yield store
    finally:
        store.seal_archive()
        store.close()


def _file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _item_time(item: Item) -> datetime:
    return item.published or item.fetched_at


def _sort_key(hit: ItemHit, sort: str) -> tuple[float, float, str]:
    """热库与冷库共用的归并顺序：相关性（BM25 升序）或发布时间倒序。"""
    when = _item_time(hit.item)
    rank = 0.0 if sort != 'relevance' else (
        hit.bm25_rank if hit.bm25_rank is not None else float('inf'))
    return (rank, -when.timestamp(), hit.item.id)


class ArchiveManager:
    """热库与年度冷库之间的搬迁、跨年归并读取与 `storage` 运维。"""

    def __init__(self, config: AppConfig, store: Store, *,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.config, self.store, self._clock = config, store, clock
        self._tz = ZoneInfo(config.app.timezone)

    @property
    def directory(self) -> Path:
        return self.config.archive_dir

    # ---- 跨年归并读取 ------------------------------------------------------

    def merge_items(self, q: ItemQuery) -> tuple[list[tuple[int | None, ItemHit]], int]:
        """把热库的一页与各年冷库的一页按同一顺序归并后再分页。

        每个库都单独查询，因此跨年检索不受 SQLite 单连接最多 `ATTACH 10` 个
        数据库的限制。BM25 分值是库内统计量：跨库归并时按各库返回的分值排序，
        只有 1–2 字短词（没有倒排分值）才退回发布时间倒序。
        """
        window = min(q.offset + q.limit, _WINDOW_LIMIT)
        hits, total = self.store.list_items(replace(q, limit=window, offset=0))
        merged: list[tuple[int | None, ItemHit]] = [(None, hit) for hit in hits]
        for year in archive_years(self.directory):
            with closing(self._open(year)) as cold:
                archived, archived_total = cold.list_items(replace(q, limit=window, offset=0))
            total += archived_total
            merged.extend((year, hit) for hit in archived)
        merged.sort(key=lambda entry: _sort_key(entry[1], q.sort))
        return merged[q.offset:q.offset + q.limit], total

    def find_item(self, item_id: str) -> tuple[int, Item, Evaluation | None, list[Evaluation]] | None:
        """按年份从新到旧找一个已归档条目及其评估。"""
        for year in archive_years(self.directory):
            with closing(self._open(year)) as cold:
                item = cold.get_item(item_id)
                if item is None:
                    continue
                return year, item, cold.latest_evaluation(item_id), cold.item_evaluations(item_id)
        return None

    # ---- storage status / reindex ------------------------------------------

    def status(self) -> StorageStatus:
        """热库体积、各表行数、条目时间范围与全部年度冷库清单。"""
        stats, db_path = self.store.database_stats(), self.store.path
        return StorageStatus(
            path=str(db_path), db_bytes=_file_bytes(db_path),
            wal_bytes=_file_bytes(db_path.with_name(db_path.name + '-wal')),
            schema_version=stats.schema_version, tables=stats.tables,
            fts_indexed_rows=stats.fts_indexed_rows,
            oldest_item_at=stats.oldest_item_at, newest_item_at=stats.newest_item_at,
            hot_retention_days=self.config.app.storage.hot_retention_days,
            archive_dir=str(self.directory),
            archives=[self._snapshot(year) for year in archive_years(self.directory)])

    def _snapshot(self, year: int) -> ArchiveSnapshot:
        path = archive_path(self.directory, year)
        with closing(open_archive(path)) as cold:
            stats = cold.database_stats()
        return ArchiveSnapshot(year=year, path=str(path), bytes=_file_bytes(path),
                               items=stats.tables.get('items', 0),
                               evaluations=stats.tables.get('evaluations', 0),
                               fts_indexed_rows=stats.fts_indexed_rows)

    def reindex(self, *, optimize: bool, include_archive: bool) -> StorageReindex:
        """在线重建（`--optimize` 时改为合并）FTS5 索引，可带上全部冷库。"""
        began = monotonic()
        results = [self._reindex_store(self.store, None, optimize)]
        if include_archive:
            for year in archive_years(self.directory):
                with cold_writer(archive_path(self.directory, year)) as cold:
                    results.append(self._reindex_store(cold, year, optimize))
        return StorageReindex(optimize=optimize, rows=sum(result.rows for result in results),
                              seconds=monotonic() - began, databases=results)

    @staticmethod
    def _reindex_store(store: Store, year: int | None, optimize: bool) -> ReindexedDatabase:
        began = monotonic()
        rows = store.reindex_fts(optimize=optimize)
        return ReindexedDatabase(path=str(store.path), year=year, rows=rows,
                                 seconds=monotonic() - began)

    # ---- storage archive ---------------------------------------------------

    def archive(self, *, before: str | None = None, dry_run: bool = False,
                vacuum: bool = False) -> StorageArchive:
        """两阶段搬迁：先写冷库并校验，再在一个热库事务里写墓碑删冷行。"""
        began = monotonic()
        now = self._clock()
        cutoff = self._cutoff(before, now)
        feedback_since = now - parse_duration(self.config.persona_update.feedback_window)
        activity_since = now - timedelta(days=ACTIVITY_DAYS)
        copies, exempt = self.store.archive_candidates(
            cutoff, activity_since=activity_since, feedback_since=feedback_since)
        groups: dict[int, list[ArchiveCopy]] = defaultdict(list)
        for copy in copies:
            groups[self._year(copy.item)].append(copy)
        exempt_view = ArchiveExempt(recent_activity=exempt.recent_activity,
                                    active_feedback=exempt.active_feedback,
                                    unresolved_errors=exempt.unresolved_errors)
        if dry_run:
            return StorageArchive(dry_run=True, cutoff=cutoff, vacuumed=False, items=len(copies),
                                  evaluations=sum(len(c.evaluations) for c in copies), deferred=0,
                                  years=[self._planned(year, entries)
                                         for year, entries in sorted(groups.items())],
                                  exempt=exempt_view, seconds=monotonic() - began)
        years: list[ArchiveYear] = []
        moved = moved_evaluations = deferred = 0
        for year, entries in sorted(groups.items()):
            path = archive_path(self.directory, year)
            self._write_cold(path, entries)
            archived = set(self.store.archive_move(
                year, [copy.item for copy in entries], now=now,
                activity_since=activity_since, feedback_since=feedback_since))
            stale = [copy for copy in entries if copy.item.id not in archived]
            if stale:
                # 归档期间被更新的条目留在热库，冷库里那份过期副本必须撒掉
                with cold_writer(path) as cold:
                    cold.drop_archived([copy.item.id for copy in stale])
                deferred += len(stale)
            kept = [copy for copy in entries if copy.item.id in archived]
            moved += len(kept)
            moved_evaluations += sum(len(copy.evaluations) for copy in kept)
            years.append(ArchiveYear(year=year, path=str(path), items=len(kept),
                                     evaluations=sum(len(c.evaluations) for c in kept),
                                     bytes=_file_bytes(path)))
        if vacuum:
            self.store.vacuum()
        return StorageArchive(dry_run=False, cutoff=cutoff, vacuumed=vacuum, items=moved,
                              evaluations=moved_evaluations, deferred=deferred, years=years,
                              exempt=exempt_view, seconds=monotonic() - began)

    def _planned(self, year: int, entries: Sequence[ArchiveCopy]) -> ArchiveYear:
        return ArchiveYear(year=year, path=str(archive_path(self.directory, year)),
                           items=len(entries),
                           evaluations=sum(len(entry.evaluations) for entry in entries), bytes=0)

    def _write_cold(self, path: Path, entries: Sequence[ArchiveCopy]) -> None:
        """第一阶段：把完整副本写进年度冷库并校验，随后才允许删热库冷行。"""
        with cold_writer(path) as cold:
            cold.import_archived([entry.item for entry in entries],
                                 [evaluation for entry in entries for evaluation in entry.evaluations])

    def _open(self, year: int) -> Store:
        return open_archive(archive_path(self.directory, year))

    def _year(self, item: Item) -> int:
        return _item_time(item).astimezone(self._tz).year

    def _cutoff(self, before: str | None, now: datetime) -> datetime:
        if before is None:
            return now - timedelta(days=self.config.app.storage.hot_retention_days)
        try:
            return parse_since(before, now, self._tz)
        except (OverflowError, ValueError) as exc:
            raise ConfigError(f'invalid --before value: {exc}') from None
