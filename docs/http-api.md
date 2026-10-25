# HTTP API and server

Start `tentacles serve` (or `python -m tentacles.cli serve`). CLI `--host`, `--port`,
`--scheduler` / `--no-scheduler` override YAML server options. Defaults: loopback `127.0.0.1:8000`,
scheduler enabled, 30-second polling, `../frontend/dist` relative to YAML as the static root.
The loop waits for each tick; no overlapping rounds. It logs a sanitized failure and continues.
Shutdown stops polling, waits for the current tick to finish, then closes Service and its resources.
Long operations may delay shutdown up to their configured network/retry limits.

All `/api` operations except `GET /api/health` require `Authorization: Bearer TOKEN` when
`server.token` is nonempty (including OpenAPI). The comparison uses `hmac.compare_digest`.
Empty token disables authentication. Tokens must be printable ASCII without whitespace. External
binding without a token warns on stderr. This is a single-user service: use HTTPS and a trusted
reverse proxy for remote access; do not expose an unauthenticated instance.
Static pages/assets remain public so the browser can reach Settings to enter the token.
CORS allows only explicitly configured `server.cors_origins`; list origins without trailing `/`,
paths or wildcards. CORS preflights are handled before API authentication.
Disallowed browser origins are rejected before API operations, including bodyless/simple POSTs
that do not cause a CORS preflight. A no-token server also rejects unknown Host names to prevent
DNS rebinding from manufacturing a trusted same-origin request; loopback aliases, the configured
host and hosts in explicit CORS origins are trusted. A wildcard bind also accepts literal IP hosts.
For a reverse-proxy hostname with no token, explicitly list its public origin in `cors_origins`.
Non-browser clients without Origin remain supported on trusted hosts. Cross-site requests without
an Origin are rejected when the browser supplies cross-site fetch metadata.
Access logging is disabled to keep request URLs/content out of logs and stdout. An outer ASGI
privacy boundary also sanitizes lifespan failure messages and prevents raw framework exception
rethrows from reaching Uvicorn logs. Interrupted responses preserve only a fixed safe error.

## Routes

OpenAPI is `GET /api/openapi.json`; all operations declare response models shared with the CLI.
Generate types offline with `python -m tentacles.api_schema`, without user configuration or DB access.

| Method / path | Response / behavior |
| --- | --- |
| `GET /api/health` | `{ "ok": true }`, no token required |
| `GET /api/settings` | non-sensitive display metadata: `{ "timezone": "Asia/Shanghai" }`; no credentials or paths |
| `GET /api/sources` | `SourceView[]` |
| `POST /api/sources/fetch` | optional `{ "ids": ["id"] }`, returns `FetchOutcome[]`; omitted/empty IDs fetch enabled sources |
| `POST /api/sources/{source_id}/fetch` | `FetchOutcome`; unknown ID 404, busy 409, failed fetch 502 |
| `GET /api/items` | `Page[ItemView]`; `sources` and `kinds` may repeat; `since`, `until`, `search`, `pipeline`, `passed`, `limit`, `offset` |
| `GET /api/items/{item_id}` | `ItemDetail`, including body and evaluations |
| `GET /api/personas` | 当前 `PersonaView[]` |
| `GET /api/personas/current` | `name` 必填，`version` 可选（正 int64）；自然名称含 `/` 也作为查询值传递 |
| `PATCH /api/personas` | `name`、`expected_version` 必填；0 表示创建；profile、skip_tags、关键词 add/remove、note 同 Service |
| `GET /api/personas/history` | `name` 必填，返回版本快照列表 |
| `POST /api/personas/evolve` | `name`、`expected_version`、可选 instruction/use_feedback；始终只预览，返回 `PersonaProposal` |
| `POST /api/personas/apply` | 原样提交提案，校验 base_version 后返回新 `PersonaView` |
| `POST /api/personas/rollback` | `name`、`version`、`expected_version`，可选 note；创建新版本而不是删除历史 |
| `GET /api/items/{item_id}/feedback` | 可选 persona，返回最新 `FeedbackView` 或 null |
| `POST /api/items/{item_id}/feedback` | rating 严格整数 +1/-1，可选 persona/note，返回 `FeedbackView` |
| `GET /api/pipelines` | `PipelineView[]`：persona、来源、处理方式、计划、待报告数和最近状态 |
| `POST /api/pipelines/{pipeline_id}/filter` | `PipelineOutcome`；可选 `{ "reevaluate": true, "since": "7d" }`，since 仅配合重新评估 |
| `POST /api/pipelines/{pipeline_id}/run` | `PipelineOutcome`；ok/no_new 为 200，busy 409，处理失败 502 |
| `GET /api/reports` | `Page[ReportView]`，可选 `pipeline`、`limit`、`offset`（int64 上限） |
| `GET /api/reports/{report_id}` | `ReportView`，Markdown 与引用 item_ids |
| `GET /api/models` | `ModelView[]`，只有 key_configured 布尔值，没有密钥 |
| `POST /api/models/{name}/test` | `ModelTest`：完成的测试即使不可用也返回 200 / ok=false，保留延迟和已脱敏的错误；未知模型 404 |
| `GET /api/runs` | `RunView[]`, optional `kind`, `target`, `limit` |
| `GET /api/scheduler` | `TaskView[]` from Service |
| `GET /api/scheduler/loop` | polling enabled/running state, interval, cycle/failure counts and last tick/error |
| `POST /api/scheduler/tick` | `TickOutcome[]`, manual due-task execution even if the polling loop is disabled |

