"""Versioned personas through Service, using real SQLite and injected HTTP/time."""
import asyncio
import json

import httpx
import pytest
import yaml

from tentacles.errors import ConfigError, NotFound
from tentacles.service import TentaclesService


def test_seed_imports_once_and_database_wins_after_restart(write_config):
    path = write_config({'personas': {'default': {'profile': 'AI first',
                                                'include': ['AI', 'ai', 'Robotics']}}})

    async def scenario():
        service = TentaclesService.open(path, env={})
        try:
            [seed] = service.list_personas()
            assert seed.profile == 'AI first' and seed.version == 1 and seed.source == 'seed'
            assert seed.include == ['AI', 'Robotics']
            updated = await service.set_persona('default', profile='Health first', skip_tags=True)
            assert updated.version == 2 and updated.source == 'manual'
        finally:
            await service.aclose()
        write_config({'personas': {'default': {'profile_file': 'missing.md', 'include': ['Other']}}})
        service = TentaclesService.open(path, env={})
        try:
            latest = service.get_persona('default')
            assert latest.profile == 'Health first' and latest.include == ['AI', 'Robotics']
            assert len(service.persona_history('default')) == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_persona_can_be_created_edited_and_keywords_are_casefold_deduplicated(write_config, open_service):
    service = open_service(write_config({}))

    async def scenario():
        first = await service.set_persona('工作', profile='Tech', include_add=['AI', 'ai', 'Straße'],
                                          exclude_add=['Ads', 'ads'], skip_tags=True, note='created')
        assert first.version == 1 and first.source == 'manual' and first.note == 'created'
        assert first.include == ['AI', 'Straße'] and first.exclude == ['Ads']
        second = await service.set_persona('工作', include_remove=['aI'], include_add=['STRASSE', 'Science'],
                                           exclude_remove=['ADS'], note='keywords only')
        assert second.profile == 'Tech' and second.include == ['Straße', 'Science'] and second.exclude == []
        assert second.version == 2 and second.tag_status == 'unchanged'
        assert service.get_persona('工作', version=1).note == 'created'
        with pytest.raises(NotFound):
            service.get_persona('工作', version=10)
        with pytest.raises(ConfigError):
            await service.set_persona('工作')

    asyncio.run(scenario())


def tags(*names):
    return [{'tag': name, 'description': f'{name} topics'} for name in names]


BASE_TAGS = tags('AI', 'Robotics', 'Health', 'Science', 'Space')


def test_profile_change_extracts_and_updates_priority_tags_via_persona_model(write_config, open_service, web):
    from test_models import model_config, chat_reply, CHAT_URL
    received = []
    replies = [{'tags': BASE_TAGS}, {'keep': BASE_TAGS, 'add': tags('Energy'), 'remove': []}]

    def respond(request):
        received.append(json.loads(request.content))
        return httpx.Response(200, json=chat_reply(json.dumps(replies.pop(0))))

    web.handle(CHAT_URL, respond)
    service = open_service(write_config(model_config()))

    async def scenario():
        first = await service.set_persona('default', profile='AI, robots, health, science, space')
        assert [t.tag for t in first.tags] == ['AI', 'Robotics', 'Health', 'Science', 'Space']
        assert first.tag_status == 'ok' and first.tag_error is None
        second = await service.set_persona('default', profile='Add energy as the last priority')
        assert [t.tag for t in second.tags] == ['AI', 'Robotics', 'Health', 'Science', 'Space', 'Energy']
        assert second.source == 'manual' and second.version == 2
        before = len(received)
        await service.set_persona('default', include_add=['new keyword'])
        await service.set_persona('default', profile=second.profile, note='same profile')
        assert len(received) == before
        system = received[0]['messages'][0]['content']
        assert '5' in system and '20' in system and '优先' in system
        update_system = received[1]['messages'][0]['content']
        assert '原名' in update_system and 'keep' in update_system
        [latest, previous, *_] = service.list_runs(kind='persona')
        assert latest.status == previous.status == 'ok'

    asyncio.run(scenario())


