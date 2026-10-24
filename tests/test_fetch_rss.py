import asyncio

import httpx
import pytest

from conftest import rss_feed

FEED = "https://news.example.com/rss"


def rss_config(**source):
    return {"sources": [{"id": "news", "type": "rss", "url": FEED, "schedule": "30m", **source}]}


def test_fetched_rss_entries_become_listable_items(web, write_config, open_service):
    web.serve(FEED, rss_feed(
        {"title": "Older story", "link": "https://news.example.com/a", "guid": "a",
         "pubDate": "Mon, 20 Oct 2026 08:00:00 GMT", "description": "<p>first <b>body</b></p>"},
        {"title": "Newer story", "link": "https://news.example.com/b", "guid": "b",
         "pubDate": "Tue, 21 Oct 2026 08:00:00 GMT", "description": "second"},
    ))
    svc = open_service(write_config(rss_config()))

    [outcome] = asyncio.run(svc.fetch_sources())
    page = svc.list_items()

    assert (outcome.source_id, outcome.status, outcome.new, outcome.dup) == ("news", "ok", 2, 0)
    assert [i.title for i in page.items] == ["Newer story", "Older story"]
    assert page.total == 2
    assert page.items[1].summary == "first body"
    assert page.items[0].source_id == "news" and page.items[0].kind == "rss"


def test_second_fetch_sends_validators_and_304_yields_nothing(web, write_config, open_service):
    calls = []

    def handler(request):
        calls.append(request)
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"ETag": '"v1"', "Last-Modified": "Tue, 21 Oct 2026 08:00:00 GMT"},
                              content=rss_feed({"title": "Only", "link": "https://news.example.com/x"}).encode())

    web.handle(FEED, handler)
    svc = open_service(write_config(rss_config()))

    first = asyncio.run(svc.fetch_sources())[0]
    second = asyncio.run(svc.fetch_sources())[0]

    assert first.new == 1
    assert (second.status, second.new, second.dup) == ("ok", 0, 0)
    assert calls[1].headers["If-Modified-Since"] == "Tue, 21 Oct 2026 08:00:00 GMT"
    # 304 之后游标仍然保留，下一次还会带上
    asyncio.run(svc.fetch_sources())
    assert calls[2].headers["If-None-Match"] == '"v1"'


def two_sources(**extra):
    return {"sources": [
        {"id": "good", "type": "rss", "url": FEED, "schedule": "30m"},
        {"id": "bad", "type": "rss", "url": "https://broken.example.com/rss", "schedule": "1h", **extra},
    ]}


def test_one_broken_source_does_not_stop_the_others_and_keeps_its_cursor(web, write_config, open_service):
    web.serve(FEED, rss_feed({"title": "Fine", "link": "https://news.example.com/f"}))
    web.serve("https://broken.example.com/rss", rss_feed({"title": "B", "link": "https://broken.example.com/1"}),
              headers={"ETag": '"b1"'})
    svc = open_service(write_config(two_sources()))
    asyncio.run(svc.fetch_sources(["bad"]))

    web.serve("https://broken.example.com/rss", "oops", status=500)
    outcomes = {o.source_id: o for o in asyncio.run(svc.fetch_sources())}

    assert outcomes["good"].status == "ok" and outcomes["good"].new == 1
    assert outcomes["bad"].status == "error" and "HTTP 500" in outcomes["bad"].error
    bad = next(s for s in svc.list_sources() if s.id == "bad")
    assert (bad.last_status, bad.total_items) == ("error", 1)
    assert "HTTP 500" in bad.last_error
    # 失败时保留旧游标：下一次请求仍带着上次成功时的 ETag
    asyncio.run(svc.fetch_sources(["bad"]))
    assert web.requests_to("https://broken.example.com/rss")[-1].headers["If-None-Match"] == '"b1"'
    runs = svc.list_runs(target="bad")
    assert [r.status for r in runs] == ["failed", "failed", "ok"]


