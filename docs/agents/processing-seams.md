# Processing seams

`pipelines/adapters.py` defines the Stage and Processor interfaces;
`pipelines/registry.py` selects their stateless adapters. The configuration
schemas remain the explicit YAML vocabulary. To implement another configured
kind, add its adapter (and any new options schema) and register it; do not add
dispatch branches to the cascade, manager, or configuration validation.

- Each adapter declares its model requirements: default stage and model kind.
  A Stage's declaration is a function of its configured stage (`Stage.models(stage)`),
  because a spelling can choose its own default stage. Central validation reads those
  declarations even when unrelated configuration fields are invalid. `none` needs no
  model, but still validates an explicit model override.
- A Stage writes one ordered `StageScore` per input into the caller's mutable
  `StageBatch`, recording usage as completions arrive. `score=None` is an item
  error, not a rejection. `batch.error` stops the cascade without discarding
  completed decisions. Exceptions and cancellation must leave known charges
  available to the manager's `finally` block.
  `keyword` is the zero-cost Stage: it reports every hit word in
  `details.matched_include/matched_exclude/matched_hard_exclude` plus readable
  `details.match_points`. `hard_exclude` always rejects; a soft `exclude` plus a
  positive hit scores 0.55, which is above the usual rejection threshold and
  below any `accept_above`, so the entry reaches the next semantic stage instead
  of being cached as a 0.0 rejection.
- Every stage's positive evidence reaches the evaluation: the cascade merges the
  `tags` and `details.match_points` of the stages an item actually passed through,
  in stage order and without duplicates, keeps at most `MAX_MATCH_POINTS` points
  and takes `reason` from the first one. A stage that produced no score
  contributes nothing, so an item error can never become a fabricated reason.
- `semantic` is one Stage for two model kinds: `type: semantic`, and the pre-#80
  spellings `llm` and `decision`, all select the same adapter. Its
  `ModelRequirement` declares `kind=None`, which resolves through
  `ModelRegistry.resolve_any` and lets the adapter dispatch on `models.spec(name).kind`.
  The spelling only picks which default the stage prefers (`decision` keeps
  `defaults.models.decision`, the others keep `defaults.models.filter`), and the
  adapter declares that preference from the configured stage, so central validation
  checks the default the stage really resolves. The two
  executors share `batch_size` and `summary_chars`; `relevance_type` and
  `jev_batch_mode` only apply to the Decisions executor, which packs one shared
  persona per batch, halves a payload the endpoint refuses with 400/413 or answers
  only partly (a dropped answer surfaces as `MissingDecisionAnswers`) once, and
  then falls back to one request per item for the rest of the stage. A refused
  second step (interest tags) keeps the step-one relevance verdicts; only items
  that genuinely fail stay `score=None` item errors in the ledger.
- `FilterBatch.checkpoint` is how a stage's finished work reaches disk mid-Run:
  it receives the evaluations of one completed chunk and the manager publishes
  them through `RunOperation.checkpoint` → `Store.checkpoint_run`, under the same
  lease fence and `input_revision` check as the final commit but without ending
  the Run. Chunks follow the largest `Stage.batch_size` the stage's own adapter
  declares (100 when nothing declares one, as in a keyword-only cascade), which is
  why a semantic `batch_size` is also the paid request size. A cancelled
  or failed Run keeps every checkpointed chunk, and the next run's candidate
  query skips those items by itself.
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
not inferred from a fallback URL/fingerprint. Opted-in RSS and Web list/llm emit
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
A non-email Item must keep an http(s) `url` or `page_url`: the fetch pipeline
drops entries that have neither, counts them in `stats.invalid_url` and logs the
rejection, so an adapter must set one of the two before publishing and must not
rely on Store to repair it.

Web list and llm share `WebSource._classify_links` and `_details`. The opt-in
classification callback uses `Store.classify_links`: `refresh` requires this
source's stored standard external identity; suppression wins, observations alone
and other sources' article URLs are `skip`, otherwise `new`. Refresh outranks the
bounded seen cache, but a cache entry alone never authorizes refresh. These reads
only avoid unnecessary HTTP requests; Store still resolves writes under its
fence. Web llm reuses that same classification and detail path on descriptor-cache hits.
Selectors preserve node/attribute presence in `ItemObservation`; list excerpts
are never body observations. Only a successful detail parse supplies full
`content`, and all details finish before publication. Web llm caches bounded,
validated current list descriptors in the same fenced source cursor, never full
detail bodies or historical guesses. Its key covers effective text, the full
grounding link set and model/extraction identity (not API credentials). Missing
and explicit-empty observations survive serialization; relative dates are
resolved only when descriptors are built. Cache hits with details disabled do
not deliver Items; with details enabled they reclassify current descriptors and
refresh eligible details without another extraction. Old cursors may need one
paid build; failed builds/details never advance the cache. No historical URLs
are revisited.

