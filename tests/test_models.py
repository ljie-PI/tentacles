"""LLM capabilities at the Service seam; doubles only replace outbound HTTP."""
import asyncio
import json

import httpx
import pytest

from tentacles.errors import ConfigError, NotFound, UpstreamError
from tentacles.service import TentaclesService

CHAT_URL = 'https://llm.test/v1/chat/completions'
ANTHROPIC_URL = 'https://claude.test/v1/messages'
DECISION_URL = 'https://router.test/api/alpha/decisions'


def model_config(**overrides):
    fast = {'provider': 'openai', 'model': 'test-chat', 'base_url': 'https://llm.test/v1',
            'api_key': 'private-test-key', 'retries': 0, **overrides}
    return {'models': {
        'fast': fast,
        'decision': {'provider': 'openrouter', 'kind': 'decision', 'model': 'typesafe/jev-1.13',
                     'base_url': 'https://router.test/api', 'api_key': '', 'retries': 0}},
        'defaults': {'models': dict(extract='fast', filter='fast', decision='decision',
                                   persona='fast', process='fast')}}


def chat_reply(text='OK', **overrides):
    return {'model': 'actual-chat', 'choices': [{'message': {'content': text}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15, 'cost': 0.01},
            **overrides}


def test_models_list_reports_configuration_without_secrets(write_config, open_service):
    service = open_service(write_config(model_config()))
    rows = service.list_models()
    fast = next(row for row in rows if row.name == 'fast')
    assert fast.kind == 'chat' and fast.provider == 'openai'
    assert fast.key_configured is True
    assert 'private-test-key' not in json.dumps([r.model_dump() for r in rows])
    assert 'api_key' not in fast.model_dump()
    assert next(row for row in rows if row.name == 'decision').key_configured is False


def test_openai_complete_by_stage_constructs_request_and_parses_usage(web, write_config, open_service):
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers['authorization'] == 'Bearer private-test-key'
        assert body['model'] == 'test-chat'
        assert body['messages'] == [{'role': 'system', 'content': 'Be brief'},
                                    {'role': 'user', 'content': 'Hello'}]
        assert body['max_tokens'] == 99 and body['temperature'] == 0.3
        assert body['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json=chat_reply([{'type': 'text', 'text': 'Hello '},
                                                   {'type': 'text', 'text': 'world'}]))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(model_config(temperature=0.3, max_tokens=99,
        extra_body={'response_format': {'type': 'json_object'}})))
    result = asyncio.run(service.complete_model([{'role': 'user', 'content': 'Hello'}],
                                                 stage='filter', system='Be brief'))
    assert result.text == 'Hello world' and result.model == 'actual-chat'
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.total_tokens) == (12, 3, 15)
    assert result.cost == 0.01
    assert len(seen) == 1


def test_anthropic_moves_system_to_top_level_and_joins_all_text_blocks(web, write_config, open_service):
    def respond(request):
        body = json.loads(request.content)
        assert request.headers['x-api-key'] == 'private-test-key'
        assert request.headers['anthropic-version'] == '2023-06-01'
        assert 'authorization' not in request.headers
        assert body['system'] == 'Main\nSecond'
        assert body['max_tokens'] == 123
        assert body['messages'] == [{'role': 'user', 'content': 'Hello'}]
        return httpx.Response(200, json={'model': 'claude-actual', 'content': [
            {'type': 'thinking', 'thinking': 'hidden'}, {'type': 'text', 'text': 'one'},
            {'type': 'text', 'text': 'two'}], 'usage': {'input_tokens': 10, 'output_tokens': 4}})

    web.handle(ANTHROPIC_URL, respond)
    service = open_service(write_config(model_config(provider='anthropic',
                                      base_url='https://claude.test/v1')))
    result = asyncio.run(service.complete_model([{'role': 'system', 'content': 'Second'},
        {'role': 'user', 'content': 'Hello'}], system='Main', max_tokens=123))
    assert (result.text, result.model, result.usage.total_tokens, result.cost) == ('onetwo', 'claude-actual', 14, None)