`since` / `until` accept the same duration/ISO/local-date formats as CLI. Page limits are 1–500,
offset between 0 and SQLite’s signed 64-bit maximum. Out-of-range numbers/dates are invalid (400). API filters reject unknown item kinds. Batch fetch and tick return each target's status,
including mixed failures, rather than turning a partially successful batch into a single HTTP error.
A failed individual fetch's detailed sanitized status is available in run/source records.

## Errors

```json
{"error":{"code":"not_found","message":"unknown item 'missing'"}}
```

Domain errors: `invalid` 400, `not_found` 404, `conflict` and `busy` 409, `upstream` 502,
unexpected exceptions 500 (`error`, generic message). Missing/wrong token is 401 (`unauthorized`),
with `WWW-Authenticate: Bearer`. Untrusted browser origins or no-token API hosts are 403 (`forbidden`). Request validation is 400, not FastAPI's default 422.
Request-shape validation errors never echo their input. Domain errors may identify supplied IDs or invalid values, with configured credentials redacted; unexpected exceptions use a generic message.

## Frontend

The Vite development proxy explicitly preserves Host (`changeOrigin: false`), so same-origin
frontend POSTs pass the origin guard without granting broad development CORS access.

See [frontend setup](../frontend/README.md). Build separately with pnpm, then keep `server.static_dir`
pointed at the built directory. GETs outside `/api` serve a matching asset or fall back to index.html;
unknown API routes remain 404 JSON and resolved paths outside the static root are not served.
If no build exists, API works normally and non-API GETs return a helpful 404.


The frontend formats Feed/detail/source timestamps using the authenticated `/api/settings` timezone,
not the browser timezone. If the browser does not support that IANA timezone, it shows explicitly
labeled UTC and a warning rather than silently showing local time.


## Management pages

Pipelines actions, model tests and manual ticks are explicit, potentially billable operations, never
triggered by loading a page. Even disabled pipelines can be run manually, matching Service/CLI.
Pipeline error responses are generic; Runs contains the persistent run status/stats/known fees.
Model availability is a completed diagnostic result (`ok=false` with latency/error), not an API error.
Its provider error excerpt retains Service's configured-key redaction and bounds.

Feed shares its pipeline and passed filters with the URL. Cleared pipeline selection clears the
passed filter. Reports render GFM tables, suppress raw HTML, restrict links and turn images into
explicit no-referrer links rather than auto-fetching remote content. Referenced items can be opened
in Feed. Visible Runs and scheduler views refresh every five seconds; background tabs pause polling by default, and refresh on return when stale; null cost is displayed as unknown.
Saving a browser token reloads Settings after clearing caches, resetting active observers and prior
probe results so a credential change cannot leave stale protected data visible.


## Persona and feedback

Persona names are query/body values to preserve existing Unicode, slash and space names without
reserved-path ambiguity. API edits and rollback require an explicit expected_version; a concurrent
change returns 409. The same version remains fenced after paid tag extraction. Existing CLI defaults
remain unchanged when no expected version is supplied. Stale previews are rejected before model work;
apply also checks the proposal base version atomically.

Evolution's use_feedback defaults true; false sends only the saved persona and instruction, with a
zero feedback count. Empty instruction without eligible/enabled feedback is invalid. Previews do not
write persona versions or runs; model calls may still incur cost. Applying a preview is a separate
explicit action. Profile changes may call the tag model; extraction failure still saves the profile,
retains previous tags and reports tag_status=error. Tags are read-only in the manual editor.

Feedback remains append-only for existing evolution semantics. GET returns the latest insertion for
the selected persona/item, even if the wall clock moves backward. No vote-delete operation is added;
re-clicking an already selected thumb is a UI no-op, the opposite thumb records a new event. Votes are
optimistic display state only: failed requests restore previous state and show an error, then refetch.
Mutations retain their original item/persona key across selection changes; card/drawer share a cache.

The editor holds an explicit draft/base snapshot rather than replacing it on query refresh. Conflict
refresh is explicitly labeled as abandoning the draft. Switching named personas asks before discarding
work; new-persona creation is an exclusive form. Changed evolution instruction/feedback choice clears
an old preview. Profile diffs use jsdiff line changes, with bounded work and a full-text fallback for
very large differences; no model-provided HTML is rendered.

The corresponding agent CLI exposes optional `--expected-version` on set/evolve/rollback,
`--no-feedback` on evolution, and `items feedback` for latest-vote reads. Existing defaults are unchanged.
Unsaved evolution instructions and feedback-choice changes are included in the editor's work guard;
manual profile saves retain these inputs, while apply/confirmed rollback/explicit discard clears them.

When Feed has a selected pipeline, feedback stays disabled until that pipeline's persona metadata is
successfully resolved. Missing or failed metadata never silently falls back to default, even when an
old cache entry exists. Clear the pipeline filter to choose an explicit standalone feedback persona.

Source diagnostics retain safe error categories and HTTP status rather than source URLs, raw network/
parser messages, arbitrary exception text or tracebacks. This applies to logs, fetch outcomes, source
state and run records, including failure-recording/lease cleanup. CLI unexpected failures likewise
report a category only; structured expected domain errors retain their normal safe diagnostics.