def test_rollback_creates_new_version_and_export_contains_complete_snapshot(write_config, open_service):
    service = open_service(write_config({}))

    async def scenario():
        old = await service.set_persona('default', profile='Original', include_add=['AI'],
                                        exclude_add=['Ads'], skip_tags=True, note='first')
        await service.set_persona('default', profile='Changed', include_remove=['AI'],
                                  exclude_remove=['Ads'], include_add=['Health'], skip_tags=True)
        restored = await service.rollback_persona('default', 1, note='restore')
        assert restored.version == 3 and restored.source == 'rollback' and restored.note == 'restore'
        assert (restored.profile, restored.include, restored.exclude, restored.tags) == (
            old.profile, old.include, old.exclude, old.tags)
        assert [v.version for v in service.persona_history('default')] == [3, 2, 1]
        exported = yaml.safe_load(service.export_persona('default', version=1))
        assert exported['version'] == 1 and exported['profile'] == old.profile
        assert exported['include'] == ['AI'] and exported['exclude'] == ['Ads']
        with pytest.raises(NotFound):
            await service.rollback_persona('default', 99)
        assert len(service.persona_history('default')) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize('reply', [
    {'tags': []}, {'tags': tags('one')}, {'tags': tags(*map(str, range(21)))},
    {'tags': tags('AI', 'ai', 'Science', 'Health', 'Space')}, {'tags': tags('', 'a', 'b', 'c', 'd')},
    {'keep': BASE_TAGS[:-1], 'add': [], 'remove': []},
])
def test_tag_extraction_failure_preserves_previous_tags_and_is_recorded(write_config, open_service, web, reply):
    from test_models import model_config, chat_reply, CHAT_URL
    responses = [{'tags': BASE_TAGS}, reply]
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(json.dumps(responses.pop(0)))))
    service = open_service(write_config(model_config()))

    async def scenario():
        old = await service.set_persona('default', profile='First profile')
        result = await service.set_persona('default', profile='Changed profile')
        assert result.profile == 'Changed profile' and result.tags == old.tags
        assert result.tag_status == 'error' and result.tag_error
        [run, *_] = service.list_runs(kind='persona')
        assert run.stats['tag_status'] == 'error' and run.error == result.tag_error

    asyncio.run(scenario())


def test_update_can_interleave_new_priorities_and_preserves_old_name_spelling(write_config, open_service, web):
    from test_models import model_config, chat_reply, CHAT_URL
    responses = [{'tags': BASE_TAGS}, {'keep': tags('ai', 'Robotics', 'Health', 'Science', 'Space'),
        'add': tags('Energy'), 'remove': [], 'order': ['Energy', 'ai', 'Robotics', 'Health', 'Science', 'Space']}]
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(json.dumps(responses.pop(0)))))
    service = open_service(write_config(model_config()))

    async def scenario():
        await service.set_persona('default', profile='Original')
        result = await service.set_persona('default', profile='Energy now comes first')
        assert [t.tag for t in result.tags] == ['Energy', 'AI', 'Robotics', 'Health', 'Science', 'Space']

    asyncio.run(scenario())


def test_seed_file_is_config_relative_and_import_transaction_is_all_or_nothing(write_config, tmp_path):
    path = write_config({'personas': {'a': {'profile': 'must roll back'}, 'b': {'profile_file': 'missing.md'}}})
    with pytest.raises(ConfigError, match='profile_file'):
        TentaclesService.open(path, env={})
    (path.parent / 'profile.md').write_text('From a file: 中文', encoding='utf-8')
    write_config({'personas': {'a': {'profile': 'new seed'}, 'b': {'profile_file': 'profile.md'}}})
    service = TentaclesService.open(path, env={})
    try:
        assert service.get_persona('a').profile == 'new seed'
        assert service.get_persona('b').profile == 'From a file: 中文'
    finally:
        asyncio.run(service.aclose())


