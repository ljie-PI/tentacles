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
Access logging is disabled to keep request URLs/content out of logs and stdout.

## Routes

OpenAPI is `GET /api/openapi.json`; all operations declare response models shared with the CLI.
Generate types offline with `python -m tentacles.api_schema`, without user configuration or DB access.

| Method / path | Response / behavior |
| --- | --- |
| `GET /api/health` | `{ "ok": true }`, no token required |
| `GET /api/sources` | `SourceView[]` |
| `POST /api/sources/fetch` | optional `{ "ids": ["id"] }`, returns `FetchOutcome[]`; omitted/empty IDs fetch enabled sources |
| `POST /api/sources/{source_id}/fetch` | `FetchOutcome`; unknown ID 404, busy 409, failed fetch 502 |
| `GET /api/items` | `Page[ItemView]`; `sources` and `kinds` may repeat; `since`, `until`, `search`, `pipeline`, `passed`, `limit`, `offset` |
| `GET /api/items/{item_id}` | `ItemDetail`, including body and evaluations |
| `GET /api/runs` | `RunView[]`, optional `kind`, `target`, `limit` |
| `GET /api/scheduler` | `TaskView[]` from Service |
| `GET /api/scheduler/loop` | polling enabled/running state, interval, cycle/failure counts and last tick/error |
| `POST /api/scheduler/tick` | `TickOutcome[]`, manual due-task execution even if the polling loop is disabled |

`since` / `until` accept the same duration/ISO/local-date formats as CLI. Page limits are 1–500,
offset >=0. API filters reject unknown item kinds. Batch fetch and tick return each target's status,
including mixed failures, rather than turning a partially successful batch into a single HTTP error.
A failed individual fetch's detailed sanitized status is available in run/source records.

## Errors

```json
{"error":{"code":"not_found","message":"unknown item 'missing'"}}
```

Domain errors: `invalid` 400, `not_found` 404, `conflict` and `busy` 409, `upstream` 502,
unexpected exceptions 500 (`error`, generic message). Missing/wrong token is 401 (`unauthorized`),
with `WWW-Authenticate: Bearer`. Request validation is 400, not FastAPI's default 422.
Responses never echo validation input, unexpected exception content or configured credentials.

## Frontend

See [frontend setup](../frontend/README.md). Build separately with pnpm, then keep `server.static_dir`
pointed at the built directory. GETs outside `/api` serve a matching asset or fall back to index.html;
unknown API routes remain 404 JSON and resolved paths outside the static root are not served.
If no build exists, API works normally and non-API GETs return a helpful 404.
