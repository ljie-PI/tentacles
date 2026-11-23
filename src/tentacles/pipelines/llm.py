"""Persona-guided batched classification; missing rows are explicit zero scores."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from tentacles.llm.json import extract_json
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item, format_item_summary, format_item_title
from tentacles.personas.models import PersonaView
from tentacles.personas.render import render_persona
from tentacles.pipelines.config import SemanticSpec, StageSpec
from tentacles.pipelines.models import StageBatch, StageScore

_SYSTEM = '''根据用户画像与有序兴趣标签筛选给定新闻，不要推测材料外的事实。
每条最多挑 3 个最相关标签；跳过标题党、广告、营销和不匹配任何兴趣的内容。
严格遵守画像的额外排除条件；相关度相近时，优先选择排在前面的标签。
match_points 用 1–3 条具体中文短句写出命中的事实点（模型、数据集、实测结果等），
不要复述标题；summary 只是标题的补充，不足以判断时以标题为准。
分数范围 0–1，只返回至少 0.3 分的匹配项，其余不要输出。
仅输出 JSON 数组：
[{"id":条目编号,"tag_ids":[标签编号],"score":0.9,"match_points":["具体匹配点"]}]。'''


class _OutputError(ValueError):
    """The model's classification text is not a reliable batch result."""


def _interest_labels(persona: PersonaView) -> list[dict[str, Any]]:
    if persona.tags:
        return [{'id': i + 1, 'name': tag.tag, 'description': tag.description}
                for i, tag in enumerate(persona.tags)]
    names: dict[str, str] = {}
    for line in persona.profile[:12000].splitlines():
        value = re.sub(r'^\s*(?:[-*+#]+\s*|\d+[.)]\s*)', '', line).strip()
        if value:
            names.setdefault(value.casefold(), value)
    return [{'id': i + 1, 'name': name, 'description': name} for i, name in enumerate(names.values())]


def _id(value: Any, allowed: dict[str, int]) -> int | None:
    if type(value) is int or isinstance(value, str):
        return allowed.get(str(value))
    return None


def _tag_ids(row: dict[str, Any], label_ids: dict[str, int]) -> list[int]:
    """`tag_ids` is the current protocol; a single `tag_id` is its legacy spelling."""
    raw = row.get('tag_ids', row.get('tag_id'))
    if raw is None:
        raise ValueError('invalid llm tag')
    values = raw if isinstance(raw, list) else [raw]
    found: list[int] = []
    for value in values:
        tag = _id(value, label_ids)
        if tag is None or tag in found:
            raise ValueError('invalid llm tag')
        found.append(tag)
    return found


def _match_points(row: dict[str, Any], models: ModelRegistry) -> list[str]:
    """`match_points` is the current protocol; `reason` is its legacy single value."""
    raw = row.get('match_points', row.get('reason'))
    if raw is None:
        raise ValueError('invalid llm match point')
    values = raw if isinstance(raw, list) else [raw]
    points: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('invalid llm match point')
        point = models.redact(value.strip())[:300]
        if point not in points:
            points.append(point)
    return points


def _parse(text: str, ids: list[int], labels: list[dict[str, Any]], model: str,
           index: int, models: ModelRegistry, type_name: str) -> list[StageScore]:
    data = extract_json(text)
    if not isinstance(data, list):
        raise ValueError('llm classification must be an array')
    allowed = {str(number): number for number in ids}
    label_ids = {str(label['id']): label['id'] for label in labels}
    label_names = {label['id']: label['name'] for label in labels}
    returned = {}
    for row in data:
        if not isinstance(row, dict):
            raise ValueError('llm classification rows must be objects')
        number = _id(row.get('id'), allowed)
        if number is None:
            continue
        if number in returned:
            raise ValueError('duplicate llm classification id')
        score = row.get('score')
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1 or not math.isfinite(score):
            raise ValueError('invalid llm relevance score')
        # Only items at or above the prompt's 0.3 floor come back, so each returned
        # row must name what matched; a score alone is not reviewable evidence.
        tags = _tag_ids(row, label_ids)
        points = _match_points(row, models)
        returned[number] = StageScore(index=index, type=type_name, score=score,
            tags=[label_names[tag] for tag in tags], reason=points[0],
            details={'model': model, 'id': number, 'executor': 'chat', 'match_points': points})
    return [returned.get(number) or _missing(index, model, number, type_name) for number in ids]


def _missing(index: int, model: str, number: int, type_name: str) -> StageScore:
    return StageScore(index=index, type=type_name, score=0, reason='not returned by llm filter',
                      details={'model': model, 'id': number, 'executor': 'chat'})


async def score_llm(items: list[Item], persona: PersonaView, stage: StageSpec, index: int,
                    models: ModelRegistry, name: str, progress: StageBatch) -> StageBatch:
    assert isinstance(stage, SemanticSpec)
    labels = _interest_labels(persona)
    if not labels:
        progress.scores = [StageScore(index=index, type=stage.type, error='empty_persona_interests',
                                      details={'executor': 'chat'}) for _ in items]
        return progress
    rendered = render_persona(persona)
    if stage.instructions:
        rendered += '\n\n本流水线筛选约束（必须与画像同时满足）：\n' + stage.instructions
    numbered = [(i + 1, item) for i, item in enumerate(items)]

    def failed(chunk: list[tuple[int, Item]], exc: Exception) -> list[StageScore]:
        return [StageScore(index=index, type=stage.type, error=type(exc).__name__,
                           details={'executor': 'chat'}) for _ in chunk]

    async def ask(chunk: list[tuple[int, Item]]) -> list[StageScore]:
        payload = {'persona': rendered, 'tags': labels,
                   'items': [{'id': number, 'source': item.source_id, 'title': format_item_title(item),
                              'summary': format_item_summary(item)[:stage.summary_chars]}
                             for number, item in chunk]}
        result = await models.chat(name).complete(
            [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}], system=_SYSTEM,
            on_completion=progress.metrics.record)
        try:
            return _parse(result.text, [n for n, _ in chunk], labels, result.model, index, models,
                          stage.type)
        except ValueError:
            raise _OutputError('invalid llm classification output') from None

    for offset in range(0, len(numbered), stage.batch_size):
        chunk = numbered[offset:offset + stage.batch_size]
        try:
            progress.scores.extend(await ask(chunk))
        except _OutputError:
            # Split only once. A singleton has one nonempty retry, never an empty request.
            midpoint = max(1, len(chunk) // 2)
            for half in (chunk[:midpoint], chunk[midpoint:]):
                if not half:
                    continue
                try:
                    progress.scores.extend(await ask(half))
                except Exception as exc:
                    progress.scores.extend(failed(half, exc))
        except Exception as exc:
            progress.scores.extend(failed(chunk, exc))
    return progress
