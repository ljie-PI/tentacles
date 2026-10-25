"""Pipeline domain work; RunLifecycle owns execution and persistence coordination."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Callable

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
from tentacles.runs import RunLifecycle, RunOperation, RunResult
from tentacles.store import Store
from tentacles.tasks import Task
from tentacles.timeutil import parse_duration, to_utc


class PipelineManager:
    def __init__(self, config: AppConfig, store: Store, models: ModelRegistry,
                 clock: Callable[[], datetime]):
        self._config, self._store, self._models, self._clock = config, store, models, clock
        self._runs = RunLifecycle(store, clock)
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

    @staticmethod
    def _outcome(spec: PipelineSpec, result: RunResult | None) -> PipelineOutcome | None:
        if result is None:
            return None
        reports = result.stats.get('report_ids', [])
        return PipelineOutcome(pipeline_id=spec.id, run_id=result.run_id,
            status='no_new' if result.status == 'ok' and result.stats.get('no_new') else result.status,
            report_id=reports[0] if reports else None, stats=result.stats, error=result.error)

    async def filter(self, pipeline_id: str, *, persona: PersonaView | None = None, reevaluate: bool = False,
                     due_only: bool = False, on_finished: Callable[[RunResult], None] | None = None,
                     since: datetime | None = None) -> PipelineOutcome | None:
        spec = self.spec(pipeline_id)

        async def work(op: RunOperation) -> None:
            current = persona or await run_store_call(self._store.get_persona, spec.persona)
            assert current is not None
            query = self._query(spec, current, op.started_at, spec.max_items)
            if since is not None:
                query = replace(query, since=to_utc(since))
            candidates = await run_store_call(self._store.filter_candidates,
                query,
                reevaluate=reevaluate, run_id=op.run_id)
            batch = FilterBatch([], [])
            try:
                await filter_items(candidates, current, spec, op.started_at, self._models, batch)
            finally:
                op.stats.update(filter_stats(batch, len(candidates)))
                for stage in batch.batches:
                    op.metrics.merge(stage.metrics)
            await op.commit(lambda writer: writer.save_evaluations(batch.evaluations), error=batch.error)
        result = await self._runs.execute(self._tasks['filter', spec.id], work, due_only=due_only,
            failure_message='pipeline operation failed', on_finished=on_finished)
        return self._outcome(spec, result)

    async def run(self, pipeline_id: str, *, due_only: bool = False) -> PipelineOutcome | None:
        spec = self.spec(pipeline_id)

        async def work(op: RunOperation) -> None:
            persona = await run_store_call(self._store.get_persona, spec.persona)
            assert persona is not None
            def record_filter(result: RunResult) -> None:
                op.stats.update(result.stats, filter_status=result.status, filter_run_id=result.run_id)
                op.metrics.merge(result.metrics)
            filtered = await self.filter(pipeline_id, persona=persona, on_finished=record_filter)
            assert filtered is not None
            if filtered.status == 'error':
                raise UpstreamError('pipeline filter failed')
            selected = await run_store_call(self._store.pending_items,
                self._query(spec, persona, op.started_at, spec.processor.max_items))
            op.stats['processor_candidates'] = len(selected)
            if not selected:
                op.stats['no_new'] = True
                await op.commit(lambda writer: writer.save_reports([], []))
                return
            evaluations = [evaluation for _, evaluation in selected]
            op.stats['consumed'] = 0
            if spec.processor.type == 'deep':
                reports = await make_deep(self._config, spec, persona, selected, self._models,
                                          self._clock, op.metrics.record)
            elif spec.processor.type == 'none':
                reports = []
            else:
                make_report = make_summarize if spec.processor.type == 'summarize' else make_digest
                reports = [await make_report(self._config, spec, persona, selected, self._models,
                                              self._clock, op.metrics.record)]

            await op.commit(lambda writer: writer.save_reports(reports, evaluations),
                            stats={'consumed': len(selected), 'report_ids': [r.id for r in reports]})
        result = await self._runs.execute(self._tasks['pipeline', spec.id], work, due_only=due_only,
                                         failure_message='pipeline operation failed')
        return self._outcome(spec, result)
