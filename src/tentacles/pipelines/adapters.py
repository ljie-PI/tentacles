"""Small processing interfaces; adapters own variation, callers own orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from tentacles.config import AppConfig
from tentacles.diagnostics import DiagnosticEvent
from tentacles.llm.config import ModelKind, Stage as ModelStage
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.config import PipelineSpec, StageSpec
from tentacles.pipelines.models import Evaluation, ReportDraft, StageBatch, StageScore
from tentacles.schemas import Completion


@dataclass(frozen=True)
class ModelRequirement:
    stage: ModelStage
    # None: the stage accepts either kind and dispatches on the resolved model.
    kind: ModelKind | None
    # none does not use a model, but an explicit override must still be valid.
    optional: bool = False

    def resolve(self, models: ModelRegistry, override: str | None) -> str:
        if self.kind is None:
            return models.resolve_any(self.stage, override)
        name = models.resolve(self.stage, override)
        models.spec(name, kind=self.kind)
        return name


class Stage(Protocol):
    def models(self, stage: StageSpec) -> tuple[ModelRequirement, ...]:
        """The model requirements of one configured stage.

        The declaration depends on the stage because a spelling can choose its
        own default stage (see `SemanticSpec.model_stage`); central validation
        reads it even when other options of that stage are invalid.
        """
        ...

    def batch_size(self, stage: StageSpec) -> int | None:
        """The paid request size this stage wants for one configured stage.

        None: the stage makes no paid requests, so it does not constrain how
        many items the cascade finishes before it persists a chunk.
        """
        ...

    def item_error_code(self, scored: StageScore) -> str:
        """The shared vocabulary code for an item this stage could not score."""
        ...

    async def score(self, items: list[Item], persona: PersonaView, stage: StageSpec,
                    index: int, models: ModelRegistry, progress: StageBatch) -> StageBatch:
        """Write ordered scores and charges to the caller-owned progress object.

        One score per input; score=None forwards that item unless this is
        the last stage. progress.error aborts the cascade, preserving completed
        decisions. Record charges as they arrive, including before an exception
        or cancellation; never replace progress or its metrics.
        """
        ...


class Processor(Protocol):
    @property
    def models(self) -> tuple[ModelRequirement, ...]: ...

    async def process(self, config: AppConfig, spec: PipelineSpec, persona: PersonaView,
                      selected: list[tuple[Item, Evaluation]], models: ModelRegistry,
                      clock: Callable[[], datetime],
                      on_completion: Callable[[Completion], None], *,
                      record_error: Callable[[DiagnosticEvent], None]) -> list[ReportDraft]:
        """Return location-free drafts, recording every known completion.

        Publication allocates identities/paths in the fenced Store transaction
        and consumes ALL selected evaluations, not just report members.
        Failures leave the entire selection pending. A degraded batch that still
        produces a report must record its triage snapshot through `record_error`.
        """
        ...