`WebOptions.on_detail_error` decides what one failed detail page costs.
`fail_batch` (the default) keeps the atomic batch: it records every failure of
that round first and then raises, so the rollback still leaves one ledger row per
broken target. `record_and_skip` drops only the failed pages from `items` and
`observations` and removes their identities from the cursor's `seen` list; it
never touches `source_seen_ids`, because a skipped page never reaches
`upsert_items`. Both properties are what makes the next fetch classify the
skipped target as `new` again and lets its later success resolve the rows. A
failed target must therefore never reach `seen`, while the llm descriptor cache
keeps it deliberately: an unchanged page has to re-deliver it for another detail
fetch.

Sources and pipelines report per-target failures through callbacks, not the
ledger: `FetchContext.record_error`, `FilterBatch.record_error` and the
`record_error` argument of `Processor.process` all point at the owning
`RunOperation`. An adapter that records a failure itself raises
`sources.base.DiagnosedError`, which `fetch_source` recognises
(`exc.diagnosed`) so one failure never produces both a concrete row and a generic
whole-source row. `filtering.filter_items` records an item error only where the
cascade finally gives up on that item: `score=None` on the last stage, or any
stage that aborts the batch; the code it records is the one that stage's adapter
declares in `item_error_code` (the `semantic` adapter picks it from
`details.executor`), never a branch on `stage.type`.

Schema 13 adds source-scoped alias mappings without changing existing identities;
schema 12 preserves existing rows with revision 1 and null content-update time.
Schema 14 adds `items.page_url`, `items.content_truncated` and
`evaluations.match_points` and backfills stored bodies and summaries to the unit
caps; schema 15 adds the `run_errors` diagnostics ledger; schema 16 adds
`persona_versions.hard_exclude` with an empty default, so existing versions stay
readable and only hard-exclude-less seeds start empty; schema 17 adds the `items_fts`
trigram index over the four searchable columns, kept in sync by SQL triggers rather
than by the write paths. `Store.list_items` retains single-database search depth:
three or more character terms come from the index, shorter ones are matched natively
per column, so callers must not build their own `LIKE` scans to narrow a listing.
Store projects the query's primary ordering value (BM25, short-only title priority,
or neutral for time order) into internal `ItemHit` metadata. Its `order_key` carries
that decision, with publish/fetch time descending and identity ascending as ties;
merging must use it, never infer relevance again from the outward `bm25_rank`.
Schema 18 adds the `archived_identities` tombstone table. `archive.py`
(`ArchiveManager`) owns the two-phase yearly cold archive: it writes complete copies
into `data/archive/tentacles-YYYY.db` (opened through `Store`, so the cold database
carries the same schema and its own `items_fts`) and `Store.import_archived` verifies
rows and index before `Store.archive_move` tombstones identities and deletes the hot
rows in one transaction. Cold databases are sealed out of WAL mode so a plain
read-only `Store(read_only=True)` never creates side files. `ArchiveManager.list_items`
and `get_item` are the placement-hiding read seam used by the Service for list, search
and show, with or without archives. Service retains user-input/configuration validation
and list/search response projection, not hot/cold dispatch or detail assembly.
`--include-archive` queries each database separately and merges Store's ordering keys
before pagination; every database contributes up to offset + limit candidates. This
keeps cross-year reads independent of SQLite's ten-database `ATTACH` limit and preserves
short-only title priority even when there are no cold files. BM25 remains database-local,
not global corpus statistics. Detail lookup is hot-first, then years newest-first;
ArchiveManager assembles the Item, latest valid associated Evaluation and per-pipeline
history in the same way for both placements. The detail `evaluations` collection keeps
its retrospective semantics (latest Persona version per pipeline, possibly an older
input revision); only the top-level association requires the current content revision.
Schema 19 adds
`archived_identities.kind` and `identity_policy` so a tombstone is read through the
exact identity rules of the row it replaced: external identity always suppresses,
URL only for `standard`, fingerprint only for `standard` non-email. An archived
`revision` URL and an archived email fingerprint therefore stay as unsuppressing as
they were while hot; `Store.seen_links` applies the same URL guard. Tombstones
written before schema 19 keep the conservative `standard`/non-email defaults.
Pipeline activity is `COALESCE(updated_at, fetched_at)` with expression indexes;
the Feed deliberately retains `COALESCE(published, fetched_at)`. Filter captures
the input revision before scoring, and evaluation/report publication validates it
under the same write lock as updates. Updates delete obsolete evaluations, not
consumption, feedback or reports. Fresh evaluations inherit the pipeline's
existing consumption marker even when they reject or accept the latest content.
`Store.list_items` always attaches the latest evaluation whose `input_revision`
matches the Item's `content_revision`, which is what the default association and
`--min-score` / `--topic` / `--persona` rely on: a stale newest evaluation must
never hide an older valid one, and any new ranked query must filter revisions
before ranking, not after.

