"""All scheduled Task identities, dispatch ordering and shared status views.

RunLifecycle remains the only execution protocol; this module decides which
domain operation to dispatch, not how to claim, commit or clean up a Run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import threading
from typing import Callable, Literal

import httpx

from tentacles.asyncutil import gather_draining
from tentacles.config import AppConfig, SourceSpec
from tentacles.diagnostics import configured_secrets
from tentacles.errors import TentaclesError
from tentacles.llm.registry import ModelRegistry
from tentacles.net import HttpPool
from tentacles.pipelines.manager import PipelineManager
from tentacles.pipelines.models import PipelineView
from tentacles.runs import RunLifecycle, RunOperation
from tentacles.schemas import FetchOutcome, SourceView, TaskView, TickOutcome
from tentacles.sources.imap_client import ImapFactory, connect_imap
from tentacles.sources.runner import fetch_source
from tentacles.store import Store
from tentacles.tasks import Task
from tentacles.timeutil import to_utc

FETCH_CONCURRENCY = 4


class Scheduler:
    def __init__(self, config: AppConfig, store: Store, models: ModelRegistry,
                 clock: Callable[[], datetime], *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 imap_factory: ImapFactory = connect_imap):
        self._config, self._store, self._models, self._clock = config, store, models, clock
        self._lock = threading.RLock()
        self._transport, self._imap_factory = transport, imap_factory
        self._runs = RunLifecycle(store, clock, secrets=configured_secrets(config))
        self._tasks = self._build_tasks(config)
        self.pipelines = PipelineManager(config, store, models, clock, task=self.task)

    @staticmethod
    def _build_tasks(config: AppConfig) -> dict[tuple[str, str], Task]:
        tasks = [Task('fetch', s.id, s.schedule, config.app.timezone, s.enabled) for s in config.sources]
        tasks.extend(Task(kind, spec.id,
                          spec.filter_schedule if kind == 'filter' and spec.filter_schedule else spec.schedule,
                          config.app.timezone, spec.enabled and (kind != 'filter' or spec.filter_schedule is not None))
                     for kind in ('filter', 'pipeline') for spec in config.pipelines)
        return {(task.kind, task.target): task for task in tasks}

    def _snapshot(self) -> tuple[AppConfig, dict[tuple[str, str], Task]]:
        with self._lock:
            return self._config, self._tasks

    def reload_config(self, config: AppConfig) -> None:
        tasks = self._build_tasks(config)
        secrets = configured_secrets(config)
        self._store.register_tasks(list(tasks.values()), to_utc(self._clock()))
        with self._lock:
            self._config = config
            self._tasks = tasks
            self._runs.set_secrets(secrets)
            self.pipelines.reload_config(config, secrets=secrets)

    def initialize(self) -> None:
        _, tasks = self._snapshot()
        self._store.register_tasks(list(tasks.values()), to_utc(self._clock()))

    def task(self, kind: str, target: str) -> Task:
        """The same identity is used for scheduled and forced/manual execution."""
        _, tasks = self._snapshot()
        return tasks[kind, target]

    def _views(self, tasks: dict[tuple[str, str], Task] | None = None) -> dict[tuple[str, str], TaskView]:
        if tasks is None:
            _, tasks = self._snapshot()
        now = to_utc(self._clock())
        views = {}
        for key, task in tasks.items():
            state = self._store.get_task_state(task)
            last_status, last_error = state.last_status, state.last_error
            if task.kind == 'fetch' and state.last_run_at is None and last_status is None:
                # Older fetches may have only source_state diagnostics. Fall
                # back as a pair, never reviving an old error after a Task result.
                source = self._store.get_source_state(task.target)
                last_status, last_error = source.last_status, source.last_error
            views[key] = TaskView(
                kind=task.kind, target=task.target, schedule=task.schedule, enabled=task.enabled,
                last_run_at=state.last_run_at,
                next_run_at=state.next_run(task) if task.enabled else None,
                running=state.running(now), last_status=last_status, last_error=last_error)
        return views

    def status(self) -> list[TaskView]:
        _, task_map = self._snapshot()
        views = self._views(task_map)
        pending = self.pipelines.pending_counts(to_utc(self._clock()))
        for view in views.values():
            if view.kind != 'fetch' and view.target in pending:
                view.pending = pending[view.target]
        return list(views.values())

    def list_sources(self) -> list[SourceView]:
        config, task_map = self._snapshot()
        tasks = self._views(task_map)
        views = []
        for spec in config.sources:
            state = self._store.get_source_state(spec.id)
            task = tasks['fetch', spec.id]
            views.append(SourceView(
                id=spec.id, type=spec.type, name=spec.name, schedule=spec.schedule, enabled=spec.enabled,
                last_fetch_at=state.last_fetch_at, last_status=task.last_status, last_error=task.last_error,
                total_items=state.total_items, next_run_at=task.next_run_at, running=task.running))
        return views

    def list_pipelines(self) -> list[PipelineView]:
        config, task_map = self._snapshot()
        tasks = self._views(task_map)
        pending = self.pipelines.pending_counts(to_utc(self._clock()))
        views = []
        for spec in config.pipelines:
            task, filtered = tasks['pipeline', spec.id], tasks['filter', spec.id]
            views.append(PipelineView(
                id=spec.id, name=spec.name, persona=spec.persona, sources=spec.sources,
                schedule=spec.schedule, enabled=spec.enabled, processor=spec.processor.type,
                pending=pending.get(spec.id, 0), filter_schedule=spec.filter_schedule,
                next_run_at=task.next_run_at, filter_next_run_at=filtered.next_run_at,
                running=task.running, filter_running=filtered.running,
                last_run_at=task.last_run_at, last_status=task.last_status, last_error=task.last_error))
        return views

    async def fetch_sources(self, ids: list[str] | None = None, *,
                            distill: bool = False) -> list[FetchOutcome]:
        """Explicit IDs bypass enablement and due checks, but never bypass leases.

        `distill` is the explicit `sources distill` refresh: a browser source
        explores again even though its fixed script already exists.
        """
        config, _ = self._snapshot()
        specs = ([config.source(i) for i in dict.fromkeys(ids)] if ids
                 else [s for s in config.sources if s.enabled])
        return await self._fetch_batch(specs, due_only=False, distill=distill)

    async def tick(self) -> list[TickOutcome]:
        """Drain fetches, defer busy dependencies, then drain filters and runs."""
        config, task_map = self._snapshot()
        fetched = await self._fetch_batch([s for s in config.sources if s.enabled], due_only=True)
        outcomes = [TickOutcome.of_fetch(outcome) for outcome in fetched]
        busy_sources = {outcome.source_id for outcome in fetched if outcome.status == 'busy'}
        deferred = {spec.id for spec in config.pipelines if busy_sources.intersection(spec.sources)}
        for kind in ('filter', 'pipeline'):
            for task in task_map.values():
                if task.kind != kind or not task.enabled or task.target in deferred:
                    continue
                # Never advance a dependent schedule against an incomplete fetch.
                role: Literal['filter', 'pipeline'] = 'filter' if kind == 'filter' else 'pipeline'
                result = (await self.pipelines.filter(task.target, due_only=True) if role == 'filter'
                          else await self.pipelines.run(task.target, due_only=True))
                if result is not None:
                    outcomes.append(TickOutcome.of_pipeline(role, result))
        return outcomes

    async def _fetch_task(self, spec: SourceSpec, http: HttpPool, *, due_only: bool,
                          distill: bool = False) -> FetchOutcome | None:
        config, task_map = self._snapshot()
        task = task_map.get(('fetch', spec.id)) or Task('fetch', spec.id, spec.schedule, config.app.timezone, spec.enabled)

        async def work(op: RunOperation) -> None:
            await fetch_source(spec, op, store=self._store, http=http, imap_factory=self._imap_factory,
                               timezone=config.app.timezone, models=self._models,
                               config=config, distill=distill)

        result = await self._runs.execute(task, work, due_only=due_only,
            failure_message=lambda exc: exc.message if isinstance(exc, TentaclesError)
            else f'fetch failed ({type(exc).__name__})')
        if result is None:
            return None
        # Preserve fetch's no-model adapter output; durable Run stats stay unified.
        stats = result.stats if result.metrics.models else {
            key: value for key, value in result.stats.items() if key not in {'usage', 'cost', 'models'}}
        return FetchOutcome(source_id=spec.id, run_id=result.run_id, status=result.status,
                            new=stats.get('new', 0), updated=stats.get('updated', 0), dup=stats.get('dup', 0),
                            error=result.error, stats=stats, error_count=result.error_count,
                            failed_targets=result.failed_targets)

    async def _fetch_batch(self, specs: list[SourceSpec], *, due_only: bool,
                           distill: bool = False) -> list[FetchOutcome]:
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        async with HttpPool(self._transport) as http:
            async def one(spec):
                async with sem:
                    return await self._fetch_task(spec, http, due_only=due_only, distill=distill)

            tasks = [asyncio.create_task(one(s)) for s in specs]
            return [result for result in await gather_draining(tasks) if result is not None]
