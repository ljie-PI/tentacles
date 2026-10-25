"""HTTP-only request/response envelopes; domain views are shared with CLI."""
from pydantic import BaseModel, ConfigDict


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