def test_sources_list_shows_config_and_fetch_state(web, write_config, open_service):
    web.serve(FEED, rss_feed({"title": "A", "link": "https://news.example.com/a"}))
    svc = open_service(write_config({"sources": [
        {"id": "news", "type": "rss", "url": FEED, "schedule": "30m", "name": "News"},
        {"id": "off", "type": "rss", "url": "https://off.example.com/rss", "schedule": "1d", "enabled": False},
    ]}))

    asyncio.run(svc.fetch_sources())
    news, off = svc.list_sources()

    assert (news.type, news.name, news.schedule, news.last_status, news.total_items) == ("rss", "News", "30m", "ok", 1)
    assert news.last_fetch_at is not None
    assert (off.enabled, off.last_fetch_at, off.total_items) == (False, None, 0)
    assert web.requests_to("https://off.example.com/rss") == []  # disabled sources are skipped


def test_entries_older_than_max_age_are_dropped(web, write_config, open_service):
    web.serve(FEED, rss_feed(
        {"title": "Ancient", "link": "https://news.example.com/old", "pubDate": "Mon, 01 Jan 2001 00:00:00 GMT"},
        {"title": "Undated", "link": "https://news.example.com/undated"},
    ))
    svc = open_service(write_config(rss_config(max_age_days=3)))

    [outcome] = asyncio.run(svc.fetch_sources())

    assert outcome.new == 1
    assert [i.title for i in svc.list_items().items] == ["Undated"]


def test_per_source_headers_are_sent(web, write_config, open_service):
    web.serve(FEED, rss_feed())
    svc = open_service(write_config(rss_config(headers={"Cookie": "session=1"})))

    asyncio.run(svc.fetch_sources())

    assert web.requests_to(FEED)[0].headers["Cookie"] == "session=1"


def test_guid_is_the_identity_even_when_the_link_changes(web, write_config, open_service):
    web.serve(FEED, rss_feed({"title": "Story", "link": "https://news.example.com/v1", "guid": "g-1"}))
    svc = open_service(write_config(rss_config()))
    asyncio.run(svc.fetch_sources())

    web.serve(FEED, rss_feed({"title": "Story (updated)", "link": "https://news.example.com/v2", "guid": "g-1"}))
    [outcome] = asyncio.run(svc.fetch_sources())

    assert (outcome.new, outcome.dup) == (0, 1)


def test_unknown_source_is_not_found(write_config, open_service):
    import pytest
    from tentacles.errors import NotFound

    svc = open_service(write_config(rss_config()))
    with pytest.raises(NotFound):
        asyncio.run(svc.fetch_sources(["nope"]))


def test_max_age_zero_keeps_only_undated_entries(web, write_config, open_service):
    web.serve(FEED, rss_feed(
        {"title": "Yesterday", "link": "https://news.example.com/y", "pubDate": "Mon, 01 Jan 2024 00:00:00 GMT"},
        {"title": "Undated", "link": "https://news.example.com/u"},
    ))
    svc = open_service(write_config(rss_config(max_age_days=0)))

    asyncio.run(svc.fetch_sources())

    assert [i.title for i in svc.list_items().items] == ["Undated"]


def test_runs_record_real_start_and_finish_times(web, write_config):
    from datetime import datetime, timedelta, timezone
    from tentacles.service import TentaclesService

    ticks = iter(datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc) + timedelta(seconds=10 * i) for i in range(100))
    web.serve(FEED, rss_feed({"title": "A", "link": "https://news.example.com/a"}))
    svc = TentaclesService.open(write_config(rss_config()), transport=web.transport(), env={},
                                clock=lambda: next(ticks))
    try:
        asyncio.run(svc.fetch_sources())
        [run] = svc.list_runs()
        assert run.finished_at > run.started_at
        assert svc.list_sources()[0].last_fetch_at == run.started_at
    finally:
        svc.store.close()


def test_similar_titles_in_one_batch_keep_the_first_entry(web, write_config, open_service):
    web.serve(FEED, rss_feed(
        {"title": "Python 3.13.2 released", "link": "https://news.example.com/3132"},
        {"title": "Python 3.13.1 released", "link": "https://news.example.com/3131"},
    ))
    svc = open_service(write_config(rss_config()))

    [outcome] = asyncio.run(svc.fetch_sources())

    assert (outcome.new, outcome.dup) == (1, 1)
    assert svc.list_items().items[0].title == "Python 3.13.2 released"


