"""Ordered cascade; a stage error never becomes a cached rejection."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from tentacles.diagnostics import DiagnosticEvent
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.config import PipelineSpec, StageSpec
from tentacles.llm.registry import ModelRegistry
from tentacles.pipelines.registry import STAGES
from tentacles.pipelines.models import Evaluation, StageScore, StageBatch, FilterBatch


def _item_error_code(stage_type: str, scored: StageScore) -> str:
    """The shared vocabulary code for an item a stage could not score."""
    if stage_type == 'keyword':
        return 'regex_timeout' if scored.error in {'TimeoutError', 'Timeout'} else 'unexpected_exception'
    if stage_type == 'llm':
        return 'llm_batch_parse_error'
    return 'decision_item_error'


def _record_item_error(progress: FilterBatch, spec: PipelineSpec, stage: StageSpec,
                       index: int, item: Item, scored: StageScore) -> None:
    """Leave the concrete item behind when the cascade gives up on it.

    Only the final destination is recorded: an item error that a later stage
    still evaluated is not a lost item, and would never be resolved.
    """
    if progress.record_error is None:
        return
    progress.record_error(DiagnosticEvent(
        phase='filter_stage', error_code=_item_error_code(stage.type, scored),
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
    pending = list(items)
    if not spec.filters:
        progress.evaluations = [Evaluation(pipeline_id=spec.id, item_id=item.id, persona_version=persona.version,
            input_revision=item.content_revision,
            passed=True, score=1, reason='no filtering stages configured', stage='none', evaluated_at=now)
            for item in items]
        return progress
    for i, stage in enumerate(spec.filters):
        last = i == len(spec.filters) - 1
        forwarded = []
        batch = progress.batches[i]
        await STAGES[stage.type].score(pending, persona, stage, i, models, batch)
        scores = batch.scores
        for item, scored in zip(pending, scores):
            traces[item.id].append(scored)
            if scored.score is None:
                counts[i]['errors'] += 1
                if not last and batch.error is None:
                    counts[i]['forwarded'] += 1
                    forwarded.append(item)
                    continue
                _record_item_error(progress, spec, stage, i, item, scored)
                continue
            rejected = scored.score < stage.rejection
            accepted = not rejected and (last or stage.accept_above is not None and scored.score >= stage.accept_above)
            if rejected or accepted:
                counts[i]['rejected' if rejected else 'passed'] += 1
                final[item.id] = Evaluation(pipeline_id=spec.id, item_id=item.id,
                    input_revision=item.content_revision,
                    persona_version=persona.version, passed=accepted, score=scored.score,
                    tags=scored.tags, match_points=list(scored.details.get('match_points') or []),
                    reason=scored.reason, stage=stage.type,
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
    return {'candidates': candidates, 'evaluated': len(progress.evaluations), 'stages': progress.stages,
            'passed': sum(e.passed for e in progress.evaluations),
            'rejected': sum(not e.passed for e in progress.evaluations),
            'errors': candidates - len(progress.evaluations)}
