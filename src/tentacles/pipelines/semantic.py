"""One configured semantic stage for both model kinds; the model's kind dispatches."""
from __future__ import annotations

from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.adapters import ModelRequirement
from tentacles.pipelines.config import SemanticSpec, StageSpec
from tentacles.pipelines.decision import score_decisions
from tentacles.pipelines.llm import score_llm
from tentacles.pipelines.models import StageBatch, StageScore


class SemanticStage:
    """Chat-LLM batch classification or JEV Decisions scoring, chosen by model kind.

    `llm` and `decision` remain valid `type` values: they only move `decision`
    to the front of the default-model lookup, so old configurations keep their
    existing model and still run through the executor their model's kind names.
    """

    models = (ModelRequirement('filter', None),)

    def item_error_code(self, scored: StageScore) -> str:
        return 'llm_batch_parse_error' if scored.details.get('executor') == 'chat' else 'decision_item_error'

    async def score(self, items: list[Item], persona: PersonaView, stage: StageSpec,
                    index: int, models: ModelRegistry, progress: StageBatch) -> StageBatch:
        assert isinstance(stage, SemanticSpec)
        name = ModelRequirement(stage.model_stage, None).resolve(models, stage.model)
        progress.model_name = name
        if models.spec(name).kind == 'decision':
            return await score_decisions(items, persona, stage, index, models, name, progress)
        return await score_llm(items, persona, stage, index, models, name, progress)
