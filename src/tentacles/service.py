"""Service facade: the one entry point both adapters (CLI, REST API) call."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Any
from zoneinfo import ZoneInfo

import httpx

from tentacles.config import AppConfig, load_config
from tentacles.config_validation import validate_config
from tentacles.errors import ConfigError, ConfigValidationError, NotFound, UpstreamError
from tentacles.net import HttpPool
from tentacles.llm.registry import ModelRegistry
from tentacles.llm.config import Stage
from tentacles.llm.json import extract_json
from tentacles.schemas import ConfigCheck, FetchOutcome, ItemDetail, ItemView, Page, RunView, SourceView, TaskView, ModelView, Completion, ModelTest, TickOutcome
from tentacles.store import ItemQuery, Store
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
                          note: str | None = None, expected_version: int | None = None) -> PersonaView:
        return await self.personas.set(name, profile=profile, skip_tags=skip_tags,
                                       include_add=include_add, include_remove=include_remove,
                                       exclude_add=exclude_add, exclude_remove=exclude_remove, note=note, expected_version=expected_version)

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
        return self.personas.submit_feedback(item_id, rating=rating, persona=persona, note=note)

    def get_feedback(self, item_id: str, *, persona: str | None = None) -> FeedbackView | None:
        return self.personas.get_feedback(item_id, persona=persona)

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
