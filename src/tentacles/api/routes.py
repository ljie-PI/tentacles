"""Thin routes over Service; FastAPI offloads synchronous query operations."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from tentacles.api.models import DisplaySettings, SchedulerLoopView, SourceFetch
from tentacles.models import ItemKind
from tentacles.errors import Busy, UpstreamError
from tentacles.schemas import FetchOutcome, ItemDetail, ItemView, Page, RunView, SourceView, TaskView, TickOutcome
from tentacles.service import TentaclesService


def service(request: Request) -> TentaclesService:
    return request.app.state.service


Service = Annotated[TentaclesService, Depends(service)]
router = APIRouter()


@router.get('/sources', response_model=list[SourceView])
def sources(svc: Service):
    return svc.list_sources()


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


@router.get('/items', response_model=Page[ItemView])
def items(svc: Service, sources: Annotated[list[str] | None, Query()] = None,
          kinds: Annotated[list[ItemKind] | None, Query()] = None,
          since: str | None = None, until: str | None = None, search: str | None = None,
          limit: int = 50, offset: int = 0, pipeline: str | None = None, passed: bool | None = None):
    return svc.list_items(sources=sources, kinds=[str(kind) for kind in kinds] if kinds else None, since=since, until=until, search=search,
                          limit=limit, offset=offset, pipeline=pipeline, passed=passed)


@router.get('/items/{item_id}', response_model=ItemDetail)
def item(item_id: str, svc: Service):
    return svc.get_item(item_id)


@router.get('/runs', response_model=list[RunView])
def runs(svc: Service, kind: str | None = None, target: str | None = None, limit: int = 50):
    return svc.list_runs(kind=kind, target=target, limit=limit)


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
