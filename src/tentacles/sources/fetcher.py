"""Fetch RSS / Atom feeds concurrently and turn entries into Articles."""

from __future__ import annotations

import asyncio
import logging
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import feedparser
import httpx

from tentacles.models import Article

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "tentacles/0.1 (+news feed aggregator)"}


def _parse_time(value: time.struct_time | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)


def _entry_published(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return _parse_time(parsed)
    # 尝试 Date 头格式（RFC 822）
    raw = getattr(entry, "published", None)
    if raw:
        try:
            return parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            pass
    return None


def _clean_summary(entry) -> str:
    import re
    from html import unescape

    raw = entry.get("summary") or entry.get("description") or ""
    if entry.get("content"):
        raw = entry.content[0].value
    # feed 条目里的 HTML 摘要去标签即可，够 LLM 使用
    text = re.sub(r"<[^>]+>", "", raw)
    text = unescape(text)
    return text[:2000]


def entry_to_article(entry, source: str) -> Article | None:
    link = (entry.get("link") or "").strip()
    title = (entry.get("title") or "").strip()
    if not link or not title:
        return None
    return Article(
        url=link,
        source=source,
        title=title,
        summary=_clean_summary(entry),
        published=_entry_published(entry),
    )


async def _fetch_one(client: httpx.AsyncClient, name: str, url: str,
                     semaphore: asyncio.Semaphore) -> list[Article]:
    async with semaphore:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("fetch feed %s failed: %s", name, exc)
            return []
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        log.warning("parse feed %s failed: %s", name, parsed.bozo_exception)
        return []
    articles = [a for entry in parsed.entries if (a := entry_to_article(entry, name))]
    log.info("feed %s: %d entries", name, len(articles))
    return articles


async def fetch_feeds(feeds: dict[str, str], *, timeout: float = 30.0,
                      max_concurrency: int = 8) -> list[Article]:
    """并发抓取 {feed 名: feed url}，返回全部文章（未去重）。"""
    semaphore = asyncio.Semaphore(max_concurrency)
    async with httpx.AsyncClient(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(_fetch_one(client, name, url, semaphore) for name, url in feeds.items())
        )
    return [article for batch in results for article in batch]