@pytest.mark.parametrize('status', [408, 409, 425, 429, 500, 503, 524, 529])
def test_transient_statuses_retry_and_preserve_usage(web, write_config, open_service, status):
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(status, headers={'Retry-After': '0'}, text='temporary')
        return httpx.Response(200, json=chat_reply())

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(model_config(retries=1)))
    assert asyncio.run(service.complete_model([{'role': 'user', 'content': 'Hi'}])).text == 'OK'
    assert len(attempts) == 2


@pytest.mark.parametrize('text,expected', [
    ('{"a": 1}', {'a': 1}),
    ('Here you go:\n```json\n{"tags":["x",],}\n```', {'tags': ['x']}),
    ('Explanation before {"inside": {"array":[1,2]}} afterwards', {'inside': {'array': [1, 2]}}),
    ('Text {"s":"escaped \\\" [ }", "other":1,} final', {'s': 'escaped " [ }', 'other': 1}),
    ('```json\n{"s":",}","a":[1,],}\n```', {'s': ',}', 'a': [1]}),
    ('[note] result: [{"x":1}, {"x":2},] end', [{'x': 1}, {'x': 2}]),
])
def test_dirty_json_is_extracted_through_model_operation(web, write_config, open_service, text, expected):
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(text)))
    service = open_service(write_config(model_config()))
    result = asyncio.run(service.complete_model([{'role': 'user', 'content': 'Return JSON'}], json_output=True))
    assert result.data == expected


def test_model_probe_returns_latency_sample_and_usage(web, write_config, open_service):
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('Available')))
    service = open_service(write_config(model_config()))
    result = asyncio.run(service.test_model('fast'))
    assert result.ok and result.name == 'fast' and result.sample == 'Available'
    assert result.latency_ms >= 0 and result.error is None
    assert result.model == 'actual-chat' and result.usage.total_tokens == 15 and result.cost == 0.01


def test_decision_model_probe_uses_decisions_api_without_collecting_data(web, write_config, open_service):
    data = model_config()
    data['models']['decision']['api_key'] = 'router-test-key'

    def respond(request):
        body = json.loads(request.content)
        assert body['model'] == 'typesafe/jev-1.13'
        assert body['provider']['data_collection'] == 'deny'
        assert body['state'] and body['questions']['available']['type'] == 'noul'
        return httpx.Response(200, json={'model': 'jev-actual', 'answers': {
            'available': {'type': 'noul', 'noul': 0.99}}, 'usage': {
                'input_tokens': 40, 'output_tokens': 2, 'cost': 0.0001}})

    web.handle(DECISION_URL, respond)
    result = asyncio.run(open_service(write_config(data)).test_model('decision'))
    assert result.ok and json.loads(result.sample)['available']['noul'] == 0.99
    assert result.usage.input_tokens == 40 and result.cost == 0.0001


@pytest.mark.parametrize('stage,name,fragment', [('filter', 'missing', 'unknown model'),
    ('decision', 'fast', 'kind decision'), ('persona', 'decision', 'kind chat'),
    ('extract', None, 'default model is required')])
def test_default_references_and_kinds_are_validated_at_loading(write_config, stage, name, fragment):
    data = model_config()
    if name is None:
        del data['defaults']['models'][stage]
    else:
        data['defaults']['models'][stage] = name
    with pytest.raises(ConfigError, match=fragment):
        TentaclesService.open(write_config(data), env={})


def test_empty_keys_are_valid_until_called_and_name_is_in_upstream_error(write_config, open_service):
    service = open_service(write_config(model_config(api_key='')))
    assert service.check_config().ok
    with pytest.raises(UpstreamError, match='fast.*api_key'):
        asyncio.run(service.complete_model([{'role': 'user', 'content': 'Hello'}]))
    result = asyncio.run(service.test_model('fast'))
    assert not result.ok and 'fast' in result.error
    with pytest.raises(NotFound):
        asyncio.run(service.test_model('missing'))


