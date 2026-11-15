"""The only dispatch tables for filter and processor variation.

Adapters are stateless: all per-invocation state belongs to their arguments.
Configuration schemas describe accepted YAML; these tables select behavior.
"""
from tentacles.pipelines.adapters import Processor, Stage
from tentacles.pipelines.deep import DeepProcessor
from tentacles.pipelines.digest import DigestProcessor
from tentacles.pipelines.keyword import KeywordStage
from tentacles.pipelines.none import NoneProcessor
from tentacles.pipelines.semantic import SemanticStage
from tentacles.pipelines.summarize import SummarizeProcessor

STAGES: dict[str, Stage] = {
    'keyword': KeywordStage(),
    # `llm` and `decision` are the pre-#80 spellings of one semantic block.
    'semantic': SemanticStage(),
    'llm': SemanticStage(),
    'decision': SemanticStage(),
}

PROCESSORS: dict[str, Processor] = {
    'digest': DigestProcessor(),
    'deep': DeepProcessor(),
    'summarize': SummarizeProcessor(),
    'none': NoneProcessor(),
}