def test_concurrent_keyword_edits_have_unique_versions_without_lost_changes(write_config):
    path = write_config({'personas': {'default': {'profile': 'Base'}}})

    async def scenario():
        one, two = TentaclesService.open(path, env={}), TentaclesService.open(path, env={})
        try:
            results = await asyncio.gather(one.set_persona('default', include_add=['AI']),
                                           two.set_persona('default', include_add=['Health']))
            assert sorted(r.version for r in results) == [2, 3]
            assert set(one.get_persona('default').include) == {'AI', 'Health'}
            assert [r.version for r in one.persona_history('default')] == [3, 2, 1]
        finally:
            await one.aclose()
            await two.aclose()

    asyncio.run(scenario())


def test_extraction_cannot_overwrite_an_edit_made_while_it_was_waiting(write_config):
    from test_models import model_config, chat_reply
    from tentacles.errors import Conflict
    path = write_config(model_config())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=chat_reply(json.dumps({'tags': BASE_TAGS})))

        service = TentaclesService.open(path, transport=httpx.MockTransport(respond), env={})
        task = None
        try:
            await service.set_persona('default', profile='Original', skip_tags=True)
            task = asyncio.create_task(service.set_persona('default', profile='Slow edit'))
            await asyncio.wait_for(entered.wait(), 2)
            await service.set_persona('default', profile='Newer edit', skip_tags=True)
            release.set()
            with pytest.raises(Conflict):
                await task
            assert service.get_persona('default').profile == 'Newer edit'
            assert len(service.persona_history('default')) == 2
            assert {r.status for r in service.list_runs(kind='persona')} == {'ok', 'failed'}
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', [[], tags('one'), tags(*map(str, range(21))),
    tags('AI', 'ai', 'Science', 'Health', 'Space'), tags('  ', 'a', 'b', 'c', 'd')])
def test_first_extraction_validates_count_names_and_uniqueness(write_config, open_service, web, invalid):
    from test_models import model_config, chat_reply, CHAT_URL
    web.handle(CHAT_URL, lambda r: httpx.Response(200, json=chat_reply(json.dumps({'tags': invalid}))))
    service = open_service(write_config(model_config()))
    result = asyncio.run(service.set_persona('default', profile='User profile'))
    assert result.tags == [] and result.tag_status == 'error' and result.tag_error
    assert service.get_persona('default').profile == 'User profile'


def test_upstream_failure_downgrades_without_echoing_private_provider_output(write_config, open_service, web):
    from test_models import model_config, CHAT_URL
    web.serve(CHAT_URL, 'private profile echoed by provider private-test-key', status=500)
    service = open_service(write_config(model_config()))
    result = asyncio.run(service.set_persona('default', profile='New profile'))
    assert result.tag_status == 'error' and result.tags == []
    assert 'private profile' not in result.tag_error and 'private-test-key' not in result.tag_error


def test_persona_timestamps_use_utc_with_the_injected_clock(write_config):
    from datetime import datetime, timedelta, timezone
    path = write_config({'personas': {'default': {'profile': 'Seed'}}})
    clock = lambda: datetime(2030, 1, 2, 8, tzinfo=timezone(timedelta(hours=8)))
    service = TentaclesService.open(path, clock=clock, env={})

    async def scenario():
        try:
            seed = service.get_persona('default')
            current = await service.set_persona('default', profile='Manual', skip_tags=True)
            assert seed.model_dump(mode='json')['created_at'] == '2030-01-02T00:00:00Z'
            assert current.model_dump(mode='json')['created_at'] == '2030-01-02T00:00:00Z'
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_failed_run_cleanup_does_not_hide_cancellation(write_config):
    import sqlite3
    from test_models import model_config

    async def scenario():
        entered = asyncio.Event()

        async def respond(request):
            entered.set()
            await asyncio.Event().wait()

        service = TentaclesService.open(write_config(model_config()),
                                        transport=httpx.MockTransport(respond), env={})
        connection = sqlite3.connect(service.config.db_path)
        connection.executescript("""
            CREATE TRIGGER reject_finish BEFORE UPDATE ON runs WHEN NEW.kind = 'persona'
            BEGIN SELECT RAISE(ABORT, 'finish unavailable'); END;
        """)
        try:
            task = asyncio.create_task(service.set_persona('default', profile='New'))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service.list_personas() == []
        finally:
            connection.close()
            await service.aclose()

    asyncio.run(scenario())