def test_cancelled_fetch_marks_the_run_failed(web, write_config, open_service):
    import pytest

    def cancel(request):
        raise asyncio.CancelledError()

    web.handle(FEED, cancel)
    svc = open_service(write_config(rss_config()))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(svc.fetch_sources())

    [run] = svc.list_runs()
    assert run.status == "failed" and run.finished_at is not None


def test_charset_from_content_type_header_is_honoured(web, write_config, open_service):
    body = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            '<item><title>中文标题</title><link>https://news.example.com/zh</link></item></channel></rss>')
    web.serve(FEED, body.encode("gbk"), headers={"Content-Type": "text/xml; charset=gbk"})
    svc = open_service(write_config(rss_config()))

    asyncio.run(svc.fetch_sources())

    assert svc.list_items().items[0].title == "中文标题"


def test_changing_the_feed_url_drops_old_validators(web, write_config, open_service):
    web.serve(FEED, rss_feed({"title": "Old", "link": "https://news.example.com/o"}), headers={"ETag": '"old"'})
    NEW = "https://news.example.com/new-rss"
    web.serve(NEW, rss_feed({"title": "New", "link": "https://news.example.com/n"}))
    path = write_config(rss_config())
    asyncio.run(open_service(path).fetch_sources())

    svc = open_service(write_config({"sources": [{"id": "news", "type": "rss", "url": NEW, "schedule": "30m"}]}))
    [outcome] = asyncio.run(svc.fetch_sources())

    assert "If-None-Match" not in web.requests_to(NEW)[0].headers
    assert outcome.new == 1


def test_relative_links_resolve_against_the_feed_url(web, write_config, open_service):
    web.serve(FEED, rss_feed({"title": "Rel", "link": "/post/1"}))
    other = "https://other.example.org/rss"
    web.serve(other, rss_feed({"title": "Different story", "link": "/post/1"}))
    svc = open_service(write_config({"sources": [
        {"id": "news", "type": "rss", "url": FEED, "schedule": "30m"},
        {"id": "other", "type": "rss", "url": other, "schedule": "30m"}]}))

    out = {o.source_id: o for o in asyncio.run(svc.fetch_sources())}

    assert out["news"].new == 1 and out["other"].new == 1
    assert {i.url for i in svc.list_items().items} == {"https://news.example.com/post/1",
                                                      "https://other.example.org/post/1"}


def test_entries_suppressed_as_cross_source_duplicates_stay_suppressed(web, write_config):
    from datetime import datetime, timedelta, timezone
    from tentacles.service import TentaclesService

    now = [datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)]
    other = "https://other.example.org/rss"
    web.serve(other, rss_feed({"title": "Officials report major earthquake in Japan", "link": "https://o.org/q"}))
    web.serve(FEED, rss_feed({"title": "Officials report major earthquake in Japan!", "link": "https://n.com/q"}))
    svc = TentaclesService.open(write_config({"sources": [
        {"id": "news", "type": "rss", "url": FEED, "schedule": "30m"},
        {"id": "other", "type": "rss", "url": other, "schedule": "30m"}]}),
        transport=web.transport(), env={}, clock=lambda: now[0])
    try:
        asyncio.run(svc.fetch_sources(["other"]))
        assert asyncio.run(svc.fetch_sources(["news"]))[0].new == 0
        now[0] += timedelta(days=3)  # 另一来源的标题已经超出 48 小时窗口
        assert asyncio.run(svc.fetch_sources(["news"]))[0].new == 0
    finally:
        svc.store.close()


def test_an_html_page_instead_of_a_feed_is_an_error_but_an_empty_feed_is_not(web, write_config, open_service):
    web.serve(FEED, "<html><body>Not a feed</body></html>", headers={"Content-Type": "text/html"})
    svc = open_service(write_config(rss_config()))
    assert asyncio.run(svc.fetch_sources())[0].status == "error"

    web.serve(FEED, rss_feed(), headers={"Content-Type": "text/html"})  # 合法 feed，只是类型标错且为空
    assert asyncio.run(svc.fetch_sources())[0].status == "ok"


