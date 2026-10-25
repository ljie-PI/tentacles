"""Persona seeds, immutable version snapshots, and atomic edit requests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tentacles.errors import ConfigError
from tentacles.timeutil import parse_duration
from tentacles.llm.types import UsageRecord

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

    def record_completion(self, result: UsageRecord) -> None:
        self.run_stats['usage'] = result.usage.model_dump()
        self.run_stats['actual_model'] = result.model
        self.cost = result.cost


class FeedbackView(BaseModel):
    id: int
    item_id: str
    persona: str
    rating: Literal[-1, 1]
    note: str = ''
    created_at: datetime


class EvolutionContent(BaseModel):
    """Validated model output, also used at the untrusted proposal-file boundary."""
    model_config = ConfigDict(extra='forbid')
    profile: str = Field(min_length=1, max_length=50000)
    tags: list[InterestTag] = Field(min_length=5, max_length=20)
    include: list[str] = Field(max_length=100)
    exclude: list[str] = Field(max_length=100)
    summary: str = Field(min_length=1, max_length=2000)

    def check_profile_length(self, limit: int) -> None:
        if len(self.profile) > limit:
            raise ValueError('evolved profile exceeds the configured length limit')

    @field_validator('profile', 'summary')
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('evolved profile and summary must not be blank')
        return value

    @field_validator('tags')
    @classmethod
    def _unique_tags(cls, values: list[InterestTag]) -> list[InterestTag]:
        names = [tag.tag.casefold() for tag in values]
        if len(names) != len(set(names)):
            raise ValueError('duplicate interest tags')
        if any(len(tag.tag) > 128 or len(tag.description) > 2000 for tag in values):
            raise ValueError('interest tags are too long')
        return values

    @field_validator('include', 'exclude')
    @classmethod
    def _bounded_keywords(cls, values: list[str]) -> list[str]:
        values = keywords(values)
        if any(len(value) > 256 for value in values):
            raise ValueError('keywords are too long')
        return values


class PersonaProposal(EvolutionContent):
    name: str
    base_version: int = Field(ge=1, strict=True)
    diff: str
    feedback_count: int = Field(ge=0, strict=True)


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feedback_window: str = '30d'
    max_feedback: int = Field(default=100, ge=1, le=1000, strict=True)
    max_profile_chars: int = Field(default=12000, ge=1, le=50000, strict=True)

    @field_validator('feedback_window')
    @classmethod
    def _window(cls, value: str) -> str:
        if parse_duration(value).total_seconds() <= 0:
            raise ValueError('feedback window must be positive')
        return value.strip()