def test_concurrent_first_open_imports_each_seed_only_once(write_config):
    from concurrent.futures import ThreadPoolExecutor
    path = write_config({'personas': {'one': {'profile': 'First'}, 'two': {'profile': 'Second'}}})

    def open_and_read(_):
        service = TentaclesService.open(path, env={})
        try:
            return [(p.name, p.version) for p in service.list_personas()], len(service.persona_history('one'))
        finally:
            asyncio.run(service.aclose())

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(open_and_read, range(8)))
    assert all(rows == [('one', 1), ('two', 1)] and count == 1 for rows, count in results)


@pytest.mark.parametrize('cancel_again', [False, True])
def test_cancelled_commit_drains_io_and_keeps_an_already_committed_success(write_config, cancel_again):
    import sqlite3
    import threading
    from test_models import model_config, chat_reply
    path = write_config(model_config())

    async def scenario():
        timer = None
        task = None
        connection = None

        def respond(request):
            nonlocal timer, connection
            connection = sqlite3.connect(service.config.db_path, check_same_thread=False)
            connection.execute('BEGIN IMMEDIATE')
            timer = threading.Timer(0.2, connection.commit)
            timer.start()
            asyncio.get_running_loop().call_later(0.03, task.cancel)
            if cancel_again:
                asyncio.get_running_loop().call_later(0.06, task.cancel)
            return httpx.Response(200, json=chat_reply(json.dumps({'tags': BASE_TAGS})))

        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
        try:
            task = asyncio.create_task(service.set_persona('default', profile='Committed profile'))
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service.get_persona('default').profile == 'Committed profile'
            assert service.get_persona('default').tag_status == 'ok'
            assert service.list_runs(kind='persona')[0].status == 'ok'
            await service.aclose()
        finally:
            if timer:
                timer.join()
            if connection:
                connection.close()
            await service.aclose()

    asyncio.run(scenario())


def test_same_profile_shortcut_cannot_commit_with_a_newer_profiles_tags(write_config):
    import threading
    from datetime import datetime, timezone
    from test_models import model_config, chat_reply
    from tentacles.errors import Conflict
    path = write_config(model_config())

    async def scenario():
        armed = False
        entered, release = threading.Event(), threading.Event()
        observer = None

        def clock():
            # Gate the external clock once the public run history shows this
            # edit in progress, not by mocking a method or counting internal calls.
            if armed and any(r.status == 'running' for r in observer.list_runs(kind='persona')):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError('concurrent edit did not finish')
            return datetime.now(timezone.utc)

        def respond(request):
            body = json.loads(json.loads(request.content)['messages'][-1]['content'])
            fresh = tags(*(f'{body["profile"]}{i}' for i in range(5)))
            payload = {'tags': fresh} if 'old_tags' not in body else {
                'keep': [], 'add': fresh, 'remove': [t['tag'] for t in body['old_tags']]}
            return httpx.Response(200, json=chat_reply(json.dumps(payload)))

        one = TentaclesService.open(path, env={}, clock=clock, transport=httpx.MockTransport(respond))
        observer = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond))
        task = None
        try:
            await observer.set_persona('default', profile='A')
            armed = True
            task = asyncio.create_task(one.set_persona('default', profile='A', include_add=['AI']))
            assert await asyncio.to_thread(entered.wait, 2)
            newer = await observer.set_persona('default', profile='B')
            release.set()
            with pytest.raises(Conflict):
                await task
            latest = observer.get_persona('default')
            assert latest.version == newer.version == 2 and latest.profile == 'B'
            assert all(tag.tag.startswith('B') for tag in latest.tags)
            assert len(observer.persona_history('default')) == 2
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await one.aclose()
            await observer.aclose()

    asyncio.run(scenario())