def test_an_empty_body_is_reported_as_not_a_feed(web, write_config, open_service):
    web.serve(FEED, b"", headers={"Content-Type": "application/json"})
    svc = open_service(write_config(rss_config()))

    [outcome] = asyncio.run(svc.fetch_sources())

    assert outcome.status == "error" and outcome.error.startswith("cannot parse feed")


def test_description_only_feeds_retain_the_complete_body(web, write_config, open_service):
    body = "x" * 2100 + "THE_END"
    web.serve(FEED, rss_feed({"title": "Full article", "link": "https://news.example.com/full",
                              "description": body}))
    svc = open_service(write_config(rss_config()))

    asyncio.run(svc.fetch_sources())
    detail = svc.get_item(svc.list_items().items[0].id)

    assert len(detail.summary) <= 2000
    assert detail.content == body


def test_guid_only_entries_without_a_link_are_kept_and_deduplicated(web, write_config, open_service):
    web.serve(FEED, '''<rss version="2.0"><channel><title>News</title><item>
        <title>Private bulletin</title><guid isPermaLink="false">internal-42</guid>
        <description>Full bulletin body</description></item></channel></rss>''')
    svc = open_service(write_config(rss_config()))

    first = asyncio.run(svc.fetch_sources())[0]
    second = asyncio.run(svc.fetch_sources())[0]

    assert (first.new, second.new, second.dup) == (1, 0, 1)
    detail = svc.get_item(svc.list_items().items[0].id)
    assert detail.external_id == "internal-42"
    assert detail.url == ""
    assert detail.content == "Full bulletin body"


def test_more_than_500_suppressed_entries_stay_suppressed_after_restart(web, write_config):
    from datetime import datetime, timedelta, timezone
    from tentacles.service import TentaclesService

    now = [datetime(2026, 10, 25, tzinfo=timezone.utc)]
    other = "https://other.example.org/rss"
    for url, name in [(other, "other"), (FEED, "news")]:
        web.serve(url, rss_feed(*[
            {"title": f"Bulletin {i:06x}", "link": f"https://{name}.example.org/{i}",
             "guid": f"{name}-{i}", "description": f"{name} coverage"}
            for i in range(501)]))
    path = write_config({"sources": [
        {"id": "news", "type": "rss", "url": FEED, "schedule": "30m"},
        {"id": "other", "type": "rss", "url": other, "schedule": "30m"}]})
    svc = TentaclesService.open(path, transport=web.transport(), env={}, clock=lambda: now[0])
    try:
        assert asyncio.run(svc.fetch_sources(["other"]))[0].new == 501
        first = asyncio.run(svc.fetch_sources(["news"]))[0]
        assert (first.new, first.dup) == (0, 501)
    finally:
        asyncio.run(svc.aclose())

    now[0] += timedelta(days=3)
    svc = TentaclesService.open(path, transport=web.transport(), env={}, clock=lambda: now[0])
    try:
        after_restart = asyncio.run(svc.fetch_sources(["news"]))[0]
        assert (after_restart.new, after_restart.dup) == (0, 501)
    finally:
        asyncio.run(svc.aclose())


def test_similar_titles_in_different_batches_of_one_source_are_kept(web, write_config, open_service):
    svc = open_service(write_config(rss_config()))
    for version in ("3.13.2", "3.13.1"):
        web.serve(FEED, rss_feed({"title": f"Python {version} released",
                                  "link": f"https://news.example.com/{version}"}))
        assert asyncio.run(svc.fetch_sources())[0].new == 1
    assert svc.list_items().total == 2


def test_repeated_entries_in_one_feed_do_not_suppress_the_first_copy(web, write_config, open_service):
    entry = {"title": "A repeated headline", "link": "https://news.example.com/repeated", "guid": "same"}
    web.serve(FEED, rss_feed(entry, entry))
    svc = open_service(write_config(rss_config()))

    result = asyncio.run(svc.fetch_sources())[0]

    assert (result.new, result.dup, svc.list_items().total) == (1, 1, 1)


