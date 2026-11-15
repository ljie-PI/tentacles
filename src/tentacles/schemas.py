"""View models returned by the Service facade. The CLI prints them as JSON and
the REST API returns them, so both adapters share one contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, computed_field, model_validator

from tentacles.models import Item, ItemKind
from tentacles.pipelines.models import Evaluation, StageScore, PipelineOutcome
from tentacles.llm.types import TokenUsage as TokenUsage

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
    running: bool = False


class FetchOutcome(BaseModel):
    source_id: str
    run_id: int
    status: Literal["ok", "error", "busy"]
    new: int
    dup: int
    updated: int = 0
    error: str | None = None
    stats: dict[str, Any] = {}


class TickOutcome(BaseModel):
    kind: Literal['fetch', 'filter', 'pipeline']
    target: str
    status: Literal['ok', 'error', 'busy', 'no_new']
    run_id: int = 0
    source_id: str | None = None
    pipeline_id: str | None = None
    new: int = 0
    updated: int = 0
    dup: int = 0
    report_id: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @classmethod
    def of_fetch(cls, outcome: FetchOutcome) -> 'TickOutcome':
        return cls(kind='fetch', target=outcome.source_id, **outcome.model_dump())

    @classmethod
    def of_pipeline(cls, kind: Literal['filter', 'pipeline'], outcome: PipelineOutcome) -> 'TickOutcome':
        return cls(kind=kind, target=outcome.pipeline_id, **outcome.model_dump())


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
    pending: int | None = None


class ItemView(BaseModel):
    id: str
    source_id: str
    kind: ItemKind
    title: str
    url: str
    page_url: str = ""
    author: str
    summary: str
    # 入库即截断到 1000 units 的正文，以及是否发生过截断
    content: str = ""
    content_truncated: bool = False
    published: datetime | None
    fetched_at: datetime
    updated_at: datetime | None = None

    pipeline_id: str | None = None
    persona_version: int | None = None
    passed: bool | None = None
    # topics / match_score / match_points 是下游 Agent 的主字段，
    # tags / score / reason 为兼容别名，两者取值一致。
    topics: list[str] = Field(default_factory=list)
    match_score: float | None = None
    match_points: list[str] = Field(default_factory=list)
    score: float | None = None
    tags: list[str] = Field(default_factory=list)
    reason: str | None = None
    stage: str | None = None
    stage_scores: list[StageScore] = Field(default_factory=list)

    @model_validator(mode="after")
    def _mirror_aliases(self) -> "ItemView":
        if not self.topics:
            self.topics = list(self.tags)
        if self.match_score is None:
            self.match_score = self.score
        if not self.match_points and self.reason:
            self.match_points = [self.reason]
        return self

    @computed_field
    def is_updated(self) -> bool:
        return self.updated_at is not None

    @computed_field
    def match_level(self) -> Literal["high", "medium", "low", "rejected", "unscored"]:
        if self.passed is False:
            return "rejected"
        if self.match_score is None:
            return "unscored"
        if self.match_score >= 0.8:
            return "high"
        return "medium" if self.match_score >= 0.5 else "low"

    @classmethod
    def of(cls, item: Item, evaluation: Evaluation | None = None) -> "ItemView":
        return cls.model_validate({**item.model_dump(), **(evaluation.model_dump() if evaluation else {})})


class ItemDetail(ItemView):
    external_id: str
    fingerprint: str
    meta: dict[str, Any]
    evaluations: list[Evaluation] = Field(default_factory=list)


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


class ModelView(BaseModel):
    name: str
    provider: str
    kind: Literal['chat', 'decision']
    model: str
    base_url: str
    key_configured: bool


class Completion(BaseModel):
    text: str
    model: str
    usage: TokenUsage
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    data: Any = None


class ModelTest(BaseModel):
    name: str
    ok: bool
    latency_ms: float
    sample: str = ''
    model: str | None = None
    usage: TokenUsage | None = None
    cost: float | None = None
    error: str | None = None


class DecisionCompletion(BaseModel):
    answers: dict[str, Any]
    model: str
    usage: TokenUsage
    cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
