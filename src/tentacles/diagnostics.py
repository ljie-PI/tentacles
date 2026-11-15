"""Structured failure diagnostics for the rollback-safe `run_errors` ledger.

This module never touches SQLite or a business transaction: it owns the shared
error-code vocabulary, the collected event value and the sanitizer that keeps a
bounded, credential-free triage snapshot (`debug_context`). The ledger itself
lives in `store.py`; collection lives in `runs.py`.
"""

from __future__ import annotations

import json
import re
import traceback
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from tentacles.config import AppConfig

REDACTED = '[REDACTED]'
MAX_DEBUG_CONTEXT_BYTES = 16 * 1024

PHASES: tuple[str, ...] = (
    'fetch_page', 'parse_entry', 'fetch_detail', 'imap_fetch', 'browser_replay',
    'browser_heal', 'llm_extract', 'filter_stage', 'report_process', 'script_pr',
)
SEVERITIES: tuple[str, ...] = ('error', 'warning')

# error_code -> the phases that may emit it. This table is the one vocabulary the
# CLI documents and the ledger validates, so a typo cannot reach the database.
# `unexpected_exception` is the only code valid in every phase: it is what a
# top-level failure that nothing else classified is recorded as.
ERROR_PHASES: dict[str, tuple[str, ...]] = {
    'http_status_error': ('fetch_page', 'fetch_detail'),
    'http_redirect_blocked': ('fetch_page', 'fetch_detail'),
    'network_timeout': ('fetch_page', 'fetch_detail'),
    'connection_failed': ('fetch_page', 'fetch_detail'),
    'selector_no_match': ('parse_entry',),
    'empty_extracted_body': ('fetch_detail',),
    'playwright_selector_timeout': ('browser_replay',),
    'script_assertion_failed': ('browser_replay',),
    'pi_heal_exhausted': ('browser_heal',),
    'git_or_pr_failed': ('script_pr',),
    'hallucinated_link_dropped': ('llm_extract',),
    'invalid_json_schema': ('llm_extract',),
    'imap_auth_failed': ('imap_fetch',),
    'imap_message_parse_error': ('imap_fetch',),
    'decision_item_error': ('filter_stage',),
    'llm_batch_parse_error': ('filter_stage',),
    'regex_timeout': ('filter_stage',),
    'filter_stage_aborted': ('filter_stage',),
    'summarize_batch_unparsed': ('report_process',),
    'citation_out_of_range': ('report_process',),
    'feed_parse_error': ('fetch_page',),
    # Appended after the shared vocabulary was published: RSS/Atom entries that
    # carry neither a title nor any identity can never become an Item.
    'entry_unusable': ('parse_entry',),
    'unexpected_exception': PHASES,
}
ERROR_CODES: tuple[str, ...] = tuple(ERROR_PHASES)

_SENSITIVE_NAMES = frozenset({
    'authorization', 'proxy-authorization', 'cookie', 'cookies', 'set-cookie', 'x-api-key', 'api-key',
    'x-auth-token', 'token', 'key', 'sig', 'signature', 'password', 'passwd', 'secret',
    'api_key', 'apikey', 'access_token', 'refresh_token', 'client_secret', 'auth',
    'session', 'sessionid', 'csrf', 'bearer',
})
# Request/response header maps a triage snapshot may carry under these keys.
_HEADER_KEYS = frozenset({'headers', 'request_headers', 'response_headers'})
# HTTP statuses worth retrying; every 5xx is retried too.
_RETRYABLE_STATUS = frozenset({408, 429})
_HTTP_STATUS = re.compile(r'\bHTTP(?:\s*/\s*[\d.]+)?[\s:=]*([1-5]\d\d)\b', re.IGNORECASE)
# Only features an Agent can act on survive a captured response: content type,
# redirect target, backoff hint, edge/CDN markers and rate-limit counters.
_SAFE_HEADERS = frozenset({'content-type', 'location', 'retry-after', 'server', 'cf-mitigated'})
_SAFE_HEADER_PREFIXES = ('x-ratelimit-',)
# The scheme run is length-capped: an unbounded `[a-z0-9+.\-]*` backtracks over every
# position of a long credential-free blob (base64, inline JS) and turns sanitizing
# quadratics. No real URL scheme is longer than this.
_URL_IN_TEXT = re.compile(r"[a-z][a-z0-9+.\-]{1,15}://[^\s'\"<>]+", re.IGNORECASE)
_SCRIPT_BLOCK = re.compile(r'<(script|style)\b[^>]*>.*?</\1\s*>', re.IGNORECASE | re.DOTALL)
_HTML_COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)
_TAG = re.compile(r'<(/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|\'[^\']*\'|[^>])*)>')
_ATTRIBUTE = re.compile(r'([\w:.-]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s"\'>]+)')
# Layout-bearing attributes an Agent needs to retarget a selector; text is dropped.
_LAYOUT_ATTRIBUTES = frozenset({'class', 'id', 'slot', 'href', 'rel', 'role', 'itemprop', 'data-testid'})
_WHITESPACE = re.compile(r'\s+')
_AUTH_SCHEMES = frozenset({'bearer', 'basic', 'digest', 'token'})