def test_cancelling_a_blocked_insert_does_not_lose_source_counts(web, write_config, open_service):
    import sqlite3
    import threading
    import pytest

    svc = open_service(write_config(rss_config()))
    locker = sqlite3.connect(svc.config.db_path, check_same_thread=False)
    timers = []
    first = True

    def handler(request):
        nonlocal first
        if first:
            first = False
            locker.execute("BEGIN IMMEDIATE")
            timer = threading.Timer(0.2, locker.commit)
            timers.append(timer)
            timer.start()
            asyncio.get_running_loop().call_later(0.05, task.cancel)
        return httpx.Response(200, content=rss_feed(
            {"title": "A cancellation story", "link": "https://news.example.com/cancel"}).encode())

    web.handle(FEED, handler)

    async def cancel_fetch():
        nonlocal task
        task = asyncio.create_task(svc.fetch_sources())
        with pytest.raises(asyncio.CancelledError):
            await task

    task = None
    try:
        asyncio.run(cancel_fetch())
        assert svc.list_items().total == 1
        assert svc.list_sources()[0].total_items == 1
        assert svc.list_runs()[0].status == "failed"
        assert asyncio.run(svc.fetch_sources())[0].new == 0
        assert svc.list_sources()[0].total_items == 1
    finally:
        for timer in timers:
            timer.join()
        locker.close()


def test_cancelling_a_blocked_run_start_leaves_no_running_record(web, write_config, open_service):
    import sqlite3
    import threading
    import pytest

    svc = open_service(write_config(rss_config()))
    locker = sqlite3.connect(svc.config.db_path, check_same_thread=False)
    locker.execute("BEGIN IMMEDIATE")
    timer = threading.Timer(0.2, locker.commit)
    timer.start()

    async def cancel_start():
        task = asyncio.create_task(svc.fetch_sources())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(cancel_start())
        [run] = svc.list_runs()
        assert run.status == "failed"
    finally:
        timer.join()
        locker.close()


def test_existing_skeleton_databases_keep_suppression_and_repair_counts(web, write_config, open_service, tmp_path):
    import sqlite3
    from pathlib import Path

    db = tmp_path / "data" / "tentacles.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    try:
        conn.executescript((Path(__file__).parent / "fixtures" / "skeleton-v1.sql").read_text())
    finally:
        conn.close()
    web.serve(FEED, '<rss version="2.0"><channel><title>News</title><item>'
                    '<title>A suppressed bulletin</title><guid isPermaLink="false">remembered-guid</guid>'
                    '<link>https://news.example.com/remembered</link></item></channel></rss>')
    path = write_config({"sources": [
        {"id": "news", "type": "rss", "url": FEED, "schedule": "1h"},
        {"id": "other", "type": "rss", "url": "https://other.example.org/rss", "schedule": "1h"}]})
    svc = open_service(path)

    assert asyncio.run(svc.fetch_sources(["news"]))[0].new == 0
    assert svc.list_items().total == 1
    assert {s.id: s.total_items for s in svc.list_sources()} == {"news": 0, "other": 1}
    asyncio.run(svc.aclose())
    reopened = open_service(path)
    assert asyncio.run(reopened.fetch_sources(["news"]))[0].new == 0


@pytest.mark.parametrize("shared_service", [True, False])
@pytest.mark.parametrize("duplicate_by", ["url", "fingerprint", "title"])
def test_concurrent_fetches_deduplicate_atomically(web, write_config, shared_service, duplicate_by):
    import sqlite3
    import threading
    from tentacles.service import TentaclesService

    other = "https://other.example.org/rss"
    path = write_config({"sources": [
        {"id": "news", "type": "rss", "url": FEED, "schedule": "1h"},
        {"id": "other", "type": "rss", "url": other, "schedule": "1h"}]})
    ready = 0
    timer = None
    locker = None
    gate = None

    async def handler(request):
        nonlocal ready, timer, locker
        ready += 1
        if ready == 2:
            locker = sqlite3.connect(svc1.config.db_path, check_same_thread=False)
            locker.execute("BEGIN IMMEDIATE")
            timer = threading.Timer(0.2, locker.commit)
            timer.start()
            gate.set()
        await gate.wait()
        host = request.url.host
        link = "https://article.example/shared" if duplicate_by == "url" else f"https://{host}/article"
        body = "same body" if duplicate_by == "fingerprint" else f"coverage from {host}"
        return httpx.Response(200, content=rss_feed(
            {"title": "Officials report major earthquake in Japan", "link": link,
             "description": body}).encode())

    svc1 = TentaclesService.open(path, transport=httpx.MockTransport(handler), env={})
    svc2 = svc1 if shared_service else TentaclesService.open(path, transport=httpx.MockTransport(handler), env={})

    async def fetch_concurrently():
        nonlocal gate
        gate = asyncio.Event()
        batches = await asyncio.gather(svc1.fetch_sources(["news"]), svc2.fetch_sources(["other"]))
        return [batch[0] for batch in batches]

    try:
        outcomes = asyncio.run(fetch_concurrently())
        assert [o.status for o in outcomes] == ["ok", "ok"]
        assert sorted(o.new for o in outcomes) == [0, 1]
        assert sorted(o.dup for o in outcomes) == [0, 1]
        assert svc1.list_items().total == 1
        assert sum(s.total_items for s in svc1.list_sources()) == 1
    finally:
        if timer is not None:
            timer.join()
        if locker is not None:
            locker.close()
        asyncio.run(svc1.aclose())
        if svc2 is not svc1:
            asyncio.run(svc2.aclose())


