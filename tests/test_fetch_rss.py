import asyncio

import httpx

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


def test_a_series_of_similar_titles_in_one_feed_is_kept(web, write_config, open_service):
    web.serve(FEED, rss_feed(
        {"title": "Python 3.13.2 released", "link": "https://news.example.com/3132"},
        {"title": "Python 3.13.1 released", "link": "https://news.example.com/3131"},
    ))
    svc = open_service(write_config(rss_config()))

    [outcome] = asyncio.run(svc.fetch_sources())

    assert (outcome.new, outcome.dup) == (2, 0)


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
