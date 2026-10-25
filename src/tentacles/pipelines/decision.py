"""Build one relevance request per item, with optional priority-label choice."""
from __future__ import annotations

from typing import Any

from tentacles.llm.decision_batch import DecisionInput, DecisionBatchError, decide_many
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.personas.render import render_persona
from tentacles.pipelines.config import DecisionSpec
from tentacles.pipelines.models import StageBatch, StageScore
from tentacles.schemas import DecisionCompletion


async def score_decisions(items: list[Item], persona: PersonaView, stage: DecisionSpec,
                          index: int, models: ModelRegistry, progress: StageBatch) -> StageBatch:
    name = models.resolve('decision', stage.model)
    progress.model_name = name
    relevance: dict[str, Any] = {'type': stage.relevance_type,
        'instructions': 'Would this user want to read this item, according to the persona and its priorities?'}
    if stage.relevance_type == 'noul':
        relevance['criteria'] = {'true': 'Relevant and wanted', 'false': 'Irrelevant or unwanted'}
    else:
        relevance['criteria'] = ['Not interested', 'Slight interest', 'Relevant', 'Essential reading']
    questions = {'relevance': relevance}
    labels = {f't{i}': tag.tag for i, tag in enumerate(persona.tags)}
    if labels:
        questions['tag'] = {'type': 'choice', 'instructions': 'Select one interest tag; prefer earlier tags on ties, or other.',
                            'criteria': {**{key: f'{tag.tag}: {tag.description}' for key, tag in zip(labels, persona.tags)},
                                         'other': 'None of the configured interest tags'}}
    rendered = render_persona(persona)
    requests = [DecisionInput({'persona': rendered, 'source': item.source_id, 'title': item.title,
                               'author': item.author, 'summary': item.summary[:1000]}, questions) for item in items]
    error, status = None, None
    try:
        batch = await decide_many(models.decision(name), requests, progress.metrics)
    except DecisionBatchError as exc:
        batch, error, status = exc.batch, exc.message, exc.status
    scores = []
    for result in batch.results:
        if not isinstance(result, DecisionCompletion):
            scores.append(StageScore(index=index, type='decision', error=type(result).__name__ if result else 'batch_aborted'))
            continue
        related = result.answers['relevance']
        selected = result.answers.get('tag', {}).get('choice', 'other')
        scores.append(StageScore(index=index, type='decision',
            score=related['noul'] if stage.relevance_type == 'noul' else related['score'],
            tags=[labels[selected]] if selected in labels else [], reason='decision relevance',
            details={'model': result.model, 'answers': result.answers, 'labels': labels}))
    progress.scores, progress.error, progress.http_status = scores, error, status
    return progress
