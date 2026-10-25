"""Validated pipeline/filter/processor configuration."""
from __future__ import annotations

import regex as re
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tentacles.schedule import Schedule
from tentacles.timeutil import parse_duration


class ThresholdSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reject_below: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    min_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    accept_above: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @property
    def rejection(self) -> float:
        return self.reject_below if self.reject_below is not None else self.min_score if self.min_score is not None else 0.5

    @model_validator(mode='after')
    def _thresholds(self):
        if self.accept_above is not None and self.accept_above < self.rejection:
            raise ValueError('accept_above must not be below the rejection threshold')
        return self


class KeywordSpec(ThresholdSpec):
    type: Literal['keyword'] = 'keyword'
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    regex: bool = False

    @model_validator(mode='after')
    def _keywords(self):
        for pattern in self.include + self.exclude:
            if not pattern.strip():
                raise ValueError('keywords must not be blank')
            if self.regex:
                try:
                    re.compile(pattern, re.IGNORECASE | re.VERSION1)
                except re.error:
                    raise ValueError('invalid keyword regular expression') from None
        return self


class DecisionSpec(ThresholdSpec):
    type: Literal['decision']
    model: str | None = None
    relevance_type: Literal['noul', 'score'] = 'noul'


class LLMSpec(ThresholdSpec):
    type: Literal['llm']
    model: str | None = None
    batch_size: int = Field(default=20, ge=1, le=100)


StageSpec = Annotated[Union[KeywordSpec, DecisionSpec, LLMSpec], Field(discriminator='type')]


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
    filter_schedule: str | None = None
    enabled: bool = True
    max_items: int = Field(default=500, ge=1, le=5000)
    filters: list[StageSpec] = Field(default_factory=list)
    processor: ProcessorSpec = Field(default_factory=ProcessorSpec)

    @field_validator('filters', mode='before')
    @classmethod
    def _default_stage_type(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{'type': 'keyword', **stage} if isinstance(stage, dict) else stage for stage in value]
        return value

    @field_validator('window')
    @classmethod
    def _window(cls, value: str) -> str:
        try:
            if parse_duration(value) <= timedelta(0):
                raise ValueError('window must be positive')
        except OverflowError:
            raise ValueError('window is too large') from None
        return value.strip()

    @field_validator('schedule', 'filter_schedule')
    @classmethod
    def _schedule(cls, value: str | None) -> str | None:
        if value is None:
            return None
        Schedule.parse(value)
        return value.strip()
