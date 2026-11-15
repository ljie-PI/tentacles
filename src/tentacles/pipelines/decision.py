"""JEV scoring: one shared-Persona request per batch, or one request per item."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tentacles.errors import UpstreamError
from tentacles.llm.client import DecisionModel
from tentacles.llm.decision_batch import DecisionBatchError, DecisionInput, decide_many, decide_packed
from tentacles.llm.registry import ModelRegistry
from tentacles.llm.retry import ModelHTTPError, RequestGate
from tentacles.models import Item
from tentacles.personas.models import InterestTag, PersonaView
from tentacles.personas.render import render_persona
from tentacles.pipelines.config import SemanticSpec, StageSpec
from tentacles.pipelines.models import StageBatch, StageScore
from tentacles.schemas import DecisionCompletion

_RELEVANCE = 'Would this user want to read this item, according to the persona and its priorities?'
_SCALE = ('Not interested', 'Slight interest', 'Relevant', 'Essential reading')
_TAG_INSTRUCTIONS = 'Select one interest tag; prefer earlier tags on ties, or other.'
# A packed payload the endpoint refuses as too large or as too many questions.
_REFUSED_STATUSES = {400, 413}


class _PackingRefused(Exception):
    """The endpoint refused this packed payload."""


@dataclass
class _ChunkResult:
    scores: list[StageScore] = field(default_factory=list)
    # False: this stage stops packing for its remaining chunks.
    packed: bool = True
    # Set: the stage aborts here, keeping every decision already reached.
    error: str | None = None
    status: int | None = None


def _relevance_question(stage: SemanticSpec) -> dict[str, Any]:
    question: dict[str, Any] = {'type': stage.relevance_type, 'instructions': _RELEVANCE}
    if stage.relevance_type == 'noul':
        question['criteria'] = {'true': 'Relevant and wanted', 'false': 'Irrelevant or unwanted'}
    else:
        question['criteria'] = list(_SCALE)
    return question


def _tag_question(labels: dict[str, InterestTag]) -> dict[str, Any]:
    return {'type': 'choice', 'instructions': _TAG_INSTRUCTIONS,
            'criteria': {key: f'{tag.tag}: {tag.description}' for key, tag in labels.items()}
                        | {'other': 'None of the configured interest tags'}}


def _labels(persona: PersonaView) -> dict[str, InterestTag]:
    return {f't{i}': tag for i, tag in enumerate(persona.tags)}


def _state(rendered: str, members: list[tuple[int, Item]], stage: SemanticSpec) -> dict[str, Any]:
    return {'persona': rendered,
            'items': [{'idx': number, 'source': item.source_id, 'title': item.title,
                       'author': item.author, 'summary': item.summary[:stage.summary_chars]}
                      for number, item in members]}


def _relevance(related: dict[str, Any], relevance_type: str) -> float:
    return related['noul'] if relevance_type == 'noul' else related['score']


def _evidence(related: dict[str, Any], relevance_type: str, label: InterestTag | None) -> str:
    """Readable why-this-matched text, replacing the old constant relevance reason."""
    if relevance_type == 'noul':
        point = f'JEV 相关性 {related["noul"]:.2f}'
    else:
        rank = min(int(related['raw_score']), len(_SCALE) - 1)
        point = f'JEV 量表评级：{_SCALE[rank]} ({related["score"]:.2f})'
    if label is not None:
        point += f'，命中优先兴趣「{label.tag}」'
        if label.description.strip():
            point += f'（{label.description.strip()}）'
    return point


def _score(index: int, stage: SemanticSpec, related: dict[str, Any], tag: dict[str, Any] | None,
           labels: dict[str, InterestTag], model: str) -> StageScore:
    selected = (tag or {}).get('choice', 'other')
    label = labels.get(selected)
    points = [_evidence(related, stage.relevance_type, label)]
    answers = {'relevance': related} | ({'tag': tag} if tag is not None else {})
    return StageScore(index=index, type=stage.type, score=_relevance(related, stage.relevance_type),
        tags=[label.tag] if label is not None else [], reason=points[0][:300],
        details={'model': model, 'answers': answers, 'labels': {k: v.tag for k, v in labels.items()},
                 'executor': 'decision', 'match_points': points})


def _failure(index: int, stage: SemanticSpec, error: str, **details: Any) -> StageScore:
    return StageScore(index=index, type=stage.type, error=error,
                      details={'executor': 'decision', **details})


def _failures(members: list[tuple[int, Item]], stage: SemanticSpec, index: int,
              exc: Exception) -> list[StageScore]:
    return [_failure(index, stage, type(exc).__name__, stage_message=str(exc)[:300]) for _ in members]


def _refused(exc: UpstreamError) -> bool:
    return isinstance(exc, ModelHTTPError) and exc.status in _REFUSED_STATUSES


async def _packed_scores(model: DecisionModel, members: list[tuple[int, Item]], rendered: str,
                         labels: dict[str, InterestTag], stage: SemanticSpec, index: int,
                         progress: StageBatch, gate: RequestGate) -> list[StageScore]:
    """Step 1 asks relevance for every packed item; step 2 adds tags for survivors."""
    relevance = {f'r_{number}': _relevance_question(stage) for number, _ in members}
    try:
        first = await decide_packed(model, _state(rendered, members, stage), relevance,
                                    progress.metrics, gate)
    except UpstreamError as exc:
        if _refused(exc):
            raise _PackingRefused() from None
        raise
    related = {number: first.answers[f'r_{number}'] for number, _ in members}
    survivors = [number for number, _ in members
                 if _relevance(related[number], stage.relevance_type) >= stage.rejection]
    tags: dict[int, dict[str, Any]] = {}
    tag_error: str | None = None
    model_name = first.model
    if survivors and labels:
        wanted = [(number, item) for number, item in members if number in survivors]
        try:
            # Step 2 only refines topics: a failure keeps the paid relevance verdicts.
            second = await decide_packed(model, _state(rendered, wanted, stage),
                                         {f't_{number}': _tag_question(labels) for number, _ in wanted},
                                         progress.metrics, gate)
            tags = {number: second.answers[f't_{number}'] for number, _ in wanted}
            model_name = second.model
        except Exception as exc:
            tag_error = type(exc).__name__
    scores = []
    for number, _ in members:
        scored = _score(index, stage, related[number], tags.get(number), labels, model_name)
        if tag_error is not None and number in survivors:
            scored.details['tag_error'] = tag_error
        scores.append(scored)
    return scores


async def _packed_chunk(model: DecisionModel, chunk: list[tuple[int, Item]], rendered: str,
                        labels: dict[str, InterestTag], stage: SemanticSpec, index: int,
                        progress: StageBatch, gate: RequestGate) -> _ChunkResult:
    """Pack one chunk, halving a refused payload once before giving up on packing."""
    try:
        return _ChunkResult(await _packed_scores(model, chunk, rendered, labels, stage, index, progress, gate))
    except _PackingRefused:
        if len(chunk) < 2:
            return _ChunkResult([_failure(index, stage, 'packing_refused',
                                          stage_message='endpoint refused a single-question request')])
    except UpstreamError as exc:
        return _ChunkResult(_failures(chunk, stage, index, exc))
    midpoint = len(chunk) // 2
    halves = (chunk[:midpoint], chunk[midpoint:])
    scores: list[StageScore] = []
    try:
        scores.extend(await _packed_scores(model, halves[0], rendered, labels, stage, index, progress, gate))
    except _PackingRefused:
        # One retry level only: an endpoint that refuses halves cannot pack at all.
        result = await _windowed_chunk(model, chunk, rendered, labels, stage, index, progress)
        return _ChunkResult(result.scores, packed=False, error=result.error, status=result.status)
    except UpstreamError as exc:
        return _ChunkResult(_failures(chunk, stage, index, exc))
    try:
        scores.extend(await _packed_scores(model, halves[1], rendered, labels, stage, index, progress, gate))
    except _PackingRefused:
        # Keep the half that already answered; the stage stops packing from here.
        tail = await _windowed_chunk(model, halves[1], rendered, labels, stage, index, progress)
        return _ChunkResult(scores + tail.scores, packed=False, error=tail.error, status=tail.status)
    except UpstreamError as exc:
        return _ChunkResult(scores + _failures(halves[1], stage, index, exc))
    return _ChunkResult(scores)


async def _windowed_chunk(model: DecisionModel, chunk: list[tuple[int, Item]], rendered: str,
                          labels: dict[str, InterestTag], stage: SemanticSpec, index: int,
                          progress: StageBatch) -> _ChunkResult:
    """Mode B: one request per item through the model's fixed worker pool."""
    questions = {'relevance': _relevance_question(stage)}
    if labels:
        questions['tag'] = _tag_question(labels)
    requests = [DecisionInput({'persona': rendered, 'source': item.source_id, 'title': item.title,
                               'author': item.author, 'summary': item.summary[:stage.summary_chars]},
                              questions)
                for _, item in chunk]
    results = None
    error = status = None
    try:
        results = (await decide_many(model, requests, progress.metrics)).results
    except DecisionBatchError as exc:
        results, error, status = exc.batch.results, exc.message, exc.status
    scores: list[StageScore] = []
    for result in results or []:
        if not isinstance(result, DecisionCompletion):
            scores.append(_failure(index, stage, type(result).__name__ if result is not None else 'batch_aborted',
                                   **({'stage_message': str(result)[:300]} if result is not None else {})))
            continue
        scores.append(_score(index, stage, result.answers['relevance'], result.answers.get('tag'), labels,
                             result.model))
    return _ChunkResult(scores, error=error, status=status)


async def score_decisions(items: list[Item], persona: PersonaView, stage: StageSpec,
                          index: int, models: ModelRegistry, name: str, progress: StageBatch) -> StageBatch:
    assert isinstance(stage, SemanticSpec)
    rendered = render_persona(persona)
    labels = _labels(persona)
    model = models.decision(name)
    gate = RequestGate()
    numbered = [(number, item) for number, item in enumerate(items, start=1)]
    packed = stage.jev_batch_mode == 'packed_questions'
    for offset in range(0, len(numbered), stage.batch_size):
        chunk = numbered[offset:offset + stage.batch_size]
        if packed:
            result = await _packed_chunk(model, chunk, rendered, labels, stage, index, progress, gate)
        else:
            result = await _windowed_chunk(model, chunk, rendered, labels, stage, index, progress)
        progress.scores.extend(result.scores)
        packed = result.packed
        if result.error is not None:
            progress.error, progress.http_status = result.error, result.status
            return progress
        if gate.failure is not None:
            # Credentials or terminal quota: no later chunk can succeed either.
            progress.error = str(gate.failure)
            progress.http_status = getattr(gate.failure, 'status', None)
            return progress
    return progress
