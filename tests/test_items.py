import asyncio
import sqlite3

import pytest

from conftest import rss_feed
from tentacles.errors import ConfigError, NotFound
from tentacles.models import Item

A, B = "https://a.example.com/rss", "https://b.example.com/rss"


def config(*urls):
    return {"sources": [{"id": f"s{i}", "type": "rss", "url": u, "schedule": "1h"}
                        for i, u in enumerate(urls)]}


def fetch(svc):
    return {o.source_id: o for o in asyncio.run(svc.fetch_sources())}


def test_same_article_from_two_sources_is_kept_once(web, write_config, open_service):
    web.serve(A, rss_feed({"title": "Launch day", "link": "https://site.com/p?utm_source=a"}))
    web.serve(B, rss_feed({"title": "Launch day!", "link": "https://site.com/p?utm_source=b"}))
    svc = open_service(write_config(config(A, B)))

    out = fetch(svc)

    assert out["s0"].new + out["s1"].new == 1
    assert out["s0"].dup + out["s1"].dup == 1
    assert svc.list_items().items[0].url == "https://site.com/p"


def test_same_content_under_different_urls_is_a_duplicate(web, write_config, open_service):
    web.serve(A, rss_feed({"title": "Same", "link": "https://a.com/1", "description": "body"}))
    web.serve(B, rss_feed({"title": "Same", "link": "https://b.com/2", "description": "body"}))
    svc = open_service(write_config(config(A, B)))

    out = fetch(svc)

    assert sorted([out["s0"].new, out["s1"].new]) == [0, 1]


def test_near_identical_titles_in_one_batch_keep_only_the_first(web, write_config, open_service):
    web.serve(A, rss_feed(
        {"title": "Officials report major earthquake in Japan", "link": "https://a.com/1"},
        {"title": "Officials report major earthquake in Japan!", "link": "https://a.com/2"},
        {"title": "Unrelated gardening tips", "link": "https://a.com/3"},
    ))
    svc = open_service(write_config(config(A)))

    out = fetch(svc)

    assert (out["s0"].new, out["s0"].dup) == (2, 1)


def test_emails_with_identical_content_are_not_fingerprint_duplicates(write_config, open_service):
    # 邮件源在后续 ticket 才有；这里直接经由存储验证去重规则对 email 的豁免
    svc = open_service(write_config(config(A)))
    mails = [Item(source_id="inbox", kind="email", external_id=f"<m{i}@x>", title="Daily report",
                  summary="all green") for i in range(2)]

    new, dup = svc.store.upsert_items(mails)

    assert (len(new), dup) == (2, 0)


def published(day: int) -> str:
    return f"{['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][day - 20]}, {day} Oct 2026 08:00:00 GMT"


def seeded(web, write_config, open_service):
    web.serve(A, rss_feed(*[{"title": f"Alpha story {d}", "link": f"https://a.com/{d}", "pubDate": published(d),
                             "description": "rockets" if d % 2 else "gardens"} for d in range(20, 25)]))
    web.serve(B, rss_feed({"title": "Beta news", "link": "https://b.com/1", "pubDate": published(22)}))
    svc = open_service(write_config(config(A, B)))
    fetch(svc)
    return svc


def test_items_are_newest_first_and_paginated(web, write_config, open_service):
    svc = seeded(web, write_config, open_service)

    first = svc.list_items(limit=2)
    second = svc.list_items(limit=2, offset=2)

    assert first.total == 6
    assert [i.title for i in first.items] == ["Alpha story 24", "Alpha story 23"]
    assert [i.title for i in second.items][0] in {"Alpha story 22", "Beta news"}


def test_items_filter_by_source_time_and_keyword(web, write_config, open_service):
    svc = seeded(web, write_config, open_service)

    assert {i.source_id for i in svc.list_items(sources=["s1"]).items} == {"s1"}
    assert [i.title for i in svc.list_items(since="2026-10-23T00:00:00+00:00").items] == \
        ["Alpha story 24", "Alpha story 23"]
    assert [i.title for i in svc.list_items(until="2026-10-21T00:00:00+00:00").items] == ["Alpha story 20"]
    assert {i.title for i in svc.list_items(search="rockets").items} == {"Alpha story 21", "Alpha story 23"}


def test_items_reject_bad_queries(web, write_config, open_service):
    svc = seeded(web, write_config, open_service)

    with pytest.raises(NotFound):
        svc.list_items(sources=["missing"])
    with pytest.raises(ConfigError):
        svc.list_items(since="yesterday-ish")
    with pytest.raises(ConfigError):
        svc.list_items(limit=0)


