"""Version-aware persona and feedback operations over the shared Service."""
from typing import Annotated

from fastapi import APIRouter, Query

from tentacles.api.dependencies import Service
from tentacles.api.models import ItemFeedback, PersonaEvolve, PersonaRollback, PersonaSet
from tentacles.personas.models import FeedbackView, PersonaProposal, PersonaView

router = APIRouter()


@router.get('/items/{item_id}/feedback', response_model=FeedbackView | None)
def feedback(item_id: str, svc: Service, persona: str | None = None):
    return svc.get_feedback(item_id, persona=persona)


@router.post('/items/{item_id}/feedback', response_model=FeedbackView)
def submit_feedback(item_id: str, body: ItemFeedback, svc: Service):
    return svc.submit_feedback(item_id, rating=body.rating, persona=body.persona, note=body.note)


@router.get('/personas', response_model=list[PersonaView])
def personas(svc: Service):
    return svc.list_personas()


# Names are query/body values rather than path segments: existing valid names
# can contain Unicode, slashes or reserved-looking suffixes such as /history.
@router.get('/personas/current', response_model=PersonaView)
def persona(svc: Service, name: str, version: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None):
    return svc.get_persona(name, version)


@router.get('/personas/history', response_model=list[PersonaView])
def history(svc: Service, name: str):
    return svc.persona_history(name)


@router.patch('/personas', response_model=PersonaView)
async def set_persona(body: PersonaSet, svc: Service):
    return await svc.set_persona(**body.model_dump())


@router.post('/personas/evolve', response_model=PersonaProposal)
async def evolve(body: PersonaEvolve, svc: Service):
    return await svc.evolve_persona(**body.model_dump(), dry_run=True)


@router.post('/personas/apply', response_model=PersonaView)
async def apply(body: PersonaProposal, svc: Service):
    return await svc.apply_persona(body)


@router.post('/personas/rollback', response_model=PersonaView)
async def rollback(body: PersonaRollback, svc: Service):
    return await svc.rollback_persona(**body.model_dump())
