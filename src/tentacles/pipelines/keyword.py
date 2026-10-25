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

    if any(matches(word) for word in persona.exclude + stage.exclude):
        score, reason = 0.0, 'exclude keyword matched'
    elif any(matches(word) for word in persona.include + stage.include):
        score, reason = 1.0, 'include keyword matched'
    else:
        score, reason = 0.5, 'no keyword matched'
    tags = [tag.tag for tag in persona.tags if tag.tag.casefold() in text.casefold()]
    return StageScore(index=index, type=stage.type, score=score, reason=reason, tags=tags)


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
