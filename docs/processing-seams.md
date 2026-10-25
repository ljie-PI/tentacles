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
- A Processor returns a list of existing `ReportView` values, possibly empty,
  and records completions through the provided callback. It does not publish
  reports or consume items. The manager commits reports and **all** selected
  evaluations together, including deep events below the size threshold or past
  the report limit, and `none` selections.

RunLifecycle continues to own leases, cancellation cleanup, fenced publication
and late-charge backfill after ownership loss. Report identity/path generation
and citation, clustering, classification and summary policies stay in the
existing implementations.

A temporary no-model Stage can replace an entry in `registry.STAGES` to exercise
item-error forwarding, rejection/acceptance and batch aborts through the same
`filter_items` interface; restore the entry after the check. No model client,
alternate cascade or asynchronous persistence interface is needed.
