"""Lazy named model registry; every instance shares the same HTTP pool."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from tentacles.timeutil import utcnow
from tentacles.errors import ConfigError, NotFound
from tentacles.llm.client import ChatModel, DecisionModel
from tentacles.llm.config import Defaults, ModelSpec, Stage, STAGES
from tentacles.net import HttpPool


class ModelRegistry:
    def __init__(self, specs: dict[str, ModelSpec], defaults: Defaults, http: HttpPool, *,
                 clock: Callable[[], datetime] = utcnow):
        self._specs, self._defaults, self._http = specs, defaults, http
        self._clock = clock
        self._chats: dict[str, ChatModel] = {}
        self._decisions: dict[str, DecisionModel] = {}
        self._keys = sorted((s.api_key.get_secret_value() for s in specs.values()
                             if s.api_key.get_secret_value()), key=len, reverse=True)

    def redact(self, text: str) -> str:
        for key in self._keys:
            text = text.replace(key, '[REDACTED]')
        return text

    def resolve(self, stage: Stage, override: str | None = None) -> str:
        if stage not in STAGES:
            raise ConfigError(f'unknown model stage {stage!r}')
        name = override if override is not None else self._defaults.models.get(stage)
        if name is None:
            raise ConfigError(f'defaults.models.{stage}: no model configured')
        self.spec(name, kind='decision' if stage == 'decision' else 'chat')
        return name

    def spec(self, name: str, *, kind: str | None = None) -> ModelSpec:
        if name not in self._specs:
            raise NotFound(f'unknown model {name!r}')
        spec = self._specs[name]
        if kind is not None and spec.kind != kind:
            raise ConfigError(f'model {name!r} must have kind {kind}')
        return spec

    def chat(self, name: str) -> ChatModel:
        spec = self.spec(name, kind='chat')
        if name not in self._chats:
            self._chats[name] = ChatModel(name, spec, self._http, self.redact, self._clock)
        return self._chats[name]

    def decision(self, name: str) -> DecisionModel:
        spec = self.spec(name, kind='decision')
        if name not in self._decisions:
            self._decisions[name] = DecisionModel(name, spec, self._http, self.redact, self._clock)
        return self._decisions[name]