class FailedTargetRef(BaseModel):
    """Pointer from an execution result to one persisted ledger row."""

    error_id: int
    target_url: str | None = None
    external_id: str | None = None
    error_code: str


@dataclass(frozen=True)
class DiagnosticEvent:
    """One failure or degradation collected during a Run, before persistence.

    Values are validated against the shared vocabulary here rather than in the
    database, so a misspelled phase or code fails at the recording site.
    """

    phase: str
    error_code: str
    message: str
    severity: str = 'error'
    source_id: str | None = None
    pipeline_id: str | None = None
    page_url: str | None = None
    target_url: str | None = None
    item_id: str | None = None
    external_id: str | None = None
    http_status: int | None = None
    exception_type: str | None = None
    retryable: bool = True
    occurred_at: datetime | None = None
    debug_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.phase not in ERROR_PHASES.get(self.error_code, ()):
            raise ValueError(f'error_code {self.error_code!r} does not apply to phase {self.phase!r}')
        if self.severity not in SEVERITIES:
            raise ValueError(f'unknown severity {self.severity!r}')

    def redacted(self, secrets: Sequence[str] = ()) -> DiagnosticEvent:
        """Strip configured credentials and bound the triage snapshot."""
        return replace(
            self,
            message=redact_text(self.message, secrets),
            page_url=redact_url(self.page_url) if self.page_url else None,
            target_url=redact_url(self.target_url) if self.target_url else None,
            debug_context=bound_debug_context(self.debug_context, secrets),
        )


@dataclass(frozen=True)
class RunErrorRecord:
    """One ledger row without its (potentially 16 KB) debug context."""

    id: int
    run_id: int
    occurred_at: datetime
    phase: str
    severity: str
    source_id: str | None
    pipeline_id: str | None
    page_url: str | None
    target_url: str | None
    item_id: str | None
    external_id: str | None
    error_code: str
    http_status: int | None
    exception_type: str | None
    message: str
    retryable: bool
    resolved_at: datetime | None


@dataclass(frozen=True)
class RunErrorDetail(RunErrorRecord):
    debug_context: dict[str, Any]


