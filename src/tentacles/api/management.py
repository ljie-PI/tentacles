"""Pipeline/report/model adapters; no domain decisions are duplicated here."""
from fastapi import APIRouter

from tentacles.api.dependencies import Service
from tentacles.api.models import PipelineFilter
from tentacles.errors import Busy, UpstreamError
from tentacles.pipelines.models import PipelineOutcome, PipelineView, ReportView
from tentacles.schemas import ModelTest, ModelView, Page

router = APIRouter()


def completed_pipeline(outcome: PipelineOutcome) -> PipelineOutcome:
    if outcome.status == 'busy':
        raise Busy('pipeline is already running')
    if outcome.status == 'error':
        raise UpstreamError('pipeline operation failed; see run history')
    return outcome


@router.get('/pipelines', response_model=list[PipelineView])
def pipelines(svc: Service):
    return svc.list_pipelines()


@router.post('/pipelines/{pipeline_id}/filter', response_model=PipelineOutcome)
async def filter_pipeline(pipeline_id: str, svc: Service, body: PipelineFilter | None = None):
    request = body or PipelineFilter()
    return completed_pipeline(await svc.filter_pipeline(pipeline_id, reevaluate=request.reevaluate,
                                                        since=request.since))


@router.post('/pipelines/{pipeline_id}/run', response_model=PipelineOutcome)
async def run_pipeline(pipeline_id: str, svc: Service):
    return completed_pipeline(await svc.run_pipeline(pipeline_id))


@router.get('/reports', response_model=Page[ReportView])
def reports(svc: Service, pipeline: str | None = None, limit: int = 50, offset: int = 0):
    return svc.list_reports(pipeline=pipeline, limit=limit, offset=offset)


@router.get('/reports/{report_id}', response_model=ReportView)
def report(report_id: str, svc: Service):
    return svc.get_report(report_id)


@router.get('/models', response_model=list[ModelView])
def models(svc: Service):
    return svc.list_models()


@router.post('/models/{name}/test', response_model=ModelTest)
async def test_model(name: str, svc: Service):
    # A completed connectivity test may legitimately report ok=false, including
    # its latency/error. It is not a failed API operation or an empty 502 body.
    return await svc.test_model(name)
