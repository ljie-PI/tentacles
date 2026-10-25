"""Consume selected items without generating reports or calling a model."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from tentacles.config import AppConfig
from tentacles.llm.registry import ModelRegistry
from tentacles.models import Item
from tentacles.personas.models import PersonaView
from tentacles.pipelines.adapters import ModelRequirement
from tentacles.pipelines.config import PipelineSpec
from tentacles.pipelines.models import Evaluation, ReportDraft
from tentacles.schemas import Completion


class NoneProcessor:
    models = (ModelRequirement('process', 'chat', optional=True),)

    async def process(self, config: AppConfig, spec: PipelineSpec, persona: PersonaView,
                      selected: list[tuple[Item, Evaluation]], models: ModelRegistry,
                      clock: Callable[[], datetime],
                      on_completion: Callable[[Completion], None]) -> list[ReportDraft]:
        return []
