"""Atomic YAML persistence and validation for Web/API source mutations."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast
from urllib.parse import unquote, urlsplit, urlunsplit

import yaml

from tentacles.config import AppConfig, SourceSpec, _ENV_REF, interpolate, load_env_values, load_raw_yaml
from tentacles.config_validation import parse_config, validate_source
from tentacles.diagnostics import REDACTED, _sensitive_name
from tentacles.errors import ConfigError, ConfigIssue, ConfigValidationError, Conflict, NotFound
from tentacles.schemas import SourceConfigView, SourceCreate, SourceUpdate

_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    'rss': ('url', 'max_age_days', 'timeout'),
    'imap': ('folders', 'include_subfolders', 'initial_days', 'max_fetch', 'search', 'exclude_senders', 'timeout'),
    'web': ('url', 'mode', 'selectors', 'selector', 'fetch_content', 'on_detail_error',
            'emit_baseline', 'max_input_chars', 'model', 'concurrency', 'timeout'),
    'browser': ('page_url', 'task', 'chrome_profile', 'max_items', 'on_detail_error', 'timeout'),
}

_NULL_IF_BLANK = frozenset({'name', 'selector', 'model', 'chrome_profile', 'task'})
_ENV_REF_FORBIDDEN_MSG = 'environment variable references (${...}) must be edited in tentacles.yaml directly'


@dataclass(frozen=True)
class _YamlPatch:
    updated_source_idx: int | None = None
    appended_source: dict[str, Any] | None = None
    changed_pipelines: Mapping[int, list[str]] = field(default_factory=dict)


def _redact_url_preserving_env(url: str) -> str:
    """Redact literal userinfo and sensitive query values while keeping ${VAR} placeholders."""
    if '://' not in url:
        return url
    parsed = urlsplit(url)
    netloc = parsed.netloc
    if '@' in netloc:
        userinfo, hostpart = netloc.rsplit('@', 1)
        if not _ENV_REF.search(userinfo):
            netloc = f'{REDACTED}@{hostpart}'
    query = parsed.query
    if query:
        kept: list[str] = []
        for part in query.split('&'):
            name, sep, val = part.partition('=')
            if sep and _sensitive_name(unquote(name)) and not _ENV_REF.search(val):
                kept.append(f'{name}={REDACTED}')
            else:
                kept.append(part)
        query = '&'.join(kept)
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def _strings_with_env_refs(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if _ENV_REF.search(value) else set()
    if isinstance(value, Mapping):
        found: set[str] = set()
        for k, v in value.items():
            found.update(_strings_with_env_refs(k))
            found.update(_strings_with_env_refs(v))
        return found
    if isinstance(value, (list, tuple)):
        found = set()
        for item in value:
            found.update(_strings_with_env_refs(item))
        return found
    return set()


def _url_origin(raw_url: Any, env_values: Mapping[str, str]) -> tuple[str, str, int | None] | None:
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    resolved = interpolate(raw_url, env_values, errors=[]) if _ENV_REF.search(raw_url) else raw_url
    try:
        parsed = urlsplit(resolved.strip())
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or '').lower()
    if not scheme or not host:
        return None
    default_port = 443 if scheme == 'https' else 80 if scheme == 'http' else None
    return scheme, host, port if port is not None else default_port


def _matches_source_id(item: Any, source_id: str, env_values: Mapping[str, str]) -> bool:
    if not isinstance(item, dict):
        return False
    raw_id = item.get('id')
    if raw_id == source_id:
        return True
    if isinstance(raw_id, str) and _ENV_REF.search(raw_id):
        return interpolate(raw_id, env_values, errors=[]) == source_id
    return False


def _raw_str(raw: Mapping[str, Any], key: str, fallback: str | None) -> str | None:
    if key in raw:
        val = raw[key]
        return str(val) if val is not None else None
    return fallback


def _safe_view_url(raw: Mapping[str, Any], key: str, fallback: str | None) -> str | None:
    candidate = _raw_str(raw, key, fallback)
    return _redact_url_preserving_env(candidate) if candidate is not None else None


def _raw_str_list(raw: Mapping[str, Any], key: str, fallback: Any) -> list[str] | None:
    candidate = raw[key] if key in raw else fallback
    if isinstance(candidate, list):
        return [str(v) for v in candidate if v is not None]
    return None


def _raw_selectors(raw: Mapping[str, Any], fallback: Any) -> dict[str, str] | None:
    candidate = raw['selectors'] if 'selectors' in raw else fallback
    if isinstance(candidate, dict):
        return {str(k): str(v) for k, v in candidate.items()
                if v is not None and not isinstance(v, (dict, list, tuple))} or None
    return None


def _coerced_options(config: AppConfig, spec: SourceSpec) -> dict[str, Any]:
    raw_opts = spec.options
    try:
        validated = validate_source(spec, 'source', config).model_dump()
    except ConfigValidationError:
        return raw_opts
    return {key: validated.get(key, raw_opts[key]) for key in raw_opts}


def get_source_config(config: AppConfig, source_id: str) -> SourceConfigView:
    spec = config.source(source_id)
    raw_entry: Mapping[str, Any] = {}
    if config.path.is_file():
        _, raw_doc = load_raw_yaml(config.path)
        env_values = load_env_values(config.path)
        for item in raw_doc.get('sources') or []:
            if _matches_source_id(item, source_id, env_values):
                raw_entry = item
                break
    return _view_of(config, spec, raw_entry)


def _view_of(config: AppConfig, spec: SourceSpec, raw_entry: Mapping[str, Any] | None = None) -> SourceConfigView:
    opts = _coerced_options(config, spec)
    source_type = cast(Any, spec.type)
    raw_map = dict(raw_entry) if raw_entry is not None else {}
    view_mode = opts.get('mode')
    if spec.type == 'browser' and view_mode == 'script':
        view_mode = None
    return SourceConfigView(
        id=spec.id,
        type=source_type,
        name=_raw_str(raw_map, 'name', spec.name),
        schedule=_raw_str(raw_map, 'schedule', spec.schedule) or spec.schedule,
        enabled=spec.enabled,
        update_existing=spec.update_existing,
        url=_safe_view_url(raw_map, 'url', opts.get('url')),
        max_age_days=opts.get('max_age_days'),
        timeout=opts.get('timeout'),
        host=_raw_str(raw_map, 'host', opts.get('host')),
        port=opts.get('port'),
        ssl=opts.get('ssl'),
        folders=_raw_str_list(raw_map, 'folders', opts.get('folders')),
        include_subfolders=opts.get('include_subfolders'),
        initial_days=opts.get('initial_days'),
        max_fetch=opts.get('max_fetch'),
        search=_raw_str(raw_map, 'search', opts.get('search')),
        exclude_senders=_raw_str_list(raw_map, 'exclude_senders', opts.get('exclude_senders')),
        mode=view_mode,
        selectors=_raw_selectors(raw_map, opts.get('selectors')),
        selector=_raw_str(raw_map, 'selector', opts.get('selector')),
        fetch_content=opts.get('fetch_content'),
        on_detail_error=opts.get('on_detail_error'),
        emit_baseline=opts.get('emit_baseline'),
        max_input_chars=opts.get('max_input_chars'),
        model=_raw_str(raw_map, 'model', opts.get('model')),
        concurrency=opts.get('concurrency'),
        page_url=_safe_view_url(raw_map, 'page_url', opts.get('page_url')),
        task=_raw_str(raw_map, 'task', opts.get('task')),
        chrome_profile=_raw_str(raw_map, 'chrome_profile', opts.get('chrome_profile')),
        max_items=opts.get('max_items'),
        pipelines=[p.id for p in config.pipelines if spec.id in p.sources],
    )


def _clean_value(key: str, value: Any) -> Any:
    if key in _NULL_IF_BLANK and isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if key == 'selectors' and isinstance(value, dict):
        cleaned = {k: v.strip() for k, v in value.items() if isinstance(v, str) and v.strip()}
        return cleaned or None
    return value


def _runtime_field_value(config: AppConfig | None, spec: SourceSpec | None,
                         coerced_opts: Mapping[str, Any], key: str) -> Any:
    if spec is None or config is None:
        return object()
    if key in SourceSpec.model_fields:
        return getattr(spec, key)
    return coerced_opts.get(key)


def _apply_fields(entry: dict[str, Any], source_type: str, values: dict[str, Any],
                  fields: set[str], *, env_values: Mapping[str, str],
                  config: AppConfig | None = None,
                  current_spec: SourceSpec | None = None) -> None:
    allowed = {'name', 'schedule', 'enabled', 'update_existing', *_TYPE_FIELDS[source_type]}
    issues: list[ConfigIssue] = []
    for extra_key in sorted(fields - allowed - {'id', 'type', 'pipelines'}):
        if values.get(extra_key) is not None:
            issues.append(ConfigIssue(
                f'sources.{extra_key}',
                f'{extra_key} is not supported for source type {source_type!r}',
            ))
    coerced_opts = (_coerced_options(config, current_spec)
                    if config is not None and current_spec is not None else {})
    has_attached_creds = bool(entry.get('headers')) or bool(entry.get('cookies'))
    for key in sorted(fields & allowed):
        val = _clean_value(key, values.get(key))
        existing_raw = entry.get(key)
        unmatched_env_strings = _strings_with_env_refs(val) - _strings_with_env_refs(existing_raw)
        if unmatched_env_strings:
            issues.append(ConfigIssue(f'sources.{key}', _ENV_REF_FORBIDDEN_MSG))
            continue
        if isinstance(existing_raw, str) and _ENV_REF.search(existing_raw):
            if val == existing_raw or val == _runtime_field_value(config, current_spec, coerced_opts, key):
                continue
        if key in ('url', 'page_url') and isinstance(existing_raw, str) and '://' in existing_raw:
            if val == _redact_url_preserving_env(existing_raw):
                continue
        if key == 'url' and has_attached_creds and val is not None:
            if _url_origin(val, env_values) != _url_origin(existing_raw, env_values):
                issues.append(ConfigIssue(
                    'sources.url',
                    'cannot change URL origin via API when custom headers or cookies are configured',
                ))
                continue
        if val is None:
            entry.pop(key, None)
        else:
            entry[key] = val
    if issues:
        raise ConfigValidationError(issues)
    if source_type == 'web':
        mode = entry.get('mode', 'list')
        if mode == 'list':
            for stale in ('selector', 'emit_baseline', 'model', 'max_input_chars'):
                entry.pop(stale, None)
        elif mode == 'page':
            for stale in ('selectors', 'model', 'fetch_content', 'on_detail_error',
                          'concurrency', 'max_input_chars'):
                entry.pop(stale, None)
            if 'update_existing' not in fields or not values.get('update_existing'):
                entry.pop('update_existing', None)
        elif mode == 'llm':
            for stale in ('selectors', 'selector', 'emit_baseline'):
                entry.pop(stale, None)


def _resolve_scalar(value: Any, env_values: Mapping[str, str]) -> Any:
    if isinstance(value, str) and _ENV_REF.search(value):
        return interpolate(value, env_values, errors=[])
    return value


def _sync_pipelines(raw: dict[str, Any], source_id: str, selected: list[str],
                    env_values: Mapping[str, str]) -> dict[int, list[str]]:
    if _strings_with_env_refs(selected):
        raise ConfigValidationError([ConfigIssue('pipelines', _ENV_REF_FORBIDDEN_MSG)])
    pipelines = raw.get('pipelines')
    if pipelines is None:
        if selected:
            raise NotFound(f'unknown pipeline {selected[0]!r}')
        return {}
    if not isinstance(pipelines, list):
        raise ConfigError('pipelines must be a list')
    indexed: dict[str, tuple[int, dict[str, Any]]] = {}
    for idx, p in enumerate(pipelines):
        if isinstance(p, dict) and isinstance(p.get('id'), str):
            resolved_pid = str(_resolve_scalar(p['id'], env_values))
            indexed[resolved_pid] = (idx, p)
    target = set(selected)
    for pid in selected:
        if pid not in indexed:
            raise NotFound(f'unknown pipeline {pid!r}')
    changed: dict[int, list[str]] = {}
    for pid, (idx, p) in indexed.items():
        current = list(p.get('sources') or [])
        resolved_current = [_resolve_scalar(sid, env_values) for sid in current]
        updated = list(current)
        if pid in target and source_id not in resolved_current:
            updated.append(source_id)
        elif pid not in target and source_id in resolved_current:
            updated = [sid for sid, rsid in zip(current, resolved_current) if rsid != source_id]
        if updated != current:
            p['sources'] = updated
            changed[idx] = updated
    return changed


def _content_end(text: str, node: yaml.Node) -> int:
    if getattr(node, 'flow_style', False):
        return node.end_mark.index
    if isinstance(node, yaml.MappingNode) and node.value:
        return _content_end(text, node.value[-1][1])
    if isinstance(node, yaml.SequenceNode) and node.value:
        return _content_end(text, node.value[-1])
    end = node.end_mark.index
    if isinstance(node, yaml.ScalarNode) and node.style in ('|', '>'):
        while end > node.start_mark.index and text[end - 1] in ' \t\r\n':
            end -= 1
    return end


def _line_end(text: str, pos: int) -> int:
    nl = text.find('\n', pos)
    if nl == -1:
        return len(text)
    return nl - 1 if nl > 0 and text[nl - 1] == '\r' else nl


def _dump_flow(value: Any) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False,
                          default_flow_style=True, width=100000).strip()


def _dump_block_at_col(value: Any, col: int) -> str:
    dumped = yaml.safe_dump(value, allow_unicode=True, sort_keys=False,
                            default_flow_style=False, width=120).rstrip('\n')
    lines = dumped.split('\n')
    prefix = ' ' * col
    return '\n'.join([lines[0], *(prefix + line for line in lines[1:])])


def _mapping_lookup(node: yaml.MappingNode, key: str) -> tuple[yaml.Node, yaml.Node] | None:
    for k_node, v_node in node.value:
        if isinstance(k_node, yaml.ScalarNode) and k_node.value == key:
            return k_node, v_node
    return None


def _render_yaml(original_text: str, raw: dict[str, Any], patch: _YamlPatch) -> str:
    if original_text.strip():
        try:
            spliced = _splice_yaml(original_text, raw, patch)
            if spliced is not None and yaml.safe_load(spliced) == raw:
                return spliced
        except yaml.YAMLError:
            pass
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _splice_yaml(text: str, raw: dict[str, Any], patch: _YamlPatch) -> str | None:
    root = yaml.compose(text)
    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        return None
    edits: list[tuple[int, int, str]] = []

    if patch.updated_source_idx is not None or patch.appended_source is not None:
        sources_pair = _mapping_lookup(root, 'sources')
        if sources_pair is None:
            if patch.appended_source is None or patch.updated_source_idx is not None:
                return None
            sep = '' if text.endswith('\n') else '\n'
            block = f'{sep}sources:\n  - {_dump_block_at_col(patch.appended_source, 4)}\n'
            edits.append((len(text), len(text), block))
        else:
            sk_node, sv_node = sources_pair
            if patch.updated_source_idx is not None:
                if not isinstance(sv_node, yaml.SequenceNode) or sv_node.flow_style:
                    return None
                if patch.updated_source_idx >= len(sv_node.value):
                    return None
                item_node = sv_node.value[patch.updated_source_idx]
                entry_dict = raw['sources'][patch.updated_source_idx]
                if getattr(item_node, 'flow_style', False):
                    edits.append((item_node.start_mark.index, item_node.end_mark.index, _dump_flow(entry_dict)))
                else:
                    col = item_node.start_mark.column
                    end = _line_end(text, _content_end(text, item_node))
                    edits.append((item_node.start_mark.index, end, _dump_block_at_col(entry_dict, col)))
            if patch.appended_source is not None:
                if isinstance(sv_node, yaml.SequenceNode) and not sv_node.flow_style and sv_node.value:
                    last_node = sv_node.value[-1]
                    line_start = text.rfind('\n', 0, last_node.start_mark.index) + 1
                    dash_pos = text.find('-', line_start, last_node.start_mark.index)
                    if dash_pos == -1:
                        return None
                    dash_col = dash_pos - line_start
                    insert_at = _line_end(text, _content_end(text, last_node))
                    formatted = '\n' + (' ' * dash_col) + '- ' + _dump_block_at_col(patch.appended_source, dash_col + 2)
                    edits.append((insert_at, insert_at, formatted))
                elif (
                    (isinstance(sv_node, yaml.SequenceNode) and not sv_node.value)
                    or (isinstance(sv_node, yaml.ScalarNode) and sv_node.value == '')
                ):
                    end = _line_end(text, sv_node.end_mark.index)
                    formatted = ':\n  - ' + _dump_block_at_col(patch.appended_source, 4)
                    edits.append((sk_node.end_mark.index, end, formatted))
                else:
                    return None

    if patch.changed_pipelines:
        pipelines_pair = _mapping_lookup(root, 'pipelines')
        if pipelines_pair is None or not isinstance(pipelines_pair[1], yaml.SequenceNode):
            return None
        pipelines_node = pipelines_pair[1]
        if pipelines_node.flow_style:
            return None
        for p_idx, new_sources in patch.changed_pipelines.items():
            if p_idx >= len(pipelines_node.value):
                return None
            p_node = pipelines_node.value[p_idx]
            if not isinstance(p_node, yaml.MappingNode) or p_node.flow_style:
                return None
            src_pair = _mapping_lookup(p_node, 'sources')
            if src_pair is None or not isinstance(src_pair[1], yaml.SequenceNode):
                return None
            pk_node, pv_node = src_pair
            if getattr(pv_node, 'flow_style', False):
                edits.append((pv_node.start_mark.index, pv_node.end_mark.index, _dump_flow(new_sources)))
            elif not new_sources:
                end = _line_end(text, _content_end(text, pv_node))
                edits.append((pk_node.end_mark.index, end, ': []'))
            else:
                col = pv_node.start_mark.column
                end = _line_end(text, _content_end(text, pv_node))
                edits.append((pv_node.start_mark.index, end, _dump_block_at_col(new_sources, col)))

    edits.sort(key=lambda item: (item[0], item[1]), reverse=True)
    prev_start = len(text) + 1
    result = text
    for start, end, replacement in edits:
        if end > prev_start:
            return None
        result = result[:start] + replacement + result[end:]
        prev_start = start
    return result


def _validate_and_save(original_text: str, raw: dict[str, Any], path: Path,
                       existing_personas: Collection[str], *,
                       env_values: Mapping[str, str],
                       patch: _YamlPatch) -> AppConfig:
    errors: list[ConfigIssue] = []
    interpolated = interpolate(deepcopy(raw), env_values, errors=errors)
    parsed = parse_config(interpolated, path, errors, existing_personas=existing_personas)
    rendered = _render_yaml(original_text, raw, patch)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.tentacles-',
                                suffix='.yaml.tmp', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(rendered)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    return parsed


def create_source_in_config(config: AppConfig, payload: SourceCreate, *,
                            existing_personas: Collection[str]) -> tuple[AppConfig, SourceConfigView]:
    source_id = payload.id.strip()
    if _strings_with_env_refs(source_id):
        raise ConfigValidationError([ConfigIssue('sources.id', _ENV_REF_FORBIDDEN_MSG)])
    if any(s.id == source_id for s in config.sources):
        raise Conflict(f'source {source_id!r} already exists')
    original_text, raw = load_raw_yaml(config.path) if config.path.is_file() else ('', {})
    env_values = load_env_values(config.path)
    sources = list(raw.get('sources') or [])
    entry: dict[str, Any] = {'id': source_id, 'type': payload.type}
    fields = set(payload.model_fields_set) | {'schedule', 'enabled', 'update_existing'}
    _apply_fields(entry, payload.type, payload.model_dump(), fields, env_values=env_values)
    sources.append(entry)
    raw['sources'] = sources
    changed_pipelines = (_sync_pipelines(raw, source_id, payload.pipelines, env_values)
                         if payload.pipelines else {})
    updated = _validate_and_save(
        original_text,
        raw,
        config.path,
        existing_personas,
        env_values=env_values,
        patch=_YamlPatch(appended_source=entry, changed_pipelines=changed_pipelines),
    )
    return updated, get_source_config(updated, source_id)


def update_source_in_config(config: AppConfig, source_id: str, payload: SourceUpdate, *,
                            existing_personas: Collection[str]) -> tuple[AppConfig, SourceConfigView]:
    spec = config.source(source_id)
    original_text, raw = load_raw_yaml(config.path)
    env_values = load_env_values(config.path)
    sources = raw.get('sources')
    if not isinstance(sources, list):
        raise NotFound(f'unknown source {source_id!r}')
    target_idx: int | None = None
    target: dict[str, Any] | None = None
    for idx, item in enumerate(sources):
        if _matches_source_id(item, source_id, env_values):
            target_idx = idx
            target = item
            break
    if target is None or target_idx is None:
        raise NotFound(f'unknown source {source_id!r}')
    _apply_fields(
        target, spec.type, payload.model_dump(), set(payload.model_fields_set),
        env_values=env_values, config=config, current_spec=spec,
    )
    changed_pipelines: dict[int, list[str]] = {}
    if 'pipelines' in payload.model_fields_set:
        changed_pipelines = _sync_pipelines(raw, source_id, payload.pipelines or [], env_values)
    updated = _validate_and_save(
        original_text,
        raw,
        config.path,
        existing_personas,
        env_values=env_values,
        patch=_YamlPatch(updated_source_idx=target_idx, changed_pipelines=changed_pipelines),
    )
    return updated, get_source_config(updated, source_id)
