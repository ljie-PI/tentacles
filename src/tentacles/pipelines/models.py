"""Pipeline evaluation and operation views shared by Service adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tentacles.llm.types import TokenUsage, UsageTotals


class StageScore(BaseModel):
    index: int
    type: str
    score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    tags: list[str] = Field(default_factory=list)
    reason: str = ''
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Evaluation(BaseModel):
    pipeline_id: str
    item_id: str
    persona_version: int
    input_revision: int = Field(default=1, ge=1)
    passed: bool
    score: float
    tags: list[str] = Field(default_factory=list)
    reason: str
    stage: str
    stage_scores: list[StageScore] = Field(default_factory=list)
    evaluated_at: datetime
    reported_at: datetime | None = None


@dataclass
class StageBatch:
    scores: list[StageScore]
    metrics: UsageTotals = field(default_factory=UsageTotals)
    model_name: str | None = None
    error: str | None = None
    http_status: int | None = None


@dataclass
class FilterBatch:
    evaluations: list[Evaluation]
    stages: list[dict[str, Any]]
    error: str | None = None
    batches: list[StageBatch] = field(default_factory=list)


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


class ReportDraft(BaseModel):
    """Processor-owned content and provenance, without a publication location."""

    pipeline_id: str
    persona_version: int
    kind: str
    title: str
    markdown: str
    item_ids: list[str]
    model: str | None
    usage: TokenUsage
    cost: float | None
    created_at: datetime


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
    filter_schedule: str | None = None
    next_run_at: datetime | None = None
    filter_next_run_at: datetime | None = None
    running: bool = False
    filter_running: bool = False
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
