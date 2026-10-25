"""Summarize processor through Service, injected IMAP factory and HTTP only."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from test_fetch_imap import FakeIMAP, Mailbox, NOW, imap_config, mail
from test_models import CHAT_URL, chat_reply, model_config
from tentacles.service import TentaclesService


def summary_config(**processor):
    return {**model_config(), **imap_config(),
            'personas': {'default': {'profile': 'Project mail and concrete actions'}},
            'pipelines': [{'id': 'mail-summary', 'name': 'Mail brief', 'sources': ['inbox'],
                           'persona': 'default', 'window': '7d', 'filters': [],
                           'processor': {'type': 'summarize', **processor}}]}


def row(item_id, *, summary='Useful summary', action_required=False, deadline=None, todos=None):
    return {'id': item_id, 'summary': summary, 'action_required': action_required,
            'deadline': deadline, 'todos': todos or []}


def test_summarize_renders_sections_deadline_order_metadata_and_consumes_once(write_config, web):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={
        1: mail(subject='Later action', message_id='<later>', body='Approve the budget by November 15.'),
        2: mail(subject='Earlier action', message_id='<earlier>', body='Send results by October 28.'),
        3: mail(subject='FYI launch', message_id='<fyi>', body='The launch succeeded.'),
    })})
    seen = []

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        response = []
        for item in payload['items']:
            if item['title'] == 'Later action':
                response.append(row(item['id'], summary='Later summary', action_required=True,
                                    deadline='2026-11-15', todos=['Approve budget']))
            elif item['title'] == 'Earlier action':
                response.append(row(item['id'], summary='Earlier summary', action_required=True,
                                    deadline='2026-10-28', todos=['Send results']))
            else:
                response.append(row(item['id'], summary='Launch summary'))
        return httpx.Response(200, json=chat_reply(json.dumps({'items': response})))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 3
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok' and result.stats['consumed'] == 3
            report = service.get_report(result.report_id)
            assert report.kind == 'summarize' and report.pipeline_id == 'mail-summary'
            text = report.markdown
            assert all(heading in text for heading in ['## 需要处理', '## 仅供参考', '## 未能解析'])
            assert text.index('Earlier summary') < text.index('Later summary') < text.index('## 仅供参考')
            assert text.index('Launch summary') > text.index('## 仅供参考')
            assert '- [ ] Send results' in text and '- [ ] Approve budget' in text
            assert 'alice@example.test' in text and 'INBOX' in text and 'UIDVALIDITY' in text
            assert all(message_id in text for message_id in ['earlier', 'later', 'fyi'])
            assert len(report.item_ids) == 3 and report.usage.total_tokens == 15 and report.cost == 0.01
            assert Path(report.path).read_text(encoding='utf-8') == text
            assert Path(report.path).parent.name == 'mail-summary' and '-summarize-' in Path(report.path).name
            for item_id in report.item_ids:
                assert service.get_item(item_id).evaluations[0].reported_at is not None
            assert service.list_pipelines()[0].pending == 0
            assert (await service.run_pipeline('mail-summary')).status == 'no_new'
            assert len(seen) == 1 and len(seen[0]['items']) == 3
            assert service.list_reports().total == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_batches_are_ten_bodies_bounded_and_bad_batches_degrade_atomically(write_config, web):
    long_body = 'Concrete mail content ' + 'x' * 12000 + ' BODY_END'
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={i: mail(subject=f'Mail {i:02d}', message_id=f'<mail-{i}>',
                                                        body=long_body) for i in range(1, 24)})})
    seen = []

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        rows = [row(item['id'], summary=f'Good summary {item["id"]}') for item in payload['items']]
        if len(seen) == 1:
            rows[-1]['id'] = 'not-an-input-id'
            text = json.dumps({'items': rows})
        elif len(seen) == 2:
            text = 'unparseable response containing private-test-key'
        else:
            text = '```json\n' + json.dumps({'items': list(reversed(rows))}) + '\n```'
        return httpx.Response(200, json=chat_reply(text))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config(max_items=30))

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 23
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok' and result.stats['consumed'] == 23
            assert [len(payload['items']) for payload in seen] == [10, 10, 3]
            assert all(len(item['content']) < len(long_body) and 'BODY_END' not in item['content']
                       for payload in seen for item in payload['items'])
            report = service.get_report(result.report_id)
            failed_part = report.markdown.split('## 未能解析', 1)[1]
            information_part = report.markdown.split('## 仅供参考', 1)[1].split('## 未能解析', 1)[0]
            by_id = {item.id: item for item in service.list_items().items}
            for payload in seen[:2]:
                for item in payload['items']:
                    assert by_id[item['id']].title in failed_part
                    assert f'Good summary {item["id"]}' not in report.markdown
            for item in seen[2]['items']:
                assert f'Good summary {item["id"]}' in information_part
            assert report.usage.total_tokens == 45 and report.cost == pytest.approx(0.03)
            assert result.stats['usage']['total_tokens'] == 45 and result.stats['cost'] == pytest.approx(0.03)
            assert 'private-test-key' not in report.markdown
            assert set(report.item_ids) == set(by_id)
            assert all(service.get_item(item_id).evaluations[0].reported_at is not None for item_id in report.item_ids)
            assert (await service.run_pipeline('mail-summary')).status == 'no_new'
            assert len(seen) == 3
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_custom_prompt_and_model_override_honor_common_processor_options(write_config, web):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(subject='Custom mail')})})
    config = summary_config(model='summary-model', language='en-US', instructions='Only concrete tasks',
                            prompt_file='summary-prompt.md')
    config['models']['summary-model'] = {**config['models']['fast'], 'model': 'configured-summary'}
    path = write_config(config)
    (path.parent / 'summary-prompt.md').write_text('''[system]
Custom summarizer
[user]
{"custom":true,"items":{{items|tojson}},"persona":{{persona|tojson}},"tags":{{tags|tojson}},
"now":{{now|tojson}},"pipeline":{{pipeline|tojson}}}
''', encoding='utf-8')
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        payload = json.loads(body['messages'][1]['content'])
        assert payload['custom'] is True and payload['pipeline']['id'] == 'mail-summary'
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [
            row(item['id'], summary='Custom summary') for item in payload['items']]})))

    web.handle(CHAT_URL, respond)

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok' and 'Custom summary' in service.get_report(result.report_id).markdown
            assert seen[0]['model'] == 'configured-summary'
            system = seen[0]['messages'][0]['content']
            assert system.startswith('Custom summarizer') and 'en-US' in system and 'Only concrete tasks' in system
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_action_order_uses_deadline_timezones_and_unknowns_last(write_config, web):
    deadlines = {'Earliest': '2026-10-30T09:30:00+09:00',
                 'Next': '2026-10-30T09:00:00+08:00',
                 'Naive': '2026-10-30T12:00:00', 'Date only': '2026-10-30', 'Unknown': None}
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={i: mail(subject=title, message_id=f'<{i}>')
                                                 for i, title in enumerate(deadlines, 1)})})

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [
            row(item['id'], summary=f'Summary {item["title"]}', action_required=True,
                deadline=deadlines[item['title']], todos=['Review']) for item in payload['items']]})))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok'
            report = service.get_report(result.report_id)
            positions = [report.markdown.index(f'Summary {title}') for title in deadlines]
            assert positions == sorted(positions)
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', [
    {}, {'items': []}, {'items': ['not an object']},
    {'summary': ' '}, {'action_required': 'false'}, {'action_required': 1},
    {'deadline': '2026-02-30'}, {'deadline': 'tomorrow'}, {'deadline': '2026-10-30T25:00'}, {'deadline': '2026-10-30T09:00+00:80'},
    {'todos': [' ']}, {'todos': 'Call team'}, {'unexpected': 'field'},
])
def test_invalid_model_schema_degrades_whole_batch_and_consumes_every_item(write_config, web, invalid):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(message_id='<one>'), 2: mail(message_id='<two>')})})

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        rows = [row(item['id']) for item in payload['items']]
        if not invalid or 'items' in invalid:
            reply = invalid
        else:
            rows[0].update(invalid)
            reply = {'items': rows}
        return httpx.Response(200, json=chat_reply(json.dumps(reply)))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok' and result.stats['consumed'] == 2
            report = service.get_report(result.report_id)
            failed = report.markdown.split('## 未能解析', 1)[1]
            assert all(item_id in failed for item_id in report.item_ids)
            assert 'Useful summary' not in report.markdown
            assert report.cost == 0.01 and report.usage.total_tokens == 15
            assert (await service.run_pipeline('mail-summary')).status == 'no_new'
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['http', 'content'])
def test_generation_failure_keeps_all_pending_and_records_known_billing(write_config, web, caplog, failure):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={i: mail(message_id=f'<mail-{i}>') for i in range(1, 12)})})
    seen, fail = [], [True]

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        if len(seen) == 2 and fail[0]:
            if failure == 'http':
                return httpx.Response(400, text='private mail body secret-app-password private-test-key')
            return httpx.Response(200, json=chat_reply(None))
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [
            row(item['id']) for item in payload['items']]})))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'error' and result.report_id is None
            assert service.list_reports().total == 0 and service.list_pipelines()[0].pending == 11
            assert all(service.get_item(item.id).evaluations[0].reported_at is None for item in service.list_items().items)
            [run] = service.list_runs(kind='pipeline')
            assert run.status == 'failed' and run.cost == pytest.approx(0.01 if failure == 'http' else 0.02)
            assert run.stats['usage']['total_tokens'] == (15 if failure == 'http' else 30)
            assert not list(service.config.reports_dir.glob('**/*.md'))
            public = run.model_dump_json() + result.model_dump_json() + caplog.text
            assert not any(secret in public for secret in ['private mail body', 'secret-app-password', 'private-test-key'])
            fail[0] = False
            retried = await service.run_pipeline('mail-summary')
            assert retried.status == 'ok' and retried.stats['consumed'] == 11
            report = service.get_report(retried.report_id)
            assert report.usage.total_tokens == 30 and report.cost == pytest.approx(0.02)
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_repeated_cancellation_drains_http_cleanup_and_preserves_pending_mail(write_config):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={i: mail(message_id=f'<mail-{i}>') for i in range(1, 12)})})
    path = write_config(summary_config())

    async def scenario():
        entered, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
        seen = []

        async def respond(request):
            payload = json.loads(json.loads(request.content)['messages'][1]['content'])
            seen.append(payload)
            if len(seen) == 2:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
                    cleaned.set()
            return httpx.Response(200, json=chat_reply(json.dumps({'items': [
                row(item['id']) for item in payload['items']]})))

        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        pending = None
        try:
            await service.fetch_sources()
            pending = asyncio.create_task(service.run_pipeline('mail-summary'))
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            await asyncio.wait_for(cleaning.wait(), 2)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done() and not cleaned.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert cleaned.is_set() and service.list_reports().total == 0
            assert service.list_pipelines()[0].pending == 11
            [run] = service.list_runs(kind='pipeline')
            assert run.status == 'failed' and run.cost == 0.01 and run.stats['usage']['total_tokens'] == 15
            assert (await service.run_pipeline('mail-summary')).status == 'ok'
        finally:
            release.set()
            if pending is not None and not pending.done():
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
            await service.aclose()

    asyncio.run(scenario())


def test_structured_fields_are_literal_and_secret_redaction_cannot_create_markdown_links(write_config, web):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(subject='Title [not a link]')})})

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [row(
            item['id'], summary='!private-test-key(https://tracker.example.test/pixel) <script>alert(1)</script>\x1bc',
            action_required=True, todos=['private-test-key(https://tracker.example.test/click)\n## forged\x07'])
            for item in payload['items']]})))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok'
            text = service.get_report(result.report_id).markdown
            assert 'private-test-key' not in text and '<script>' not in text
            assert '\x1b' not in text and '\x07' not in text
            assert '![REDACTED](' not in text and '[REDACTED](https://' not in text
            assert r'\[REDACTED\]' in text and r'\#\# forged' in text
            assert text.count('## 需要处理') == text.count('## 仅供参考') == text.count('## 未能解析') == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_oversized_model_output_degrades_without_echoing_or_reparsing_it(write_config, web):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail()})})

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        text = ' ' * 1_000_001 + json.dumps({'items': [row(item['id'], summary='Oversized response summary')
                                                     for item in payload['items']]})
        return httpx.Response(200, json=chat_reply(text))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok' and result.stats['consumed'] == 1
            report = service.get_report(result.report_id)
            assert report.item_ids[0] in report.markdown.split('## 未能解析', 1)[1]
            assert 'Oversized response summary' not in report.markdown
            assert report.cost == 0.01
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_nonmail_provenance_links_are_safe_and_do_not_send_url_credentials(write_config, web):
    from conftest import rss_feed
    feed = 'https://news.example.test/rss'
    config = summary_config()
    config['sources'].append({'id': 'news', 'type': 'rss', 'url': feed, 'schedule': '1h'})
    config['pipelines'][0]['sources'] = ['news']
    web.serve(feed, rss_feed(
        {'guid': 'research', 'title': 'Robotics research advances', 'link': 'https://articles.example.test/robot_(edition)'},
        {'guid': 'unsafe', 'title': 'Ocean expedition journal', 'link': 'javascript:alert(1)'},
        {'guid': 'credential', 'title': 'Agricultural harvest statistics',
         'link': 'https://reader:private-test-key@articles.example.test/private'}))
    seen = []

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [row(item['id']) for item in payload['items']]})))

    web.handle(CHAT_URL, respond)
    path = write_config(config)

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=FakeIMAP().factory, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources(['news']))[0].new == 3
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok' and result.stats['consumed'] == 3
            text = service.get_report(result.report_id).markdown
            assert '[原文](<https://articles.example.test/robot_(edition)>)' in text
            assert 'javascript:' not in text and 'private-test-key' not in text and 'reader:' not in text
            assert text.count('**原文定位：**') == 2
            outgoing = {item['title']: item['url'] for item in seen[0]['items']}
            assert outgoing['Ocean expedition journal'] == outgoing['Agricultural harvest statistics'] == ''
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_processor_limit_and_consumption_survive_parse_failure_and_persona_change(write_config, web):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={i: mail(message_id=f'<mail-{i}>') for i in range(1, 6)})})
    seen = []

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        text = 'broken JSON' if len(seen) == 1 else json.dumps({'items': [row(item['id']) for item in payload['items']]})
        return httpx.Response(200, json=chat_reply(text))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config(max_items=2))

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            consumed = []
            for expected in [2, 2, 1]:
                result = await service.run_pipeline('mail-summary')
                assert result.status == 'ok' and result.stats['consumed'] == expected
                consumed.extend(service.get_report(result.report_id).item_ids)
            assert len(consumed) == len(set(consumed)) == 5
            assert [len(payload['items']) for payload in seen] == [2, 2, 1]
            await service.set_persona('default', profile='Changed priorities', skip_tags=True)
            assert (await service.run_pipeline('mail-summary')).status == 'no_new'
            await service.filter_pipeline('mail-summary', reevaluate=True, since='7d')
            assert (await service.run_pipeline('mail-summary')).status == 'no_new'
            assert len(seen) == 3 and service.list_reports().total == 3
            assert all(e.reported_at is not None for item_id in consumed for e in service.get_item(item_id).evaluations)
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('deadline', ['2026-10-30T09:00+0800', '2026-10-30T09:00+08'])
def test_iso_deadline_offsets_are_consistent_on_supported_python_versions(write_config, web, deadline):
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail()})})

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [
            row(item['id'], summary='Temporal summary', action_required=True, deadline=deadline)
            for item in payload['items']]})))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok'
            text = service.get_report(result.report_id).markdown
            assert 'Temporal summary' in text and deadline in text
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_source_time_at_datetime_boundary_falls_back_to_utc_without_losing_report(write_config, web):
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['Subject'], msg['Message-ID'] = 'Far future metadata', '<future>'
    msg['From'] = 'Alice <alice@example.test>'
    msg['Date'] = 'Fri, 31 Dec 9999 23:59:59 +0000'
    msg.set_content('A message with unusual date metadata')
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: msg.as_bytes()})})

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [row(item['id']) for item in payload['items']]})))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok' and result.stats['consumed'] == 1
            assert '9999-12-31T23:59:59+00:00' in service.get_report(result.report_id).markdown
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_all_concrete_todos_are_kept_without_an_arbitrary_per_mail_count_limit(write_config, web):
    todos = [f'Process task {i:02d}' for i in range(1, 62)]
    fake = FakeIMAP({'"INBOX"': Mailbox(messages={1: mail(body='\n'.join(todos))})})

    def respond(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json=chat_reply(json.dumps({'items': [
            row(item['id'], summary='Project task checklist', action_required=True, todos=todos)
            for item in payload['items']]})))

    web.handle(CHAT_URL, respond)
    path = write_config(summary_config())

    async def scenario():
        service = TentaclesService.open(path, env={'MAIL_PASSWORD': 'secret-app-password'},
                                        imap_factory=fake.factory, transport=web.transport(), clock=lambda: NOW)
        try:
            await service.fetch_sources()
            result = await service.run_pipeline('mail-summary')
            assert result.status == 'ok'
            text = service.get_report(result.report_id).markdown
            assert text.count('- [ ]') == 61 and all(todo in text for todo in todos)
        finally:
            await service.aclose()

    asyncio.run(scenario())
