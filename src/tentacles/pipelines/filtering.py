"""Ordered cascade; a stage error never becomes a cached rejection."""
from __future__ import annotations

import asyncio
from time import monotonic

import regex as re
from datetime import datetime
from typing import Any

from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.config import PipelineSpec, KeywordSpec
from tentacles.llm.registry import ModelRegistry
from tentacles.pipelines.decision import score_decisions
from tentacles.pipelines.models import Evaluation, StageScore, StageBatch, FilterBatch


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



async def filter_items(items: list[Item], persona: PersonaView, spec: PipelineSpec,
                       now: datetime, models: ModelRegistry, progress: FilterBatch) -> FilterBatch:
    counts: list[dict[str, Any]] = [dict(index=i, type=stage.type, passed=0, rejected=0, forwarded=0, errors=0)
                                   for i, stage in enumerate(spec.filters)]
    progress.stages = counts
    progress.batches = [StageBatch([]) for _ in spec.filters]
    traces: dict[str, list[StageScore]] = {item.id: [] for item in items}
    final: dict[str, Evaluation] = {}
    pending = list(items)
    if not spec.filters:
        progress.evaluations = [Evaluation(pipeline_id=spec.id, item_id=item.id, persona_version=persona.version,
            passed=True, score=1, reason='no filtering stages configured', stage='none', evaluated_at=now)
            for item in items]
        return progress
    for i, stage in enumerate(spec.filters):
        last = i == len(spec.filters) - 1
        forwarded = []
        batch = progress.batches[i]
        if stage.type == 'decision':
            await score_decisions(pending, persona, stage, i, models, batch)
            scores = batch.scores
        else:
            scores = []
            for item in pending:
                await asyncio.sleep(0)
                try:
                    scores.append(_keyword(item, persona, stage, i))
                except Exception as exc:
                    scores.append(StageScore(index=i, type=stage.type, error=type(exc).__name__))
            batch.scores = scores
        for item, scored in zip(pending, scores):
            traces[item.id].append(scored)
            if scored.score is None:
                counts[i]['errors'] += 1
                if not last and batch.error is None:
                    counts[i]['forwarded'] += 1
                    forwarded.append(item)
                continue
            rejected = scored.score < stage.rejection
            accepted = not rejected and (last or stage.accept_above is not None and scored.score >= stage.accept_above)
            if rejected or accepted:
                counts[i]['rejected' if rejected else 'passed'] += 1
                final[item.id] = Evaluation(pipeline_id=spec.id, item_id=item.id,
                    persona_version=persona.version, passed=accepted, score=scored.score,
                    tags=scored.tags, reason=scored.reason, stage=stage.type,
                    stage_scores=traces[item.id], evaluated_at=now)
            elif batch.error is None:
                counts[i]['forwarded'] += 1
                forwarded.append(item)
        progress.evaluations = [final[item.id] for item in items if item.id in final]
        if batch.error is not None:
            progress.error = batch.error
            return progress
        pending = forwarded
    return progress


def filter_stats(progress: FilterBatch, candidates: int) -> dict[str, Any]:
    for counts, batch in zip(progress.stages, progress.batches):
        counts.update(usage=batch.metrics.usage.model_dump(), cost=batch.metrics.cost)
        if batch.model_name is not None:
            counts.update(models=batch.metrics.models, model_name=batch.model_name,
                          aborted=batch.error is not None, abort_status=batch.http_status)
    costs = [batch.metrics.cost for batch in progress.batches if batch.metrics.cost is not None]
    return {'candidates': candidates, 'evaluated': len(progress.evaluations), 'stages': progress.stages,
            'passed': sum(e.passed for e in progress.evaluations),
            'rejected': sum(not e.passed for e in progress.evaluations),
            'errors': candidates - len(progress.evaluations), 'cost': sum(costs) if costs else None,
            'usage': {key: sum(getattr(batch.metrics.usage, key) for batch in progress.batches)
                      for key in ('input_tokens', 'output_tokens', 'total_tokens')}}
