"""Service facade: the one entry point both adapters (CLI, REST API) call."""

from __future__ import annotations

import asyncio
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Any, Literal
from zoneinfo import ZoneInfo
from uuid import uuid4

import httpx

from tentacles.config import AppConfig, SourceSpec, load_config
from tentacles.errors import ConfigError, NotFound, UpstreamError
from tentacles.net import HttpPool
from tentacles.llm.registry import ModelRegistry
from tentacles.llm.config import Stage
from tentacles.llm.json import extract_json
from tentacles.schemas import ConfigCheck, FetchOutcome, ItemDetail, ItemView, Page, RunView, SourceView, TaskView, ModelView, Completion, ModelTest, TickOutcome
from tentacles.sources.runner import fetch_source
from tentacles.asyncutil import run_store_call, gather_draining
from tentacles.store import ItemQuery, Store
from tentacles.personas.manager import PersonaManager
from tentacles.personas.models import FeedbackView, PersonaProposal, PersonaView
from tentacles.pipelines.manager import PipelineManager
from tentacles.pipelines.models import PipelineOutcome, ReportView, PipelineView
from tentacles.timeutil import parse_since, utcnow, to_utc
from tentacles.tasks import Task
from tentacles.sources.imap_client import ImapFactory, connect_imap

log = logging.getLogger(__name__)

FETCH_CONCURRENCY = 4


