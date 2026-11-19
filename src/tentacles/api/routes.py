"""Thin routes over Service; FastAPI offloads synchronous query operations."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from tentacles.api.models import DisplaySettings, SchedulerLoopView, SourceCreate, SourceFetch, SourceUpdate
from tentacles.models import ItemKind
from tentacles.errors import Busy, UpstreamError
from tentacles.schemas import ErrorEventDetail, ErrorEventView, FetchOutcome, ItemDetail, ItemView, Page, RunDetail, RunView, SourceConfigView, SourceView, TaskView, TickOutcome
from tentacles.api.dependencies import Service


router = APIRouter()


@router.get('/sources', response_model=list[SourceView])
def sources(svc: Service):
    return svc.list_sources()


@router.post('/sources', response_model=SourceConfigView, status_code=201)
def create_source(body: SourceCreate, svc: Service):
    return svc.create_source(body)


@router.get('/sources/{source_id}', response_model=SourceConfigView)
def source_detail(source_id: str, svc: Service):
    return svc.get_source_config(source_id)


@router.patch('/sources/{source_id}', response_model=SourceConfigView)
def update_source(source_id: str, body: SourceUpdate, svc: Service):
    return svc.update_source(source_id, body)


@router.post('/sources/fetch', response_model=list[FetchOutcome])
async def fetch_sources(svc: Service, body: SourceFetch | None = None):
    return await svc.fetch_sources(body.ids if body else None)


@router.post('/sources/{source_id}/fetch', response_model=FetchOutcome)
async def fetch_source(source_id: str, svc: Service):
    result = (await svc.fetch_sources([source_id]))[0]
    if result.status == 'busy':
        raise Busy('source is already running')
    if result.status == 'error':
        raise UpstreamError('source fetch failed; see run history')
    return result


@router.post('/sources/{source_id}/distill', response_model=FetchOutcome)
async def distill_source(source_id: str, svc: Service):
    """Explicit refresh: explore again and re-distill this browser source's script."""
    result = await svc.distill_source(source_id)
    if result.status == 'busy':
        raise Busy('source is already running')
    if result.status == 'error':
        raise UpstreamError('source distill failed; see run history')
    return result


@router.get('/items', response_model=Page[ItemView])
def items(svc: Service, sources: Annotated[list[str] | None, Query()] = None,
          kinds: Annotated[list[ItemKind] | None, Query()] = None,
          since: str | None = None, until: str | None = None, search: str | None = None,
          limit: int = 50, offset: int = 0, pipeline: str | None = None, persona: str | None = None,
          passed: bool | None = None, min_score: float | None = None, topic: str | None = None,
          sort: str = 'time', include_archive: bool = False):
    return svc.list_items(sources=sources, kinds=[str(kind) for kind in kinds] if kinds else None, since=since, until=until, search=search,
                          limit=limit, offset=offset, pipeline=pipeline, persona=persona, passed=passed,
                          min_score=min_score, topic=topic, sort=sort, include_archive=include_archive)


@router.get('/items/{item_id}', response_model=ItemDetail)
def item(item_id: str, svc: Service, include_archive: bool = False):
    return svc.get_item(item_id, include_archive=include_archive)


@router.get('/runs', response_model=list[RunView])
def runs(svc: Service, kind: str | None = None, target: str | None = None, limit: int = 50):
    return svc.list_runs(kind=kind, target=target, limit=limit)


@router.get('/runs/{run_id}', response_model=RunDetail)
def run_detail(run_id: int, svc: Service):
    return svc.get_run(run_id)


@router.get('/errors', response_model=Page[ErrorEventView])
def errors(svc: Service, unresolved: bool = False, source: str | None = None,
           pipeline: str | None = None, phase: str | None = None, error_code: str | None = None,
           run_id: int | None = None, since: str | None = None, until: str | None = None,
           limit: int = 50, offset: int = 0):
    return svc.list_errors(unresolved=unresolved, source=source, pipeline=pipeline, phase=phase,
                           error_code=error_code, run_id=run_id, since=since, until=until,
                           limit=limit, offset=offset)


@router.get('/errors/{error_id}', response_model=ErrorEventDetail)
def error_detail(error_id: int, svc: Service):
    return svc.get_error(error_id)


@router.get('/scheduler', response_model=list[TaskView])
def scheduler(svc: Service):
    return svc.scheduler_status()


@router.post('/scheduler/tick', response_model=list[TickOutcome])
async def tick(svc: Service):
    return await svc.tick()


@router.get('/scheduler/loop', response_model=SchedulerLoopView)
def scheduler_loop(request: Request):
    return request.app.state.scheduler_loop


@router.get('/settings', response_model=DisplaySettings)
def display_settings(svc: Service):
    return DisplaySettings(timezone=svc.config.app.timezone)
