"""Persona seeds, immutable version snapshots, and atomic edit requests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tentacles.errors import ConfigError

PersonaSource = Literal['seed', 'manual', 'llm', 'rollback']
TagStatus = Literal['ok', 'error', 'skipped', 'unchanged']


def persona_name(value: str) -> str:
    value = value.strip()
    if not value or any(ord(c) < 32 for c in value):
        raise ConfigError('persona name must be nonempty and contain no control characters')
    return value


def keywords(values: list[str]) -> list[str]:
    result: dict[str, str] = {}
    for value in values:
        text = value.strip()
        if not text:
            raise ValueError('keywords must not be blank')
        result.setdefault(text.casefold(), text)
    return list(result.values())


def edit_keywords(current: list[str], add: list[str], remove: list[str]) -> list[str]:
    removed = {v.casefold() for v in keywords(remove)}
    return keywords([v for v in current if v.casefold() not in removed] + add)


class PersonaSeed(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: str | None = None
    profile_file: Path | None = None
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    note: str = ''

    @model_validator(mode='after')
    def _one_profile(self):
        if (self.profile is None) == (self.profile_file is None):
            raise ValueError('provide exactly one of profile or profile_file')
        return self

    @field_validator('include', 'exclude')
    @classmethod
    def _keywords(cls, value: list[str]) -> list[str]:
        return keywords(value)


class InterestTag(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tag: str
    description: str = ''

    @field_validator('tag')
    @classmethod
    def _nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('tag names must not be blank')
        return value


class PersonaView(BaseModel):
    name: str
    version: int
    profile: str
    tags: list[InterestTag] = Field(default_factory=list)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    source: PersonaSource
    note: str = ''
    created_at: datetime
    tag_status: TagStatus = 'skipped'
    tag_error: str | None = None


@dataclass
class PersonaEdit:
    name: str
    profile: str | None = None
    tags: list[InterestTag] | None = None
    include_add: list[str] = field(default_factory=list)
    include_remove: list[str] = field(default_factory=list)
    exclude_add: list[str] = field(default_factory=list)
    exclude_remove: list[str] = field(default_factory=list)
    include_replace: list[str] | None = None
    exclude_replace: list[str] | None = None
    source: PersonaSource = 'manual'
    note: str = ''
    tag_status: TagStatus = 'unchanged'
    tag_error: str | None = None
    expected_version: int | None = None
    run_stats: dict[str, Any] = field(default_factory=dict)
    cost: float | None = None