A temporary no-model Stage can replace an entry in `registry.STAGES` to exercise
item-error forwarding, rejection/acceptance and batch aborts through the same
`filter_items` interface; restore the entry after the check. No model client,
alternate cascade or asynchronous persistence interface is needed.

`type: browser` adds one source adapter that runs a different policy: `FetchContext`
carries the loaded `AppConfig` (the `browser` block) and a `record_error` callback for
per-target failures, so the adapter never reaches the Store or the ledger directly.
Session policy stays in `sources/browser_session.py` (one headed work tab per
`(cdp_url, chrome_profile)`, attached over CDP or launched headed, profile verified from
`chrome://version`, tab closed on every exit path, never `browser.close()`), and script
policy stays in `sources/browser_script.py` (an `ast.parse` import/name/attribute
allowlist gate before `exec`) and `sources/browser_contract.py` (the
`BrowserExtractOutput` contract, kept pure data because a script imports it). Disabling that work tab's HTTP cache over CDP
(`Network.setCacheDisabled`) is a deliberate replay-freshness decision: a replay must read
the live page, so it pays a per-tab reload cost instead of trusting the user's Chrome
cache. A fixed script must not own the tab lifecycle: only the runtime can capture the
failure site, so timeouts, script errors and zero-item results become `browser_replay`
events whose `debug_context` carries the URL, title, A11y excerpt and traceback.

A `type: browser` source without a fixed script is a cold start, and that policy lives in
the same three modules plus `sources/pi_rpc.py`, `sources/browser_bridge.py`,
`sources/trajectory.py` and `sources/distiller.py`. The Agent is one controlled
`pi --mode rpc` child (ephemeral session, no discovery, no built-in tools, model and
thinking level pinned on the command line); its only tools are the ones the shipped
`sources/browser_bridge.js` extension registers, each of which arrives back as one
loopback JSON request so the fetching process — not the Agent — owns the page, the
trajectory and the ledger. Every step is recorded to a local, redacted
`data/trajectories/<id>/<run>.jsonl` before the next one runs, and provider usage is
forwarded to the owning `Run` as it arrives. A distilled script is repository code even
when an Agent wrote it, so it is written only after the same static gate and a standalone
trial on a fresh real `BrowserContext`, with one corrective turn; otherwise the failure
lands as `browser_distill` and no script exists for the next round to replay. The search
for a script is a filesystem check (`<scripts_dir>/<id>.py`), never a database flag.

A replay that raises, times out or returns no entries is not a dead end either: one
`sources/browser_heal.py` session sells the same controlled `pi` process the old script, the
failure site and the local meta selectors, gives every turn the real work tab for live
probing, and gates each candidate (at most two) through `distiller.verify_candidate` — the
same static gate and standalone trial, so healing cannot install what a cold start would
reject. Only a passing candidate reaches `distiller.install_script`; a session that exhausts
its candidates writes `pi_heal_exhausted` under `browser_heal`, leaves the old script and
meta byte-identical, opens no PR and fails the Run (the consumed `pi` cost stays on it).
`browser.pr.enabled` is therefore a real switch, not a schema field.

`sources/script_pr.py` owns the only code path that touches git: a throwaway `git worktree`
outside the repository starts from `origin/<base_branch>` (or the existing review branch),
stages exactly the one relative script path, commits, pushes `HEAD:refs/heads/<prefix><id>`
without ever forcing, and then `gh pr create` or `gh pr comment` when that branch already has
an open PR. The worktree and its scratch directory are removed in `finally` on every path,
`git`/`gh` failures become `git_or_pr_failed` (`script_pr`, warning) instead of exceptions,
and a script that already matches the branch is reported as `unchanged` rather than
re-committed. `FetchOutcome.stats` carries `pr_status`, `pr_url` and `pr_branch`; the base
branch is only ever read. `sources distill <ID>` is the explicit refresh: `FetchContext.distill`
skips the "a script exists, so replay it" decision and re-runs the cold-start policy, PR
publication included.
