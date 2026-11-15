"""Bounded keyword matching, independent of cascade routing."""
from __future__ import annotations

import asyncio
from time import monotonic

import regex as re

from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.adapters import ModelRequirement
from tentacles.pipelines.config import KeywordSpec, StageSpec
from tentacles.pipelines.models import StageBatch, StageScore

# 软排除词与正向兴趣词同时命中的灰区得分：高于默认 reject_below，低于任何
# accept_above 短路阈值，因此条目会转交下一关由语义模型结合完整画像裁决。
GUARDED_EXCLUDE_SCORE = 0.55
# 纯正向命中的常规得分；仅在 accept_above 明确要求 1.0 时才给满分短路。
INCLUDE_SCORE = 0.85


def _unique(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        seen.setdefault(value.casefold(), value)
    return list(seen.values())


def _keyword(item: Item, persona: PersonaView, stage: KeywordSpec, index: int) -> StageScore:
    text = item.title + '\n' + item.summary
    folded = text.casefold()
    deadline = monotonic() + 0.05

    def matches(pattern: str) -> bool:
        if not stage.regex:
            return pattern.casefold() in folded
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError('keyword regex budget exhausted')
        return bool(re.search(pattern, text, re.IGNORECASE | re.VERSION1, timeout=remaining))

    def hits(words: list[str]) -> list[str]:
        return _unique([word for word in words if matches(word)])

    tags = _unique([tag.tag for tag in persona.tags if tag.tag.casefold() in folded])
    include_hits = _unique(hits(persona.include + stage.include) + tags)
    exclude_hits = hits(persona.exclude + stage.exclude)
    hard_hits = hits(persona.hard_exclude + stage.hard_exclude)

    if hard_hits:
        score, reason = 0.0, 'hard exclude keyword matched'
        points = [f'触发硬排除特征：{"、".join(hard_hits)}']
    elif exclude_hits and (not stage.guard_ambiguous_exclude or not include_hits):
        score, reason = 0.0, 'exclude keyword matched'
        points = [f'触发排除关键词：{"、".join(exclude_hits)}']
    elif exclude_hits:
        score, reason = GUARDED_EXCLUDE_SCORE, 'exclude keyword guarded by positive interest'
        points = [f'命中排除词「{"、".join(exclude_hits)}」，'
                  f'受正向兴趣「{"、".join(include_hits)}」豁免转交语义精筛']
    elif include_hits:
        # 保留旧配置 accept_above: 1.0 的短路省钱用法，其余情况统一进下一关。
        short_circuit = stage.accept_above is not None and stage.accept_above >= 1.0
        score = 1.0 if short_circuit else INCLUDE_SCORE
        reason = 'include keyword matched'
        points = [f'命中包含关键词：{"、".join(include_hits)}']
    else:
        score, reason, points = 0.5, 'no keyword matched', []
    return StageScore(index=index, type=stage.type, score=score, reason=reason, tags=tags,
                      details={'matched_include': include_hits, 'matched_exclude': exclude_hits,
                               'matched_hard_exclude': hard_hits, 'match_points': points})


class KeywordStage:
    models: tuple[ModelRequirement, ...] = ()

    async def score(self, items: list[Item], persona: PersonaView, stage: StageSpec,
                    index: int, models: ModelRegistry, progress: StageBatch) -> StageBatch:
        assert isinstance(stage, KeywordSpec)
        for item in items:
            await asyncio.sleep(0)
            try:
                progress.scores.append(_keyword(item, persona, stage, index))
            except Exception as exc:
                progress.scores.append(StageScore(index=index, type=stage.type, error=type(exc).__name__))
        return progress
