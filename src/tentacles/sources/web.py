"""Selector-driven web lists and content-version page monitoring."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from difflib import unified_diff
import hashlib
from datetime import datetime
from http.cookies import SimpleCookie
from email.message import Message
from typing import Any, ClassVar, Literal, Type, cast
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import dateparser
from dateparser.search import search_dates
import httpx
import soupsieve
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tentacles.asyncutil import gather_draining, run_blocking, run_store_call
from tentacles.errors import UpstreamError
from tentacles.models import Item, normalize_url
from tentacles.sources.base import FetchContext, FetchResult, Source, register
from tentacles.text import html_to_text, truncate
from tentacles.timeutil import to_utc


@dataclass(frozen=True)
class Selector:
    css: str
    attribute: str | None

    @classmethod
    def parse(cls, value: str) -> Selector:
        value = value.strip()
        if not value or len(value) > 2000:
            raise ValueError('selector must contain 1–2000 characters')
        quote, escaped, depth, split = '', False, 0, -1
        for index, char in enumerate(value):
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif quote:
                if char == quote:
                    quote = ''
            elif char in {'"', "'"}:
                quote = char
            elif char == '[':
                depth += 1
            elif char == ']':
                depth -= 1
            elif char == '@' and depth == 0:
                if split >= 0:
                    raise ValueError('selector has multiple attribute suffixes')
                split = index
        css, attribute = (value, None) if split < 0 else (value[:split].strip() or ':scope', value[split + 1:].strip())
        if attribute is not None and (not attribute or any(not (c.isalnum() or c in '_:-.') for c in attribute)):
            raise ValueError('invalid selector attribute suffix')
        try:
            soupsieve.compile(css)
        except soupsieve.SelectorSyntaxError:
            raise ValueError('invalid CSS selector') from None
        return cls(css, attribute.lower() if attribute is not None else None)

    def value(self, root: Tag, *, default_attribute: str | None = None) -> str:
        node = root if self.css == ':scope' else root.select_one(self.css)
        if node is None:
            return ''
        attribute = self.attribute or default_attribute
        if attribute is not None:
            value = node.get(attribute, '')
            return ' '.join(value) if isinstance(value, list) else str(value or '').strip()
        return html_to_text(str(node))


class ListSelectors(BaseModel):
    model_config = ConfigDict(extra='forbid')
    item: str
    title: str
    link: str
    summary: str | None = None
    date: str | None = None

    @field_validator('item', 'title', 'link', 'summary', 'date')
    @classmethod
    def _selector(cls, value: str | None) -> str | None:
        if value is not None:
            Selector.parse(value)
        return value

    @field_validator('item')
    @classmethod
    def _items_are_nodes(cls, value: str) -> str:
        if Selector.parse(value).attribute is not None:
            raise ValueError('item selector must select nodes, not an attribute')
        return value


class WebOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str
    mode: Literal['list', 'page'] = 'list'
    selectors: ListSelectors | None = None
    selector: str | None = None
    fetch_content: bool = False
    concurrency: int = Field(default=4, ge=1, le=16)
    emit_baseline: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)
    proxy: str | None = None

    @field_validator('url')
    @classmethod
    def _url(cls, value: str) -> str:
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            raise ValueError('must be an http(s) URL without credentials or controls') from None
        if (url.scheme not in {'http', 'https'} or not url.host or url.userinfo or
                any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError('must be an http(s) URL without credentials or controls')
        return str(url)

    @field_validator('proxy')
    @classmethod
    def _proxy(cls, value: str | None) -> str | None:
        return value or None

    @field_validator('selector')
    @classmethod
    def _region(cls, value: str | None) -> str | None:
        if value is not None and Selector.parse(value).attribute is not None:
            raise ValueError('page selector must select nodes, not an attribute')
        return value

    @model_validator(mode='after')
    def _mode_options(self):
        if self.mode == 'list' and self.selectors is None:
            raise ValueError('list mode requires selectors')
        return self


def _link(base: str, value: str) -> str | None:
    if not value:
        return None
    try:
        url = httpx.URL(urljoin(base, value))
        if url.scheme not in {'http', 'https'} or not url.host or url.userinfo:
            return None
        return normalize_url(str(url))
    except (ValueError, httpx.InvalidURL):
        return None


def _published(value: str, ctx: FetchContext) -> datetime | None:
    if not value.strip() or len(value) > 200:
        return None
    try:
        settings = {'RELATIVE_BASE': to_utc(ctx.now).astimezone(ZoneInfo(ctx.timezone)),
                    'TIMEZONE': ctx.timezone, 'TO_TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True,
                    'PREFER_DATES_FROM': 'past'}
        parsed = dateparser.parse(value, settings=settings)
        if parsed is None:
            matches = search_dates(value, settings=settings, strategy='ngram')
            parsed = matches[0][1] if matches else None
    except (ValueError, OverflowError):
        return None
    return to_utc(parsed) if parsed is not None else None


def _document(html: bytes, content_type: str) -> BeautifulSoup:
    header = Message()
    header['content-type'] = content_type
    return BeautifulSoup(html, 'html.parser', from_encoding=header.get_content_charset())


def _article_body(html: bytes, content_type: str) -> str:
    soup = _document(html, content_type)
    for node in soup.select('script, style, noscript, nav, header, footer, aside, form'):
        node.decompose()
    for selector in ('article', 'main', 'body'):
        for node in soup.select(selector):
            text = html_to_text(str(node))
            if text:
                return text
    return html_to_text(str(soup))


@register
class WebSource(Source):
    type: ClassVar[str] = 'web'
    Options: ClassVar[Type[BaseModel]] = WebOptions

    async def _get(self, url: str, ctx: FetchContext) -> httpx.Response:
        opts = cast(WebOptions, self.options)
        def origin(value: str) -> tuple[str, str, int]:
            parsed = httpx.URL(value)
            return parsed.scheme, parsed.host, parsed.port or (443 if parsed.scheme == 'https' else 80)

        same_origin = origin(url) == origin(opts.url)
        client = ctx.http.client(opts.proxy, scope=self.spec.id)
        request = client.build_request('GET', url, headers=opts.headers if same_origin else None, timeout=opts.timeout)
        if same_origin and opts.cookies:
            cookies: SimpleCookie = SimpleCookie()
            cookies.load(request.headers.get('cookie', ''))
            for name, value in opts.cookies.items():
                cookies[name] = value
            request.headers['Cookie'] = '; '.join(cookie.OutputString() for cookie in cookies.values())
        elif not same_origin:
            request.headers.pop('cookie', None)
        response = await client.send(request, follow_redirects=False)
        if not response.is_success:
            raise UpstreamError(f'web source HTTP {response.status_code}')
        return response

    async def fetch(self, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult:
        opts = cast(WebOptions, self.options)
        try:
            response = await self._get(opts.url, ctx)
            if opts.mode == 'page':
                return await run_blocking(self._page, response.content, response.headers.get('content-type', ''), cursor, ctx)
            result = await run_blocking(self._list, response.content, response.headers.get('content-type', ''), str(response.url), cursor, ctx)
            seen = set(cursor.get('seen', [])) if cursor.get('url') == opts.url else set()
            if ctx.seen_links is not None:
                seen.update(await run_store_call(ctx.seen_links, [item.external_id for item in result.items]))
            result.items = [item for item in result.items if item.external_id not in seen]
            result.stats['new_links'] = len(result.items)
            if opts.fetch_content:
                await self._details(result, ctx)
            return result
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError(f'web fetch failed ({type(exc).__name__})') from None

    async def _details(self, result: FetchResult, ctx: FetchContext) -> None:
        opts = cast(WebOptions, self.options)
        pending = list(enumerate(result.items))
        iterator = iter(pending)

        async def worker() -> None:
            for index, item in iterator:
                response = await self._get(item.url, ctx)
                text = await run_blocking(_article_body, response.content, response.headers.get('content-type', ''))
                result.items[index] = Item.model_validate({**item.model_dump(), 'content': text,
                    'summary': item.summary or truncate(text, 2000), 'fingerprint': ''})

        tasks = [asyncio.create_task(worker()) for _ in range(min(opts.concurrency, len(pending)))]
        if tasks:
            await gather_draining(tasks)
        result.stats['details_fetched'] = len(pending)

    def _page(self, html: bytes, content_type: str, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult:
        opts = cast(WebOptions, self.options)
        soup = _document(html, content_type)
        regions = soup.select(opts.selector) if opts.selector else [soup]
        if not regions:
            raise UpstreamError('web page selector matched no region')
        text = '\n'.join(html_to_text(str(region)) for region in regions).strip()
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        url = normalize_url(opts.url)
        identity = {'url': url, 'mode': 'page', 'selector': opts.selector}
        previous = cursor if cursor.get('identity') == identity else {}
        baseline = 'content_hash' not in previous
        changed = not baseline and previous['content_hash'] != digest
        summary = truncate('基线：\n' + text, 2000) if baseline else ''
        if changed:
            lines = list(unified_diff(str(previous.get('content', '')).splitlines(), text.splitlines(), lineterm=''))
            summary = truncate('\n'.join(line for line in lines[2:] if line.startswith(('+', '-'))), 2000)
        stats = {'baseline': baseline, 'changed': changed, 'content_hash': digest,
                 'previous_hash': previous.get('content_hash'), 'diff': summary if changed else ''}
        items = []
        if changed or baseline and opts.emit_baseline:
            title = (html_to_text(str(soup.title)) if soup.title else '') or self.spec.name or httpx.URL(opts.url).host
            local_date = to_utc(ctx.now).astimezone(ZoneInfo(ctx.timezone)).date().isoformat()
            items = [Item(source_id=self.spec.id, kind='web', identity_policy='revision',
                external_id=url + '#' + digest, url=url, title=f'{title} {local_date}', summary=summary,
                content=text, published=ctx.now, meta={'web_mode': 'page', 'content_hash': digest, 'baseline': baseline})]
        return FetchResult(items, {'identity': identity, 'content_hash': digest, 'content': text}, stats)

    def _list(self, html: bytes, content_type: str, base: str, cursor: dict[str, Any], ctx: FetchContext) -> FetchResult:
        opts = cast(WebOptions, self.options)
        assert opts.selectors is not None
        selectors = opts.selectors
        title, link = Selector.parse(selectors.title), Selector.parse(selectors.link)
        summary = Selector.parse(selectors.summary) if selectors.summary else None
        published = Selector.parse(selectors.date) if selectors.date else None
        soup = _document(html, content_type)
        items, seen = [], set()
        for node in soup.select(selectors.item):
            name = title.value(node).strip()
            url = _link(base, link.value(node, default_attribute='href'))
            if not name or url is None or url in seen:
                continue
            seen.add(url)
            excerpt = truncate(summary.value(node), 2000) if summary else ''
            items.append(Item(source_id=self.spec.id, kind='web', external_id=url, url=url,
                title=name, summary=excerpt, content=excerpt,
                published=_published(published.value(node), ctx) if published else None, meta={'web_mode': 'list'}))
        recent = dict.fromkeys(cursor.get('seen', []) if cursor.get('url') == opts.url else [])
        for item in items:
            recent.pop(item.external_id, None)
            recent[item.external_id] = None
        cached = list(recent)[-500:]
        return FetchResult(items, {'url': opts.url, 'seen': cached}, {'entries': len(items), 'seen_cached': len(cached)})
