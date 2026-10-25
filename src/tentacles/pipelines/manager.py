"""Pipeline operations share persistent, fenced leases with scheduled entry points."""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig
from tentacles.errors import NotFound, UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.personas.models import PersonaView
from tentacles.pipelines.config import PipelineSpec
from tentacles.pipelines.digest import make_digest
from tentacles.pipelines.deep import make_deep
from tentacles.pipelines.summarize import make_summarize
from tentacles.pipelines.filtering import filter_items, filter_stats
from tentacles.pipelines.models import PipelineOutcome, PipelineQuery, PipelineView, FilterBatch
from tentacles.store import Store
from tentacles.schemas import Completion
from tentacles.tasks import Task
from tentacles.timeutil import parse_duration, to_utc

log = logging.getLogger(__name__)


@dataclass
class _Operation:
    task: Task
    token: str
    started_at: datetime
    outcome: PipelineOutcome
    completed: bool = False
    cost: float | None = None


class PipelineManager:
    def __init__(self, config: AppConfig, store: Store, models: ModelRegistry,
                 clock: Callable[[], datetime]):
        self._config, self._store, self._models, self._clock = config, store, models, clock
        self._specs = {spec.id: spec for spec in config.pipelines}
        self._tasks = {(kind, spec.id): Task(kind, spec.id,
                            spec.filter_schedule if kind == 'filter' and spec.filter_schedule else spec.schedule,
                            config.app.timezone, spec.enabled and (kind != 'filter' or spec.filter_schedule is not None))
                       for kind in ('filter', 'pipeline') for spec in config.pipelines}

    def initialize(self) -> None:
        self._store.sync_pipeline_schedules(list(self._tasks.values()), to_utc(self._clock()))

    def scheduled_tasks(self) -> list[Task]:
        return [task for task in self._tasks.values() if task.enabled]

    def spec(self, pipeline_id: str) -> PipelineSpec:
        if pipeline_id not in self._specs:
            raise NotFound(f'unknown pipeline {pipeline_id!r}')
        return self._specs[pipeline_id]

    def pending_counts(self, now: datetime) -> dict[str, int]:
        counts = {}
        for spec in self._specs.values():
            persona = self._store.get_persona(spec.persona)
            assert persona is not None
            counts[spec.id] = self._store.pending_count(self._query(spec, persona, now, spec.processor.max_items))
        return counts

    def list_pipelines(self) -> list[PipelineView]:
        views = []
        now = to_utc(self._clock())
        pending = self.pending_counts(now)
        for spec in self._specs.values():
            task = self._tasks['pipeline', spec.id]
            filter_task = self._tasks['filter', spec.id]
            state = self._store.get_task_state(task)
            filter_state = self._store.get_task_state(filter_task)
            views.append(PipelineView(id=spec.id, name=spec.name, persona=spec.persona,
                sources=spec.sources, schedule=spec.schedule, enabled=spec.enabled, processor=spec.processor.type,
                pending=pending[spec.id], filter_schedule=spec.filter_schedule,
                next_run_at=state.next_run(task) if task.enabled else None,
                filter_next_run_at=filter_state.next_run(filter_task) if filter_task.enabled else None,
                last_run_at=state.last_run_at, last_status=state.last_status, last_error=state.last_error))
        return views

    def _query(self, spec: PipelineSpec, persona: PersonaView, now: datetime, limit: int) -> PipelineQuery:
        return PipelineQuery(spec.id, spec.sources, persona.version, now - parse_duration(spec.window), now, limit)

    async def _execute(self, spec: PipelineSpec, kind: str,
                       work: Callable[[_Operation], Awaitable[None]], *, due_only: bool = False,
                       on_finished: Callable[[PipelineOutcome], None] | None = None) -> PipelineOutcome | None:
        task = self._tasks[kind, spec.id]
        operation = _Operation(task, uuid4().hex, to_utc(self._clock()),
                               PipelineOutcome(pipeline_id=spec.id, status='ok'))
        needs_cleanup, not_due = True, False
        try:
            claim = await run_store_call(self._store.claim_task, task, operation.token,
                                         operation.started_at, force=not due_only)
            if claim.status != 'ready':
                needs_cleanup = False
                if claim.status == 'not_due':
                    not_due = True
                    return None
                operation.outcome.status = 'busy'
            else:
                assert claim.run_id is not None
                operation.outcome.run_id = claim.run_id
                await work(operation)
        except BaseException as exc:
            if not operation.completed:
                operation.outcome.status = 'error'
                operation.outcome.error = f'pipeline operation failed ({type(exc).__name__})'
                if operation.outcome.run_id:
                    try:
                        await run_store_call(self._store.finish_run, operation.outcome.run_id, status='failed',
                            now=to_utc(self._clock()), error=operation.outcome.error,
                            stats=operation.outcome.stats, cost=operation.cost)
                    except Exception as failure:
                        log.warning('could not finish pipeline run (%s)', type(failure).__name__)
            if not isinstance(exc, Exception):
                raise
        finally:
            try:
                if needs_cleanup and not operation.completed:
                    try:
                        await run_store_call(self._store.finish_task, task, operation.token, to_utc(self._clock()))
                    except Exception as failure:
                        log.warning('could not release pipeline lease (%s)', type(failure).__name__)
            finally:
                if on_finished is not None and not not_due:
                    on_finished(operation.outcome)
        return operation.outcome

    async def filter(self, pipeline_id: str, *, persona: PersonaView | None = None, reevaluate: bool = False,
                     due_only: bool = False, on_finished: Callable[[PipelineOutcome], None] | None = None,
                     since: datetime | None = None) -> PipelineOutcome | None:
        spec = self.spec(pipeline_id)

        async def work(op: _Operation) -> None:
            current = persona or await run_store_call(self._store.get_persona, spec.persona)
            assert current is not None
            query = self._query(spec, current, op.started_at, spec.max_items)
            if since is not None:
                query = replace(query, since=to_utc(since))
            candidates = await run_store_call(self._store.filter_candidates,
                query,
                reevaluate=reevaluate, run_id=op.outcome.run_id)
            batch = FilterBatch([], [])
            try:
                await filter_items(candidates, current, spec, op.started_at, self._models, batch)
            finally:
                stats = filter_stats(batch, len(candidates))
                op.cost, op.outcome.stats = stats['cost'], stats
            results = batch.evaluations
            if batch.error is not None:
                op.outcome.status, op.outcome.error = 'error', batch.error

            def commit() -> None:
                self._store.save_evaluations(results, op.outcome.run_id, to_utc(self._clock()), stats, cost=op.cost,
                                             error=batch.error)
                op.completed = True
            await run_store_call(commit)
        return await self._execute(spec, 'filter', work, due_only=due_only, on_finished=on_finished)

    async def run(self, pipeline_id: str, *, due_only: bool = False) -> PipelineOutcome | None:
        spec = self.spec(pipeline_id)

        async def work(op: _Operation) -> None:
            persona = await run_store_call(self._store.get_persona, spec.persona)
            assert persona is not None
            def record_filter(outcome: PipelineOutcome) -> None:
                op.outcome.stats = {**outcome.stats, 'filter_status': outcome.status,
                                    'filter_run_id': outcome.run_id}
                op.cost = outcome.stats.get('cost')
            filtered = await self.filter(pipeline_id, persona=persona, on_finished=record_filter)
            assert filtered is not None
            if filtered.status == 'error':
                raise UpstreamError('pipeline filter failed')
            selected = await run_store_call(self._store.pending_items,
                self._query(spec, persona, op.started_at, spec.processor.max_items))
            op.outcome.stats['processor_candidates'] = len(selected)
            if not selected:
                op.outcome.status = 'no_new'
                op.outcome.stats['no_new'] = True

                def finish_empty() -> None:
                    self._store.save_reports([], [], op.outcome.run_id, to_utc(self._clock()), op.outcome.stats, cost=op.cost)
                    op.completed = True
                await run_store_call(finish_empty)
                return
            evaluations = [evaluation for _, evaluation in selected]
            op.outcome.stats['consumed'] = 0
            def record_completion(result: Completion) -> None:
                if result.cost is not None:
                    op.cost = (op.cost or 0) + result.cost
                previous = op.outcome.stats.get('usage', {})
                op.outcome.stats['usage'] = {key: previous.get(key, 0) + value
                                            for key, value in result.usage.model_dump().items()}
                op.outcome.stats['cost'] = op.cost
                op.outcome.stats['model'] = result.model
            if spec.processor.type == 'deep':
                reports = await make_deep(self._config, spec, persona, selected, self._models,
                                          self._clock, record_completion)
            elif spec.processor.type == 'none':
                reports = []
            else:
                make_report = make_summarize if spec.processor.type == 'summarize' else make_digest
                reports = [await make_report(self._config, spec, persona, selected, self._models,
                                              self._clock, record_completion)]

            def publish() -> None:
                success = {**op.outcome.stats, 'consumed': len(selected), 'report_ids': [r.id for r in reports]}
                self._store.save_reports(reports, evaluations, op.outcome.run_id, to_utc(self._clock()),
                                         success, cost=op.cost)
                op.outcome.stats = success
                op.outcome.report_id = reports[0].id if reports else None
                op.completed = True
            await run_store_call(publish)
        return await self._execute(spec, 'pipeline', work, due_only=due_only)
