"""Shared CSS/`@attr` selector parsing, URL resolution, and flexible date parsing for Web and Browser sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import dateparser
from dateparser.search import search_dates
import httpx
import soupsieve

from tentacles.models import normalize_url
from tentacles.sources.base import FetchContext
from tentacles.timeutil import to_utc


@dataclass(frozen=True)
class CssAttrBranch:
    """One CSS selector + optional `@attribute` extraction target."""

    css: str
    attribute: str | None

    @classmethod
    def parse_single(cls, value: str) -> CssAttrBranch:
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 2000:
            raise ValueError('selector must contain 1–2000 characters')
        quote, escaped, bracket_depth, paren_depth, split = '', False, 0, 0, -1
        for index, char in enumerate(cleaned):
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
                bracket_depth += 1
            elif char == ']':
                bracket_depth = max(0, bracket_depth - 1)
            elif char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth = max(0, paren_depth - 1)
            elif char == '@' and bracket_depth == 0 and paren_depth == 0:
                if split >= 0:
                    raise ValueError('selector has multiple attribute suffixes')
                split = index
        if quote or bracket_depth != 0 or paren_depth != 0:
            raise ValueError('invalid CSS selector')
        css, attribute = (
            (cleaned, None)
            if split < 0
            else (cleaned[:split].strip() or ':scope', cleaned[split + 1:].strip())
        )
        if attribute is not None and (
            not attribute or any(not (c.isalnum() or c in '_:-.') for c in attribute)
        ):
            raise ValueError('invalid selector attribute suffix')
        try:
            soupsieve.compile(css)
        except soupsieve.SelectorSyntaxError:
            raise ValueError('invalid CSS selector') from None
        return cls(css, attribute.lower() if attribute is not None else None)


def split_top_level_commas(value: str) -> list[str]:
    """Split a selector list on top-level commas outside quotes, `[]` and `()`."""
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 2000:
        raise ValueError('selector must contain 1–2000 characters')
    parts: list[str] = []
    start = 0
    quote, escaped, bracket_depth, paren_depth = '', False, 0, 0
    for index, char in enumerate(cleaned):
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
            bracket_depth += 1
        elif char == ']':
            bracket_depth = max(0, bracket_depth - 1)
        elif char == '(':
            paren_depth += 1
        elif char == ')':
            paren_depth = max(0, paren_depth - 1)
        elif char == ',' and bracket_depth == 0 and paren_depth == 0:
            part = cleaned[start:index].strip()
            if not part:
                raise ValueError('invalid CSS selector')
            parts.append(part)
            start = index + 1
    if quote or bracket_depth != 0 or paren_depth != 0:
        raise ValueError('invalid CSS selector')
    tail = cleaned[start:].strip()
    if not tail:
        raise ValueError('invalid CSS selector')
    parts.append(tail)
    return parts


@dataclass(frozen=True)
class MultiCssSelector:
    """One or more `CssAttrBranch` candidates tried in order (`a@data-url, a@href` or `a, b@attr`)."""

    branches: tuple[CssAttrBranch, ...]

    @classmethod
    def parse(cls, value: str) -> MultiCssSelector:
        raw_parts = split_top_level_commas(value)
        parsed = [CssAttrBranch.parse_single(part) for part in raw_parts]
        # When the trailing branch has `@attr` and earlier comma-separated branches omit `@`,
        # propagate the trailing attribute so `a, b@aria-label` extracts `@aria-label` from both `a` and `b`.
        if len(parsed) > 1 and parsed[-1].attribute is not None and all(b.attribute is None for b in parsed[:-1]):
            shared_attr = parsed[-1].attribute
            parsed = [CssAttrBranch(b.css, shared_attr) for b in parsed]
        return cls(tuple(parsed))

    def payload(self, *, default_attribute: str | None = None) -> list[dict[str, str | None]]:
        return [
            {
                'css': b.css,
                'attribute': b.attribute,
                'default_attribute': default_attribute if b.attribute is None else None,
            }
            for b in self.branches
        ]


def validate_node_selector(value: str) -> str:
    parsed = MultiCssSelector.parse(value)
    if any(b.attribute is not None for b in parsed.branches):
        raise ValueError('selector must select nodes, not an attribute')
    return value.strip()


def resolve_http_url(base_url: str, value: str | None) -> str:
    """Resolve `value` against `base_url`; return normalized `http(s)` URL or empty string."""
    if not value or not value.strip():
        return ''
    try:
        url = httpx.URL(urljoin(base_url, value.strip()))
    except (ValueError, httpx.InvalidURL):
        return ''
    if url.scheme not in {'http', 'https'} or not url.host or url.userinfo:
        return ''
    return normalize_url(str(url))


def parse_flexible_datetime(value: str | None, ctx: FetchContext) -> datetime | None:
    """Parse ISO 8601 timestamps or localized relative dates into UTC `datetime`."""
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 200:
        return None
    try:
        return to_utc(datetime.fromisoformat(cleaned.replace('Z', '+00:00')))
    except ValueError:
        pass
    try:
        settings: Any = {
            'RELATIVE_BASE': to_utc(ctx.now).astimezone(ZoneInfo(ctx.timezone)),
            'TIMEZONE': ctx.timezone,
            'TO_TIMEZONE': 'UTC',
            'RETURN_AS_TIMEZONE_AWARE': True,
            'PREFER_DATES_FROM': 'past',
        }
        parsed = dateparser.parse(cleaned, settings=settings)
        if parsed is None:
            matches = search_dates(cleaned, settings=settings, strategy='ngram')
            parsed = matches[0][1] if matches else None
    except (ValueError, OverflowError):
        return None
    return to_utc(parsed) if parsed is not None else None
