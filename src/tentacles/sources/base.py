"""Source plugin mechanism: each source type registers itself with an options
model, and turns a cursor into new items plus the next cursor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Type

from pydantic import BaseModel, ValidationError

from tentacles.config import SourceSpec, format_loc
from tentacles.errors import ConfigError
from tentacles.models import Item
from tentacles.net import HttpPool
from tentacles.sources.imap_client import ImapFactory, connect_imap


@dataclass
class FetchContext:
    http: HttpPool
    now: datetime
    imap_factory: ImapFactory = connect_imap


@dataclass
class FetchResult:
    items: list[Item]
    cursor: dict[str, Any]
    stats: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    type: ClassVar[str]
    Options: ClassVar[Type[BaseModel]]
    # 同批次及跨来源近似标题去重；邮件等主题经常重复的类型应关闭
    dedup_titles: ClassVar[bool] = True

    def __init__(self, spec: SourceSpec, options: BaseModel):
        self.spec = spec
        self.options = options

    @abstractmethod
    async def fetch(self, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult: ...


SOURCE_TYPES: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    SOURCE_TYPES[cls.type] = cls
    return cls


def validate_source(spec: SourceSpec, where: str = "source") -> BaseModel:
    cls = SOURCE_TYPES.get(spec.type)
    if cls is None:
        known = ", ".join(sorted(SOURCE_TYPES))
        raise ConfigError(f"{where}.type: unknown source type {spec.type!r} (known: {known})")
    try:
        return cls.Options.model_validate(spec.options)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise ConfigError(f"{where}.{format_loc(err['loc'])}: {err['msg']}") from exc


def build_source(spec: SourceSpec) -> Source:
    return SOURCE_TYPES[spec.type](spec, validate_source(spec, where=f"source {spec.id!r}"))
