---
name: tentacles-persona
description: Inspect, edit, give like/dislike feedback, evolve via LLM proposals, reevaluate, or roll back Tentacles Personas. Use when the user rates an item, adjusts interest topics/keywords, updates their profile, or wants recent items re-scored.
---

# Tentacles Persona & Feedback

Manage versioned Personas in SQLite through immutable append-only versions.

## Global invocation rule

Place `--json` before the subcommand and read `stdout` only:

```bash
tentacles --json [--config PATH] <subcommand>
```

Check exit code `4` (`conflict`): Persona mutations use optimistic locking (`--expected-version` / `base_version`); on `conflict`, re-read `persona show` and retry with the new version.

## 1. Inspect current state and history

```bash
tentacles --json persona list
tentacles --json persona show default [--version N]
tentacles --json persona history default
tentacles --json persona export default   # stdout is a JSON string containing YAML
```

Note `tag_status`: `ok`, `error` (profile saved safely, old tags kept), `skipped`, or `unchanged`.

## 2. Direct edits (`persona set`)

Use when the user gives explicit keywords or profile text (creates `v1` if missing):

```bash
# Keywords only (zero LLM cost; safe across concurrent keyword edits)
tentacles --json persona set default \
  --include "具身智能" \
  --exclude "带货" \
  --remove-exclude "促销" \
  --hard-exclude "限时特惠" \
  --remove-hard-exclude "扫码加群" \
  --note "按用户要求微调关键词"

# Replace profile text (pass --expected-version from persona show; add --skip-tags to avoid LLM cost)
tentacles --json persona set default \
  --text "优先关注本地大模型推理与开源工具链……" \
  --expected-version 3 \
  --note "聚焦本地推理"
```

- `--exclude` is a **soft exclude** (exempted when positive `include`/tag terms also match, forwarding to semantic filtering with score `0.55`).
- `--hard-exclude` is an **unconditional reject** (`0.0`).
- `--text`, `--file`, and `--stdin` are mutually exclusive; without them, `profile` stays unchanged and zero model calls occur.

## 3. Feedback -> Evolve -> Apply workflow

Use when the user likes/dislikes specific items or gives a high-level instruction:

```bash
# Step 1: Record feedback (omit --persona if only one persona or using "default")
tentacles --json feedback <ITEM_ID> --like|--dislike --persona default --note "原因说明"
tentacles --json items feedback <ITEM_ID> --persona default

# Step 2: Generate read-only dry-run proposal (calls defaults.models.persona; never writes DB)
tentacles --json persona evolve default \
  --instruction "提高对本地量化实测的优先级" \
  --expected-version 3 \
  --dry-run > proposal.json

# Step 3: Inspect proposal.json (summary, profile, tags, include, exclude, hard_exclude), then apply atomically
tentacles --json persona apply --proposal proposal.json
```

- Pass `--no-feedback` to `persona evolve` to ignore recent like/dislike history and use `--instruction` only.
- Omitting `hard_exclude` (or `null`) in a proposal keeps the current `hard_exclude` list; `"hard_exclude": []` clears it. `include` and `exclude` must always supply the full new lists.
- `persona apply` makes zero model calls and rejects stale `base_version` with exit code `4` (`conflict`).

## 4. Roll back or reevaluate

```bash
# Append a new version copying historical version VERSION (zero model calls)
tentacles --json persona rollback default <VERSION> --expected-version <CURRENT_VERSION> --note "回退原因"

# Re-score recent items under the new Persona version (never clears consumption ledger)
tentacles --json pipelines filter <PIPELINE_ID> --reevaluate --since 7d
```

## Completion criteria

- Any profile rewrite or `evolve` used `--expected-version` from a fresh `persona show` and verified the resulting `version` and `tag_status`.
- Feedback/evolution changes did not claim to clear report consumption history (`pipelines filter --reevaluate` only recomputes scores for unreported items).
