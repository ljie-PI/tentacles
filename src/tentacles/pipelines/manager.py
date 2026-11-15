"""Pipeline domain work; RunLifecycle owns execution and persistence coordination."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Callable

from tentacles.asyncutil import run_store_call
from tentacles.config import AppConfig
from tentacles.diagnostics import DiagnosticEvent, configured_secrets
from tentacles.errors import NotFound, UpstreamError
from tentacles.llm.registry import ModelRegistry
from tentacles.personas.models import PersonaView
from tentacles.pipelines.config import PipelineSpec
from tentacles.pipelines.filtering import filter_items, filter_stats
from tentacles.pipelines.models import Evaluation, PipelineOutcome, PipelineQuery, FilterBatch
from tentacles.pipelines.registry import PROCESSORS
from tentacles.runs import RunLifecycle, RunOperation, RunResult
from tentacles.store import RunWriter, Store
from tentacles.tasks import Task
from tentacles.timeutil import parse_duration, to_utc


class PipelineManager:
    def __init__(self, config: AppConfig, store: Store, models: ModelRegistry,
                 clock: Callable[[], datetime], *, task: Callable[[str, str], Task]):
        self._config, self._store, self._models, self._clock = config, store, models, clock
        self._runs = RunLifecycle(store, clock, secrets=configured_secrets(config))
        self._specs = {spec.id: spec for spec in config.pipelines}
        self._task = task

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

    def _query(self, spec: PipelineSpec, persona: PersonaView, now: datetime, limit: int) -> PipelineQuery:
        return PipelineQuery(spec.id, spec.sources, persona.version, now - parse_duration(spec.window), now, limit)

    @staticmethod
    def _outcome(spec: PipelineSpec, result: RunResult | None) -> PipelineOutcome | None:
        if result is None:
            return None
        reports = result.stats.get('report_ids', [])
        return PipelineOutcome(pipeline_id=spec.id, run_id=result.run_id,
            status='no_new' if result.status == 'ok' and result.stats.get('no_new') else result.status,
            report_id=reports[0] if reports else None, stats=result.stats, error=result.error,
            error_count=result.error_count, failed_targets=result.failed_targets)

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

            async def checkpoint(finished: list[Evaluation]) -> None:
                # Charge only what the stages have reported so far; the commit
                # writes the final totals once the stage metrics are merged.
                charged = [stage.metrics.cost for stage in batch.batches if stage.metrics.cost is not None]
                await op.checkpoint(lambda writer: writer.save_evaluations(finished),
                                    cost=sum(charged) if charged else None)

            batch = FilterBatch([], [], record_error=op.record_error, checkpoint=checkpoint)
            try:
                await filter_items(candidates, current, spec, op.started_at, self._models, batch)
            finally:
                op.stats.update(filter_stats(batch, len(candidates)))
                for stage in batch.batches:
                    op.metrics.merge(stage.metrics)
            if batch.error is not None:
                op.record_error(DiagnosticEvent(
                    phase='filter_stage', error_code='filter_stage_aborted', message=batch.error,
                    pipeline_id=spec.id, retryable=True,
                    debug_context={'stages': batch.stages}))
            await op.commit(lambda writer: writer.save_evaluations(batch.evaluations), error=batch.error)
        result = await self._runs.execute(self._task('filter', spec.id), work, due_only=due_only,
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
                # The child filter Run owns its own staged diagnostics; the parent
                # Run records the pointer that stopped this report batch.
                op.record_error(DiagnosticEvent(
                    phase='filter_stage', error_code='filter_stage_aborted',
                    message=filtered.error or 'pipeline filter failed', pipeline_id=spec.id,
                    retryable=True, debug_context={'filter_run_id': filtered.run_id,
                                                   'filter_error_count': filtered.error_count}))
                raise UpstreamError('pipeline filter failed')
            selected = await run_store_call(self._store.pending_items,
                self._query(spec, persona, op.started_at, spec.processor.max_items))
            op.stats['processor_candidates'] = len(selected)
            if not selected:
                op.stats['no_new'] = True
                await op.commit(lambda writer: None)
                return
            evaluations = [evaluation for _, evaluation in selected]
            op.stats['consumed'] = 0
            drafts = await PROCESSORS[spec.processor.type].process(
                self._config, spec, persona, selected, self._models, self._clock, op.metrics.record,
                record_error=op.record_error)

            # Failed SQL/commit must not leak allocated IDs into failure stats.
            publication_stats = {'consumed': len(selected), 'report_ids': []}

            def publish(writer: RunWriter) -> None:
                reports = writer.save_reports(drafts, evaluations, reports_dir=self._config.reports_dir)
                publication_stats['report_ids'] = [report.id for report in reports]

            await op.commit(publish, stats=publication_stats)
        result = await self._runs.execute(self._task('pipeline', spec.id), work, due_only=due_only,
                                         failure_message='pipeline operation failed')
        return self._outcome(spec, result)
