"""`type: browser` + `mode: list`: infinite-scroll CSS selector & metric-prefilter extraction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import random
import re
from time import monotonic
from typing import Any, Literal
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import dateparser
from dateparser.search import search_dates
import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import soupsieve

from tentacles.config import AppConfig
from tentacles.diagnostics import DiagnosticEvent, stack_frames
from tentacles.models import Item, normalize_url
from tentacles.sources.base import DiagnosedError, FetchContext
from tentacles.sources.browser_session import page_snapshot, work_tab
from tentacles.text import truncate
from tentacles.timeutil import to_utc

_INITIAL_SETTLE_MS = 1200
_TIMEOUT_SAFETY_S = 3.0
_DERIVED_TITLE_LIMIT = 140
_METRIC_KEYS: tuple[str, ...] = ('votes', 'likes', 'comments', 'reposts', 'views')

_METRIC_NUMBER_RE = re.compile(
    r'(?<![\w@#/])'
    r'(?:[+-]\s*)?'
    r'(\d{1,3}(?:,\d{3})+|\d+)'
    r'(?:\.(\d+))?'
    r'\s*([kKmMbB]|万|亿)?'
)
_METRIC_MULTIPLIERS: dict[str, int] = {
    'k': 1_000,
    'm': 1_000_000,
    'b': 1_000_000_000,
    '万': 10_000,
    '亿': 100_000_000,
}

_JS_EXTRACT_CARDS = """
(spec) => {
  const queryValue = (root, sel) => {
    if (!sel) return null;
    const node = sel.css === ':scope' ? root : root.querySelector(sel.css);
    if (!node) return null;
    const attr = sel.attribute || sel.default_attribute;
    if (attr) {
      if (!node.hasAttribute(attr)) return null;
      const val = node.getAttribute(attr);
      return val === null ? null : String(val).trim();
    }
    const raw = (typeof node.innerText === 'string' && node.innerText) ? node.innerText : (node.textContent || '');
    return raw.replace(/\\r\\n?/g, '\\n').trim();
  };

  const matchesRule = (root, css) => {
    if (css === ':scope') return true;
    return root.matches(css) || root.querySelector(css) !== null;
  };

  const elements = Array.from(document.querySelectorAll(spec.item));
  const cards = [];
  for (const el of elements) {
    let skipped = false;
    for (const rule of spec.skip_if) {
      if (matchesRule(el, rule)) {
        skipped = true;
        break;
      }
    }
    if (!skipped) {
      for (const rule of spec.require) {
        if (!matchesRule(el, rule)) {
          skipped = true;
          break;
        }
      }
    }
    const rawText = typeof el.innerText === 'string' && el.innerText ? el.innerText : (el.textContent || '');
    if (skipped) {
      const adLink = queryValue(el, spec.link);
      cards.push({
        skipped_ad: true,
        ad_key: adLink || rawText.replace(/\\s+/g, ' ').trim().slice(0, 240) || el.outerHTML.slice(0, 240),
      });
      continue;
    }
    const metrics = {};
    for (const [key, sel] of Object.entries(spec.metrics)) {
      metrics[key] = queryValue(el, sel);
    }
    cards.push({
      skipped_ad: false,
      link: queryValue(el, spec.link),
      title: queryValue(el, spec.title),
      content: queryValue(el, spec.content),
      summary: queryValue(el, spec.summary),
      author: queryValue(el, spec.author),
      date: queryValue(el, spec.date),
      comments_url: queryValue(el, spec.comments_url),
      external_url: queryValue(el, spec.external_url),
      metrics,
    });
  }
  return { base_url: document.baseURI || location.href, cards };
}
"""

_JS_SCROLL_DOWN = """
(itemSelector) => {
  const cards = document.querySelectorAll(itemSelector);
  const last = cards.length ? cards[cards.length - 1] : null;
  if (last) {
    last.scrollIntoView({ behavior: 'instant', block: 'end' });
  }
  const step = Math.max(Math.floor(window.innerHeight * 1.4), 900);
  window.scrollBy(0, step);
}
"""


@dataclass(frozen=True)
class BrowserSelector:
    css: str
    attribute: str | None

    @classmethod
    def parse(cls, value: str) -> BrowserSelector:
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

    def payload(self, *, default_attribute: str | None = None) -> dict[str, str | None]:
        return {
            'css': self.css,
            'attribute': self.attribute,
            'default_attribute': default_attribute if self.attribute is None else None,
        }


def _validate_selector(value: str | None) -> str | None:
    if value is not None:
        BrowserSelector.parse(value)
    return value


def _validate_node_selector(value: str) -> str:
    parsed = BrowserSelector.parse(value)
    if parsed.attribute is not None:
        raise ValueError('selector must select nodes, not an attribute')
    return parsed.css


class BrowserClickStep(BaseModel):
    """One pre-scan click step (e.g. switching from X 'For you' to 'Following')."""

    model_config = ConfigDict(extra='forbid')

    selector: str
    has_text: str | list[str] | None = None
    wait_ms: int = Field(default=1500, ge=0, le=30000)

    @field_validator('selector')
    @classmethod
    def _selector(cls, value: str) -> str:
        return _validate_node_selector(value)

    @field_validator('has_text')
    @classmethod
    def _has_text(cls, value: str | list[str] | None) -> str | list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                raise ValueError('has_text must not be empty')
            return cleaned
        cleaned_list = [v.strip() for v in value if isinstance(v, str) and v.strip()]
        if not cleaned_list:
            raise ValueError('has_text list must contain at least one non-empty string')
        return cleaned_list


class BrowserScrollOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_scrolls: int = Field(default=20, ge=0, le=200)
    wait_min_ms: int = Field(default=1200, ge=100, le=30000)
    wait_max_ms: int = Field(default=3000, ge=100, le=60000)
    idle_rounds: int = Field(default=2, ge=1, le=20)

    @model_validator(mode='after')
    def _wait_bounds(self) -> BrowserScrollOptions:
        if self.wait_max_ms < self.wait_min_ms:
            raise ValueError('wait_max_ms must be greater than or equal to wait_min_ms')
        return self


class BrowserMetricsSelectors(BaseModel):
    model_config = ConfigDict(extra='forbid')

    votes: str | None = None
    likes: str | None = None
    comments: str | None = None
    reposts: str | None = None
    views: str | None = None

    @field_validator('votes', 'likes', 'comments', 'reposts', 'views')
    @classmethod
    def _selector(cls, value: str | None) -> str | None:
        return _validate_selector(value)


class BrowserListSelectors(BaseModel):
    model_config = ConfigDict(extra='forbid')

    item: str
    link: str
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    author: str | None = None
    date: str | None = None
    comments_url: str | None = None
    external_url: str | None = None
    prefix_author_in_title: bool = False
    prepend_metadata_in_content: bool = False
    before_scan_clicks: list[BrowserClickStep] = Field(default_factory=list)
    skip_if: list[str] = Field(default_factory=list)
    require: list[str] = Field(default_factory=list)
    metrics: BrowserMetricsSelectors = Field(default_factory=BrowserMetricsSelectors)

    @field_validator('item')
    @classmethod
    def _item_nodes(cls, value: str) -> str:
        return _validate_node_selector(value)

    @field_validator('link', 'title', 'content', 'summary', 'author', 'date', 'comments_url', 'external_url')
    @classmethod
    def _field_selectors(cls, value: str | None) -> str | None:
        return _validate_selector(value)

    @field_validator('skip_if', 'require')
    @classmethod
    def _filter_selectors(cls, values: list[str]) -> list[str]:
        return [_validate_node_selector(value) for value in values]

    @model_validator(mode='after')
    def _require_text_field(self) -> BrowserListSelectors:
        if not any((self.title, self.content, self.summary)):
            raise ValueError('at least one of title, content or summary selector is required')
        return self


class BrowserPrefilterOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    match: Literal['any', 'all'] = 'any'
    min_votes: int | None = Field(default=None, ge=0)
    min_likes: int | None = Field(default=None, ge=0)
    min_comments: int | None = Field(default=None, ge=0)
    min_reposts: int | None = Field(default=None, ge=0)
    min_views: int | None = Field(default=None, ge=0)
    min_text_chars: int = Field(default=0, ge=0, le=10000)

    def passes(self, metrics: dict[str, int], *, text_chars: int) -> bool:
        if self.min_text_chars > 0 and text_chars < self.min_text_chars:
            return False
        thresholds: list[tuple[str, int]] = []
        for key in _METRIC_KEYS:
            threshold = getattr(self, f'min_{key}')
            if threshold is not None:
                thresholds.append((key, threshold))
        if not thresholds:
            return True
        checks = [metrics.get(key, 0) >= threshold for key, threshold in thresholds]
        return any(checks) if self.match == 'any' else all(checks)


def parse_metric_count(raw: str | None) -> int:
    """Parse localized or compact engagement counts into a non-negative integer."""
    if not raw:
        return 0
    cleaned = raw.replace('\xa0', ' ').replace('\u202f', ' ').strip()
    if not cleaned:
        return 0
    match = _METRIC_NUMBER_RE.search(cleaned)
    if match is None:
        return 0
    whole_raw, frac_raw, unit_raw = match.groups()
    whole = int(whole_raw.replace(',', ''))
    if not unit_raw:
        return whole
    multiplier = _METRIC_MULTIPLIERS[unit_raw.lower()]
    if not frac_raw:
        return whole * multiplier
    scale = 10 ** len(frac_raw)
    return (whole * scale + int(frac_raw)) * multiplier // scale


def clean_author(raw: str | None) -> str:
    """Normalize author strings, stripping trailing X-style `· timestamp` suffixes."""
    if not raw:
        return ''
    lines = [re.sub(r'[ \t\xa0]+', ' ', line).strip() for line in raw.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ''
    if '·' in lines:
        lines = lines[:lines.index('·')]
    cleaned_lines: list[str] = []
    for line in lines:
        if ' · ' in line and '@' in ' '.join([*cleaned_lines, line]):
            head = line.split(' · ', 1)[0].strip()
            if head:
                cleaned_lines.append(head)
            break
        cleaned_lines.append(line)
    if len(cleaned_lines) >= 2 and cleaned_lines[1].startswith('@') and not cleaned_lines[0].startswith('@'):
        return f'{cleaned_lines[0]} ({cleaned_lines[1]})'
    return ' '.join(cleaned_lines).strip()


def derive_title(
    explicit_title: str | None,
    content: str,
    summary: str,
    *,
    author: str,
    url: str,
    prefix_author: bool = False,
) -> str:
    """Use explicit title when present, otherwise derive from content/summary or author + URL."""
    base_title = ''
    if explicit_title and explicit_title.strip():
        base_title = truncate(re.sub(r'\s+', ' ', explicit_title).strip(), 1000)
    else:
        for text in (content, summary):
            for line in text.splitlines():
                cleaned = re.sub(r'\s+', ' ', line).strip()
                if cleaned:
                    base_title = truncate(cleaned, _DERIVED_TITLE_LIMIT)
                    break
            if base_title:
                break
    if not base_title:
        if author:
            return truncate(f'{author}: {url}', _DERIVED_TITLE_LIMIT)
        return truncate(url, _DERIVED_TITLE_LIMIT)
    if prefix_author and author and not base_title.startswith(f'{author}:'):
        return truncate(f'{author}: {base_title}', 1000)
    return base_title


def format_metadata_header(
    *,
    author: str,
    published: datetime | None,
    metrics: Mapping[str, int],
) -> str:
    """Compact factual header so downstream LLM stages see author, UTC timestamp and metrics."""
    parts: list[str] = []
    if author:
        parts.append(f'Author: {author}')
    if published is not None:
        parts.append(f'Published: {published.strftime("%Y-%m-%d %H:%M UTC")}')
    metric_parts = [f'{key}={metrics[key]}' for key in _METRIC_KEYS if key in metrics]
    if metric_parts:
        parts.append('Metrics: ' + ', '.join(metric_parts))
    return '[' + ' | '.join(parts) + ']' if parts else ''


def resolve_http_url(base_url: str, value: str | None) -> str:
    if not value or not value.strip():
        return ''
    try:
        url = httpx.URL(urljoin(base_url, value.strip()))
    except (ValueError, httpx.InvalidURL):
        return ''
    if url.scheme not in {'http', 'https'} or not url.host or url.userinfo:
        return ''
    return normalize_url(str(url))


def parse_published(value: str | None, ctx: FetchContext) -> datetime | None:
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


def build_extractor_payload(selectors: BrowserListSelectors) -> dict[str, Any]:
    def optional(raw: str | None, *, default_attribute: str | None = None) -> dict[str, str | None] | None:
        return BrowserSelector.parse(raw).payload(default_attribute=default_attribute) if raw is not None else None

    metrics_payload: dict[str, dict[str, str | None]] = {}
    for key in _METRIC_KEYS:
        sel = getattr(selectors.metrics, key)
        if sel is not None:
            metrics_payload[key] = BrowserSelector.parse(sel).payload()

    return {
        'item': selectors.item,
        'link': BrowserSelector.parse(selectors.link).payload(default_attribute='href'),
        'title': optional(selectors.title),
        'content': optional(selectors.content),
        'summary': optional(selectors.summary),
        'author': optional(selectors.author),
        'date': optional(selectors.date),
        'comments_url': optional(selectors.comments_url, default_attribute='href'),
        'external_url': optional(selectors.external_url, default_attribute='href'),
        'skip_if': list(selectors.skip_if),
        'require': list(selectors.require),
        'metrics': metrics_payload,
    }


@dataclass
class BrowserListScanResult:
    items: list[Item]
    stats: dict[str, Any]
    snapshot: dict[str, Any]


def accumulate_round_cards(
    raw_batch: Mapping[str, Any],
    *,
    source_id: str,
    page_url: str,
    selectors: BrowserListSelectors,
    prefilter: BrowserPrefilterOptions | None,
    max_items: int,
    ctx: FetchContext,
    seen_keys: set[str],
    seen_ads: set[str],
    accepted_items: list[Item],
    counters: dict[str, int],
) -> int:
    """Process one round's evaluated cards, mutating `seen_keys`, `seen_ads`, `accepted_items` and `counters`."""
    base_url = str(raw_batch.get('base_url') or page_url)
    cards = raw_batch.get('cards')
    if not isinstance(cards, list):
        return 0
    configured_metric_keys = [
        key for key in _METRIC_KEYS if getattr(selectors.metrics, key) is not None
    ]
    new_on_page = 0
    for idx, card in enumerate(cards):
        if not isinstance(card, Mapping):
            continue
        if card.get('skipped_ad'):
            ad_key = _as_optional_str(card.get('ad_key')) or f'ad:{idx}'
            if ad_key not in seen_ads:
                seen_ads.add(ad_key)
                counters['skipped_ads'] += 1
            continue
        counters['mounted_non_ad_seen'] += 1
        url = resolve_http_url(base_url, _as_optional_str(card.get('link')))
        if not url:
            counters['missing_link'] += 1
            continue
        if url in seen_keys:
            continue
        seen_keys.add(url)
        new_on_page += 1
        counters['dom_scanned'] += 1

        explicit_title = _as_optional_str(card.get('title'))
        raw_content = (_as_optional_str(card.get('content')) or '').strip()
        raw_summary = (_as_optional_str(card.get('summary')) or '').strip()
        author = clean_author(_as_optional_str(card.get('author')))

        raw_metrics = card.get('metrics')
        parsed_metrics: dict[str, int] = {}
        if isinstance(raw_metrics, Mapping):
            for key in configured_metric_keys:
                parsed_metrics[key] = parse_metric_count(_as_optional_str(raw_metrics.get(key)))

        body_for_length = raw_content or raw_summary or (explicit_title or '').strip()
        if prefilter is not None and not prefilter.passes(parsed_metrics, text_chars=len(body_for_length)):
            counters['filtered_out'] += 1
            continue

        counters['matched'] += 1
        if len(accepted_items) >= max_items:
            continue

        title = derive_title(
            explicit_title,
            raw_content,
            raw_summary,
            author=author,
            url=url,
            prefix_author=selectors.prefix_author_in_title,
        )
        published = parse_published(_as_optional_str(card.get('date')), ctx)
        content = raw_content or truncate(raw_summary, 2000) or title
        summary = truncate(raw_summary or raw_content or title, 2000)
        if selectors.prepend_metadata_in_content:
            header = format_metadata_header(author=author, published=published, metrics=parsed_metrics)
            if header:
                content = f'{header}\n{content}' if content else header
                summary = truncate(f'{header} {summary}' if summary else header, 2000)

        meta: dict[str, Any] = {
            'browser_mode': 'list',
            'browser_entry_url': page_url,
            'metrics': dict(parsed_metrics),
        }
        comments_url = resolve_http_url(base_url, _as_optional_str(card.get('comments_url')))
        if comments_url:
            meta['comments_url'] = comments_url
        external_url = resolve_http_url(base_url, _as_optional_str(card.get('external_url')))
        if external_url and external_url != url:
            meta['external_url'] = external_url

        accepted_items.append(Item(
            source_id=source_id,
            kind='browser',
            external_id=url,
            url=url,
            page_url=page_url,
            title=title,
            author=author,
            summary=summary,
            content=content,
            published=published,
            meta=meta,
        ))
    return new_on_page


