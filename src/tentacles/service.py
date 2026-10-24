"""Service facade: the one entry point both adapters (CLI, REST API) call."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo
from uuid import uuid4

import httpx

from tentacles.config import AppConfig, SourceSpec, load_config
from tentacles.errors import ConfigError, NotFound
from tentacles.net import HttpPool
from tentacles.schemas import ConfigCheck, FetchOutcome, ItemDetail, ItemView, Page, RunView, SourceView, TaskView
from tentacles.sources.runner import fetch_source
from tentacles.asyncutil import run_store_call
from tentacles.store import ItemQuery, Store
from tentacles.timeutil import parse_since, utcnow, to_utc
from tentacles.tasks import Task

FETCH_CONCURRENCY = 4


class TentaclesService:
    def __init__(self, config: AppConfig, store: Store, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] = utcnow):
        self.config = config
        self.store = store
        self._transport = transport
        self._clock = clock
        self._tasks = {s.id: Task('fetch', s.id, s.schedule, config.app.timezone, s.enabled)
                       for s in config.sources}
        try:
            store.register_tasks(list(self._tasks.values()), to_utc(clock()))
        except BaseException:
            store.close()
            raise

    @classmethod
    def open(cls, config_path: Path | str | None = None, *,
             transport: httpx.AsyncBaseTransport | None = None,
             env: Mapping[str, str] | None = None,
             clock: Callable[[], datetime] = utcnow) -> "TentaclesService":
        config = load_config(config_path, env=env)
        return cls(config, Store(config.db_path), transport=transport, clock=clock)

    async def aclose(self) -> None:
        self.store.close()

    # ---- config -------------------------------------------------------------

    def check_config(self) -> ConfigCheck:
        # 能打开 Service 就说明配置已通过全部校验
        return ConfigCheck(ok=True, path=str(self.config.path), sources=len(self.config.sources))

    # ---- sources ------------------------------------------------------------

    def list_sources(self) -> list[SourceView]:
        views = []
        for s in self.config.sources:
            st = self.store.get_source_state(s.id)
            views.append(SourceView(
                id=s.id, type=s.type, name=s.name, schedule=s.schedule, enabled=s.enabled,
                last_fetch_at=st.last_fetch_at, last_status=st.last_status,
                last_error=st.last_error, total_items=st.total_items,
                next_run_at=self.store.get_task_state(self._tasks[s.id]).next_run(self._tasks[s.id])
                if s.enabled else None,
            ))
        return views

    async def fetch_sources(self, ids: list[str] | None = None) -> list[FetchOutcome]:
        """Fetch the named sources (even disabled ones), or every enabled source."""
        if ids:
            specs = [self.config.source(i) for i in dict.fromkeys(ids)]
        else:
            specs = [s for s in self.config.sources if s.enabled]
        return await self._fetch_batch(specs, due_only=False)

    async def tick(self) -> list[FetchOutcome]:
        """Execute enabled, due source tasks, reporting occupied leases as busy."""
        return await self._fetch_batch([s for s in self.config.sources if s.enabled], due_only=True)

    def scheduler_status(self) -> list[TaskView]:
        now = to_utc(self._clock())
        views = []
        for task in self._tasks.values():
            state = self.store.get_task_state(task)
            views.append(TaskView(
                kind=task.kind, target=task.target, schedule=task.schedule, enabled=task.enabled,
                last_run_at=state.last_run_at,
                next_run_at=state.next_run(task) if task.enabled else None,
                running=state.running(now), last_status=state.last_status, last_error=state.last_error))
        return views

    async def _fetch_task(self, spec: SourceSpec, http: HttpPool, *, due_only: bool) -> FetchOutcome | None:
        task = self._tasks[spec.id]
        token, started_at = uuid4().hex, to_utc(self._clock())
        try:
            claim = await run_store_call(self.store.claim_task, task, token, started_at,
                                      force=not due_only)
            if claim.status == 'not_due':
                return None
            if claim.status == 'busy':
                return FetchOutcome(source_id=spec.id, run_id=0, status='busy', new=0, dup=0)
            return await fetch_source(spec, store=self.store, http=http, clock=self._clock,
                                      run_id=claim.run_id, started_at=started_at)
        finally:
            await run_store_call(self.store.finish_task, task, token, to_utc(self._clock()))

    async def _fetch_batch(self, specs: list[SourceSpec], *, due_only: bool) -> list[FetchOutcome]:
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        async with HttpPool(self._transport) as http:
            async def one(spec):
                async with sem:
                    return await self._fetch_task(spec, http, due_only=due_only)

            tasks = [asyncio.create_task(one(s)) for s in specs]
            try:
                return [result for result in await asyncio.gather(*tasks) if result is not None]
            except BaseException:
                # gather can return as soon as a semaphore waiter is cancelled,
                # while active sources are still draining their database workers.
                for task in tasks:
                    task.cancel()
                cleanup = asyncio.gather(*tasks, return_exceptions=True)
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        pass  # repeated cancellation must not interrupt resource cleanup
                cleanup.result()
                raise

    # ---- items --------------------------------------------------------------

    def list_items(self, *, sources: list[str] | None = None, kinds: list[str] | None = None,
                   since: str | datetime | None = None, until: str | datetime | None = None,
                   search: str | None = None, limit: int = 50, offset: int = 0) -> Page[ItemView]:
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        if offset < 0:
            raise ConfigError("offset must be >= 0")
        if sources:
            known = {s.id for s in self.config.sources} | self.store.source_ids()
            for s in sources:  # 已从配置移除的源仍可按 id 查询历史条目
                if s not in known:
                    raise NotFound(f"unknown source {s!r}")
        q = ItemQuery(source_ids=sources or None, kinds=kinds or None, since=self._time(since),
                      until=self._time(until), search=search or None, limit=limit, offset=offset)
        items, total = self.store.list_items(q)
        return Page[ItemView](items=[ItemView.of(i) for i in items], total=total,
                              limit=limit, offset=offset)

    def get_item(self, item_id: str) -> ItemDetail:
        item = self.store.get_item(item_id)
        if item is None:
            raise NotFound(f"unknown item {item_id!r}")
        return ItemDetail.model_validate(item.model_dump(include=set(ItemDetail.model_fields)))

    # ---- runs ---------------------------------------------------------------

    def list_runs(self, *, kind: str | None = None, target: str | None = None,
                  limit: int = 50) -> list[RunView]:
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        return [RunView.model_validate(r, from_attributes=True)
                for r in self.store.list_runs(kind, target, limit)]

    def _time(self, value: str | datetime | None) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        try:
            return parse_since(value, self._clock(), ZoneInfo(self.config.app.timezone))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
