"""Source plugin mechanism: each source type registers itself with an options
model, and turns a cursor into new items plus the next cursor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, ClassVar, Type, TYPE_CHECKING

from pydantic import BaseModel

from tentacles.config import AppConfig, SourceSpec
from tentacles.models import Item, ItemObservation
from tentacles.net import HttpPool
from tentacles.sources.imap_client import ImapFactory, connect_imap
from tentacles.llm.types import UsageTotals

if TYPE_CHECKING:
    from tentacles.llm.registry import ModelRegistry


@dataclass
class FetchContext:
    http: HttpPool
    now: datetime
    imap_factory: ImapFactory = connect_imap
    timezone: str = 'UTC'
    seen_links: Callable[[list[str]], set[str]] | None = None
    models: ModelRegistry | None = None
    metrics: UsageTotals = field(default_factory=UsageTotals)


@dataclass
class FetchResult:
    items: list[Item]
    cursor: dict[str, Any]
    stats: dict[str, Any] = field(default_factory=dict)
    observations: dict[str, ItemObservation] = field(default_factory=dict)


class Source(ABC):
    type: ClassVar[str]
    Options: ClassVar[Type[BaseModel]]
    # 同批次及跨来源近似标题去重；邮件等主题经常重复的类型应关闭
    dedup_titles: ClassVar[bool] = True
    supports_updates: ClassVar[bool] = False

    def __init__(self, spec: SourceSpec, options: BaseModel):
        self.spec = spec
        self.options = options

    @abstractmethod
    async def fetch(self, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult: ...


SOURCE_TYPES: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    SOURCE_TYPES[cls.type] = cls
    return cls


def validate_source(spec: SourceSpec, where: str = "source", *, config: AppConfig | None = None) -> BaseModel:
    from tentacles.config_validation import validate_source as validate

    return validate(spec, where, config)


def build_source(spec: SourceSpec) -> Source:
    return SOURCE_TYPES[spec.type](spec, validate_source(spec, where=f"source {spec.id!r}"))