def classify_failure(exc: BaseException) -> tuple[str, bool, int | None]:
    """Best-effort `(error_code, retryable, http_status)` for a top-level source failure.

    Source adapters re-raise transport problems as `UpstreamError` with the
    cause suppressed but the readable reason in the message, so each hop is
    asked for an HTTP status first and the chained context is inspected too:
    that is what separates a retryable 5xx/timeout from a deterministic defect.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 4:
        seen.add(id(current))
        status = _exception_status(current)
        if status is not None:
            return 'http_status_error', status_retryable(status), status
        if isinstance(current, (httpx.TimeoutException, TimeoutError)):
            return 'network_timeout', True, None
        if isinstance(current, (httpx.TransportError, ConnectionError, OSError)):
            return 'connection_failed', True, None
        current = current.__cause__ or current.__context__
    return 'unexpected_exception', False, None


def status_retryable(status: int) -> bool:
    """Whether an HTTP status is worth retrying: throttling or a server fault."""
    return status in _RETRYABLE_STATUS or status >= 500


def _exception_status(exc: BaseException) -> int | None:
    """The upstream HTTP status an exception carries in its attributes or text."""
    for holder in (exc, getattr(exc, 'response', None)):
        for attribute in ('status_code', 'status'):
            value = getattr(holder, attribute, None)
            if isinstance(value, int) and 100 <= value <= 599:
                return value
    match = _HTTP_STATUS.search(str(exc))
    return int(match.group(1)) if match else None


def configured_secrets(config: AppConfig) -> list[str]:
    """Every credential value the sanitizer must strip verbatim.

    Covers model API keys, source options such as IMAP passwords and request
    headers, and proxy URLs carrying userinfo or sensitive query parameters.
    """
    secrets: list[str] = []
    for spec in config.models.values():
        key = spec.api_key.get_secret_value()
        if key:
            secrets.append(key)
    for source in config.sources:
        secrets.extend(_option_secrets(source.options))
    return list(dict.fromkeys(secrets))


def _option_secrets(value: Any, name: str = '', sensitive: bool = False) -> list[str]:
    """Every credential value below `value`; a sensitive key claims its whole subtree."""
    found: list[str] = []
    sensitive = sensitive or _sensitive_name(name)
    if isinstance(value, Mapping):
        for key, item in value.items():
            found.extend(_option_secrets(item, str(key), sensitive))
        return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_option_secrets(item, name, sensitive))
        return found
    if not isinstance(value, str) or not value:
        return found
    if sensitive:
        found.append(value)
        found.extend(part for part in value.split() if part.lower() not in _AUTH_SCHEMES)
    if '://' in value:
        found.extend(_url_secrets(value))
    return found


def _url_secrets(url: str) -> list[str]:
    found: list[str] = []
    parsed = urlsplit(url)
    if '@' in parsed.netloc:
        found.extend(part for part in parsed.netloc.rsplit('@', 1)[0].split(':') if part)
    found.extend(value for name, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if value and _sensitive_name(name))
    return found


def redact_url(url: str) -> str:
    """Replace userinfo and sensitive query values, keeping the target readable."""
    parsed = urlsplit(url)
    netloc = parsed.netloc
    if '@' in netloc:
        netloc = f'{REDACTED}@{netloc.rsplit("@", 1)[1]}'
    return urlunsplit((parsed.scheme, netloc, parsed.path, _redact_query(parsed.query), parsed.fragment))


def _redact_query(query: str) -> str:
    if not query:
        return query
    kept = []
    for part in query.split('&'):
        name, separator, _ = part.partition('=')
        kept.append(f'{name}={REDACTED}' if separator and _sensitive_name(unquote(name)) else part)
    return '&'.join(kept)


def _sensitive_name(name: str) -> bool:
    return name.strip().lower() in _SENSITIVE_NAMES


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Replace configured credential values and sensitive query values in text."""
    text = _URL_IN_TEXT.sub(lambda match: redact_url(match.group(0)), text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def redact_headers(headers: Mapping[str, str], secrets: Iterable[str] = ()) -> dict[str, str]:
    """Redact credential-bearing request/response headers."""
    return {name: REDACTED if _sensitive_name(name) else redact_text(str(value), secrets)
            for name, value in headers.items()}


def safe_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Keep only the response features that explain a failed request."""
    kept: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in _SAFE_HEADERS or lowered.startswith(_SAFE_HEADER_PREFIXES):
            kept[lowered] = redact_url(str(value)) if lowered == 'location' else str(value)
    return kept


def html_skeleton(html: str, *, limit: int = 2048) -> str:
    """Bound a page's structure for selector triage, never its whole body."""
    text = _HTML_COMMENT.sub('', _SCRIPT_BLOCK.sub('', html))
    return clip(_WHITESPACE.sub(' ', text).strip(), limit)


def html_structure_snippet(html: str, *, limit: int = 1500) -> str:
    """Tag skeleton of one matched node: names and layout attributes, no text.

    A selector miss is usually a wrapper or class rename, so the copy stays
    comparable to the configured selector while dropping page text that may
    carry private content.
    """
    kept: list[str] = []
    for match in _TAG.finditer(_HTML_COMMENT.sub('', _SCRIPT_BLOCK.sub('', html))):
        closing, name, attributes = match.group(1), match.group(2).lower(), match.group(3)
        if closing:
            kept.append(f'</{name}>')
            continue
        layout = [f'{key}="{value}"' for key, value in _layout_attributes(attributes)]
        kept.append(f'<{name}{" " + " ".join(layout) if layout else ""}>')
        if len(kept) * 40 > limit:
            break
    return clip(''.join(kept), limit)


def _layout_attributes(attributes: str) -> list[tuple[str, str]]:
    """Keep only the attributes that describe layout, not page text."""
    kept: list[tuple[str, str]] = []
    for match in _ATTRIBUTE.finditer(attributes):
        name = match.group(1).lower()
        if name in _LAYOUT_ATTRIBUTES:
            kept.append((name, clip(match.group(2).strip('"\''), 200)))
    return kept


def validation_errors(exc: BaseException) -> list[str]:
    """Where a schema violation sits, without the offending value.

    A pydantic failure is reported as its field path plus error type: its
    message can embed the offending value, which may be model output derived
    from private page or mailbox content. Any other exception is reported as
    its type and its (code-authored) message.
    """
    if not isinstance(exc, ValidationError):
        return [f'{type(exc).__name__}: {exc}']
    return [' '.join(str(part) for part in error['loc']) + f': {error["type"]}' for error in exc.errors()]


def stack_frames(exc: BaseException, *, limit: int = 4096) -> str:
    """Traceback tail without local values; the sanitizer redacts it afterwards."""
    return clip(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip(), limit)


def clip(text: str, limit: int) -> str:
    """Cut text to a character budget, marking the cut."""
    return text if len(text) <= limit else text[:max(0, limit - 1)] + '…'


def redact_debug_context(context: Mapping[str, Any], secrets: Iterable[str] = ()) -> dict[str, Any]:
    """Recursively strip credentials from a triage snapshot."""
    sanitized = _sanitize(context, tuple(secret for secret in secrets if secret))
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        return {str(name): REDACTED if _sensitive_name(str(name))
                else redact_headers(item, secrets) if str(name).lower() in _HEADER_KEYS
                and isinstance(item, Mapping)
                else _sanitize(item, secrets)
                for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value), secrets)