class TentaclesService:
    def __init__(self, config: AppConfig, store: Store, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] = utcnow, imap_factory: ImapFactory = connect_imap):
        self.config = config
        self.store = store
        self._transport = transport
        self._clock = clock
        self._imap_factory = imap_factory
        self._model_http = HttpPool(transport)
        self.models = ModelRegistry(config.models, config.defaults, self._model_http, clock=clock)
        self.personas = PersonaManager(store, self.models, clock, config.persona_update)
        self._tasks = {s.id: Task('fetch', s.id, s.schedule, config.app.timezone, s.enabled)
                       for s in config.sources}
        try:
            self.pipelines = PipelineManager(config, store, self.models, clock)
            self.personas.seed(config)
            self.pipelines.initialize()
            store.register_tasks(list(self._tasks.values()), to_utc(clock()))
        except BaseException:
            store.close()
            raise

    @classmethod
    def open(cls, config_path: Path | str | None = None, *,
             transport: httpx.AsyncBaseTransport | None = None,
             env: Mapping[str, str] | None = None,
             clock: Callable[[], datetime] = utcnow, snapshot: bool = False,
             imap_factory: ImapFactory = connect_imap) -> "TentaclesService":
        """Open normally, or isolate all initialization and writes in an in-memory snapshot."""
        config = load_config(config_path, env=env)
        return cls(config, Store(config.db_path, snapshot=snapshot), transport=transport, clock=clock,
                   imap_factory=imap_factory)

    async def aclose(self) -> None:
        try:
            await self._model_http.aclose()
        finally:
            self.store.close()

    # ---- config -------------------------------------------------------------

    def check_config(self) -> ConfigCheck:
        # 能打开 Service 就说明配置已通过全部校验
        return ConfigCheck(ok=True, path=str(self.config.path), sources=len(self.config.sources))

    # ---- pipelines ----------------------------------------------------------

    def list_pipelines(self) -> list[PipelineView]:
        return self.pipelines.list_pipelines()

    async def filter_pipeline(self, pipeline_id: str, *, reevaluate: bool = False,
                              since: str | datetime | None = None) -> PipelineOutcome:
        start = self._time(since)
        if start is not None and (not reevaluate or to_utc(start) > to_utc(self._clock())):
            raise ConfigError('since requires reevaluate and must not be in the future')
        result = await self.pipelines.filter(pipeline_id, reevaluate=reevaluate, since=start)
        assert result is not None  # manual operations bypass the due check
        return result

    async def run_pipeline(self, pipeline_id: str) -> PipelineOutcome:
        result = await self.pipelines.run(pipeline_id)
        assert result is not None
        return result

    def list_reports(self, *, pipeline: str | None = None, limit: int = 50, offset: int = 0) -> Page[ReportView]:
        if not 1 <= limit <= 500 or offset < 0:
            raise ConfigError('reports limit must be 1–500 and offset nonnegative')
        if pipeline is not None:
            self._known_pipeline(pipeline)
        reports, total = self.store.list_reports(pipeline, limit, offset)
        return Page[ReportView](items=reports, total=total, limit=limit, offset=offset)

    def _known_pipeline(self, pipeline_id: str) -> None:
        if pipeline_id not in {p.id for p in self.config.pipelines} | self.store.pipeline_ids():
            raise NotFound(f'unknown pipeline {pipeline_id!r}')

    def get_report(self, report_id: str) -> ReportView:
        report = self.store.get_report(report_id)
        if report is None:
            raise NotFound(f'unknown report {report_id!r}')
        return report

    # ---- personas -----------------------------------------------------------

    def list_personas(self) -> list[PersonaView]:
        return self.personas.list_personas()

    def get_persona(self, name: str, version: int | None = None) -> PersonaView:
        return self.personas.get(name, version)

    def persona_history(self, name: str) -> list[PersonaView]:
        return self.personas.history(name)

    def export_persona(self, name: str, version: int | None = None) -> str:
        return self.personas.export(name, version)

    async def evolve_persona(self, name: str, *, instruction: str = '', dry_run: bool = False) -> PersonaProposal | PersonaView:
        return await self.personas.evolve(name, instruction=instruction, dry_run=dry_run)

    async def apply_persona(self, proposal: PersonaProposal | dict[str, Any]) -> PersonaView:
        return await self.personas.apply(proposal)

    async def rollback_persona(self, name: str, version: int, *, note: str | None = None) -> PersonaView:
        return await self.personas.rollback(name, version, note)

    async def set_persona(self, name: str, *, profile: str | None = None, skip_tags: bool = False,
                          include_add: list[str] | None = None, include_remove: list[str] | None = None,
                          exclude_add: list[str] | None = None, exclude_remove: list[str] | None = None,
                          note: str | None = None) -> PersonaView:
        return await self.personas.set(name, profile=profile, skip_tags=skip_tags,
                                       include_add=include_add, include_remove=include_remove,
                                       exclude_add=exclude_add, exclude_remove=exclude_remove, note=note)

    # ---- models -------------------------------------------------------------

    def list_models(self) -> list[ModelView]:
        return [ModelView(name=name, provider=spec.provider, kind=spec.kind, model=spec.model,
                          base_url=spec.endpoint, key_configured=bool(spec.api_key.get_secret_value()))
                for name, spec in self.config.models.items()]

    async def complete_model(self, messages: list[dict[str, str]], *, name: str | None = None,
                             stage: Stage = 'process', system: str | None = None,
                             max_tokens: int | None = None,
                             temperature: float | None = None, json_output: bool = False) -> Completion:
        resolved = self.models.resolve(stage, name)
        result = await self.models.chat(resolved).complete(messages, system=system,
                                                          max_tokens=max_tokens, temperature=temperature)
        if json_output:
            try:
                result.data = extract_json(result.text)
            except ValueError:
                raise UpstreamError(f'model {resolved!r}: no valid JSON in model output') from None
        return result

    async def test_model(self, name: str) -> ModelTest:
        spec = self.models.spec(name)
        started_at = self._clock()
        try:
            if spec.kind == 'chat':
                result = await self.models.chat(name).complete(
                    [{'role': 'user', 'content': 'Reply with OK to confirm you are available.'}])
                sample, model, usage, cost = result.text, result.model, result.usage, result.cost
            else:
                decision = await self.models.decision(name).decide(
                    'The model is responding to a connectivity check.',
                    {'available': {'type': 'noul', 'instructions': 'Is this a connectivity check?',
                                   'criteria': {'true': 'Connectivity check', 'false': 'Other request'}}})
                sample, model, usage, cost = (json.dumps(decision.answers, ensure_ascii=False),
                                              decision.model, decision.usage, decision.cost)
        except UpstreamError as exc:
            return ModelTest(name=name, ok=False, latency_ms=self._model_latency(started_at),
                             error=self.models.redact(exc.message))
        return ModelTest(name=name, ok=True, latency_ms=self._model_latency(started_at),
                         sample=self.models.redact(sample)[:500], model=model, usage=usage, cost=cost)

    def _model_latency(self, started_at: datetime) -> float:
        return max(0.0, (self._clock() - started_at).total_seconds() * 1000)

    # ---- sources ------------------------------------------------------------

    def list_sources(self) -> list[SourceView]:
        views = []
        for s in self.config.sources:
            st = self.store.get_source_state(s.id)
            views.append(SourceView(
                id=s.id, type=s.type, name=s.name, schedule=s.schedule, enabled=s.enabled,
                last_fetch_at=st.last_fetch_at, last_status=st.last_status,
                last_error=st.last_error, total_items=st.total_items,
                next_run_at=self.store.get_task_state(self._tasks[s.id]).next_run(self._tasks[s.id])
                if s.enabled else None,
            ))
        return views

    async def fetch_sources(self, ids: list[str] | None = None) -> list[FetchOutcome]:
        """Fetch the named sources (even disabled ones), or every enabled source."""
        if ids:
            specs = [self.config.source(i) for i in dict.fromkeys(ids)]
        else:
            specs = [s for s in self.config.sources if s.enabled]
        return await self._fetch_batch(specs, due_only=False)

    async def tick(self) -> list[TickOutcome]:
        """Drain concurrent fetches, then due filters, then due pipeline runs."""
        fetched = await self._fetch_batch([s for s in self.config.sources if s.enabled], due_only=True)
        outcomes = [TickOutcome.of_fetch(outcome) for outcome in fetched]
        busy_sources = {outcome.source_id for outcome in fetched if outcome.status == 'busy'}
        deferred = {spec.id for spec in self.config.pipelines if busy_sources.intersection(spec.sources)}
        for task in self.pipelines.scheduled_tasks():
            # Another tick may still own an upstream fetch. Do not advance a
            # dependent schedule against an incomplete snapshot of its inputs.
            if task.target in deferred:
                continue
            kind: Literal['filter', 'pipeline'] = 'filter' if task.kind == 'filter' else 'pipeline'
            result = (await self.pipelines.filter(task.target, due_only=True) if kind == 'filter'
                      else await self.pipelines.run(task.target, due_only=True))
            if result is not None:
                outcomes.append(TickOutcome.of_pipeline(kind, result))
        return outcomes

    def scheduler_status(self) -> list[TaskView]:
        now = to_utc(self._clock())
        views = []
        pending = self.pipelines.pending_counts(now)
        for task in [*self._tasks.values(), *self.pipelines.scheduled_tasks()]:
            state = self.store.get_task_state(task)
            views.append(TaskView(
                kind=task.kind, target=task.target, schedule=task.schedule, enabled=task.enabled,
                last_run_at=state.last_run_at,
                next_run_at=state.next_run(task) if task.enabled else None,
                running=state.running(now), last_status=state.last_status, last_error=state.last_error,
                pending=pending.get(task.target) if task.kind != 'fetch' else None))
        return views

    async def _fetch_task(self, spec: SourceSpec, http: HttpPool, *, due_only: bool) -> FetchOutcome | None:
        task = self._tasks[spec.id]
        token, started_at = uuid4().hex, to_utc(self._clock())
        outcome: FetchOutcome | None = None
        failure: Exception | None = None
        run_id, needs_cleanup = 0, True
        try:
            claim = await run_store_call(self.store.claim_task, task, token, started_at,
                                         force=not due_only)
            if claim.status != 'ready':
                needs_cleanup = False
                if claim.status == 'busy':
                    outcome = FetchOutcome(source_id=spec.id, run_id=0, status='busy', new=0, dup=0)
            else:
                run_id = claim.run_id or 0
                outcome = await fetch_source(spec, store=self.store, http=http, clock=self._clock,
                                             run_id=claim.run_id, started_at=started_at, imap_factory=self._imap_factory,
                                             timezone=self.config.app.timezone, models=self.models)
        except Exception as exc:
            failure = exc
        finally:
            if needs_cleanup:
                try:
                    # The token is known even if cancellation arrived after acquisition committed.
                    await run_store_call(self.store.finish_task, task, token, to_utc(self._clock()))
                except Exception as exc:
                    log.warning('could not finish task %s: %s', spec.id, exc)
                    failure = failure or exc
        if failure is not None:
            error = f'{type(failure).__name__}: {failure}'
            log.warning('fetch %s failed: %s', spec.id, error)
            return FetchOutcome(source_id=spec.id, run_id=run_id, status='error',
                                new=outcome.new if outcome else 0, dup=outcome.dup if outcome else 0,
                                error=error, stats=outcome.stats if outcome else {})
        return outcome

    async def _fetch_batch(self, specs: list[SourceSpec], *, due_only: bool) -> list[FetchOutcome]:
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        async with HttpPool(self._transport) as http:
            async def one(spec):
                async with sem:
                    return await self._fetch_task(spec, http, due_only=due_only)

            tasks = [asyncio.create_task(one(s)) for s in specs]
            return [result for result in await gather_draining(tasks) if result is not None]

    # ---- items --------------------------------------------------------------

    def list_items(self, *, sources: list[str] | None = None, kinds: list[str] | None = None,
                   since: str | datetime | None = None, until: str | datetime | None = None,
                   search: str | None = None, limit: int = 50, offset: int = 0,
                   pipeline: str | None = None, passed: bool | None = None) -> Page[ItemView]:
        if passed is not None and pipeline is None:
            raise ConfigError('passed/rejected filtering requires a pipeline')
        if pipeline is not None:
            self._known_pipeline(pipeline)
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        if not 0 <= offset <= 2**63 - 1:
            raise ConfigError("offset must be between 0 and 9223372036854775807")
        if sources:
            known = {s.id for s in self.config.sources} | self.store.source_ids()
            for s in sources:  # 已从配置移除的源仍可按 id 查询历史条目
                if s not in known:
                    raise NotFound(f"unknown source {s!r}")
        q = ItemQuery(source_ids=sources or None, kinds=kinds or None, since=self._time(since),
                      until=self._time(until), search=search or None, limit=limit, offset=offset, pipeline_id=pipeline, passed=passed)
        items, total = self.store.list_items(q)
        views = [ItemView.model_validate({**item.model_dump(), **(evaluation.model_dump() if evaluation else {})})
                 for item, evaluation in items]
        return Page[ItemView](items=views, total=total, limit=limit, offset=offset)

    def get_item(self, item_id: str) -> ItemDetail:
        item = self.store.get_item(item_id)
        if item is None:
            raise NotFound(f"unknown item {item_id!r}")
        return ItemDetail.model_validate({**item.model_dump(), "evaluations": self.store.item_evaluations(item_id)})

    def submit_feedback(self, item_id: str, *, rating: int, persona: str | None = None,
                        note: str = '') -> FeedbackView:
        if type(rating) is not int or rating not in (-1, 1):
            raise ConfigError('feedback rating must be +1 or -1')
        self.get_item(item_id)
        if persona is None:
            personas = self.list_personas()
            persona = personas[0].name if len(personas) == 1 else 'default'
        current = self.get_persona(persona)
        return self.store.submit_feedback(item_id, current.name, rating, note, self._clock())

    # ---- runs ---------------------------------------------------------------

    def list_runs(self, *, kind: str | None = None, target: str | None = None,
                  limit: int = 50) -> list[RunView]:
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        return [RunView.model_validate(r, from_attributes=True)
                for r in self.store.list_runs(kind, target, limit)]

    def _time(self, value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        try:
            if isinstance(value, datetime):
                return to_utc(value)
            return parse_since(value, self._clock(), ZoneInfo(self.config.app.timezone))
        except OverflowError:
            raise ConfigError('time value is out of range') from None
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
