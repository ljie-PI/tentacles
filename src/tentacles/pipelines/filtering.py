"""Ordered cascade; a stage error never becomes a cached rejection."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from tentacles.diagnostics import DiagnosticEvent
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.adapters import Stage
from tentacles.pipelines.config import PipelineSpec, SemanticSpec, StageSpec
from tentacles.llm.registry import ModelRegistry
from tentacles.pipelines.registry import STAGES
from tentacles.pipelines.models import MAX_MATCH_POINTS, Evaluation, StageScore, StageBatch, FilterBatch

# 纯 keyword 级联没有付费请求，仍按大块推进以免频繁写库。
CHUNK_WITHOUT_SEMANTIC = 100


def _chunk_size(spec: PipelineSpec) -> int:
    """Finished chunks are persisted, so the paid batch size is also the checkpoint unit."""
    return max((stage.batch_size for stage in spec.filters if isinstance(stage, SemanticSpec)),
               default=CHUNK_WITHOUT_SEMANTIC)


def _evidence(traces: list[StageScore]) -> tuple[list[str], list[str]]:
    """Merge every stage's positive evidence in stage order, without duplicates.

    Tags and match points only ever describe a hit, so an error score (which
    carries none) can never contribute a fabricated reason.
    """
    tags: dict[str, str] = {}
    points: dict[str, str] = {}
    for scored in traces:
        if scored.score is None:
            continue
        for tag in scored.tags:
            tags.setdefault(tag.casefold(), tag)
        for point in scored.details.get('match_points') or []:
            if isinstance(point, str) and point.strip():
                points.setdefault(point.casefold(), point.strip())
    return list(tags.values()), list(points.values())[:MAX_MATCH_POINTS]


def _record_item_error(progress: FilterBatch, spec: PipelineSpec, adapter: Stage, stage: StageSpec,
                       index: int, item: Item, scored: StageScore) -> None:
    """Leave the concrete item behind when the cascade gives up on it.

    Only the final destination is recorded: an item error that a later stage
    still evaluated is not a lost item, and would never be resolved.
    """
    if progress.record_error is None:
        return
    progress.record_error(DiagnosticEvent(
        phase='filter_stage', error_code=adapter.item_error_code(scored),
        message=f'{stage.type} stage could not score this item'
                + (f' ({scored.error})' if scored.error else ''),
        pipeline_id=spec.id, item_id=item.id, source_id=item.source_id,
        target_url=item.url or None, retryable=True,
        debug_context={'stage': stage.type, 'stage_index': index, 'stage_error': scored.error,
                       'target_url': item.url,
                       'stage_message': scored.details.get('stage_message')}))


async def filter_items(items: list[Item], persona: PersonaView, spec: PipelineSpec,
                       now: datetime, models: ModelRegistry, progress: FilterBatch) -> FilterBatch:
    counts: list[dict[str, Any]] = [dict(index=i, type=stage.type, passed=0, rejected=0, forwarded=0, errors=0)
                                   for i, stage in enumerate(spec.filters)]
    progress.stages = counts
    progress.batches = [StageBatch([]) for _ in spec.filters]
    traces: dict[str, list[StageScore]] = {item.id: [] for item in items}
    final: dict[str, Evaluation] = {}
    if not spec.filters:
        progress.evaluations = [Evaluation(pipeline_id=spec.id, item_id=item.id, persona_version=persona.version,
            input_revision=item.content_revision,
            passed=True, score=1, reason='no filtering stages configured', stage='none', evaluated_at=now)
            for item in items]
        return progress

    async def cascade(chunk: list[Item]) -> None:
        """Run one chunk through the whole cascade, leaving final evaluations behind."""
        pending = chunk
        for i, stage in enumerate(spec.filters):
            last = i == len(spec.filters) - 1
            forwarded = []
            batch = progress.batches[i]
            adapter = STAGES[stage.type]
            scored_from = len(batch.scores)
            await adapter.score(pending, persona, stage, i, models, batch)
            for item, scored in zip(pending, batch.scores[scored_from:]):
                traces[item.id].append(scored)
                if scored.score is None:
                    counts[i]['errors'] += 1
                    if not last and batch.error is None:
                        counts[i]['forwarded'] += 1
                        forwarded.append(item)
                        continue
                    _record_item_error(progress, spec, adapter, stage, i, item, scored)
                    continue
                rejected = scored.score < stage.rejection
                accepted = not rejected and (last or stage.accept_above is not None
                                             and scored.score >= stage.accept_above)
                if rejected or accepted:
                    counts[i]['rejected' if rejected else 'passed'] += 1
                    tags, points = _evidence(traces[item.id])
                    final[item.id] = Evaluation(pipeline_id=spec.id, item_id=item.id,
                        input_revision=item.content_revision,
                        persona_version=persona.version, passed=accepted, score=scored.score,
                        tags=tags, match_points=points,
                        reason=points[0] if points else scored.reason, stage=stage.type,
                        stage_scores=traces[item.id], evaluated_at=now)
                elif batch.error is None:
                    counts[i]['forwarded'] += 1
                    forwarded.append(item)
            if batch.error is not None:
                progress.error = batch.error
                return
            pending = forwarded

    size = _chunk_size(spec)
    for offset in range(0, len(items), size):
        chunk = items[offset:offset + size]
        await cascade(chunk)
        progress.evaluations = [final[item.id] for item in items if item.id in final]
        if progress.error is not None:
            return progress
        if progress.checkpoint is not None:
            finished = [final[item.id] for item in chunk if item.id in final]
            if finished:
                await progress.checkpoint(finished)
    return progress


def filter_stats(progress: FilterBatch, candidates: int) -> dict[str, Any]:
    for counts, batch in zip(progress.stages, progress.batches):
        counts.update(usage=batch.metrics.usage.model_dump(), cost=batch.metrics.cost)
        if batch.model_name is not None:
            counts.update(models=batch.metrics.models, model_name=batch.model_name,
                          aborted=batch.error is not None, abort_status=batch.http_status)
    return {'candidates': candidates, 'evaluated': len(progress.evaluations), 'stages': progress.stages,
            'passed': sum(e.passed for e in progress.evaluations),
            'rejected': sum(not e.passed for e in progress.evaluations),
            'errors': candidates - len(progress.evaluations)}
