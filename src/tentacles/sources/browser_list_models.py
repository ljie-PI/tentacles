"""Configuration schemas, built-in site presets, and metric/text normalizers for `type: browser` + `mode: list`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tentacles.sources.selectors import MultiCssSelector, validate_node_selector
from tentacles.text import truncate

METRIC_KEYS: tuple[str, ...] = ('votes', 'likes', 'comments', 'reposts', 'views')
BROWSER_SCRIPT_ONLY_FIELDS: tuple[str, ...] = ('script_path', 'pi', 'on_detail_error', 'task')
BROWSER_LIST_ONLY_FIELDS: tuple[str, ...] = ('preset', 'selectors', 'scroll', 'prefilter', 'actions', 'format')

BrowserListPresetName = Literal['reddit', 'x-following']

_DERIVED_TITLE_LIMIT = 140

# ASCII letters/digits, `@`, `#`, `/` before a digit mean an identifier/URL/handle (e.g. `GPT4`, `@user1`),
# whereas CJK characters before a digit (`赞1.2万`, `阅读10万`) are natural-language labels!
_METRIC_NUMBER_RE = re.compile(
    r'(?<![A-Za-z0-9_@#/\.])'
    r'([+-]?)\s*'
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


def _validate_selector(value: str | None) -> str | None:
    if value is not None:
        MultiCssSelector.parse(value)
    return value


class BrowserClickStep(BaseModel):
    """One pre-scan click step (e.g. switching from X 'For you' to 'Following')."""

    model_config = ConfigDict(extra='forbid')

    selector: str
    has_text: str | list[str] | None = None
    nth: int | None = Field(default=None, ge=0, le=50)
    wait_ms: int = Field(default=1500, ge=0, le=30000)

    @field_validator('selector')
    @classmethod
    def _selector(cls, value: str) -> str:
        return validate_node_selector(value)

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
    wait_min_ms: int = Field(default=2000, ge=100, le=30000)
    wait_max_ms: int = Field(default=10000, ge=100, le=60000)
    idle_rounds: int = Field(default=2, ge=1, le=20)

    @model_validator(mode='after')
    def _wait_bounds(self) -> BrowserScrollOptions:
        if self.wait_max_ms < self.wait_min_ms:
            raise ValueError('wait_max_ms must be greater than or equal to wait_min_ms')
        return self


class BrowserItemFormat(BaseModel):
    """Outward presentation options stored in `Item.meta['display_format']`."""

    model_config = ConfigDict(extra='forbid')

    prefix_author_in_title: bool = False
    prefix_metrics_in_title: bool = False
    prepend_metadata_in_content: bool = False

    def meta_dict(self) -> dict[str, bool] | None:
        if not (self.prefix_author_in_title or self.prefix_metrics_in_title or self.prepend_metadata_in_content):
            return None
        return {
            'prefix_author_in_title': self.prefix_author_in_title,
            'prefix_metrics_in_title': self.prefix_metrics_in_title,
            'prepend_metadata_in_content': self.prepend_metadata_in_content,
        }


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
    """Pure DOM/attribute selectors for `type: browser` + `mode: list`."""

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
    media: list[str] = Field(default_factory=list)
    skip_if: list[str] = Field(default_factory=list)
    require: list[str] = Field(default_factory=list)
    metrics: BrowserMetricsSelectors = Field(default_factory=BrowserMetricsSelectors)

    @field_validator('item')
    @classmethod
    def _item_nodes(cls, value: str) -> str:
        return validate_node_selector(value)

    @field_validator('link', 'title', 'content', 'summary', 'author', 'date', 'comments_url', 'external_url')
    @classmethod
    def _field_selectors(cls, value: str | None) -> str | None:
        return _validate_selector(value)

    @field_validator('skip_if', 'require', 'media')
    @classmethod
    def _filter_selectors(cls, values: list[str]) -> list[str]:
        return [validate_node_selector(value) for value in values]

    @model_validator(mode='after')
    def _require_text_field(self) -> BrowserListSelectors:
        if not any((self.title, self.content, self.summary)):
            raise ValueError('at least one of title, content or summary selector is required')
        return self


class BrowserSelectorPatch(BaseModel):
    """Typed partial/full selector map for REST `SourceCreate` / `SourceUpdate` / `SourceConfigView`."""

    model_config = ConfigDict(extra='forbid')

    item: str | None = None
    link: str | None = None
    title: str | None = None
    content: str | None = None
    summary: str | None = None
    author: str | None = None
    date: str | None = None
    comments_url: str | None = None
    external_url: str | None = None
    media: list[str] | None = None
    skip_if: list[str] | None = None
    require: list[str] | None = None
    metrics: BrowserMetricsSelectors | None = None


class BrowserScrollPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')

    max_scrolls: int | None = Field(default=None, ge=0, le=200)
    wait_min_ms: int | None = Field(default=None, ge=100, le=30000)
    wait_max_ms: int | None = Field(default=None, ge=100, le=60000)
    idle_rounds: int | None = Field(default=None, ge=1, le=20)


class BrowserItemFormatPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')

    prefix_author_in_title: bool | None = None
    prefix_metrics_in_title: bool | None = None
    prepend_metadata_in_content: bool | None = None


class BrowserPrefilterOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')

    match: Literal['any', 'all'] = 'any'
    min_votes: int | None = Field(default=None, ge=0)
    min_likes: int | None = Field(default=None, ge=0)
    min_comments: int | None = Field(default=None, ge=0)
    min_reposts: int | None = Field(default=None, ge=0)
    min_views: int | None = Field(default=None, ge=0)
    min_text_chars: int = Field(default=0, ge=0, le=10000)
    min_media_text_chars: int | None = Field(default=None, ge=0, le=10000)

    def passes(self, metrics: dict[str, int], *, text_chars: int, has_media: bool = False) -> bool:
        min_chars = (
            self.min_media_text_chars
            if has_media and self.min_media_text_chars is not None
            else self.min_text_chars
        )
        if min_chars > 0 and text_chars < min_chars:
            return False
        thresholds: list[tuple[str, int]] = []
        for key in METRIC_KEYS:
            threshold = getattr(self, f'min_{key}')
            if threshold is not None:
                thresholds.append((key, threshold))
        if not thresholds:
            return True
        checks = [metrics.get(key, 0) >= threshold for key, threshold in thresholds]
        return any(checks) if self.match == 'any' else all(checks)


@dataclass(frozen=True)
class BrowserListPreset:
    selectors: dict[str, Any]
    actions: list[dict[str, Any]]
    format: dict[str, Any]


BROWSER_LIST_PRESETS: dict[BrowserListPresetName, BrowserListPreset] = {
    'reddit': BrowserListPreset(
        selectors={
            'item': 'shreddit-post',
            'skip_if': ['[promoted]'],
            'link': '@permalink',
            'title': '@post-title',
            'content': '[slot="text-body"]',
            'author': '@author',
            'date': '@created-timestamp',
            'comments_url': '@permalink',
            'external_url': '@content-href',
            'metrics': {
                'votes': '@score',
                'comments': '@comment-count',
            },
        },
        actions=[],
        format={
            'prefix_author_in_title': True,
            'prefix_metrics_in_title': True,
            'prepend_metadata_in_content': True,
        },
    ),
    'x-following': BrowserListPreset(
        selectors={
            'item': 'article[data-testid="tweet"]',
            'skip_if': ['[data-testid="placementTracking"]'],
            'require': ['time'],
            'link': 'a[href*="/status/"]:has(time)@href',
            'content': '[data-testid="tweetText"]',
            'summary': '[data-testid="card.wrapper"]',
            'media': [
                '[data-testid="tweetPhoto"]',
                '[data-testid="videoPlayer"]',
                '[data-testid="card.wrapper"]',
            ],
            'author': '[data-testid="User-Name"]',
            'date': 'time@datetime',
            'metrics': {
                'comments': '[data-testid="reply"]@aria-label',
                'reposts': '[data-testid="retweet"], [data-testid="unretweet"]@aria-label',
                'likes': '[data-testid="like"], [data-testid="unlike"]@aria-label',
                'views': 'a[href*="/analytics"]@aria-label',
            },
        },
        actions=[
            {
                'selector': '[role="tablist"] [role="tab"]',
                'has_text': ['正在关注', 'Following', '跟隨中', 'フォロー中'],
                'nth': 1,
                'wait_ms': 1800,
            },
        ],
        format={
            'prefix_author_in_title': True,
            'prefix_metrics_in_title': True,
            'prepend_metadata_in_content': True,
        },
    ),
}


def merge_preset_selectors_dict(preset_name: BrowserListPresetName, raw_selectors: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge a raw `selectors` mapping over a named preset's default selectors."""
    base = dict(BROWSER_LIST_PRESETS[preset_name].selectors)
    if raw_selectors is None:
        return base
    override = dict(raw_selectors)
    if isinstance(override.get('metrics'), Mapping) and isinstance(base.get('metrics'), Mapping):
        override['metrics'] = {**base['metrics'], **override['metrics']}
    return {**base, **override}


