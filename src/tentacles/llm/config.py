"""Named model specifications; credentials never participate in public views."""
from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Stage = Literal['extract', 'filter', 'decision', 'persona', 'process']
ModelKind = Literal['chat', 'decision']
STAGES: tuple[Stage, ...] = ('extract', 'filter', 'decision', 'persona', 'process')
_BASE_URLS = {'openai': 'https://api.openai.com/v1',
              'anthropic': 'https://api.anthropic.com/v1',
              'openrouter': 'https://openrouter.ai/api/v1'}


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: Literal['openai', 'anthropic', 'openrouter'] = 'openai'
    kind: ModelKind = 'chat'
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=4096, gt=0)
    timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    retries: int = Field(default=2, ge=0)
    concurrency: int = Field(default=8, gt=0)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @field_validator('api_key', mode='before')
    @classmethod
    def _empty_key(cls, value):
        return '' if value is None else value

    @field_validator('model')
    @classmethod
    def _nonblank_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('model name must not be blank')
        return value.strip()

    @field_validator('base_url')
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            raise ValueError('base_url must be an http(s) URL') from None
        if (url.scheme not in {'http', 'https'} or not url.host or url.userinfo or
                url.query or url.fragment):
            raise ValueError('base_url must be http(s), without credentials, query or fragment')
        return str(url).rstrip('/')

    @field_validator('extra_body')
    @classmethod
    def _json_extras(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = {'model', 'messages', 'system', 'stream', 'state', 'questions'}
        if reserved.intersection(value):
            raise ValueError('extra_body cannot replace model/messages/system/stream/state/questions')
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('extra_body must contain finite JSON values') from None
        return value

    @model_validator(mode='after')
    def _decision_provider(self):
        if self.kind == 'decision' and self.provider != 'openrouter':
            raise ValueError('decision models require the openrouter provider')
        return self

    @property
    def endpoint(self) -> str:
        if self.kind == 'decision' and self.base_url is None:
            return 'https://openrouter.ai/api'
        endpoint = self.base_url or _BASE_URLS[self.provider]
        if self.kind == 'decision' and endpoint.endswith('/v1'):
            endpoint = endpoint[:-3]
        return endpoint


class Defaults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    models: dict[Stage, str] = Field(default_factory=dict)
