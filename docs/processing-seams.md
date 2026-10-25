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

Source adapters may return typed `ItemObservation` values alongside Items.
Only fields explicitly set on an observation may overwrite stored fields;
explicit null clears, unset preserves. Stable external identity is declared,
not inferred from a fallback URL/fingerprint. Opted-in RSS and Web list emit
these observations. Store owns target resolution, full downstream-field
comparison, content revision and evaluation invalidation in its write transaction.
Feed format determines body provenance: Atom summaries never replace missing
content, while an RSS description supplies the complete description-backed body
when explicit content is absent (only its summary is truncated). Feedparser's
ID-derived `link` is not an observed alternate URL; an actual alternate record
without `href`, as produced by RSS `<link/>`, is an explicitly empty URL.
Stable-ID entries without a title may update known Items (missing title
preserves; explicit empty/null clears), but cannot insert untitled Items.
FetchResult carries observations in entry order, not collapsed by identity.
Store first resolves a same-source stable external identity (including persisted
aliases), then a unique same-source standard Item by an actually observed,
normalized URL. Fingerprints and similar titles remain insertion deduplication,
never update identity. Suppressed and observation-only identities cannot acquire
an update target; new dedup losers remain suppressed. Identity/URL disagreement,
ambiguous same-source URLs and a target URL occupied by another standard Item
raise Conflict inside the batch transaction. Alias mappings retain the original
Item's external identity; they are not historical snapshots.
Store reserves a canonical target only after resolving a valid update or insertion
candidate, so an unknown untitled entry cannot suppress a later valid entry or
lend it the wrong fields. Every target takes only its first valid observation,
even when multiple aliases arrive or the first observation is unchanged.
Target resolution, alias persistence, first-valid selection and insertion checks
share Store's fence. Standard non-email Items alone participate in updates;
revision/page and email identities retain their insertion policy.
Standalone Store callers may still supply
the identity-keyed observation map for batches without distinct same-ID fields.
`RunWriter.upsert_items` shares that implementation so batch writes, source cursor,
counts and Run completion commit together. Counts-only metadata is not content;
the `to`/`cc` fields used by summarize do participate in content comparison.

Web list and llm share `WebSource._classify_links` and `_details`. The opt-in
classification callback uses `Store.classify_links`: `refresh` requires this
source's stored standard external identity; suppression wins, observations alone
and other sources' article URLs are `skip`, otherwise `new`. Refresh outranks the
bounded seen cache, but a cache entry alone never authorizes refresh. These reads
only avoid unnecessary HTTP requests; Store still resolves writes under its
fence. Web llm's update flag remains rejected pending its descriptor-cache slice.
Selectors preserve node/attribute presence in `ItemObservation`; list excerpts
are never body observations. Only a successful detail parse supplies full
`content`, and all details finish before publication. No descriptor or full-body
cache is added here, and no historical URLs are revisited.

Schema 13 adds source-scoped alias mappings without changing existing identities;
schema 12 preserves existing rows with revision 1 and null content-update time.
Pipeline activity is `COALESCE(updated_at, fetched_at)` with expression indexes;
the Feed deliberately retains `COALESCE(published, fetched_at)`. Filter captures
the input revision before scoring, and evaluation/report publication validates it
under the same write lock as updates. Updates delete obsolete evaluations, not
consumption, feedback or reports. Fresh evaluations inherit the pipeline's
existing consumption marker even when they reject or accept the latest content.

A temporary no-model Stage can replace an entry in `registry.STAGES` to exercise
item-error forwarding, rejection/acceptance and batch aborts through the same
`filter_items` interface; restore the entry after the check. No model client,
alternate cascade or asynchronous persistence interface is needed.
