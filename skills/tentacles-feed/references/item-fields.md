# Item & Report JSON Fields

## `ItemView` / `ItemSearchView`

| Field | Semantics |
| :--- | :--- |
| `id` | 16-char hex primary key derived from `source_id + external_id`. |
| `source_id`, `kind` | Source ID and `rss` \| `email` \| `web` \| `browser`. |
| `url` | Normalized original item URL (tracking params & `#fragment` stripped). Empty string for `email`. |
| `page_url` | Entry page URL (Feed URL, Web list/page URL, or Browser entry URL). Empty string for `email`. |
| `summary` | Cleaned summary (<= 300 units; `web` `mode: page` stores `unified_diff` here). |
| `content`, `content_truncated` | Ingested text capped at 1000 units (1 CJK char or 1 non-CJK word = 1 unit). `true` means full text requires visiting `url`. |
| `updated_at`, `is_updated` | Present when `update_existing: true` observed a real content change; `fetched_at` stays the first ingest time. |
| `topics`, `match_score`, `match_level`, `match_points` | Primary interest matching fields from the latest valid evaluation (`input_revision == content_revision`). Legacy aliases: `tags`, `score`, `reason`. |
| `match_level` | `high` (>= 0.8), `medium` (>= 0.5), `low` (< 0.5), `rejected` (`passed == false`), or `unscored`. |
| `stage`, `stage_scores` | Deciding stage (`keyword` / `semantic` / legacy `llm` / `decision`) and per-stage score trace (`details.executor` shows `chat` vs `decision`). |
| `bm25_rank`, `snippet` | Present in `items search` (`snippet` also appears in `items list --search`). Lower `bm25_rank` (more negative) = more relevant; `null` for 1–2-char short-term fallback. |
| `archive_year` | `null` for hot DB (`data/tentacles.db`); integer year (e.g. `2025`) when read from `tentacles-YYYY.db`. |

## `ItemDetail` additions (`items show`)

- `external_id`: stable source-local identity (RSS GUID, email `Message-ID` or `folder:UIDVALIDITY:UID`, normalized URL, or `URL#hash`).
- `fingerprint`: normalized `sha1(title + summary)`.
- `meta`: source-specific metadata (`full_content_sha1`, `comments_url`, `browser_script`, `browser_entry_url`, or email `folder`/`uidvalidity`/`uid`/`message_id`/`to`/`cc`/`attachments`).
- `evaluations[]`: latest evaluation per Pipeline on its newest Persona version (includes `reported_at` UTC timestamp when consumed by a report, else `null`; may include historical `input_revision`).

## `ReportView` (`reports show`)

- `id`, `pipeline_id`, `persona_version`, `kind` (`digest` \| `deep` \| `summarize`), `title`, `markdown`, `item_ids`, `model`, `usage`, `cost`, `path`, `created_at`.