def bound_debug_context(context: Mapping[str, Any], secrets: Iterable[str] = (),
                        *, limit: int = MAX_DEBUG_CONTEXT_BYTES) -> dict[str, Any]:
    """Redact credentials and guarantee the serialized snapshot fits `limit` bytes."""
    sanitized = redact_debug_context(context, secrets)
    if _encoded_size(sanitized) <= limit:
        return sanitized
    # Shrink every string until the whole snapshot fits, marking what was cut.
    cap = max(64, limit // 8)
    while cap >= 64:
        trimmed = {**_truncate_strings(sanitized, cap), 'truncated': True}
        if _encoded_size(trimmed) <= limit:
            return trimmed
        cap //= 2
    preview = _dump(_truncate_strings(sanitized, 32)).encode('utf-8')[:max(0, limit // 2)]
    return {'truncated': True, 'preview': preview.decode('utf-8', 'ignore')}


def _truncate_strings(value: Any, cap: int) -> Any:
    if isinstance(value, Mapping):
        return {name: _truncate_strings(item, cap) for name, item in value.items()}
    if isinstance(value, list):
        return [_truncate_strings(item, cap) for item in value]
    return clip(value, cap) if isinstance(value, str) else value


def _encoded_size(value: Any) -> int:
    return len(_dump(value).encode('utf-8'))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
