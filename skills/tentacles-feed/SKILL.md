---
name: tentacles-feed
description: Query and read ingested items, search by relevance, and inspect generated reports via tentacles CLI. Use when asked what news/emails arrived, searching topics or keywords, verifying report citations, or reading archived items across years.
---

# Tentacles Feed & Reports

Read ingested items and generated reports using the deterministic `--json` CLI contract.

## Global invocation rule

Place global flags before the subcommand (or export `TENTACLES_JSON=1`), and parse `stdout` only (`stderr` carries logs):

```bash
tentacles --json [--config PATH] <subcommand>
```

Check exit codes: `0` success, `2` `invalid`, `3` `not_found`, `4` `conflict`/`busy`, `5` `upstream`, `1` `error`, `130` `aborted`.

## Choose the entry command

- **Browse recent items / filter by Persona or Pipeline** -> `items list`
- **Rank matches by relevance with highlight snippets** -> `items search`
- **Read full ingested body (up to 1000 units), `meta`, and all Pipeline scores** -> `items show`
- **Read compiled digest / deep / email summary reports** -> `reports list` then `reports show`

## Workflow

### 1. List or search items

```bash
# Latest items passed by a pipeline (includes content, page_url, topics, match_score, match_points)
tentacles --json items list --pipeline daily-tech --passed --since 24h --limit 50

# Cross-pipeline view by persona, minimum score, and topic
tentacles --json items list --persona default --min-score 0.8 --topic 人工智能 --since 24h

# Relevance-ranked full-text search (FTS5 BM25 for >=3-char terms; title-first for 1-2-char CJK/short terms)
tentacles --json items search '广告推荐 AI' --limit 20
```

Common filter flags shared by `items list` and `items search`:
- `--source, -s ID` (repeatable), `--since T` / `--until T` (`90s`, `30m`, `24h`, `7d`, `2w`, or ISO-8601)
- `--pipeline ID` (`--passed` / `--rejected` require `--pipeline`), or `--persona NAME` (mutually exclusive with `--pipeline`)
- `--min-score N` (`0..1`), `--topic TAG`, `--limit 1..500`, `--offset N`
- `--include-archive` to merge read-only yearly archives (`data/archive/tentacles-YYYY.db`); archived hits carry `archive_year`.

### 2. Inspect full item detail or visit source URL

```bash
tentacles --json items show <ITEM_ID> [--include-archive]
```

- If `content_truncated` is `true` and full original text is needed, fetch the item's `url` (or `page_url` / `meta.comments_url`).
- For `kind: "email"`, `url` and `page_url` are empty; check `meta` (`folder`, `uidvalidity`, `uid`, `message_id`, `attachments`).
- Without `--include-archive`, archived items return exit code `3` (`not_found`).

### 3. Read reports and verify citations

```bash
tentacles --json reports list --pipeline daily-tech --limit 10 --offset 0
tentacles --json reports show <REPORT_ID>
```

- `ReportView.markdown` holds the rendered report; `ReportView.item_ids` lists all source item IDs cited.
- Verify any claim in a report by running `items show <ID>` on the corresponding entry in `item_ids`.

See [references/item-fields.md](references/item-fields.md) when you need exact field semantics (`match_level`, `bm25_rank`, `stage_scores`, `evaluations`, `is_updated`).

## Completion criteria

- Every referenced item or report came from parsed `stdout` JSON.
- When `content_truncated` is `true` and exact full-text details are required, report that `content` was truncated at 1000 units and use `url` for the full original source.
