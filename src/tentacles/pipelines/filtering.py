"""Ordered cascade; a stage error never becomes a cached rejection."""
from __future__ import annotations

import asyncio
from time import monotonic

import regex as re
from datetime import datetime
from typing import Any

from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.config import PipelineSpec, StageSpec
from tentacles.pipelines.models import Evaluation, StageScore


def _keyword(item: Item, persona: PersonaView, stage: StageSpec, index: int) -> StageScore:
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


async def filter_items(items: list[Item], persona: PersonaView, spec: PipelineSpec,
                 now: datetime) -> tuple[list[Evaluation], list[dict[str, Any]]]:
    counts: list[dict[str, Any]] = [dict(index=i, type=stage.type, passed=0, rejected=0, forwarded=0, errors=0,
                   usage={'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}, cost=None)
              for i, stage in enumerate(spec.filters)]
    evaluations = []
    for item in items:
        await asyncio.sleep(0)
        trace: list[StageScore] = []
        if not spec.filters:
            evaluations.append(Evaluation(pipeline_id=spec.id, item_id=item.id, persona_version=persona.version,
                passed=True, score=1, reason='no filtering stages configured', stage='none', evaluated_at=now))
            continue
        for i, stage in enumerate(spec.filters):
            await asyncio.sleep(0)
            last = i == len(spec.filters) - 1
            try:
                scored = _keyword(item, persona, stage, i)
            except Exception as exc:
                scored = StageScore(index=i, type=stage.type, error=type(exc).__name__)
            trace.append(scored)
            if scored.score is None:
                counts[i]['errors'] += 1
                if not last:
                    counts[i]['forwarded'] += 1
                continue
            rejected = scored.score < stage.rejection
            accepted = not rejected and (last or stage.accept_above is not None and scored.score >= stage.accept_above)
            if rejected or accepted:
                counts[i]['rejected' if rejected else 'passed'] += 1
                evaluations.append(Evaluation(pipeline_id=spec.id, item_id=item.id,
                    persona_version=persona.version, passed=accepted, score=scored.score,
                    tags=scored.tags, reason=scored.reason, stage=stage.type,
                    stage_scores=trace, evaluated_at=now))
                break
            counts[i]['forwarded'] += 1
    return evaluations, counts