def test_failure_finishing_a_run_does_not_advance_the_cursor(web, write_config, open_service):
    import sqlite3

    headers = []

    def handler(request):
        headers.append(request.headers.get("if-none-match"))
        version = len(headers)
        return httpx.Response(200, headers={"etag": f"v{version}"}, content=rss_feed(
            {"title": f"Bulletin {version}", "link": f"https://news.example.com/cursor/{version}"}).encode())

    web.handle(FEED, handler)
    svc = open_service(write_config(rss_config()))
    assert asyncio.run(svc.fetch_sources())[0].status == "ok"
    conn = sqlite3.connect(svc.config.db_path)
    try:
        conn.executescript("""
            CREATE TRIGGER reject_success BEFORE UPDATE OF status ON runs
            WHEN NEW.status = 'ok' BEGIN SELECT RAISE(FAIL, 'temporary write failure'); END;
        """)
        assert asyncio.run(svc.fetch_sources())[0].status == "error"
        assert svc.list_runs()[0].status == "failed"
        conn.execute("DROP TRIGGER reject_success")
        conn.commit()
        assert asyncio.run(svc.fetch_sources())[0].status == "ok"
        assert headers == [None, "v1", "v1"]
    finally:
        conn.close()


def test_cancellation_during_a_success_commit_keeps_the_completed_run(web, write_config):
    import threading
    from datetime import datetime, timezone
    from tentacles.service import TentaclesService

    pause_finish = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    headers = []

    def clock():
        if pause_finish.is_set() and not release.is_set():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test did not release the finishing clock")
        return datetime.now(timezone.utc)

    def handler(request):
        headers.append(request.headers.get("if-none-match"))
        version = len(headers)
        if version == 2:
            pause_finish.set()
        return httpx.Response(200, headers={"etag": f"v{version}"}, content=rss_feed(
            {"title": f"Commit bulletin {version}", "link": f"https://news.example.com/commit/{version}"}).encode())

    web.handle(FEED, handler)
    svc = TentaclesService.open(write_config(rss_config()), transport=web.transport(), env={}, clock=clock)

    async def cancel_commit():
        task = asyncio.create_task(svc.fetch_sources())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                await task

    try:
        asyncio.run(svc.fetch_sources())
        asyncio.run(cancel_commit())
        assert svc.list_runs()[0].status == "ok"
        assert svc.list_sources()[0].last_status == "ok"
        asyncio.run(svc.fetch_sources())
        assert headers == [None, "v1", "v2"]
    finally:
        release.set()
        asyncio.run(svc.aclose())


