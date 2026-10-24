"""Service facade: the one entry point both adapters (CLI, REST API) call."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

import httpx

from tentacles.config import AppConfig, load_config
from tentacles.errors import ConfigError, NotFound
from tentacles.net import HttpPool
from tentacles.schemas import ConfigCheck, FetchOutcome, ItemDetail, ItemView, Page, RunView, SourceView
from tentacles.sources.base import FetchContext
from tentacles.sources.runner import fetch_source
from tentacles.store import ItemQuery, Store
from tentacles.timeutil import parse_since, utcnow

FETCH_CONCURRENCY = 4


class TentaclesService:
    def __init__(self, config: AppConfig, store: Store, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] = utcnow):
        self.config = config
        self.store = store
        self._transport = transport
        self._clock = clock

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
            ))
        return views

    async def fetch_sources(self, ids: list[str] | None = None) -> list[FetchOutcome]:
        """Fetch the named sources (even disabled ones), or every enabled source."""
        if ids:
            specs = [self.config.source(i) for i in dict.fromkeys(ids)]
        else:
            specs = [s for s in self.config.sources if s.enabled]
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        async with HttpPool(self._transport) as http:
            ctx = FetchContext(http=http, now=self._clock())

            async def one(spec):
                async with sem:
                    return await fetch_source(spec, store=self.store, ctx=ctx)

            return list(await asyncio.gather(*(one(s) for s in specs)))

    # ---- items --------------------------------------------------------------

    def list_items(self, *, sources: list[str] | None = None, kinds: list[str] | None = None,
                   since: str | datetime | None = None, until: str | datetime | None = None,
                   search: str | None = None, limit: int = 50, offset: int = 0) -> Page[ItemView]:
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        if offset < 0:
            raise ConfigError("offset must be >= 0")
        for s in sources or []:
            self.config.source(s)
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
        return [RunView.model_validate(r, from_attributes=True)
                for r in self.store.list_runs(kind, target, limit)]

    def _time(self, value: str | datetime | None) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        try:
            return parse_since(value, self._clock(), ZoneInfo(self.config.app.timezone))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
