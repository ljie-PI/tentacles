"""HTTP-only request/response envelopes; domain views are shared with CLI."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Health(BaseModel):
    ok: bool = True


class SourceFetch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ids: list[str] | None = None


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    type: Literal['rss', 'web', 'browser'] = 'rss'
    name: str | None = None
    schedule: str = '1h'
    enabled: bool = True
    update_existing: bool = False
    pipelines: list[str] = Field(default_factory=list)
    url: str | None = None
    max_age_days: int | None = None
    timeout: float | None = None
    mode: Literal['list', 'page', 'llm'] | None = None
    selectors: dict[str, str] | None = None
    selector: str | None = None
    fetch_content: bool | None = None
    on_detail_error: Literal['fail_batch', 'record_and_skip'] | None = None
    emit_baseline: bool | None = None
    max_input_chars: int | None = None
    model: str | None = None
    concurrency: int | None = None
    page_url: str | None = None
    task: str | None = None
    chrome_profile: str | None = None
    max_items: int | None = None


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = None
    schedule: str | None = None
    enabled: bool | None = None
    update_existing: bool | None = None
    pipelines: list[str] | None = None
    url: str | None = None
    max_age_days: int | None = None
    timeout: float | None = None
    host: str | None = None
    port: int | None = None
    ssl: bool | None = None
    folders: list[str] | None = None
    include_subfolders: bool | None = None
    initial_days: int | None = None
    max_fetch: int | None = None
    search: str | None = None
    exclude_senders: list[str] | None = None
    mode: Literal['list', 'page', 'llm'] | None = None
    selectors: dict[str, str] | None = None
    selector: str | None = None
    fetch_content: bool | None = None
    on_detail_error: Literal['fail_batch', 'record_and_skip'] | None = None
    emit_baseline: bool | None = None
    max_input_chars: int | None = None
    model: str | None = None
    concurrency: int | None = None
    page_url: str | None = None
    task: str | None = None
    chrome_profile: str | None = None
    max_items: int | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class SchedulerLoopView(BaseModel):
    enabled: bool
    running: bool = False
    poll_interval: float
    cycles: int = 0
    failed_cycles: int = 0
    last_tick_at: str | None = None
    last_error: str | None = None


class DisplaySettings(BaseModel):
    timezone: str


class PipelineFilter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reevaluate: bool = False
    since: str | None = None


class StorageArchiveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    before: str | None = None
    dry_run: bool = False
    vacuum: bool = False


class PersonaVersionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1)
    expected_version: int = Field(ge=0, le=2**63 - 1, strict=True)


class PersonaSet(PersonaVersionRequest):
    profile: str | None = None
    skip_tags: bool = False
    include_add: list[str] = Field(default_factory=list)
    include_remove: list[str] = Field(default_factory=list)
    exclude_add: list[str] = Field(default_factory=list)
    exclude_remove: list[str] = Field(default_factory=list)
    hard_exclude_add: list[str] = Field(default_factory=list)
    hard_exclude_remove: list[str] = Field(default_factory=list)
    note: str | None = None


class PersonaEvolve(PersonaVersionRequest):
    instruction: str = ''
    use_feedback: bool = True


class PersonaRollback(PersonaVersionRequest):
    version: int = Field(ge=1, le=2**63 - 1, strict=True)
    note: str | None = None


class ItemFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rating: Literal[-1, 1]
    persona: str | None = None
    note: str = ''

    @field_validator('rating', mode='before')
    @classmethod
    def _integer_rating(cls, value):
        if type(value) is not int:
            raise ValueError('rating must be integer +1 or -1')
        return value
