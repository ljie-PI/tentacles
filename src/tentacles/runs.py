"""One Run's lease, telemetry, fenced commit and cancellation-safe cleanup."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from tentacles.asyncutil import run_store_call
from tentacles.llm.types import UsageTotals
from tentacles.store import RunWriter, Store
from tentacles.tasks import Task
from tentacles.timeutil import to_utc

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    run_id: int
    status: Literal['ok', 'busy', 'error']
    stats: dict[str, Any]
    metrics: UsageTotals
    error: str | None


class RunOperation:
    """Domain work accumulates stats/metrics, then commits synchronous writes.

    The callback passed to commit runs in Store's fenced transaction. It must
    use its RunWriter, not open another transaction or perform asynchronous IO.
    """

    def __init__(self, store: Store, clock: Callable[[], datetime]) -> None:
        self._store, self._clock = store, clock
        self.started_at = to_utc(clock())
        self.run_id = 0
        self.stats: dict[str, Any] = {}
        self.metrics = UsageTotals()
        self._status: Literal['ok', 'busy', 'error'] = 'ok'
        self._error: str | None = None
        self._committed = False

    def _result(self, stats: dict[str, Any] | None = None) -> RunResult:
        metrics = UsageTotals(self.metrics.usage.model_copy(), self.metrics.cost, list(self.metrics.models))
        combined = {**self.stats, **(stats or {})}
        if self.run_id:
            combined.update(usage=metrics.usage.model_dump(), cost=metrics.cost, models=list(metrics.models))
        return RunResult(self.run_id, self._status, combined, metrics, self._error)

    async def commit(self, write: Callable[[RunWriter], None], *, stats: dict[str, Any] | None = None,
                     error: str | None = None) -> None:
        """Publish writes, stats additions and terminal state together, or neither."""
        def publish() -> None:
            if self._committed:
                raise RuntimeError('run already committed')
            result = self._result(stats)
            self._store.commit_run(self.run_id, write, now=to_utc(self._clock()),
                                   stats=result.stats, cost=result.metrics.cost, error=error)
            # Set in the worker: cancellation while draining a committed write
            # must not turn its durable success into a reported failure.
            self._committed = True
            self.stats.update(stats or {})
            self._status = 'error' if error is not None else 'ok'
            self._error = error

        await run_store_call(publish)


class RunLifecycle:
    def __init__(self, store: Store, clock: Callable[[], datetime]) -> None:
        self._store, self._clock = store, clock

    async def execute(self, task: Task, work: Callable[[RunOperation], Awaitable[None]], *,
                      due_only: bool = False,
                      failure_message: str = 'run failed',
                      on_finished: Callable[[RunResult], None] | None = None) -> RunResult | None:
        """Busy returns a result; not-due returns None without creating a Run.

        Ordinary failures become error results. Cancellation/interruption is
        re-raised after cleanup. on_finished also receives partial results on
        cancellation so a parent Run can retain its child Run's known charges.
        """
        operation = RunOperation(self._store, self._clock)
        token = uuid4().hex
        not_due = False

        def claim() -> None:
            nonlocal not_due
            result = self._store.claim_task(task, token, operation.started_at, force=not due_only)
            not_due = result.status == 'not_due'
            if result.status == 'ready':
                assert result.run_id is not None
                # Capture in the worker even if its awaiting owner is cancelled.
                operation.run_id = result.run_id
            elif result.status == 'busy':
                operation._status = 'busy'

        def cleanup() -> None:
            # One drained worker covers BOTH operations, even on repeated cancel.
            # finish_run preserves a reclaimed Run's reason/state while backfilling
            # late known charges; finish_task releases only this token's lease.
            if operation.run_id:
                try:
                    result = operation._result()
                    self._store.finish_run(operation.run_id, status='failed', now=to_utc(self._clock()),
                                           error=result.error, stats=result.stats, cost=result.metrics.cost)
                except Exception as failure:
                    log.warning('could not finish run (%s)', type(failure).__name__)
            try:
                self._store.finish_task(task, token, to_utc(self._clock()))
            except Exception as failure:
                log.warning('could not release run lease (%s)', type(failure).__name__)

        try:
            await run_store_call(claim)
            if not_due:
                return None
            if operation.run_id:
                await work(operation)
                if not operation._committed:
                    raise RuntimeError('run work returned without committing')
        except BaseException as exc:
            if not operation._committed:
                operation._status = 'error'
                operation._error = f'{failure_message} ({type(exc).__name__})'
                await run_store_call(cleanup)
            if not isinstance(exc, Exception):
                raise
        finally:
            if on_finished is not None and not not_due:
                on_finished(operation._result())
        return operation._result()
