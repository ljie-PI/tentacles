"""`type: browser` + `mode: list`: Playwright infinite-scroll runtime & card accumulator."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import random
from time import monotonic
from typing import Any, Literal

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig
from tentacles.diagnostics import DiagnosticEvent, stack_frames
from tentacles.models import Item, ItemObservation, LinkDisposition
from tentacles.sources.base import DiagnosedError, FetchContext
from tentacles.sources.browser_list_models import (
    BROWSER_LIST_ONLY_FIELDS,
    BROWSER_LIST_PRESETS,
    BROWSER_SCRIPT_ONLY_FIELDS,
    METRIC_KEYS,
    BrowserClickStep,
    BrowserItemFormat,
    BrowserListPresetName,
    BrowserListSelectors,
    BrowserMetricsSelectors,
    BrowserPrefilterOptions,
    BrowserScrollOptions,
    clean_author,
    derive_semantic_title,
    merge_preset_selectors_dict,
    parse_metric_count,
    resolve_list_preset,
)
from tentacles.sources.browser_session import page_snapshot, work_tab
from tentacles.sources.selectors import MultiCssSelector, parse_flexible_datetime, resolve_http_url
from tentacles.text import truncate

__all__ = [
    'BROWSER_LIST_ONLY_FIELDS',
    'BROWSER_LIST_PRESETS',
    'BROWSER_SCRIPT_ONLY_FIELDS',
    'BrowserClickStep',
    'BrowserItemFormat',
    'BrowserListPresetName',
    'BrowserListScanResult',
    'BrowserListSelectors',
    'BrowserMetricsSelectors',
    'BrowserPrefilterOptions',
    'BrowserScrollOptions',
    'accumulate_round_cards',
    'build_extractor_payload',
    'clean_author',
    'derive_semantic_title',
    'merge_preset_selectors_dict',
    'parse_metric_count',
    'resolve_list_preset',
    'run_browser_list',
    'scan_browser_list_page',
]

_INITIAL_SETTLE_MS = 1200
_TIMEOUT_SAFETY_S = 3.0

_JS_EXTRACT_CARDS = """
(spec) => {
  const cleanText = (node) => {
    const raw = (typeof node.innerText === 'string' && node.innerText) ? node.innerText : (node.textContent || '');
    return raw.replace(/\\r\\n?/g, '\\n').trim();
  };

  const queryBranch = (root, branch, joinAll = false) => {
    const attr = branch.attribute || branch.default_attribute;
    if (branch.css === ':scope') {
      if (attr) {
        if (!root.hasAttribute(attr)) return null;
        const val = root.getAttribute(attr);
        return val === null ? null : String(val).trim();
      }
      return cleanText(root);
    }
    const nodes = Array.from(root.querySelectorAll(branch.css));
    if (!nodes.length) return null;
    if (attr) {
      let emptyFallback = null;
      for (const node of nodes) {
        if (node.hasAttribute(attr)) {
          const val = node.getAttribute(attr);
          const trimmed = val === null ? null : String(val).trim();
          if (trimmed) return trimmed;
          if (emptyFallback === null) emptyFallback = '';
        }
      }
      return emptyFallback;
    }
    if (joinAll) {
      const texts = nodes.map(cleanText).filter(Boolean);
      return texts.length ? texts.join('\\n\\n> ') : '';
    }
    for (const node of nodes) {
      const text = cleanText(node);
      if (text) return text;
    }
    return '';
  };

  const queryBranches = (root, branches, joinAll = false) => {
    if (!branches || !branches.length) return null;
    let emptyFallback = null;
    for (const branch of branches) {
      const result = queryBranch(root, branch, joinAll);
      if (result !== null && result !== '') return result;
      if (result === '' && emptyFallback === null) emptyFallback = '';
    }
    return emptyFallback;
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
      const adLink = queryBranches(el, spec.link);
      cards.push({
        skipped_ad: true,
        ad_key: adLink || rawText.replace(/\\s+/g, ' ').trim().slice(0, 240) || el.outerHTML.slice(0, 240),
      });
      continue;
    }
    const metrics = {};
    for (const [key, branches] of Object.entries(spec.metrics)) {
      metrics[key] = queryBranches(el, branches);
    }
    let hasMedia = false;
    for (const mediaCss of spec.media) {
      if (matchesRule(el, mediaCss)) {
        hasMedia = true;
        break;
      }
    }
    cards.push({
      skipped_ad: false,
      link: queryBranches(el, spec.link),
      title: queryBranches(el, spec.title),
      content: queryBranches(el, spec.content, true),
      summary: queryBranches(el, spec.summary, true),
      author: queryBranches(el, spec.author),
      date: queryBranches(el, spec.date),
      comments_url: queryBranches(el, spec.comments_url),
      external_url: queryBranches(el, spec.external_url),
      has_media: hasMedia,
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


def build_extractor_payload(selectors: BrowserListSelectors) -> dict[str, Any]:
    def optional(raw: str | None, *, default_attribute: str | None = None) -> list[dict[str, str | None]] | None:
        return MultiCssSelector.parse(raw).payload(default_attribute=default_attribute) if raw is not None else None

    metrics_payload: dict[str, list[dict[str, str | None]]] = {}
    for key in METRIC_KEYS:
        sel = getattr(selectors.metrics, key)
        if sel is not None:
            metrics_payload[key] = MultiCssSelector.parse(sel).payload()

    return {
        'item': selectors.item,
        'link': MultiCssSelector.parse(selectors.link).payload(default_attribute='href'),
        'title': optional(selectors.title),
        'content': optional(selectors.content),
        'summary': optional(selectors.summary),
        'author': optional(selectors.author),
        'date': optional(selectors.date),
        'comments_url': optional(selectors.comments_url, default_attribute='href'),
        'external_url': optional(selectors.external_url, default_attribute='href'),
        'media': list(selectors.media),
        'skip_if': list(selectors.skip_if),
        'require': list(selectors.require),
        'metrics': metrics_payload,
    }


@dataclass
class BrowserListScanResult:
    items: list[Item]
    observations: list[ItemObservation]
    stats: dict[str, Any]
    snapshot: dict[str, Any]


def _as_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _build_list_observation(
    item: Item,
    *,
    selectors: BrowserListSelectors,
    observed_title: str | None,
    observed_content: str | None,
    observed_summary: str | None,
    observed_author: str | None,
    observed_date: str | None,
) -> ItemObservation:
    """Construct `ItemObservation` preserving selector & DOM presence (`processing-seams.md`)."""
    fields: dict[str, Any] = {
        'stable_external_id': True,
        'url': item.url,
        'page_url': item.page_url,
        'meta': item.meta,
    }
    if selectors.title is not None:
        if observed_title is not None:
            fields['title'] = item.title
    elif observed_content is not None or observed_summary is not None:
        fields['title'] = item.title
    if selectors.content is not None and observed_content is not None:
        fields['content'] = item.content
    if (selectors.summary is not None and observed_summary is not None) or (
        selectors.content is not None and observed_content is not None
    ):
        fields['summary'] = item.summary
    if selectors.author is not None and observed_author is not None:
        fields['author'] = item.author or None
    if selectors.date is not None and observed_date is not None and (item.published is not None or not observed_date.strip()):
        fields['published'] = item.published
    return ItemObservation(**fields)


async def accumulate_round_cards(
    raw_batch: Mapping[str, Any],
    *,
    source_id: str,
    page_url: str,
    selectors: BrowserListSelectors,
    item_format: BrowserItemFormat,
    prefilter: BrowserPrefilterOptions | None,
    max_items: int,
    update_existing: bool,
    ctx: FetchContext,
    seen_keys: set[str],
    seen_ads: set[str],
    accepted_items: list[Item],
    accepted_observations: list[ItemObservation],
    counters: dict[str, int],
) -> int:
    """Process one round's evaluated cards, skipping already-stored links before `max_items` cap."""
    base_url = str(raw_batch.get('base_url') or page_url)
    cards = raw_batch.get('cards')
    if not isinstance(cards, list):
        return 0
    configured_metric_keys = [
        key for key in METRIC_KEYS if getattr(selectors.metrics, key) is not None
    ]
    display_fmt = item_format.meta_dict()

    pending_cards: list[tuple[str, Mapping[str, Any]]] = []
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
        pending_cards.append((url, card))

    if not pending_cards:
        return new_on_page

    # Classify candidate URLs against Store now so known/skipped items do NOT consume `max_items`!
    candidate_urls = [url for url, _ in pending_cards]
    link_dispositions: dict[str, LinkDisposition] = {}
    if update_existing and ctx.classify_links is not None:
        link_dispositions = await run_store_call(ctx.classify_links, candidate_urls)
    elif ctx.seen_links is not None:
        known = await run_store_call(ctx.seen_links, candidate_urls)
        link_dispositions = {url: 'skip' for url in known}

    for url, card in pending_cards:
        observed_title = _as_optional_str(card.get('title'))
        observed_content = _as_optional_str(card.get('content'))
        observed_summary = _as_optional_str(card.get('summary'))
        observed_author = _as_optional_str(card.get('author'))
        observed_date = _as_optional_str(card.get('date'))

        raw_content = (observed_content or '').strip()
        raw_summary = (observed_summary or '').strip()
        author = clean_author(observed_author)

        raw_metrics = card.get('metrics')
        parsed_metrics: dict[str, int] = {}
        if isinstance(raw_metrics, Mapping):
            for key in configured_metric_keys:
                parsed_metrics[key] = parse_metric_count(_as_optional_str(raw_metrics.get(key)))

        has_media = bool(card.get('has_media'))
        combined_texts = [t for t in (raw_content, raw_summary, (observed_title or '').strip()) if t]
        body_for_length = '\n'.join(combined_texts)
        if prefilter is not None and not prefilter.passes(
            parsed_metrics,
            text_chars=len(body_for_length),
            has_media=has_media,
        ):
            counters['filtered_out'] += 1
            continue

        counters['matched'] += 1
        disposition = link_dispositions.get(url, 'new')
        if disposition == 'skip':
            counters['skipped_known'] += 1
            continue
        if len(accepted_items) >= max_items:
            continue
        if disposition == 'refresh':
            counters['refresh_links'] += 1

        title = derive_semantic_title(observed_title, raw_content, raw_summary, url=url)
        published = parse_flexible_datetime(observed_date, ctx)
        if raw_content and raw_summary and raw_summary not in raw_content:
            merged_body = f'{raw_content}\n\n[Card] {raw_summary}'
        else:
            merged_body = raw_content or truncate(raw_summary, 2000) or title
        content = merged_body
        summary = truncate(merged_body, 2000)

        meta: dict[str, Any] = {
            'browser_mode': 'list',
            'browser_entry_url': page_url,
            'metrics': dict(parsed_metrics),
        }
        if display_fmt is not None:
            meta['display_format'] = dict(display_fmt)
        if has_media:
            meta['has_media'] = True
        comments_url = resolve_http_url(base_url, _as_optional_str(card.get('comments_url')))
        if comments_url:
            meta['comments_url'] = comments_url
        external_url = resolve_http_url(base_url, _as_optional_str(card.get('external_url')))
        if external_url and external_url != url:
            meta['external_url'] = external_url

        item = Item(
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
        )
        accepted_items.append(item)
        if update_existing:
            accepted_observations.append(_build_list_observation(
                item,
                selectors=selectors,
                observed_title=observed_title,
                observed_content=observed_content,
                observed_summary=observed_summary,
                observed_author=observed_author,
                observed_date=observed_date,
            ))
    return new_on_page


async def _run_pre_scan_actions(
    page: Page,
    actions: list[BrowserClickStep],
    *,
    deadline: float,
) -> None:
    for click_step in actions:
        remaining_ms = int((deadline - monotonic()) * 1000)
        if remaining_ms <= 0:
            raise PlaywrightTimeoutError('timeout budget exhausted before pre-scan click step')
        await page.wait_for_selector(click_step.selector, timeout=remaining_ms)
        locator = page.locator(click_step.selector)
        target = None
        if click_step.has_text is not None:
            candidates = (
                [click_step.has_text]
                if isinstance(click_step.has_text, str)
                else click_step.has_text
            )
            for text_option in candidates:
                candidate = locator.filter(has_text=text_option)
                if await candidate.count() > 0:
                    target = candidate.first
                    break
        if target is None and click_step.nth is not None:
            if await locator.count() > click_step.nth:
                target = locator.nth(click_step.nth)
        if target is None and click_step.has_text is None and click_step.nth is None:
            target = locator.first
        if target is None:
            raise PlaywrightTimeoutError(
                f'actions selector {click_step.selector!r} did not match any target'
            )
        selected = await target.get_attribute('aria-selected')
        pressed = await target.get_attribute('aria-pressed')
        if selected != 'true' and pressed != 'true':
            remaining_ms = max(200, int((deadline - monotonic()) * 1000))
            await target.click(timeout=remaining_ms)
            if click_step.wait_ms > 0:
                rem = int((deadline - monotonic()) * 1000)
                if rem > 0:
                    await page.wait_for_timeout(min(click_step.wait_ms, rem))


async def scan_browser_list_page(
    page: Page,
    *,
    source_id: str,
    page_url: str,
    selectors: BrowserListSelectors,
    actions: list[BrowserClickStep] | None = None,
    item_format: BrowserItemFormat | None = None,
    scroll: BrowserScrollOptions | None = None,
    prefilter: BrowserPrefilterOptions | None = None,
    max_items: int,
    update_existing: bool = False,
    timeout: float,
    ctx: FetchContext,
    record_error: Callable[..., None] | None = None,
    initial_settle_ms: int = _INITIAL_SETTLE_MS,
) -> BrowserListScanResult:
    """Drive `page` through navigation, pre-scan actions, selector wait, and scroll rounds."""
    resolved_scroll = scroll or BrowserScrollOptions()
    resolved_format = item_format or BrowserItemFormat()
    resolved_actions = actions or []
    started = monotonic()
    deadline = started + timeout

    def remaining_timeout_ms() -> int:
        rem = int((deadline - monotonic()) * 1000)
        if rem <= 0:
            raise PlaywrightTimeoutError('browser list timeout budget exhausted')
        return rem

    def emit_error(*, phase: str, error_code: str, message: str, snapshot: dict[str, Any],
                   exc: BaseException | None = None) -> None:
        if record_error is not None:
            record_error(phase=phase, error_code=error_code, message=message, snapshot=snapshot, exc=exc)
        else:
            _default_record_error(ctx, source_id=source_id, page_url=page_url, phase=phase,
                                  error_code=error_code, message=message, snapshot=snapshot, exc=exc)

    await page.bring_to_front()
    try:
        await page.goto(page_url, wait_until='domcontentloaded', timeout=remaining_timeout_ms())
        if resolved_actions:
            await _run_pre_scan_actions(page, resolved_actions, deadline=deadline)
        await page.wait_for_selector(selectors.item, timeout=remaining_timeout_ms())
    except (PlaywrightTimeoutError, TimeoutError, asyncio.TimeoutError) as exc:
        snap = await page_snapshot(page)
        emit_error(
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
    accepted_observations: list[ItemObservation] = []
    counters: dict[str, int] = {
        'dom_scanned': 0,
        'skipped_ads': 0,
        'filtered_out': 0,
        'matched': 0,
        'mounted_non_ad_seen': 0,
        'missing_link': 0,
        'skipped_known': 0,
        'refresh_links': 0,
    }
    scroll_rounds = 0
    idle_streak = 0
    stop_reason: Literal['max_scrolls', 'no_new_items', 'max_items', 'timeout_budget'] = 'max_scrolls'

    for round_idx in range(resolved_scroll.max_scrolls + 1):
        raw_batch = await page.evaluate(_JS_EXTRACT_CARDS, payload)
        new_on_page = await accumulate_round_cards(
            raw_batch,
            source_id=source_id,
            page_url=page_url,
            selectors=selectors,
            item_format=resolved_format,
            prefilter=prefilter,
            max_items=max_items,
            update_existing=update_existing,
            ctx=ctx,
            seen_keys=seen_keys,
            seen_ads=seen_ads,
            accepted_items=accepted_items,
            accepted_observations=accepted_observations,
            counters=counters,
        )

        if round_idx > 0:
            if new_on_page > 0:
                idle_streak = 0
            else:
                idle_streak += 1
            if idle_streak >= resolved_scroll.idle_rounds:
                stop_reason = 'no_new_items'
                break

        if len(accepted_items) >= max_items:
            stop_reason = 'max_items'
            break

        if round_idx >= resolved_scroll.max_scrolls:
            stop_reason = 'max_scrolls'
            break

        wait_ms = random.randint(resolved_scroll.wait_min_ms, resolved_scroll.wait_max_ms)
        if deadline - monotonic() <= _TIMEOUT_SAFETY_S + (wait_ms / 1000.0):
            stop_reason = 'timeout_budget'
            break

        await page.evaluate(_JS_SCROLL_DOWN, selectors.item)
        scroll_rounds += 1
        await page.wait_for_timeout(wait_ms)

    snap = await page_snapshot(page)
    # Fail with `selector_no_match` when non-ad cards were mounted on screen yet zero yielded usable links
    # (even if some ads were skipped!), OR when nothing was extracted and no ads existed either.
    if counters['dom_scanned'] == 0 and (counters['mounted_non_ad_seen'] > 0 or counters['skipped_ads'] == 0):
        emit_error(
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
        'skipped_known': counters['skipped_known'],
        'stop_reason': stop_reason,
    }
    if update_existing:
        stats['refresh_links'] = counters['refresh_links']
    return BrowserListScanResult(
        items=accepted_items[:max_items],
        observations=accepted_observations[:max_items],
        stats=stats,
        snapshot=snap,
    )


async def run_browser_list(
    config: AppConfig,
    *,
    source_id: str,
    page_url: str,
    chrome_profile: str | None,
    selectors: BrowserListSelectors,
    actions: list[BrowserClickStep] | None = None,
    item_format: BrowserItemFormat | None = None,
    scroll: BrowserScrollOptions | None = None,
    prefilter: BrowserPrefilterOptions | None = None,
    max_items: int,
    update_existing: bool = False,
    timeout: float,
    ctx: FetchContext,
    record_error: Callable[..., None] | None = None,
) -> BrowserListScanResult:
    """Open one isolated work tab in headed Chrome and run `scan_browser_list_page` under `timeout`."""
    def emit_error(*, phase: str, error_code: str, message: str, snapshot: dict[str, Any],
                   exc: BaseException | None = None) -> None:
        if record_error is not None:
            record_error(phase=phase, error_code=error_code, message=message, snapshot=snapshot, exc=exc)
        else:
            _default_record_error(ctx, source_id=source_id, page_url=page_url, phase=phase,
                                  error_code=error_code, message=message, snapshot=snapshot, exc=exc)

    async with work_tab(config, chrome_profile=chrome_profile) as page:
        try:
            return await asyncio.wait_for(
                scan_browser_list_page(
                    page,
                    source_id=source_id,
                    page_url=page_url,
                    selectors=selectors,
                    actions=actions,
                    item_format=item_format,
                    scroll=scroll,
                    prefilter=prefilter,
                    max_items=max_items,
                    update_existing=update_existing,
                    timeout=timeout,
                    ctx=ctx,
                    record_error=record_error,
                ),
                timeout=timeout + _TIMEOUT_SAFETY_S,
            )
        except asyncio.TimeoutError as exc:
            snap = await page_snapshot(page)
            emit_error(
                phase='browser_replay',
                error_code='playwright_selector_timeout',
                message=f'browser list exceeded overall timeout {timeout}s',
                snapshot=snap,
                exc=exc,
            )
            raise DiagnosedError(f'browser list exceeded overall timeout {timeout}s') from None


def _default_record_error(
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