@pytest.mark.parametrize('status', [400, 401, 402, 403, 404, 422])
def test_permanent_errors_are_not_retried_and_redact_keys(web, write_config, open_service, status):
    web.serve(CHAT_URL, 'provider said: private-test-key details ' + 'X' * 600, status=status)
    service = open_service(write_config(model_config(retries=2)))
    result = asyncio.run(service.test_model('fast'))
    assert not result.ok and str(status) in result.error and 'provider said' in result.error
    assert 'private-test-key' not in result.error and '[REDACTED]' in result.error
    assert len(result.error) < 550 and len(web.requests_to(CHAT_URL)) == 1


@pytest.mark.parametrize('error_type', [httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors_retry_without_exposing_exception_content(web, write_config, open_service, error_type):
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise error_type('private-test-key must never be displayed', request=request)
        return httpx.Response(200, json=chat_reply())

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(model_config(retries=1)))
    assert asyncio.run(service.test_model('fast')).ok
    assert len(attempts) == 2


@pytest.mark.parametrize('response', [{}, {'choices': []}, {'choices': [{'message': {'content': None}}]},
                                    {'choices': [{'message': {'content': 'a'}}], 'usage': {'prompt_tokens': -1}}])
def test_malformed_success_is_upstream_failure(web, write_config, open_service, response):
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=response))
    result = asyncio.run(open_service(write_config(model_config())).test_model('fast'))
    assert not result.ok and 'fast' in result.error


def test_invalid_generated_json_is_upstream_and_does_not_echo_output(web, write_config, open_service):
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply('private output, not JSON')))
    service = open_service(write_config(model_config()))
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(service.complete_model([{'role': 'user', 'content': 'Return JSON'}], json_output=True))
    assert 'private output' not in str(exc.value)


def test_explicit_call_options_override_configured_extra_body(web, write_config, open_service):
    def respond(request):
        body = json.loads(request.content)
        assert body['max_tokens'] == 13 and body['temperature'] == 0.8
        return httpx.Response(200, json=chat_reply())

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(model_config(extra_body={'max_tokens': 22, 'temperature': 0.2})))
    asyncio.run(service.complete_model([{'role': 'user', 'content': 'Hi'}], max_tokens=13, temperature=0.8))


@pytest.mark.parametrize('field,value', [('stream', True), ('model', 'another'), ('messages', [])])
def test_extra_body_cannot_replace_protocol_identity(write_config, field, value):
    with pytest.raises(ConfigError, match='extra_body'):
        TentaclesService.open(write_config(model_config(extra_body={field: value})), env={})


def test_openai_supports_modern_max_completion_tokens_in_extra_body(web, write_config, open_service):
    def respond(request):
        body = json.loads(request.content)
        assert body['max_completion_tokens'] == 100 and 'max_tokens' not in body
        return httpx.Response(200, json=chat_reply())

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(model_config(extra_body={'max_completion_tokens': 200})))
    asyncio.run(service.complete_model([{'role': 'user', 'content': 'Hi'}], max_tokens=100))


def test_http_date_retry_after_uses_the_injected_clock(write_config, web):
    from datetime import datetime, timezone
    now = datetime(2030, 1, 2, tzinfo=timezone.utc)
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={'Retry-After': 'Tue, 01 Jan 2030 00:00:00 GMT'})
        return httpx.Response(200, json=chat_reply())

    web.handle(CHAT_URL, respond)

    async def scenario():
        service = TentaclesService.open(write_config(model_config(retries=1)),
                                        transport=web.transport(), clock=lambda: now)
        try:
            result = await asyncio.wait_for(service.test_model('fast'), 0.2)
            assert result.ok and result.latency_ms == 0
            assert len(attempts) == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())