def test_item_detail_has_full_content(web, write_config, open_service):
    web.serve(A, "<?xml version='1.0'?><rss version='2.0'><channel><title>t</title><item>"
                 "<title>Deep</title><link>https://a.com/d</link><description>short</description>"
                 "<content:encoded xmlns:content='http://purl.org/rss/1.0/modules/content/'>"
                 "<![CDATA[<p>para one</p><p>para two</p>]]></content:encoded></item></channel></rss>")
    svc = open_service(write_config(config(A)))
    fetch(svc)

    [item] = svc.list_items().items
    detail = svc.get_item(item.id)

    assert detail.summary == "short"
    assert detail.content == "para one\npara two"
    with pytest.raises(NotFound):
        svc.get_item("nope")


def test_legacy_articles_are_migrated_once_and_the_old_table_is_kept(write_config, open_service, tmp_path):
    db = tmp_path / "data" / "tentacles.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE articles (id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
          title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', published TEXT, fetched_at TEXT NOT NULL,
          fingerprint TEXT NOT NULL);
        INSERT INTO articles VALUES ('x', 'https://old.com/1', 'hacker-news', 'Old one', 'sum',
          '2026-10-01T08:00:00+00:00', '2026-10-01T09:00:00.123456+00:00', 'fp1');
    """)
    conn.commit()
    conn.close()

    svc = open_service(write_config(config(A)))
    [item] = svc.list_items().items

    assert (item.source_id, item.kind, item.title, item.url) == ("hacker-news", "rss", "Old one", "https://old.com/1")
    assert item.published.isoformat() == "2026-10-01T08:00:00+00:00"
    asyncio.run(svc.aclose())
    svc2 = open_service(write_config(config(A)))
    assert svc2.list_items().total == 1
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1


def test_search_treats_wildcards_literally(web, write_config, open_service):
    web.serve(A, rss_feed({"title": "Growth hits 100% in snake_case land", "link": "https://a.com/1"},
                          {"title": "Plain headline", "link": "https://a.com/2"},
                          {"title": "snakeXcase", "link": "https://a.com/3"}))
    svc = open_service(write_config(config(A)))
    fetch(svc)

    assert svc.list_items(search="_").total == 1
    assert svc.list_items(search="%").total == 1
    assert [i.title for i in svc.list_items(search="snake_case").items] == ["Growth hits 100% in snake_case land"]


def test_dedup_lookup_uses_indexes(write_config, open_service):
    svc = open_service(write_config(config(A)))
    plans = svc.store.explain_duplicate_lookup()

    assert plans and not any("SCAN items" in p for p in plans)


def test_legacy_feed_names_map_to_new_ids_and_seed_counts(write_config, open_service, tmp_path):
    db = tmp_path / "data" / "tentacles.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE articles (id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
          title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', published TEXT, fetched_at TEXT NOT NULL,
          fingerprint TEXT NOT NULL);
        INSERT INTO articles VALUES ('1', 'https://sspai.com/1', '少数派', 'T1', '', NULL, '2026-10-01T09:00:00', 'f1');
        INSERT INTO articles VALUES ('2', 'https://sspai.com/2', '少数派', 'T2', '', NULL, '2026-10-01T09:00:00', 'f2');
    """)
    conn.commit()
    conn.close()

    svc = open_service(write_config({"sources": [{"id": "sspai", "type": "rss", "url": A, "schedule": "1h"}]}))

    assert svc.list_items(sources=["sspai"]).total == 2
    assert svc.list_sources()[0].total_items == 2


def test_failed_migration_leaves_the_database_retryable(write_config, tmp_path, monkeypatch):
    from tentacles import store as store_mod
    from tentacles.service import TentaclesService

    def boom(conn):
        raise RuntimeError("killed mid-migration")

    path = write_config(config(A))
    real = store_mod.MIGRATIONS[0]
    monkeypatch.setattr(store_mod, "MIGRATIONS", [lambda conn: (real(conn), boom(conn))])
    with pytest.raises(RuntimeError):
        TentaclesService.open(path, env={})
    monkeypatch.setattr(store_mod, "MIGRATIONS", [real])

    svc = TentaclesService.open(path, env={})
    try:
        assert svc.list_items().total == 0
    finally:
        svc.store.close()
