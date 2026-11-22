"""Named model specifications; credentials never participate in public views."""
from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Stage = Literal['extract', 'filter', 'decision', 'persona', 'process']
ModelKind = Literal['chat', 'decision']
Provider = Literal['openai', 'openai-compatible', 'anthropic', 'openrouter', 'opencode-go']
STAGES: tuple[Stage, ...] = ('extract', 'filter', 'decision', 'persona', 'process')
_BASE_URLS = {'openai': 'https://api.openai.com/v1',
              'anthropic': 'https://api.anthropic.com/v1',
              'openrouter': 'https://openrouter.ai/api/v1',
              'opencode-go': 'https://opencode.ai/inference/go/openai/v1'}
_RESERVED_HEADERS = frozenset({'authorization', 'x-api-key', 'host', 'content-length', 'content-type'})


def _has_controls(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider: Provider = 'openai'
    kind: ModelKind = 'chat'
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=4096, gt=0)
    timeout: float = Field(default=60, gt=0, allow_inf_nan=False)
    retries: int = Field(default=2, ge=0)
    concurrency: int = Field(default=8, gt=0)
    headers: dict[str, str] = Field(default_factory=dict)
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

    @field_validator('headers')
    @classmethod
    def _custom_headers(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        seen: set[str] = set()
        for raw_name, raw_val in value.items():
            name = raw_name.strip()
            if not name or _has_controls(name) or any(ch.isspace() or ch == ':' for ch in name):
                raise ValueError('header names must be valid HTTP token names')
            lowered = name.lower()
            if lowered in _RESERVED_HEADERS:
                raise ValueError(f'headers cannot override {lowered}')
            if lowered in seen:
                raise ValueError(f'duplicate header {lowered}')
            if _has_controls(raw_val):
                raise ValueError('header values must not contain control characters')
            seen.add(lowered)
            cleaned[name] = raw_val
        return cleaned

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
    def _provider_requirements(self):
        if self.provider == 'openai-compatible' and self.base_url is None:
            raise ValueError('openai-compatible models require an explicit base_url')
        if self.kind == 'decision':
            if self.provider != 'openrouter':
                raise ValueError('decision models require the openrouter provider')
            preferences = self.extra_body.get('provider')
            if preferences is not None and not isinstance(preferences, dict):
                raise ValueError('decision provider preferences must be an object')
            if preferences and preferences.get('data_collection') not in (None, 'allow', 'deny'):
                raise ValueError('decision data_collection must be allow or deny')
        return self

    @property
    def endpoint(self) -> str:
        if self.kind == 'decision' and self.base_url is None:
            return 'https://openrouter.ai/api'
        endpoint = self.base_url or _BASE_URLS[self.provider]
        if self.kind == 'decision' and endpoint.endswith('/v1'):
            endpoint = endpoint[:-3]
        return endpoint

    @property
    def is_opencode_go(self) -> bool:
        if self.provider == 'opencode-go':
            return True
        parsed = httpx.URL(self.endpoint)
        return parsed.host == 'opencode.ai' and (
            parsed.path.startswith('/zen/go') or parsed.path.startswith('/inference/go')
        )


class Defaults(BaseModel):
    model_config = ConfigDict(extra='forbid')
    models: dict[Stage, str] = Field(default_factory=dict)
