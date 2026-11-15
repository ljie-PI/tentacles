"""Service facade: the one entry point both adapters (CLI, REST API) call."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Mapping, Any
from zoneinfo import ZoneInfo

import httpx

from tentacles.archive import ArchiveManager
from tentacles.config import AppConfig, load_config
from tentacles.config_validation import validate_config
from tentacles.errors import ConfigError, ConfigValidationError, NotFound, UpstreamError
from tentacles.net import HttpPool
from tentacles.llm.registry import ModelRegistry
from tentacles.llm.config import Stage
from tentacles.llm.json import extract_json
from tentacles.schemas import ConfigCheck, ErrorEventDetail, ErrorEventView, FetchOutcome, ItemDetail, ItemSearchView, ItemView, Page, RunDetail, RunView, SourceView, TaskView, ModelView, Completion, ModelTest, TickOutcome, StorageArchive, StorageReindex, StorageStatus
from tentacles.store import ItemHit, ItemQuery, RunErrorQuery, Store
from tentacles.personas.manager import PersonaManager
from tentacles.personas.models import FeedbackView, PersonaProposal, PersonaView
from tentacles.pipelines.models import PipelineOutcome, ReportView, PipelineView
from tentacles.scheduler import Scheduler
from tentacles.timeutil import parse_since, utcnow, to_utc
from tentacles.sources.imap_client import ImapFactory, connect_imap

class TentaclesService:
    def __init__(self, config: AppConfig, store: Store, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], datetime] = utcnow, imap_factory: ImapFactory = connect_imap):
        try:
            errors = validate_config(config, {persona.name for persona in store.list_personas()})
            if errors:
                raise ConfigValidationError(errors)
        except BaseException:
            store.close()
            raise
        self.config = config
        self.store = store
        self._clock = clock
        self.archive = ArchiveManager(config, store, clock=clock)
        self._model_http = HttpPool(transport)
        self.models = ModelRegistry(config.models, config.defaults, self._model_http, clock=clock)
        self.personas = PersonaManager(store, self.models, clock, config.persona_update)
        try:
            self.scheduler = Scheduler(config, store, self.models, clock,
                                       transport=transport, imap_factory=imap_factory)
            self.pipelines = self.scheduler.pipelines
            self.personas.seed(config)
            self.scheduler.initialize()
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
        errors = validate_config(self.config, {persona.name for persona in self.store.list_personas()})
        if errors:
            raise ConfigValidationError(errors)
        return ConfigCheck(ok=True, path=str(self.config.path), sources=len(self.config.sources))

    # ---- pipelines ----------------------------------------------------------

    def list_pipelines(self) -> list[PipelineView]:
        return self.scheduler.list_pipelines()

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
        if not 1 <= limit <= 500 or not 0 <= offset <= 2**63 - 1:
            raise ConfigError('reports limit must be 1–500 and offset between 0 and 9223372036854775807')
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

    async def evolve_persona(self, name: str, *, instruction: str = '', dry_run: bool = False,
                             expected_version: int | None = None, use_feedback: bool = True) -> PersonaProposal | PersonaView:
        return await self.personas.evolve(name, instruction=instruction, dry_run=dry_run, expected_version=expected_version, use_feedback=use_feedback)

    async def apply_persona(self, proposal: PersonaProposal | dict[str, Any]) -> PersonaView:
        return await self.personas.apply(proposal)

    async def rollback_persona(self, name: str, version: int, *, note: str | None = None,
                               expected_version: int | None = None) -> PersonaView:
        return await self.personas.rollback(name, version, note, expected_version=expected_version)

    async def set_persona(self, name: str, *, profile: str | None = None, skip_tags: bool = False,
                          include_add: list[str] | None = None, include_remove: list[str] | None = None,
                          exclude_add: list[str] | None = None, exclude_remove: list[str] | None = None,
                          hard_exclude_add: list[str] | None = None, hard_exclude_remove: list[str] | None = None,
                          note: str | None = None, expected_version: int | None = None) -> PersonaView:
        return await self.personas.set(name, profile=profile, skip_tags=skip_tags,
                                       include_add=include_add, include_remove=include_remove,
                                       exclude_add=exclude_add, exclude_remove=exclude_remove,
                                       hard_exclude_add=hard_exclude_add, hard_exclude_remove=hard_exclude_remove,
                                       note=note, expected_version=expected_version)

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
        return self.scheduler.list_sources()

    async def fetch_sources(self, ids: list[str] | None = None) -> list[FetchOutcome]:
        """Fetch the named sources (even disabled ones), or every enabled source."""
        return await self.scheduler.fetch_sources(ids)

    async def tick(self) -> list[TickOutcome]:
        """Drain concurrent fetches, then due filters, then due pipeline runs."""
        return await self.scheduler.tick()

    def scheduler_status(self) -> list[TaskView]:
        return self.scheduler.status()

    # ---- items --------------------------------------------------------------

    def list_items(self, *, sources: list[str] | None = None, kinds: list[str] | None = None,
                   since: str | datetime | None = None, until: str | datetime | None = None,
                   search: str | None = None, limit: int = 50, offset: int = 0,
                   pipeline: str | None = None, persona: str | None = None, passed: bool | None = None,
                   min_score: float | None = None, topic: str | None = None,
                   sort: str = 'time', include_archive: bool = False) -> Page[ItemView]:
        q = self._item_query(sources=sources, kinds=kinds, since=since, until=until, search=search,
                             limit=limit, offset=offset, pipeline=pipeline, persona=persona,
                             passed=passed, min_score=min_score, topic=topic, sort=sort)
        hits, total = self._item_hits(q, include_archive)
        views = [ItemView.of(hit.item, hit.evaluation, snippet=hit.snippet or None, archive_year=year)
                 for year, hit in hits]
        return Page[ItemView](items=views, total=total, limit=q.limit, offset=q.offset)

    def search_items(self, query: str, *, sources: list[str] | None = None, kinds: list[str] | None = None,
                     since: str | datetime | None = None, until: str | datetime | None = None,
                     limit: int = 50, offset: int = 0, pipeline: str | None = None,
                     persona: str | None = None, passed: bool | None = None, min_score: float | None = None,
                     topic: str | None = None, sort: str = 'relevance',
                     include_archive: bool = False) -> Page[ItemSearchView]:
        """Rank Items against one query; the same filters as `list_items` apply."""
        if not query.strip():
            raise ConfigError('search query must not be empty')
        q = self._item_query(sources=sources, kinds=kinds, since=since, until=until, search=query,
                             limit=limit, offset=offset, pipeline=pipeline, persona=persona,
                             passed=passed, min_score=min_score, topic=topic, sort=sort)
        hits, total = self._item_hits(q, include_archive)
        views = [ItemSearchView.of(hit.item, hit.evaluation, snippet=hit.snippet,
                                   bm25_rank=hit.bm25_rank, archive_year=year)
                 for year, hit in hits]
        return Page[ItemSearchView](items=views, total=total, limit=q.limit, offset=q.offset)

    def _item_hits(self, q: ItemQuery, include_archive: bool) -> tuple[list[tuple[int | None, ItemHit]], int]:
        """热库结果；`include_archive` 时与各年冷库归并后再分页。"""
        if not include_archive:
            hits, total = self.store.list_items(q)
            return [(None, hit) for hit in hits], total
        return self.archive.merge_items(q)

    def _item_query(self, *, sources: list[str] | None, kinds: list[str] | None,
                    since: str | datetime | None, until: str | datetime | None, search: str | None,
                    limit: int, offset: int, pipeline: str | None, persona: str | None,
                    passed: bool | None, min_score: float | None, topic: str | None,
                    sort: str) -> ItemQuery:
        if sort == 'relevance':
            order: Literal['time', 'relevance'] = 'relevance'
        elif sort == 'time':
            order = 'time'
        else:
            raise ConfigError("sort must be 'time' or 'relevance'")
        if passed is not None and pipeline is None:
            raise ConfigError('passed/rejected filtering requires a pipeline')
        if pipeline is not None and persona is not None:
            raise ConfigError('choose either pipeline or persona')
        if min_score is not None and not 0 <= min_score <= 1:
            raise ConfigError('min-score must be between 0 and 1')
        if pipeline is not None:
            self._known_pipeline(pipeline)
        pipeline_ids = None
        if persona is not None:
            if persona not in {p.name for p in self.store.list_personas()}:
                raise NotFound(f"unknown persona {persona!r}")
            pipeline_ids = sorted(spec.id for spec in self.config.pipelines if spec.persona == persona)
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        if not 0 <= offset <= 2**63 - 1:
            raise ConfigError("offset must be between 0 and 9223372036854775807")
        if sources:
            known = {s.id for s in self.config.sources} | self.store.source_ids()
            for s in sources:  # 已从配置移除的源仍可按 id 查询历史条目
                if s not in known:
                    raise NotFound(f"unknown source {s!r}")
        # relevance 只在有检索词时才有意义（BM25 或标题命中优先），否则静默按时间排会误导调用方
        if sort == 'relevance' and not search:
            raise ConfigError('relevance sorting requires a search query')
        return ItemQuery(source_ids=sources or None, kinds=kinds or None, since=self._time(since),
                         until=self._time(until), search=search or None, limit=limit, offset=offset,
                         pipeline_id=pipeline, pipeline_ids=pipeline_ids, passed=passed,
                         min_score=min_score, topic=topic or None, sort=order)

    def get_item(self, item_id: str, *, include_archive: bool = False) -> ItemDetail:
        """单条详情；热库没有时，`include_archive` 会到各年冷库依次查找。"""
        year: int | None = None
        item = self.store.get_item(item_id)
        if item is not None:
            latest, evaluations = (self.store.latest_evaluation(item_id),
                                   self.store.item_evaluations(item_id))
        elif include_archive and (found := self.archive.find_item(item_id)) is not None:
            year, item, latest, evaluations = found
        else:
            raise NotFound(f"unknown item {item_id!r}")
        return ItemDetail.model_validate({**item.model_dump(), **(latest.model_dump() if latest else {}),
                                          "evaluations": evaluations, "archive_year": year})

    def submit_feedback(self, item_id: str, *, rating: int, persona: str | None = None,
                        note: str = '') -> FeedbackView:
        return self.personas.submit_feedback(item_id, rating=rating, persona=persona, note=note)

    def get_feedback(self, item_id: str, *, persona: str | None = None) -> FeedbackView | None:
        return self.personas.get_feedback(item_id, persona=persona)

    # ---- storage ------------------------------------------------------------

    def storage_status(self) -> StorageStatus:
        return self.archive.status()

    def storage_reindex(self, *, optimize: bool = False, include_archive: bool = False) -> StorageReindex:
        return self.archive.reindex(optimize=optimize, include_archive=include_archive)

    def storage_archive(self, *, before: str | None = None, dry_run: bool = False,
                        vacuum: bool = False) -> StorageArchive:
        """把超过保留期的条目按自然年搬进冷库；`--dry-run` 纯只读。"""
        if before is not None and not before.strip():
            raise ConfigError('--before must be a duration like 365d or an ISO date')
        if vacuum and dry_run:
            raise ConfigError('--vacuum cannot be combined with --dry-run')
        return self.archive.archive(before=before, dry_run=dry_run, vacuum=vacuum)

    # ---- runs ---------------------------------------------------------------

    def list_runs(self, *, kind: str | None = None, target: str | None = None,
                  limit: int = 50) -> list[RunView]:
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        return [RunView.of(record) for record in self.store.list_runs(kind, target, limit)]

    def get_run(self, run_id: int) -> RunDetail:
        record = self.store.get_run(run_id)
        if record is None:
            raise NotFound(f'unknown run {run_id}')
        errors, _ = self.store.list_run_errors(RunErrorQuery(run_id=run_id, limit=500))
        return RunDetail(**RunView.of(record).model_dump(),
                         errors=[ErrorEventView.of(error) for error in errors])

    # ---- diagnostics ledger -------------------------------------------------

    def list_errors(self, *, unresolved: bool = False, source: str | None = None,
                    pipeline: str | None = None, phase: str | None = None,
                    error_code: str | None = None, run_id: int | None = None,
                    since: str | datetime | None = None, until: str | datetime | None = None,
                    limit: int = 50, offset: int = 0) -> Page[ErrorEventView]:
        if not 1 <= limit <= 500:
            raise ConfigError("limit must be between 1 and 500")
        if not 0 <= offset <= 2**63 - 1:
            raise ConfigError("offset must be between 0 and 9223372036854775807")
        q = RunErrorQuery(unresolved=unresolved, source_id=source, pipeline_id=pipeline, phase=phase,
                          error_code=error_code, run_id=run_id, since=self._time(since),
                          until=self._time(until), limit=limit, offset=offset)
        records, total = self.store.list_run_errors(q)
        return Page[ErrorEventView](items=[ErrorEventView.of(record) for record in records],
                                    total=total, limit=limit, offset=offset)

    def get_error(self, error_id: int) -> ErrorEventDetail:
        record = self.store.get_run_error(error_id)
        if record is None:
            raise NotFound(f'unknown error {error_id}')
        return ErrorEventDetail(**ErrorEventView.of(record).model_dump(),
                                debug_context=record.debug_context)

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