def _as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


async def scan_browser_list_page(
    page: Page,
    *,
    source_id: str,
    page_url: str,
    selectors: BrowserListSelectors,
    scroll: BrowserScrollOptions,
    prefilter: BrowserPrefilterOptions | None,
    max_items: int,
    timeout: float,
    ctx: FetchContext,
    initial_settle_ms: int = _INITIAL_SETTLE_MS,
) -> BrowserListScanResult:
    """Drive `page` through navigation, initial selector wait, and virtual-DOM scroll rounds."""
    started = monotonic()
    deadline = started + timeout

    def step_timeout_ms() -> int:
        remaining = deadline - monotonic()
        return max(1000, int(min(timeout, max(1.0, remaining)) * 1000))

    await page.bring_to_front()
    try:
        await page.goto(page_url, wait_until='domcontentloaded', timeout=step_timeout_ms())
        for click_step in selectors.before_scan_clicks:
            await page.wait_for_selector(click_step.selector, timeout=step_timeout_ms())
            locator = page.locator(click_step.selector)
            if click_step.has_text is not None:
                candidates = (
                    [click_step.has_text]
                    if isinstance(click_step.has_text, str)
                    else click_step.has_text
                )
                matched_locator = None
                for text_option in candidates:
                    candidate = locator.filter(has_text=text_option)
                    if await candidate.count() > 0:
                        matched_locator = candidate.first
                        break
                if matched_locator is None:
                    raise PlaywrightTimeoutError(
                        f'before_scan_clicks selector {click_step.selector!r} did not match text {candidates!r}'
                    )
                target = matched_locator
            else:
                target = locator.first
            if (await target.get_attribute('aria-selected')) != 'true':
                await target.click(timeout=step_timeout_ms())
                if click_step.wait_ms > 0:
                    await page.wait_for_timeout(min(click_step.wait_ms, step_timeout_ms()))
        await page.wait_for_selector(selectors.item, timeout=step_timeout_ms())
    except (PlaywrightTimeoutError, TimeoutError) as exc:
        snap = await page_snapshot(page)
        _record_list_error(
            ctx,
            source_id=source_id,
            page_url=page_url,
            phase='browser_replay',
            error_code='playwright_selector_timeout',
            message=f'browser list timed out waiting for {selectors.item!r} ({type(exc).__name__})',
            snapshot=snap,
            exc=exc,
        )
        raise DiagnosedError(
            f'browser list timed out waiting for {selectors.item!r} ({type(exc).__name__})'
        ) from None

    if initial_settle_ms > 0 and deadline - monotonic() > _TIMEOUT_SAFETY_S:
        await page.wait_for_timeout(min(initial_settle_ms, max(0, int((deadline - monotonic()) * 1000))))

    payload = build_extractor_payload(selectors)
    seen_keys: set[str] = set()
    seen_ads: set[str] = set()
    accepted_items: list[Item] = []
    counters: dict[str, int] = {
        'dom_scanned': 0,
        'skipped_ads': 0,
        'filtered_out': 0,
        'matched': 0,
        'mounted_non_ad_seen': 0,
        'missing_link': 0,
    }
    scroll_rounds = 0
    idle_streak = 0
    stalled_bottom_streak = 0
    prev_scroll_y = -1
    stop_reason: Literal['max_scrolls', 'no_new_items', 'max_items', 'timeout_budget'] = 'max_scrolls'

    for round_idx in range(scroll.max_scrolls + 1):
        raw_batch = await page.evaluate(_JS_EXTRACT_CARDS, payload)
        new_on_page = accumulate_round_cards(
            raw_batch,
            source_id=source_id,
            page_url=page_url,
            selectors=selectors,
            prefilter=prefilter,
            max_items=max_items,
            ctx=ctx,
            seen_keys=seen_keys,
            seen_ads=seen_ads,
            accepted_items=accepted_items,
            counters=counters,
        )
        pos = await page.evaluate(
            "({y: Math.floor(window.scrollY), h: Math.floor(window.innerHeight), sh: Math.floor(document.documentElement.scrollHeight)})"
        )
        cur_y = int(pos.get('y', 0)) if isinstance(pos, Mapping) else 0
        at_bottom = (
            bool(cur_y + int(pos.get('h', 0)) >= int(pos.get('sh', 0)) - 200)
            if isinstance(pos, Mapping)
            else True
        )

        if round_idx > 0:
            if new_on_page > 0:
                idle_streak = 0
                stalled_bottom_streak = 0
            elif at_bottom or cur_y <= prev_scroll_y:
                idle_streak += 1
            else:
                # Still scrolling down through already-SSR'd cards toward the lazy-load trigger.
                stalled_bottom_streak += 1
                if stalled_bottom_streak >= 12:
                    idle_streak += 1
            if idle_streak >= scroll.idle_rounds:
                stop_reason = 'no_new_items'
                break
        prev_scroll_y = cur_y

        if len(accepted_items) >= max_items:
            stop_reason = 'max_items'
            break

        if round_idx >= scroll.max_scrolls:
            stop_reason = 'max_scrolls'
            break

        wait_ms = random.randint(scroll.wait_min_ms, scroll.wait_max_ms)
        if deadline - monotonic() <= _TIMEOUT_SAFETY_S + (wait_ms / 1000.0):
            stop_reason = 'timeout_budget'
            break

        await page.evaluate(_JS_SCROLL_DOWN, selectors.item)
        scroll_rounds += 1
        await page.wait_for_timeout(wait_ms)

    snap = await page_snapshot(page)
    if counters['dom_scanned'] == 0 and counters['skipped_ads'] == 0:
        _record_list_error(
            ctx,
            source_id=source_id,
            page_url=page_url,
            phase='parse_entry',
            error_code='selector_no_match',
            message='browser list selectors extracted zero items with usable links',
            snapshot={
                **snap,
                'configured_selectors': selectors.model_dump(exclude_none=True),
                'scanned_cards': counters['mounted_non_ad_seen'],
                'missing_link': counters['missing_link'],
            },
        )
        raise DiagnosedError('browser list selectors extracted zero items with usable links')

    stats: dict[str, Any] = {
        'browser_mode': 'list',
        'scroll_rounds': scroll_rounds,
        'dom_scanned': counters['dom_scanned'],
        'skipped_ads': counters['skipped_ads'],
        'filtered_out': counters['filtered_out'],
        'matched': counters['matched'],
        'stop_reason': stop_reason,
    }
    return BrowserListScanResult(items=accepted_items[:max_items], stats=stats, snapshot=snap)


