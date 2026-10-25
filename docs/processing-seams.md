# Processing seams

`pipelines/adapters.py` defines the Stage and Processor interfaces;
`pipelines/registry.py` selects their stateless adapters. The configuration
schemas remain the explicit YAML vocabulary. To implement another configured
kind, add its adapter (and any new options schema) and register it; do not add
dispatch branches to the cascade, manager, or configuration validation.

- Each adapter declares its model requirements: default stage and model kind.
  Central validation reads those declarations even when unrelated configuration
  fields are invalid. `none` needs no model, but still validates an explicit
  model override.
- A Stage writes one ordered `StageScore` per input into the caller's mutable
  `StageBatch`, recording usage as completions arrive. `score=None` is an item
  error, not a rejection. `batch.error` stops the cascade without discarding
  completed decisions. Exceptions and cancellation must leave known charges
  available to the manager's `finally` block.
- A Processor returns location-free `ReportDraft` values, possibly empty, and
  records completions through the provided callback. It owns content, citations,
  provenance, creation time and report-local usage, not final IDs or paths.
  After the entire batch is generated, `RunWriter.save_reports` allocates final
  identities/paths using the shared naming/containment policy in `report_path.py`.
  It exclusively creates private files and saves the existing outward `ReportView`
  records in Store's fenced transaction. The manager commits reports and **all**
  selected evaluations together, including deep events below the size threshold
  or past the report limit, and `none` selections. Run stats receive final report
  IDs only on successful commit.

RunLifecycle continues to own leases, cancellation cleanup, fenced publication
and late-charge backfill after ownership loss. Citation, clustering,
classification and summary policies stay in the processors. File naming still
uses each draft's creation time; publication does not regenerate content or
change the response shape. Store retains exception rollback and best-effort
cleanup of only this transaction's newly created files; filesystem and SQLite
publication is not crash-atomic.

A temporary no-model Stage can replace an entry in `registry.STAGES` to exercise
item-error forwarding, rejection/acceptance and batch aborts through the same
`filter_items` interface; restore the entry after the check. No model client,
alternate cascade or asynchronous persistence interface is needed.
