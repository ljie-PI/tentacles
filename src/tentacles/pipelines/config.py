"""Validated pipeline/filter/processor configuration."""
from __future__ import annotations

import regex as re
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tentacles.schedule import Schedule
from tentacles.timeutil import parse_duration


class StageSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['keyword'] = 'keyword'
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    regex: bool = False
    reject_below: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    min_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    accept_above: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @property
    def rejection(self) -> float:
        return self.reject_below if self.reject_below is not None else self.min_score if self.min_score is not None else 0.5

    @model_validator(mode='after')
    def _parameters(self):
        if self.accept_above is not None and self.accept_above < self.rejection:
            raise ValueError('accept_above must not be below the rejection threshold')
        for pattern in self.include + self.exclude:
            if not pattern.strip():
                raise ValueError('keywords must not be blank')
            if self.regex:
                try:
                    re.compile(pattern, re.IGNORECASE | re.VERSION1)
                except re.error:
                    raise ValueError('invalid keyword regular expression') from None
        return self


class ProcessorSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['digest', 'none'] = 'digest'
    model: str | None = None
    language: str = Field(default='zh-CN', min_length=1)
    instructions: str = ''
    max_items: int = Field(default=60, ge=1, le=500)
    prompt_file: Path | None = None


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    name: str | None = None
    persona: str = 'default'
    sources: list[str] = Field(min_length=1)
    window: str = '24h'
    schedule: str = '1d'
    enabled: bool = True
    max_items: int = Field(default=500, ge=1, le=5000)
    filters: list[StageSpec] = Field(default_factory=list)
    processor: ProcessorSpec = Field(default_factory=ProcessorSpec)

    @field_validator('window')
    @classmethod
    def _window(cls, value: str) -> str:
        try:
            if parse_duration(value) <= timedelta(0):
                raise ValueError('window must be positive')
        except OverflowError:
            raise ValueError('window is too large') from None
        return value.strip()

    @field_validator('schedule')
    @classmethod
    def _schedule(cls, value: str) -> str:
        Schedule.parse(value)
        return value.strip()
