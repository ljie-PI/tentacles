"""Web sources through Service with temporary SQLite, HTTP fixtures and time."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tentacles.errors import ConfigError
from tentacles.service import TentaclesService

URL = 'https://news.example.test/index'
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def web_config(*, mode='list', **options):
    source = {'id': 'web-news', 'type': 'web', 'mode': mode, 'url': URL, 'schedule': '1h'}
    if mode == 'list':
        source['selectors'] = {'item': '.entry', 'title': 'h2', 'link': 'a',
                               'summary': '.summary', 'date': 'time@datetime'}
    source.update(options)
    return {'sources': [source]}


def test_list_selectors_attributes_links_dates_and_missing_fields(write_config, web):
    web.serve(URL, '''<html><head><title>News</title></head><body>
<article class="entry"><h2 data-title="Robotics research advances">Ignored title</h2>
<a href="/robot?utm_source=mail#fragment">Read</a><p class="summary">New <b>robot</b> result</p>
<time datetime="2026-10-24T09:00:00+08:00">today</time></article>
<article class="entry"><h2 data-title="Ocean expedition report">Ignored</h2>
<a href="//news.example.test/ocean?topic=climate">Read</a><p class="summary">Ocean report</p>
<time datetime="Published on 24 October 2026">yesterday</time></article>
<article class="entry"><h2 data-title="Agricultural harvest statistics">Ignored</h2>
<a href="/harvest">Read</a><time datetime="not a date">?</time></article>
<article class="entry"><a href="/missing-title">Read</a></article>
<article class="entry"><h2 data-title="Missing link">Title</h2><a>No href</a></article>
<article class="entry"><h2 data-title="Unsafe link">Title</h2><a href="javascript:alert(1)">Read</a></article>
</body></html>''')
    options = {'item': '.entry', 'title': 'h2@data-title', 'link': 'a@href',
               'summary': '.summary', 'date': 'time@datetime'}
    path = write_config(web_config(selectors=options))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            result = (await service.fetch_sources())[0]
            assert result.status == 'ok' and result.new == 3
            details = {item.title: service.get_item(item.id) for item in service.list_items().items}
            robot = details['Robotics research advances']
            assert robot.kind == 'web' and robot.url == robot.external_id == 'https://news.example.test/robot'
            assert robot.summary == robot.content == 'New robot result'
            assert robot.published == datetime(2026, 10, 24, 1, tzinfo=timezone.utc)
            ocean = details['Ocean expedition report']
            assert ocean.external_id == 'https://news.example.test/ocean?topic=climate'
            assert ocean.published == datetime(2026, 10, 23, 16, tzinfo=timezone.utc)
            assert details['Agricultural harvest statistics'].published is None
            again = (await service.fetch_sources())[0]
            assert again.status == 'ok' and again.new == again.dup == 0
            assert again.stats['entries'] == 3 and again.stats['new_links'] == 0
            assert len(web.requests) == 2  # details are opt-in
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_source_headers_cookies_timeouts_and_sessions_are_isolated(write_config):
    public = 'https://news.example.test/public'
    seen = []

    def respond(request):
        seen.append(request)
        if str(request.url) == URL:
            assert request.headers['x-private'] == 'header-secret'
            assert 'session=client-secret' in request.headers['cookie']
            assert request.extensions['timeout']['read'] == 7
            return httpx.Response(200, text='<div class="entry"><h2>Robotics news</h2><a href="/robot">Read</a></div>',
                                  headers={'set-cookie': 'session=server-secret; Path=/'})
        assert 'x-private' not in request.headers
        assert not request.headers.get('cookie'), 'a different source must not inherit session cookies'
        return httpx.Response(200, text='<div class="entry"><h2>Ocean exploration report</h2><a href="/ocean">Read</a></div>')

    config = web_config(headers={'X-Private': 'header-secret'}, cookies={'session': 'client-secret'},
                        timeout=7, proxy='http://proxy.example.test:8888')
    config['sources'].append({**web_config()['sources'][0], 'id': 'public', 'url': public})
    path = write_config(config)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        try:
            results = await service.fetch_sources()
            assert all(result.status == 'ok' for result in results) and sum(result.new for result in results) == 2
            assert len(seen) == 2
            public_data = ''.join(result.model_dump_json() for result in results)
            assert not any(secret in public_data for secret in ['header-secret', 'client-secret', 'server-secret'])
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_page_versions_keep_url_diff_and_same_day_changes_but_no_unchanged_items(write_config, web):
    html = ['<html><head><title>Service status</title></head><body><nav>Ignored outside region</nav>'
            '<main id="status"><p>State: pending</p><p>Stable line</p></main></body></html>']
    web.handle(URL, lambda request: httpx.Response(200, text=html[0]))
    path = write_config(web_config(mode='page', selector='#status'))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            first = (await service.fetch_sources())[0]
            assert first.status == 'ok' and first.new == 1
            baseline = service.get_item(service.list_items().items[0].id)
            assert baseline.url == URL and baseline.external_id.startswith(URL + '#')
            assert baseline.content == 'State: pending\nStable line'
            assert 'Service status' in baseline.title and '2026-10-25' in baseline.title
            assert 'Ignored outside region' not in baseline.content
            unchanged = (await service.fetch_sources())[0]
            assert unchanged.new == 0 and unchanged.dup == 0 and unchanged.stats['changed'] is False
            html[0] = html[0].replace('State: pending', 'State: ready')
            changed = (await service.fetch_sources())[0]
            assert changed.status == 'ok' and changed.new == 1 and changed.stats['changed'] is True
            assert service.list_items().total == 2, 'same URL/title is not a duplicate content version'
            versions = [service.get_item(item.id) for item in service.list_items().items]
            revised = next(item for item in versions if item.id != baseline.id)
            assert revised.title == baseline.title and revised.url == baseline.url
            assert '-State: pending' in revised.summary and '+State: ready' in revised.summary
            assert '\n' in revised.summary and revised.content == 'State: ready\nStable line'
            assert revised.external_id != baseline.external_id
            assert revised.meta['content_hash'] == revised.external_id.rsplit('#', 1)[1]
            # Returning to the same identity reuses the saved version but logs a change.
            html[0] = html[0].replace('State: ready', 'State: pending')
            returned = (await service.fetch_sources())[0]
            assert returned.status == 'ok' and returned.new == 0 and returned.dup == 1
            assert returned.stats['changed'] is True and '+State: pending' in returned.stats['diff']
            assert service.list_items().total == 2
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_details_use_article_main_body_fallback_bounded_concurrency_and_only_new_links(write_config):
    other = 'https://external.example.test/detail'
    index = '<html>' + ''.join(f'<div class="entry"><h2>{title}</h2><a href="{link}">Read</a></div>' for title, link in [
        ('Robotics laboratory discovery', '/robot'), ('Oceanic climate expedition', '/ocean'),
        ('Agricultural harvest ledger', '/harvest'), ('Orbital station maintenance', other)]) + '</html>'
    bodies = {
        'https://news.example.test/robot': '<body><article><header>Header junk</header><nav>Nav junk</nav><p>Article body</p></article><main>Wrong main</main></body>',
        'https://news.example.test/ocean': '<body><article><nav>Empty article</nav></article><main><footer>Footer junk</footer><p>Main body</p></main></body>',
        'https://news.example.test/harvest': '<html><body><nav>Nav junk</nav><p>Body fallback</p><footer>Footer junk</footer></body></html>',
        other: '<section>Root fallback</section>',
    }
    requests, active, maximum = [], [0], [0]

    async def respond(request):
        requests.append(request)
        if str(request.url) == URL:
            return httpx.Response(200, text=index, headers={'set-cookie': 'server=origin-secret; Path=/'})
        if request.url.host == 'external.example.test':
            assert not request.headers.get('x-private') and not request.headers.get('cookie')
        else:
            assert request.headers['x-private'] == 'header-secret'
            assert 'client-secret' in request.headers['cookie']
        active[0] += 1
        maximum[0] = max(maximum[0], active[0])
        try:
            await asyncio.sleep(0.02)
            return httpx.Response(200, text=bodies[str(request.url)])
        finally:
            active[0] -= 1

    path = write_config(web_config(fetch_content=True, concurrency=2, headers={'X-Private': 'header-secret'},
                                    cookies={'session': 'client-secret'}))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        try:
            first = (await service.fetch_sources())[0]
            assert first.status == 'ok' and first.new == 4
            contents = {item.title: service.get_item(item.id).content for item in service.list_items().items}
            assert contents == {'Robotics laboratory discovery': 'Article body', 'Oceanic climate expedition': 'Main body',
                                'Agricultural harvest ledger': 'Body fallback', 'Orbital station maintenance': 'Root fallback'}
            assert maximum[0] == 2 and active[0] == 0
            assert len(requests) == 5
            assert (await service.fetch_sources())[0].new == 0
            assert len(requests) == 6 and service.list_items().total == 4
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_seen_cursor_is_bounded_but_old_duplicate_links_are_not_downloaded_again(write_config):
    links = [['https://news.example.test/seed']]
    counts = {'index': 0, 'details': 0}

    def respond(request):
        if str(request.url) == URL:
            counts['index'] += 1
            text = '<html>' + ''.join(f'<div class="entry"><h2>Repeated observation</h2><a href="{url}">Read</a></div>'
                                     for url in links[0]) + '</html>'
            return httpx.Response(200, text=text)
        counts['details'] += 1
        return httpx.Response(200, text='<article>Identical article text</article>')

    path = write_config(web_config(fetch_content=True, concurrency=8))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            links[0] = [f'https://news.example.test/history/{i}' for i in range(501)]
            # These share the stored fingerprint, so they are not retained as items.
            observed = (await service.fetch_sources())[0]
            assert observed.new == 0
            assert service.list_items().total == 1 and counts['details'] == 502
            assert observed.stats['seen_cached'] == 500
        finally:
            await service.aclose()
        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        try:
            observed = (await service.fetch_sources())[0]
            assert observed.new == 0
            assert counts['index'] == 3 and counts['details'] == 502
            assert observed.stats['seen_cached'] == 500
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_detail_backfill_recomputes_fingerprint_for_later_article_dedup(write_config, web):
    from conftest import rss_feed
    feed = 'https://feed.example.test/rss'
    web.serve(URL, '<div class="entry"><h2>Global research results</h2><a href="/paper">Read</a></div>')
    web.serve('https://news.example.test/paper', '<article>Shared report body</article>')
    web.serve(feed, rss_feed({'title': 'Global research results', 'guid': 'other-copy',
                             'link': 'https://other.example.test/paper', 'description': 'Shared report body'}))
    config = web_config(fetch_content=True)
    config['sources'].append({'id': 'rss', 'type': 'rss', 'url': feed, 'schedule': '1h'})
    path = write_config(config)
    now = [NOW]

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            assert (await service.fetch_sources(['web-news']))[0].new == 1
            now[0] += timedelta(hours=49)  # Outside cross-source title-similarity window.
            duplicate = (await service.fetch_sources(['rss']))[0]
            assert duplicate.status == 'ok' and duplicate.new == 0 and duplicate.dup == 1
            assert service.list_items().total == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['list', 'page'])
@pytest.mark.parametrize('header_charset', [False, True])
def test_html_charset_from_http_or_meta_is_preserved_for_lists_pages_and_details(write_config, web, mode, header_charset):
    declaration = '' if header_charset else '<meta charset="gbk">'
    content = ('<div class="entry"><h2>机器人研究</h2><a href="/detail">阅读</a><p class="summary">简要摘要</p></div>'
               if mode == 'list' else '<main>正文详情</main>')
    html = f'<html><head>{declaration}<title>页面标题</title></head><body>{content}</body></html>'
    headers = {'content-type': 'text/html; charset=gbk'} if header_charset else {}
    web.serve(URL, html.encode('gbk'), headers=headers)
    web.serve('https://news.example.test/detail',
              f'<html><head>{declaration}</head><body><article>正文详情</article></body></html>'.encode('gbk'), headers=headers)
    path = write_config(web_config(mode=mode, fetch_content=mode == 'list'))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            result = (await service.fetch_sources())[0]
            assert result.status == 'ok' and result.new == 1
            item = service.get_item(service.list_items().items[0].id)
            assert ('机器人研究' if mode == 'list' else '页面标题') in item.title
            assert '正文详情' in item.content and '\ufffd' not in item.content
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('first', ['page', 'article'])
def test_page_identity_is_independent_of_article_url_fingerprint_and_title_rules(write_config, web, first):
    from conftest import rss_feed
    feed = 'https://feed.example.test/rss'
    page = ['<html><title>Stable report</title><main>State pending</main></html>']
    web.handle(URL, lambda request: httpx.Response(200, text=page[0]))
    web.serve(feed, rss_feed({'guid': 'canonical', 'title': 'Stable report 2026-10-25', 'link': URL,
                             'description': 'State pending'}))
    config = web_config(mode='page', selector='main')
    config['sources'].append({'id': 'rss', 'type': 'rss', 'url': feed, 'schedule': '1h'})
    path = write_config(config)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            order = ['web-news', 'rss'] if first == 'page' else ['rss', 'web-news']
            assert (await service.fetch_sources([order[0]]))[0].new == 1
            assert (await service.fetch_sources([order[1]]))[0].new == 1
            page[0] = page[0].replace('State pending', 'State ready')
            assert (await service.fetch_sources(['web-news']))[0].new == 1
            assert service.list_items().total == 3
            assert (await service.fetch_sources(['rss']))[0].new == 0
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_page_optional_baseline_restart_and_local_date_title(write_config, web):
    html = ['<html><title>Service status</title><main>Pending</main></html>']
    web.handle(URL, lambda request: httpx.Response(200, text=html[0]))
    path = write_config(web_config(mode='page', selector='main', emit_baseline=False))
    now = [datetime(2026, 10, 25, 17, tzinfo=timezone.utc)]

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            baseline = (await service.fetch_sources())[0]
            assert baseline.status == 'ok' and baseline.new == 0 and baseline.stats['baseline'] is True
        finally:
            await service.aclose()
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: now[0])
        try:
            same = (await service.fetch_sources())[0]
            assert same.new == 0 and same.stats['baseline'] is False
            html[0] = html[0].replace('Pending', 'Ready')
            changed = (await service.fetch_sources())[0]
            assert changed.new == 1
            [item] = service.list_items().items
            assert '2026-10-26' in item.title and '-Pending' in item.summary and '+Ready' in item.summary
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_relative_dates_use_injected_clock_and_fuzzy_labels_not_machine_now(write_config, web):
    web.serve(URL, '<div class="entry"><h2>Robotics timeline</h2><a href="/time">Read</a>'
                   '<time datetime="发布于 2 小时前">today</time></div>')
    path = write_config(web_config())

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            assert service.list_items().items[0].published == NOW - timedelta(hours=2)
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_selector_root_attributes_and_at_signs_inside_css_do_not_conflict(write_config, web):
    web.serve(URL, '<a class="entry" data-owner="team@example.test" data-title="Root attribute article" href="/root">Read</a>')
    selectors = {'item': 'a.entry[data-owner="team@example.test"]', 'title': '@DATA-TITLE', 'link': '@href'}
    path = write_config(web_config(selectors=selectors))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            [item] = service.list_items().items
            assert item.title == 'Root attribute article' and item.url == 'https://news.example.test/root'
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['list', 'page'])
@pytest.mark.parametrize('status', [301, 304, 403, 404, 500])
def test_non2xx_errors_are_safe_keep_cursor_and_do_not_follow_redirects(write_config, caplog, mode, status):
    failing, requests = [False], []
    secret_url = URL + '?access_token=url-secret'
    html = '<html><title>Status</title><main>Stable</main><div class="entry"><h2>Stable article</h2><a href="/stable">Read</a></div></html>'

    def respond(request):
        requests.append(request)
        assert str(request.url) == secret_url
        if failing[0]:
            return httpx.Response(status, text='response-private header-secret cookie-secret',
                                  headers={'location': 'https://other.example.test/steal'})
        return httpx.Response(200, text=html)

    path = write_config(web_config(mode=mode, url=secret_url,
                                    headers={'X-Private': 'header-secret'}, cookies={'session': 'cookie-secret'}))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            failing[0] = True
            failed = (await service.fetch_sources())[0]
            assert failed.status == 'error' and f'HTTP {status}' in failed.error
            [run, *_] = service.list_runs(kind='fetch')
            public = failed.model_dump_json() + run.model_dump_json() + caplog.text
            assert not any(secret in public for secret in ['response-private', 'header-secret', 'cookie-secret', 'url-secret'])
            assert service.list_items().total == 1
            failing[0] = False
            recovered = (await service.fetch_sources())[0]
            assert recovered.status == 'ok' and recovered.new == 0
            if mode == 'page':
                assert recovered.stats['baseline'] is False and recovered.stats['changed'] is False
            assert len(requests) == 3
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_detail_failure_drains_other_workers_and_leaves_links_retryable(write_config):
    index = ('<div class="entry"><h2>Robotics discovery</h2><a href="/slow">Read</a></div>'
             '<div class="entry"><h2>Ocean expedition</h2><a href="/bad">Read</a></div>')
    path = write_config(web_config(fetch_content=True, concurrency=2))

    async def scenario():
        entered, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
        failing = [True]
        calls = []

        async def respond(request):
            calls.append(str(request.url))
            if str(request.url) == URL:
                return httpx.Response(200, text=index)
            if request.url.path == '/slow' and failing[0]:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
                    cleaned.set()
            if failing[0]:
                await entered.wait()
                return httpx.Response(404, text='private-cookie-secret')
            return httpx.Response(200, text='<article>Useful body</article>')

        service = TentaclesService.open(path, env={}, transport=httpx.MockTransport(respond), clock=lambda: NOW)
        pending = None
        try:
            pending = asyncio.create_task(service.fetch_sources())
            await asyncio.wait_for(cleaning.wait(), 2)
            assert not pending.done()
            release.set()
            [failed] = await asyncio.wait_for(pending, 2)
            assert failed.status == 'error' and cleaned.is_set() and 'private-cookie-secret' not in failed.error
            assert service.list_items().total == 0
            failing[0] = False
            assert (await service.fetch_sources())[0].new == 2
            assert calls.count('https://news.example.test/slow') == calls.count('https://news.example.test/bad') == 2
        finally:
            release.set()
            if pending is not None and not pending.done():
                await pending
            await service.aclose()

    asyncio.run(scenario())


def test_repeated_cancel_drains_details_without_advancing_observation_history(write_config):
    path = write_config(web_config(fetch_content=True))

    async def scenario():
        entered, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
        calls = []

        async def respond(request):
            calls.append(str(request.url))
            if str(request.url) == URL:
                return httpx.Response(200, text='<div class="entry"><h2>Robotics story</h2><a href="/one">Read</a></div>')
            if calls.count('https://news.example.test/one') == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
                    cleaned.set()
            return httpx.Response(200, text='<article>Body</article>')

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
            assert service.list_runs(kind='fetch')[0].status == 'failed'
            assert (await service.fetch_sources())[0].new == 1
            assert calls.count('https://news.example.test/one') == 2
        finally:
            release.set()
            if pending is not None and not pending.done():
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
            await service.aclose()

    asyncio.run(scenario())


def test_v8_upgrade_preserves_legacy_articles_personas_and_consumption(write_config, tmp_path, web):
    import sqlite3
    from pathlib import Path
    from conftest import rss_feed
    database = tmp_path / 'data/tentacles.db'
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript((Path(__file__).parent / 'fixtures/web-v8.sql').read_text())
    feed = 'https://feed.example.test/rss'
    web.serve(URL, '<html><title>Service status</title><main>New page baseline</main></html>')
    web.serve(feed, rss_feed({'guid': 'new-copy', 'title': 'Service status 2026-10-25', 'link': URL,
                             'description': 'Legacy article body'}))
    config = web_config(mode='page', selector='main')
    config['sources'].append({'id': 'rss', 'type': 'rss', 'url': feed, 'schedule': '1h'})
    config['pipelines'] = [{'id': 'old-news', 'sources': ['rss'], 'filters': [], 'processor': {'type': 'none'}}]
    path = write_config(config)

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert service.get_persona('default').version == 2
            assert service.get_persona('default').profile == 'Legacy updated interests'
            [legacy] = service.list_items(sources=['rss']).items
            assert service.get_item(legacy.id).content == 'Legacy article body'
            assert (await service.run_pipeline('old-news')).status == 'no_new'
            assert service.get_item(legacy.id).evaluations[0].reported_at is not None
            assert (await service.fetch_sources(['web-news']))[0].new == 1
            assert service.list_items().total == 2
            assert (await service.fetch_sources(['rss']))[0].new == 0
            assert service.list_reports().total == 0
        finally:
            await service.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize('options', [
    {'url': 'file:///etc/passwd'}, {'url': 'https://user:secret@news.example.test/index'},
    {'mode': 'unknown'}, {'selectors': None},
    {'selectors': {'item': '.entry[', 'title': 'h2', 'link': 'a'}},
    {'selectors': {'item': '.entry@id', 'title': 'h2', 'link': 'a'}},
    {'selectors': {'item': '.entry', 'title': 'h2@', 'link': 'a'}},
    {'selector': 'main['}, {'concurrency': 0}, {'timeout': 0},
])
def test_web_options_are_validated_at_service_open_without_fetching(write_config, web, options):
    path = write_config(web_config(**options))
    with pytest.raises(ConfigError):
        TentaclesService.open(path, env={}, transport=web.transport())
    assert web.requests == []


def test_missing_page_region_preserves_last_snapshot_and_recovery_does_not_rebaseline(write_config, web):
    page = ['<html><title>Status</title><main>Stable</main></html>']
    web.handle(URL, lambda request: httpx.Response(200, text=page[0]))
    path = write_config(web_config(mode='page', selector='main'))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            page[0] = '<html><title>Login page</title><p>No matching region</p></html>'
            assert (await service.fetch_sources())[0].status == 'error'
            page[0] = '<html><title>Status</title><main>Stable</main></html>'
            recovered = (await service.fetch_sources())[0]
            assert recovered.status == 'ok' and recovered.new == 0 and recovered.stats['baseline'] is False
            assert service.list_items().total == 1
        finally:
            await service.aclose()

    asyncio.run(scenario())


def test_page_diff_is_truncated_and_title_falls_back_when_html_title_empty(write_config, web):
    page = ['<html><title></title><main>' + ''.join(f'<p>Old line {i}</p>' for i in range(400)) + '</main></html>']
    web.handle(URL, lambda request: httpx.Response(200, text=page[0]))
    path = write_config(web_config(mode='page', selector='main', name='Status watch'))

    async def scenario():
        service = TentaclesService.open(path, env={}, transport=web.transport(), clock=lambda: NOW)
        try:
            assert (await service.fetch_sources())[0].new == 1
            [baseline] = service.list_items().items
            assert 'Status watch' in baseline.title
            page[0] = page[0].replace('Old line', 'New line')
            assert (await service.fetch_sources())[0].new == 1
            revised = next(service.get_item(item.id) for item in service.list_items().items if item.id != baseline.id)
            assert len(revised.summary) <= 2000 and revised.summary.endswith('…')
            assert '-Old line' in revised.summary and 'New line 399' in revised.content
        finally:
            await service.aclose()

    asyncio.run(scenario())
