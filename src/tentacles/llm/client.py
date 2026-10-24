"""Thin HTTP model clients. No provider SDKs or hidden network boundary."""
from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

from tentacles.errors import ConfigError, UpstreamError
from tentacles.llm.config import ModelSpec
from tentacles.net import HttpPool
from tentacles.schemas import Completion, DecisionCompletion, TokenUsage
from tentacles.timeutil import to_utc


class ModelClient:
    def __init__(self, name: str, spec: ModelSpec, http: HttpPool, redact: Callable[[str], str],
                 clock: Callable[[], datetime]):
        self.name, self.spec, self._http, self._redact = name, spec, http, redact
        self._clock = clock

    async def _post(self, path: str, body: dict[str, Any], *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        key = self.spec.api_key.get_secret_value()
        if not key:
            raise UpstreamError(f'model {self.name!r}: api_key is not configured')
        auth = headers or {'Authorization': f'Bearer {key}'}
        for attempt in range(self.spec.retries + 1):
            response: httpx.Response | None = None
            try:
                response = await self._http.client().post(
                    self.spec.endpoint + path, json=body, headers=auth, timeout=self.spec.timeout,
                    follow_redirects=False)
            except httpx.RequestError as exc:
                if attempt == self.spec.retries:
                    # Exception strings can contain credentials, headers or full URLs.
                    raise UpstreamError(f'model {self.name!r}: {type(exc).__name__}') from None
            else:
                if response.is_success:
                    break
                retryable = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
                if not retryable or attempt == self.spec.retries:
                    summary = self._redact(response.text)[:500]
                    raise UpstreamError(f'model {self.name!r}: HTTP {response.status_code}: {summary}')
            await asyncio.sleep(_retry_delay(response, attempt, self._clock()))
        assert response is not None
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError('expected an object')
            return data
        except ValueError:
            raise UpstreamError(f'model {self.name!r}: invalid JSON response') from None

    def _result(self, data: dict[str, Any], text: str, *, anthropic: bool = False) -> Completion:
        usage = data.get('usage') or {}
        input_tokens = usage.get('input_tokens' if anthropic else 'prompt_tokens', 0)
        output_tokens = usage.get('output_tokens' if anthropic else 'completion_tokens', 0)
        return Completion(text=text, model=data.get('model') or self.spec.model,
                          usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                                           total_tokens=usage.get('total_tokens', input_tokens + output_tokens)),
                          cost=usage.get('cost', data.get('cost')))


class ChatModel(ModelClient):
    async def complete(self, messages: list[dict[str, str]], *, system: str | None = None,
                       max_tokens: int | None = None, temperature: float | None = None) -> Completion:
        if not messages or any(m.get('role') not in {'system', 'user', 'assistant'} or
                               not isinstance(m.get('content'), str) for m in messages):
            raise ConfigError('messages require system/user/assistant roles and string content')
        if max_tokens is not None and (isinstance(max_tokens, bool) or max_tokens < 1):
            raise ConfigError('max_tokens must be a positive integer')
        anthropic = self.spec.provider == 'anthropic'
        prepared = ([{'role': 'system', 'content': system}] if system is not None else []) + messages
        headers = None
        path = '/chat/completions'
        body: dict[str, Any] = {'model': self.spec.model, 'messages': prepared,
                                'max_tokens': max_tokens if max_tokens is not None else self.spec.max_tokens}
        if anthropic:
            systems = [m['content'] for m in prepared if m['role'] == 'system']
            body['messages'] = [m for m in prepared if m['role'] != 'system']
            if systems:
                body['system'] = '\n'.join(systems)
            path = '/messages'
            headers = {'x-api-key': self.spec.api_key.get_secret_value(), 'anthropic-version': '2023-06-01'}
        temp = temperature if temperature is not None else self.spec.temperature
        if temp is not None:
            if not math.isfinite(temp) or not 0 <= temp <= 2:
                raise ConfigError('temperature must be between 0 and 2')
            body['temperature'] = temp
        body.update(self.spec.extra_body)
        token_field = 'max_tokens'
        if not anthropic and 'max_completion_tokens' in body:
            body.pop('max_tokens', None)
            token_field = 'max_completion_tokens'
        if max_tokens is not None:
            body[token_field] = max_tokens
        if temperature is not None:
            body['temperature'] = temperature
        data = await self._post(path, body, headers=headers)
        try:
            content = data['content'] if anthropic else data['choices'][0]['message']['content']
            if isinstance(content, list):
                content = ''.join(block['text'] for block in content
                                  if block.get('type') in {'text', 'output_text'} and isinstance(block.get('text'), str))
            if not isinstance(content, str):
                raise ValueError('no text')
            return self._result(data, content, anthropic=anthropic)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise UpstreamError(f'model {self.name!r}: invalid chat response') from None


class DecisionModel(ModelClient):
    async def decide(self, state: str | dict[str, Any] | list[Any],
                     questions: dict[str, Any]) -> DecisionCompletion:
        body = {'provider': {'data_collection': 'deny'}, **self.spec.extra_body,
                'model': self.spec.model, 'state': state, 'questions': questions}
        data = await self._post('/alpha/decisions', body)
        try:
            answers = data['answers']
            if not isinstance(answers, dict) or any(q not in answers for q in questions):
                raise ValueError('missing answers')
            result = self._result(data, '', anthropic=True)
            return DecisionCompletion(answers=answers, model=result.model, usage=result.usage, cost=result.cost)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise UpstreamError(f'model {self.name!r}: invalid decision response') from None


def _retry_delay(response: httpx.Response | None, attempt: int, now: datetime) -> float:
    value = response.headers.get('Retry-After') if response is not None else None
    if value is not None:
        try:
            seconds = float(value)
        except ValueError:
            try:
                deadline = parsedate_to_datetime(value)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                seconds = (deadline - to_utc(now)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = float('nan')
        if math.isfinite(seconds):
            return min(60.0, max(0.0, seconds))
    return min(30.0, 2 ** min(attempt, 5) + random.random())