def resolve_list_preset(
    preset_name: BrowserListPresetName | None,
    *,
    selectors: BrowserListSelectors | None,
    actions: list[BrowserClickStep] | None,
    item_format: BrowserItemFormat | None,
) -> tuple[BrowserListSelectors | None, list[BrowserClickStep], BrowserItemFormat]:
    """Resolve effective `(selectors, actions, format)` from optional `preset` + explicit overrides."""
    if preset_name is None:
        return selectors, list(actions or []), item_format or BrowserItemFormat()
    preset = BROWSER_LIST_PRESETS[preset_name]
    override_dict = selectors.model_dump(exclude_unset=True) if selectors is not None else None
    resolved_selectors = BrowserListSelectors.model_validate(
        merge_preset_selectors_dict(preset_name, override_dict)
    )
    resolved_actions = (
        list(actions)
        if actions is not None
        else [BrowserClickStep.model_validate(step) for step in preset.actions]
    )
    merged_format = dict(preset.format)
    if item_format is not None:
        merged_format.update(item_format.model_dump(exclude_unset=True))
    return resolved_selectors, resolved_actions, BrowserItemFormat.model_validate(merged_format)


def parse_metric_count(raw: str | None) -> int:
    """Parse localized or compact engagement counts into a non-negative integer (negatives clamp to 0)."""
    if not raw:
        return 0
    cleaned = raw.replace('\xa0', ' ').replace('\u202f', ' ').strip()
    if not cleaned:
        return 0
    match = _METRIC_NUMBER_RE.search(cleaned)
    if match is None:
        return 0
    sign_raw, whole_raw, frac_raw, unit_raw = match.groups()
    if sign_raw == '-':
        return 0
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


def derive_semantic_title(
    explicit_title: str | None,
    content: str,
    summary: str,
    *,
    url: str,
) -> str:
    """Return the clean semantic title (without author or engagement prefixes)."""
    if explicit_title and explicit_title.strip():
        return truncate(re.sub(r'\s+', ' ', explicit_title).strip(), 1000)
    for text in (content, summary):
        normalized = re.sub(r'[ \t\xa0]*\n[ \t\xa0]*([@#][\w]+)[ \t\xa0]*\n[ \t\xa0]*', r' \1 ', text)
        normalized = re.sub(r'[ \t\xa0]*\n[ \t\xa0]*(?=[@#])', ' ', normalized)
        for line in normalized.splitlines():
            cleaned = re.sub(r'\s+', ' ', line).strip()
            if cleaned:
                return truncate(cleaned, _DERIVED_TITLE_LIMIT)
    return truncate(url, _DERIVED_TITLE_LIMIT)
