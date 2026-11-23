---
name: tentacles-ops
description: Run Tentacles fetches, pipeline filtering/reports, scheduler ticks, browser script distillation, failure diagnostics, and storage maintenance. Use when fetching feeds/emails, generating reports, inspecting costs/runs, troubleshooting errors or browser scripts, or archiving cold storage.
---

# Tentacles Operations, Diagnostics & Maintenance

Execute fetches, pipelines, browser distillation, failure triage, and storage maintenance via `--json` CLI.

## Global CLI contract

Always pass `--json` before the subcommand and read `stdout` only:

```bash
tentacles --json [--config PATH] <subcommand>
```

- **Exit codes**: `0` completed, `2` `invalid`, `3` `not_found`, `4` `conflict`/`busy`, `5` `upstream`, `1` `error`, `130` `aborted`.
- **Important**: Batch/probe commands (`sources fetch`, `tick`, `pipelines run`, `models test`) still exit `0` when an individual target fails or is `busy`. Always inspect each item's `status` (or `ok`) and `error_count` / `failed_targets`.

## 1. Sources, Pipelines, Scheduler & Cost Audit

```bash
# Config & models
tentacles --json config check
tentacles --json models list
tentacles --json models test <MODEL_NAME>          # makes a real probe request

# Sources (explicit IDs run even when enabled: false)
tentacles --json sources list
tentacles --json sources fetch [SOURCE_ID...]
tentacles --json sources distill <BROWSER_ID>      # re-explore & distill type: browser script

# Pipelines & scheduler (tick barrier: 1. fetch -> 2. filter -> 3. pipeline)
tentacles --json pipelines list
tentacles --json pipelines filter <PIPELINE_ID> [--reevaluate --since 7d]
tentacles --json pipelines run <PIPELINE_ID>       # calls process model (and filter if needed)
tentacles --json scheduler status
tentacles --json tick

# Run history & cost audit
tentacles --json runs list [--kind fetch|filter|pipeline|persona] [--target ID] [--limit 20]
tentacles --json runs show <RUN_ID>
```

- **Prevent double-counting costs**: `pipelines run` creates a child `kind: "filter"` run (`stats.filter_run_id`), and the parent `pipeline` run's `usage`/`cost` **already includes** that child filter run. Sum parent `pipeline` runs, never parent + child together.
- **Busy dependency deferral**: In `tick`, if a source returns `status: "busy"`, dependent pipelines defer without advancing their schedule.

## 2. Browser Sources (`type: browser`)

Browser sources require a **headed** Chrome window via CDP (`headless` is forbidden) and support two modes:

- If `sources fetch <ID>` fails with CDP/profile errors, check `errors show` or start Chrome with the exact `--remote-debugging-port` and `--profile-directory` printed in the error (or enable `browser.auto_launch: true` with `chrome_user_data_dir`).
- **`mode: script` (default)**: replays `<scripts_dir>/<id>.py`.
  - On cold start (missing `<scripts_dir>/<id>.py`) or `sources distill <ID>`, `pi --mode rpc` explores, records `data/trajectories/<id>/<run_id>.jsonl`, distills, passes AST gate + live trial tab, installs the script, and opens/updates a PR per `browser.pr`.
  - On replay failure (exception, timeout, or 0 items), one `pi` self-healing session runs (up to 2 candidates). If exhausted (`pi_heal_exhausted`), old script/meta remain untouched and the run fails.
  - If `stats.pr_status == "error"` (`git_or_pr_failed`), fix `gh auth status` / git remote and re-run `sources distill <ID>` (idempotent).
- **`mode: list` (infinite-scroll CSS selectors & engagement prefilter)**: zero LLM / `pi` cost (`sources distill` is rejected with `invalid`). Uses `preset` (`reddit` \| `x-following`) or explicit `selectors`, optional pre-scan `actions`, `scroll`, `prefilter`, and `format`. A round where all cards are filtered out by `prefilter` or already stored returns 0 new items cleanly without error.

## 3. Six-step failure diagnostics (`run_errors`)

Use the rollback-safe error ledger whenever `error_count > 0` or items are missing:

1. Check `failed_targets[].error_id` from `sources fetch` / `tick` / `pipelines run`.
2. Inspect unresolved errors: `tentacles --json errors list --unresolved [--source ID] [--phase P] [--limit 50]`.
3. Fetch full sanitized `debug_context` (<= 16 KB): `tentacles --json errors show <ERROR_ID>`.
4. Cross-check run summary if needed: `tentacles --json runs show <RUN_ID>`.
5. Decide action by `retryable` and `error_code` (consult [references/error-codes.md](references/error-codes.md)):
   - `retryable: true` (`network_timeout`, `connection_failed`, HTTP `429`/`5xx`) -> retry later.
   - `retryable: false` (`selector_no_match`, `http_redirect_blocked`, `empty_extracted_body`, `imap_auth_failed`, `pi_script_rejected`) -> fix config/selector/script or switch static `web` source to `type: browser`.
6. Once a subsequent run succeeds for the same `(source_id, target_url|external_id)` or `(pipeline_id, item_id)`, `resolved_at` backfills automatically.

## 4. Storage inspection, FTS5 reindex & yearly cold archive

```bash
# Read-only size, row counts, and archive list (fts_indexed_rows should match tables.items)
tentacles --json storage status

# Rebuild FTS5 index (use --optimize to merge segments only)
tentacles --json storage reindex [--optimize] [--include-archive]

# Always preview archive before writing; --vacuum cannot combine with --dry-run
tentacles --json storage archive --dry-run
tentacles --json storage archive --before 365d --vacuum
```

- `storage archive` moves items older than the cutoff (by `published` / `fetched_at` calendar year) into `data/archive/tentacles-YYYY.db`, keeping `archived_identities` tombstones in hot DB so re-fetches never resurrect old items.
- Items with unresolved errors, active feedback inside `feedback_window`, or updates within 30 days are automatically exempted and kept in hot DB.

## Completion criteria

- Every batch command had its per-target `status` / `error_count` inspected (not just exit code `0`).
- Any `storage archive` run was previewed with `--dry-run` first or matched the requested cutoff.
- Cost summaries excluded child `stats.filter_run_id` duplicates.
