"""Pipeline evaluation and operation views shared by Service adapters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tentacles.llm.types import TokenUsage


class StageScore(BaseModel):
    index: int
    type: str
    score: float | None = None
    tags: list[str] = Field(default_factory=list)
    reason: str = ''
    error: str | None = None


class Evaluation(BaseModel):
    pipeline_id: str
    item_id: str
    persona_version: int
    passed: bool
    score: float
    tags: list[str] = Field(default_factory=list)
    reason: str
    stage: str
    stage_scores: list[StageScore] = Field(default_factory=list)
    evaluated_at: datetime
    reported_at: datetime | None = None


class PipelineOutcome(BaseModel):
    pipeline_id: str
    run_id: int = 0
    status: Literal['ok', 'error', 'busy', 'no_new']
    report_id: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class PipelineQuery:
    pipeline_id: str
    sources: list[str]
    persona_version: int
    since: datetime
    until: datetime
    limit: int


class ReportView(BaseModel):
    id: str
    pipeline_id: str
    persona_version: int
    kind: str
    title: str
    markdown: str
    item_ids: list[str]
    model: str | None
    usage: TokenUsage
    cost: float | None
    path: str
    created_at: datetime


class PipelineView(BaseModel):
    id: str
    name: str | None
    persona: str
    sources: list[str]
    schedule: str
    enabled: bool
    processor: str
    pending: int
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
