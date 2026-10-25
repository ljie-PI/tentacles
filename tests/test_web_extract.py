"""LLM web extraction through Service; page and model share injected HTTP."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from test_fetch_web import NOW, URL, web_config
from test_models import CHAT_URL, chat_reply, model_config
from tentacles.errors import ConfigError
from tentacles.service import TentaclesService


def extraction_config(**options):
    return {**model_config(), **web_config(mode='llm', **options)}


def extracted(title, url, *, summary='Source summary', published=None):
    return {'title': title, 'url': url, 'summary': summary, 'published': published}


def test_extract_grounds_links_cleans_html_tracks_usage_and_caches_unchanged_page(write_config, web):
    web.serve(URL, '''<html><title>Mixed news</title><nav><a href="/navigation">NAV_JUNK</a></nav>
<script>SCRIPT_JUNK</script><style>STYLE_JUNK</style><main>
<p>Robotics research advances</p><a href="/robot?utm_source=x#fragment">Article one</a>
<a href="https://news.example.test/ocean">Ocean expedition report</a>
<footer>FOOTER_JUNK</footer></main></html>''')
    seen = []

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [
            extracted('Robotics research advances', '/robot', summary='Robotics findings',
                      published='2026-10-24T09:00:00+08:00'),
            extracted('Ocean expedition report', 'https://news.example.test/ocean'),
            extracted('Invented item', 'https://invented.example.test/fake'),
        ]})))

    web.handle(CHAT_URL, respond)
    path = write_config(extraction_config())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            first = (await service.fetch_sources())[0]
            assert first.status == 'ok' and first.new == 2
            assert first.stats['usage']['total_tokens'] == 15 and first.stats['cost'] == 0.01
            [run] = service.list_runs(kind='fetch')
            assert run.status == 'ok' and run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15
            details = {item.title: service.get_item(item.id) for item in service.list_items().items}
            assert set(details) == {'Robotics research advances', 'Ocean expedition report'}
            robot = details['Robotics research advances']
            assert robot.external_id == robot.url == 'https://news.example.test/robot'
            assert robot.kind == 'web' and robot.meta['web_mode'] == 'llm'
            assert robot.summary == robot.content == 'Robotics findings'
            assert robot.published == datetime(2026, 10, 24, 1, tzinfo=timezone.utc)
            assert 'https://news.example.test/robot' in seen[0]['text']
            assert not any(junk in seen[0]['text'] for junk in ['NAV_JUNK', 'SCRIPT_JUNK', 'STYLE_JUNK', 'FOOTER_JUNK'])
            again = (await service.fetch_sources())[0]
            assert again.status == 'ok' and again.new == 0 and again.stats['not_modified'] is True
            assert len(seen) == 1 and service.list_items().total == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_source_model_override_and_page_credentials_do_not_reach_provider(write_config):
    config = extraction_config(model='alternate', headers={'X-Private': 'page-secret'}, cookies={'session': 'page-cookie'})
    config['models']['alternate'] = {**config['models']['fast'], 'model': 'override-id',
                                      'base_url': 'https://news.example.test/api', 'api_key': 'alternate-secret'}
    model_url = 'https://news.example.test/api/chat/completions'
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if str(request.url) == URL:
            assert request.headers['x-private'] == 'page-secret' and 'page-cookie' in request.headers['cookie']
            return httpx.Response(200, text='<main><a href="/one">Robotics story</a></main>',
                                  headers={'set-cookie': 'server=private-session; Path=/'})
        assert str(request.url) == model_url
        body = json.loads(request.content)
        assert body['model'] == 'override-id' and request.headers['authorization'] == 'Bearer alternate-secret'
        assert 'x-private' not in request.headers and not request.headers.get('cookie')
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [extracted('Robotics story', '/one')]}),
                                                  model='served-extract'))

    path = write_config(config)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        try:
            assert calls == []  # Registry/config checks remain lazy.
            [result] = await service.fetch_sources()
            assert result.status == 'ok' and result.new == 1 and result.stats['models'] == ['served-extract']
            assert calls == [URL, model_url]
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('reference', ['missing', 'decision', None])
def test_extract_model_references_are_validated_before_opening_sources(write_config, web, reference):
    config = extraction_config(model=reference)
    if reference is None:
        config.pop('models')
        config.pop('defaults')
    with pytest.raises(ConfigError):
        TentaclesService.open(write_config(config), env={}, transport=web.transport())
    assert web.requests == []


@pytest.mark.parametrize('bad', [
    'not JSON private-test-key page-secret', '```json\n{"items":[]}\n```', '{"items":[],}',
    '{"items":[', '{}', '{"items":["not an object"]}',
])
def test_dirty_json_fails_source_keeps_cursor_and_retains_known_fee(write_config, web, caplog, bad):
    html = ['<main><a href="/one">Robotics story</a></main>']
    web.handle(URL, lambda request: httpx.Response(200, text=html[0]))
    fail, seen = [False], []

    def respond(request):
        seen.append(json.loads(request.content))
        text = bad if fail[0] else json.dumps({'items': [extracted('Robotics story', '/one'),
            *([extracted('Ocean expedition', '/two')] if '/two' in html[0] else [])]})
        return httpx.Response(200, json=chat_reply(text))

    web.handle(CHAT_URL, respond)
    path = write_config(extraction_config())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            original = html[0]
            html[0] += '<a href="/two">Ocean expedition</a>'
            fail[0] = True
            [failed] = await service.fetch_sources()
            assert failed.status == 'error' and failed.new == 0
            assert failed.stats['usage']['total_tokens'] == 15 and failed.stats['cost'] == 0.01
            [run, *_] = service.list_runs(kind='fetch')
            assert run.status == 'failed' and run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15
            public = failed.model_dump_json() + run.model_dump_json() + caplog.text
            assert 'private-test-key' not in public and 'page-secret' not in public
            assert service.list_items().total == 1
            html[0] = original
            same = (await service.fetch_sources())[0]
            assert same.status == 'ok' and same.stats['not_modified'] is True and len(seen) == 2
            html[0] += '<a href="/two">Ocean expedition</a>'
            fail[0] = False
            assert (await service.fetch_sources())[0].new == 1
            assert len(seen) == 3 and service.list_items().total == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_input_is_bounded_and_changed_pages_do_not_reproduce_known_items(write_config, web):
    html = ['<main><a href="/one">Robotics research</a><p>' + 'x' * 1000 + ' END_MARKER</p></main>']
    web.handle(URL, lambda request: httpx.Response(200, text=html[0]))
    seen = []

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        assert len(payload['text']) <= 150 and 'END_MARKER' not in payload['text']
        rows = [extracted('Robotics research', '/one')]
        if '/two' in payload['text']:
            rows += [extracted('Ocean expedition', '/two'), extracted('Duplicate URI', '/two?utm_source=x')]
        return httpx.Response(200, json=chat_reply(json.dumps({'items': rows})))

    web.handle(CHAT_URL, respond)
    path = write_config(extraction_config(max_input_chars=150))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            html[0] = html[0].replace('<main>', '<nav>Changed navigation</nav><script>ignore this</script><main>')
            assert (await service.fetch_sources())[0].stats['not_modified'] is True and len(seen) == 1
            html[0] = html[0].replace('<main>', '<main><a href="/two">Ocean expedition</a>')
            assert (await service.fetch_sources())[0].new == 1
            assert service.list_items().total == 2 and len(seen) == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_model_definition_change_invalidates_input_cache_but_key_rotation_does_not(write_config, web):
    web.serve(URL, '<main><a href="/one">Robotics story</a></main>')
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append(body['model'])
        rows = [] if body['model'] == 'test-chat' else [extracted('Robotics story', '/one')]
        return httpx.Response(200, json=chat_reply(json.dumps({'items': rows})))

    web.handle(CHAT_URL, respond)
    config = extraction_config()
    path = write_config(config)

    async def once():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            return (await service.fetch_sources())[0]
        finally:
            await service.aclose()

    async def scenario():
        assert (await once()).new == 0
        config['models']['fast']['model'] = 'better-extract'
        write_config(config)
        assert (await once()).new == 1
        config['models']['fast']['api_key'] = 'rotated-secret'
        write_config(config)
        assert (await once()).stats['not_modified'] is True
        assert seen == ['test-chat', 'better-extract']

    asyncio.run(scenario())


def test_changed_text_with_only_known_links_and_linkless_pages_skip_paid_extraction(write_config, web):
    html = ['<main><a href="/one">Robotics story</a><p>Initial text</p></main>']
    web.handle(URL, lambda request: httpx.Response(200, text=html[0]))
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [extracted('Robotics story', '/one')]})))

    web.handle(CHAT_URL, respond)
    path = write_config(extraction_config())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            html[0] = html[0].replace('Initial text', 'A changed timestamp around the same known article')
            known = (await service.fetch_sources())[0]
            assert known.status == 'ok' and known.new == 0 and known.stats['no_new_links'] is True
            assert len(seen) == 1 and 'usage' not in known.stats
            html[0] = '<main>Text with no valid links<a href="javascript:alert(1)">invalid</a></main>'
            empty = (await service.fetch_sources())[0]
            assert empty.status == 'ok' and empty.new == 0 and empty.stats['no_links'] is True
            assert len(seen) == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['http', 'content'])
def test_model_failures_are_safe_and_keep_known_usage_before_body_validation(write_config, web, caplog, failure):
    web.serve(URL, '<main><a href="/one">Robotics story</a></main>')
    failing = [True]

    def respond(request):
        if failing[0]:
            if failure == 'http':
                return httpx.Response(400, text='private response page-password private-test-key')
            return httpx.Response(200, json=chat_reply(None))
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [extracted('Robotics story', '/one')]})))

    web.handle(CHAT_URL, respond)
    path = write_config(extraction_config())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            [result] = await service.fetch_sources()
            assert result.status == 'error' and service.list_items().total == 0
            [run] = service.list_runs(kind='fetch')
            assert run.status == 'failed'
            assert run.cost == (0.01 if failure == 'content' else None)
            if failure == 'content':
                assert run.stats['usage']['total_tokens'] == result.stats['usage']['total_tokens'] == 15
            public = result.model_dump_json() + run.model_dump_json() + caplog.text
            assert not any(secret in public for secret in ['private response', 'page-password', 'private-test-key'])
            failing[0] = False
            assert (await service.fetch_sources())[0].new == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_repeated_cancel_during_details_drains_cleanup_and_records_extraction_fee(write_config):
    path = write_config(extraction_config(fetch_content=True))

    async def scenario():
        entered, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
        model_calls, detail_calls = [], []

        async def respond(request):
            if str(request.url) == URL:
                return httpx.Response(200, text='<main><a href="/one">Robotics story</a></main>')
            if str(request.url) == CHAT_URL:
                model_calls.append(request)
                return httpx.Response(200, json=chat_reply(json.dumps({'items': [extracted('Robotics story', '/one')]})))
            detail_calls.append(request)
            if len(detail_calls) == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
                    cleaned.set()
            return httpx.Response(200, text='<article>Complete body</article>')

        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        pending = None
        try:
            pending = asyncio.create_task(service.fetch_sources())
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            await asyncio.wait_for(cleaning.wait(), 2)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done() and not cleaned.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert cleaned.is_set() and service.list_items().total == 0
            [run] = service.list_runs(kind='fetch')
            assert run.status == 'failed' and run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15
            assert (await service.fetch_sources())[0].new == 1 and len(model_calls) == 2
        finally:
            release.set()
            if pending is not None and not pending.done():
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
            await service.aclose()

    asyncio.run(scenario())


def test_expired_source_owner_keeps_late_fee_without_replacing_new_cursor(write_config):
    from datetime import timedelta
    path = write_config(extraction_config())
    now = [NOW]

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        new_calls = []

        async def old_transport(request):
            if str(request.url) == URL:
                return httpx.Response(200, text='<main><a href="/old">Old robotics story</a></main>')
            entered.set()
            await release.wait()
            return httpx.Response(200, json=chat_reply(json.dumps({'items': [extracted('Old robotics story', '/old')]}),
                                                      model='old-extract'))

        def new_transport(request):
            if str(request.url) == URL:
                return httpx.Response(200, text='<main><a href="/new">New ocean story</a></main>')
            new_calls.append(request)
            return httpx.Response(200, json=chat_reply(json.dumps({'items': [extracted('New ocean story', '/new')]}),
                                                      model='new-extract'))

        old = TentaclesService.open(path, env={}, transport=httpx.MockTransport(old_transport), clock=lambda: now[0])
        new = TentaclesService.open(path, env={}, transport=httpx.MockTransport(new_transport), clock=lambda: now[0])
        pending = None
        try:
            pending = asyncio.create_task(old.fetch_sources())
            await asyncio.wait_for(entered.wait(), 2)
            now[0] += timedelta(hours=2)
            assert (await new.fetch_sources())[0].new == 1
            release.set()
            assert (await pending)[0].status == 'error'
            [item] = new.list_items().items
            assert item.title == 'New ocean story'
            runs = new.list_runs(kind='fetch')
            assert {run.status for run in runs} == {'ok', 'failed'}
            expired = next(run for run in runs if run.status == 'failed')
            assert expired.cost == 0.01 and expired.stats['usage']['total_tokens'] == 15
            assert expired.stats['models'] == ['old-extract']
            assert new.list_sources()[0].last_status == 'ok'
            again = (await new.fetch_sources())[0]
            assert again.stats['not_modified'] is True and len(new_calls) == 1
        finally:
            release.set()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await old.aclose()
            await new.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', [
    {'title': ' '}, {'title': False}, {'summary': None}, {'summary': ['not text']},
    {'published': []}, {'extra': 'unsupported'},
])
def test_malformed_grounded_rows_fail_atomically_and_can_be_retried(write_config, web, invalid):
    web.serve(URL, '<main><a href="/one">Robotics story</a><a href="/two">Ocean expedition</a></main>')
    failing = [True]

    def respond(request):
        rows = [extracted('Robotics story', '/one'), extracted('Ocean expedition', '/two')]
        if failing[0]:
            rows[1].update(invalid)
        return httpx.Response(200, json=chat_reply(json.dumps({'items': rows})))

    web.handle(CHAT_URL, respond)
    path = write_config(extraction_config())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            failed = (await service.fetch_sources())[0]
            assert failed.status == 'error' and failed.stats['cost'] == 0.01
            assert service.list_items().total == 0
            failing[0] = False
            assert (await service.fetch_sources())[0].new == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_hallucinated_row_is_dropped_before_field_validation_and_generated_text_is_redacted(write_config, web):
    web.serve(URL, '<main><a href="/one">Robotics story</a></main>')
    web.serve(CHAT_URL, json.dumps(chat_reply(json.dumps({'items': [
        extracted('Robotics private-test-key', '/one', summary='Do not persist private-test-key'),
        {'url': 'https://invented.example.test/missing', 'title': False, 'summary': [], 'extra': 'garbage'},
    ]}))), headers={'content-type': 'application/json'})
    path = write_config(extraction_config())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            result = (await service.fetch_sources())[0]
            assert result.status == 'ok' and result.new == 1 and result.stats['dropped_links'] == 1
            [item] = service.list_items().items
            assert 'private-test-key' not in service.get_item(item.id).model_dump_json()
            assert '[REDACTED]' in item.title and '[REDACTED]' in item.summary
        finally:
            await service.aclose()

    asyncio.run(scenario())
