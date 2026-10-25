"""Persona-guided batched classification; missing rows are explicit zero scores."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from tentacles.llm.json import extract_json
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.personas.render import render_persona
from tentacles.pipelines.config import LLMSpec
from tentacles.pipelines.models import StageBatch, StageScore

_SYSTEM = '''根据用户画像与有序兴趣标签筛选给定新闻，不要推测材料外的事实。
每条最多一个最相关标签；跳过标题党、广告、营销和不匹配任何兴趣的内容。
严格遵守画像的额外排除条件；相关度相近时，优先选择排在前面的标签。
分数范围 0–1，只返回至少 0.3 分的匹配项，其余不要输出。
每项给出一句简短理由；仅输出 JSON 数组：
[{"id":条目编号,"tag_id":标签编号,"score":0.9,"reason":"简短理由"}]。'''


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


def _parse(text: str, ids: list[int], labels: list[dict[str, Any]], model: str,
           index: int, models: ModelRegistry) -> list[StageScore]:
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
        tag = _id(row.get('tag_id'), label_ids)
        reason = row.get('reason')
        if tag is None or not isinstance(reason, str) or not reason.strip():
            raise ValueError('invalid llm tag or reason')
        returned[number] = StageScore(index=index, type='llm', score=score, tags=[label_names[tag]],
            reason=models.redact(reason.strip())[:300], details={'model': model, 'id': number})
    return [returned.get(number) or StageScore(index=index, type='llm', score=0,
                reason='not returned by llm filter', details={'model': model, 'id': number}) for number in ids]


async def score_llm(items: list[Item], persona: PersonaView, stage: LLMSpec, index: int,
                    models: ModelRegistry, progress: StageBatch) -> StageBatch:
    name = models.resolve('filter', stage.model)
    progress.model_name = name
    labels = _interest_labels(persona)
    if not labels:
        progress.scores = [StageScore(index=index, type='llm', error='empty_persona_interests') for _ in items]
        return progress
    numbered = [(i + 1, item) for i, item in enumerate(items)]

    def failed(chunk: list[tuple[int, Item]], exc: Exception) -> list[StageScore]:
        return [StageScore(index=index, type='llm', error=type(exc).__name__) for _ in chunk]

    async def ask(chunk: list[tuple[int, Item]]) -> list[StageScore]:
        payload = {'persona': render_persona(persona), 'tags': labels,
                   'items': [{'id': number, 'source': item.source_id, 'title': item.title}
                             for number, item in chunk]}
        result = await models.chat(name).complete(
            [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}], system=_SYSTEM,
            on_completion=progress.metrics.record)
        try:
            return _parse(result.text, [n for n, _ in chunk], labels, result.model, index, models)
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
