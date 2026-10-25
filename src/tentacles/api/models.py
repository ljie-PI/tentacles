"""HTTP-only request/response envelopes; domain views are shared with CLI."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Health(BaseModel):
    ok: bool = True


class SourceFetch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ids: list[str] | None = None


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
