"""Fixed-size Decisions workers; result slots always follow input order."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from tentacles.asyncutil import gather_draining
from tentacles.errors import UpstreamError
from tentacles.llm.client import DecisionModel
from tentacles.llm.retry import ModelHTTPError, RequestGate
from tentacles.schemas import DecisionCompletion
from tentacles.llm.types import UsageTotals


@dataclass(frozen=True)
class DecisionInput:
    state: str | dict[str, Any] | list[Any]
    questions: dict[str, Any]


@dataclass
class DecisionBatch:
    results: list[DecisionCompletion | UpstreamError | None]
    metrics: UsageTotals


class DecisionBatchError(UpstreamError):
    def __init__(self, name: str, batch: DecisionBatch, cause: UpstreamError):
        self.batch = batch
        self.status = cause.status if isinstance(cause, ModelHTTPError) else None
        detail = f'HTTP {self.status}' if self.status is not None else type(cause).__name__
        super().__init__(f'model {name!r}: decision batch aborted ({detail})')


async def decide_many(model: DecisionModel, requests: list[DecisionInput], metrics: UsageTotals) -> DecisionBatch:
    batch = DecisionBatch([None] * len(requests), metrics)
    pending = iter(enumerate(requests))
    gate = RequestGate()

    async def worker() -> None:
        while gate.failure is None:
            await asyncio.sleep(0)
            if gate.failure is not None:
                return
            try:
                index, request = next(pending)
            except StopIteration:
                return
            try:
                batch.results[index] = await model.decide(request.state, request.questions,
                                                          on_completion=metrics.record, gate=gate)
            except Exception as exc:
                batch.results[index] = exc if isinstance(exc, UpstreamError) else UpstreamError(type(exc).__name__)

    tasks = [asyncio.create_task(worker()) for _ in range(min(model.spec.concurrency, len(requests)))]
    await gather_draining(tasks)
    if gate.failure is not None:
        raise DecisionBatchError(model.name, batch, gate.failure)
    return batch