async def run_browser_list(
    config: AppConfig,
    *,
    source_id: str,
    page_url: str,
    chrome_profile: str | None,
    selectors: BrowserListSelectors,
    scroll: BrowserScrollOptions,
    prefilter: BrowserPrefilterOptions | None,
    max_items: int,
    timeout: float,
    ctx: FetchContext,
) -> BrowserListScanResult:
    """Open one isolated work tab in headed Chrome and run `scan_browser_list_page`."""
    async with work_tab(config, chrome_profile=chrome_profile) as page:
        return await scan_browser_list_page(
            page,
            source_id=source_id,
            page_url=page_url,
            selectors=selectors,
            scroll=scroll,
            prefilter=prefilter,
            max_items=max_items,
            timeout=timeout,
            ctx=ctx,
        )


def _record_list_error(
    ctx: FetchContext,
    *,
    source_id: str,
    page_url: str,
    phase: str,
    error_code: str,
    message: str,
    snapshot: dict[str, Any],
    exc: BaseException | None = None,
) -> None:
    if ctx.record_error is None:
        return
    current = snapshot.get('current_url')
    target_url = current if isinstance(current, str) and current.startswith(('http://', 'https://')) else page_url
    context: dict[str, Any] = {'page_url': page_url, 'browser_mode': 'list', **snapshot}
    if exc is not None:
        context['traceback'] = stack_frames(exc)
    ctx.record_error(DiagnosticEvent(
        phase=phase,
        error_code=error_code,
        message=message,
        severity='error',
        source_id=source_id,
        page_url=page_url,
        target_url=target_url,
        exception_type=type(exc).__name__ if exc is not None else None,
        retryable=False,
        debug_context=context,
    ))