@pytest.mark.parametrize("cancel_again", [False, True])
def test_cancelling_many_sources_drains_fetches_before_the_service_closes(web, write_config, cancel_again):
    import sqlite3
    import threading
    from tentacles.service import TentaclesService

    path = write_config({"sources": [
        {"id": sid, "type": "rss", "url": f"https://{sid}.example.org/rss", "schedule": "1h"}
        for sid in ("a", "b", "c", "d", "e")]})
    waiting = None
    blocked = None
    locker = None
    timer = None

    async def handler(request):
        nonlocal locker, timer
        if request.url.host != "b.example.org":
            waiting.set()
            await asyncio.Event().wait()
        await waiting.wait()
        locker = sqlite3.connect(svc.config.db_path, check_same_thread=False)
        locker.execute("BEGIN IMMEDIATE")
        timer = threading.Timer(0.3, locker.commit)
        timer.start()
        blocked.set()
        return httpx.Response(200, content=rss_feed(
            {"title": "Interrupted bulletin", "link": "https://news.example.com/multi-cancel"}).encode())

    svc = TentaclesService.open(path, transport=httpx.MockTransport(handler), env={})

    async def cancel_and_close():
        nonlocal waiting, blocked
        waiting, blocked = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(svc.fetch_sources())
        await blocked.wait()
        await asyncio.sleep(0.05)
        task.cancel()
        if cancel_again:
            asyncio.get_running_loop().call_later(0.03, task.cancel)
        with pytest.raises(asyncio.CancelledError):
            await task
        # This is the CLI's lifecycle: immediately close the Service after an operation returns.
        await svc.aclose()
        await asyncio.sleep(0.05)

    try:
        asyncio.run(cancel_and_close())
        reopened = TentaclesService.open(path, env={})
        try:
            runs = reopened.list_runs()
            assert len(runs) == 4
            assert all(run.status == "failed" and run.finished_at is not None for run in runs)
            states = {s.id: s for s in reopened.list_sources()}
            assert all(states[run.target].last_status == "error" for run in runs)
            assert all(states[run.target].last_error for run in runs)
        finally:
            asyncio.run(reopened.aclose())
    finally:
        if timer is not None:
            timer.join()
        if locker is not None:
            locker.close()
        asyncio.run(svc.aclose())


def test_a_concurrent_failure_does_not_overwrite_a_successful_cursor(write_config):
    import sqlite3
    import threading
    from datetime import datetime, timezone
    from tentacles.service import TentaclesService

    path = write_config(rss_config())
    committing = threading.Event()
    finish_pending = threading.Event()
    headers = []

    def clock():
        if finish_pending.is_set():
            committing.set()
        return datetime.now(timezone.utc)

    def success(request):
        headers.append(request.headers.get("if-none-match"))
        version = len(headers)
        if version == 2:
            finish_pending.set()
        return httpx.Response(200, headers={"etag": f"v{version}"}, content=rss_feed(
            {"title": f"Bulletin {version}", "link": f"https://news.example.com/race/{version}"}).encode())

    async def failure(request):
        assert await asyncio.to_thread(committing.wait, 2)
        await asyncio.sleep(0.03)
        return httpx.Response(500)

    good = TentaclesService.open(path, transport=httpx.MockTransport(success), env={}, clock=clock)
    bad = TentaclesService.open(path, transport=httpx.MockTransport(failure), env={})
    connection = sqlite3.connect(good.config.db_path)
    try:
        asyncio.run(good.fetch_sources())
        # A slow external database trigger makes a successful write overlap the failed fetch.
        connection.executescript("""
            CREATE TRIGGER slow_success BEFORE UPDATE OF cursor ON source_state
            WHEN NEW.last_status = 'ok' BEGIN
                SELECT sum(x) FROM (
                    WITH RECURSIVE counter(x) AS (
                        VALUES(0) UNION ALL SELECT x + 1 FROM counter WHERE x < 2000000
                    ) SELECT x FROM counter
                );
            END;
        """)

        async def race():
            failed = asyncio.create_task(bad.fetch_sources())
            await asyncio.sleep(0.03)
            succeeded, failed = await asyncio.gather(good.fetch_sources(), failed)
            assert succeeded[0].status == "ok"
            assert failed[0].status == "error"

        asyncio.run(race())
        connection.execute("DROP TRIGGER slow_success")
        connection.commit()
        asyncio.run(good.fetch_sources())
        assert headers == [None, "v1", "v2"]
        assert {r.status for r in good.list_runs()} == {"ok", "failed"}
    finally:
        connection.close()
        asyncio.run(good.aclose())
        asyncio.run(bad.aclose())
