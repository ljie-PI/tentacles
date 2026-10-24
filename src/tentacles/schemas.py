"""View models returned by the Service facade. The CLI prints them as JSON and
the REST API returns them, so both adapters share one contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

from tentacles.models import Item, ItemKind

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ConfigCheck(BaseModel):
    ok: bool
    path: str
    sources: int


class SourceView(BaseModel):
    id: str
    type: str
    name: str | None
    schedule: str
    enabled: bool
    last_fetch_at: datetime | None
    last_status: str | None
    last_error: str | None
    total_items: int
    next_run_at: datetime | None = None


class FetchOutcome(BaseModel):
    source_id: str
    run_id: int
    status: Literal["ok", "error", "busy"]
    new: int
    dup: int
    error: str | None = None
    stats: dict[str, Any] = {}


class TaskView(BaseModel):
    kind: str
    target: str
    schedule: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    running: bool
    last_status: str | None
    last_error: str | None


class ItemView(BaseModel):
    id: str
    source_id: str
    kind: ItemKind
    title: str
    url: str
    author: str
    summary: str
    published: datetime | None
    fetched_at: datetime

    @classmethod
    def of(cls, item: Item) -> "ItemView":
        return cls.model_validate(item.model_dump(include=set(cls.model_fields)))


class ItemDetail(ItemView):
    external_id: str
    content: str
    fingerprint: str
    meta: dict[str, Any]


class RunView(BaseModel):
    id: int
    kind: str
    target: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    stats: dict[str, Any]
    cost: float | None
    error: str | None
