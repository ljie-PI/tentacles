# Tentacles Web

Independent Vite + React + TypeScript application. Uses TanStack Query and plain CSS variables, no UI framework.

## Development

Install Python dependencies in the repository's `.venv`, use Node >=22.12 and pnpm 10.33:

```sh
# repository root
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/tentacles serve --no-scheduler
# another terminal
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Vite binds loopback and proxies `/api` to `http://127.0.0.1:8000` with `changeOrigin: false`, preserving the browser’s Host/Origin pair, so no CORS configuration is needed.
If the backend requires a token, save it on Settings. It is stored in this browser's localStorage;
401 redirects there with a notice. Saving a token clears cached queries. Never use an untrusted browser.

## Types and production

```sh
pnpm generate:api    # exports OpenAPI from an empty config; never opens a DB or calls a provider
pnpm check:api       # fails if committed src/api-types.ts differs
pnpm check:dev-proxy # real isolated Vite + Uvicorn; fake source HTTP, checks fetch and hostile-Origin rejection
pnpm typecheck
pnpm build
```

The generator uses `../.venv/bin/python`, or `TENTACLES_PYTHON` if set to another installed Python.
TypeScript stays on 5.9 because the current openapi-typescript release declares a TypeScript 5 peer.
Commit regenerated API types and `pnpm-lock.yaml`. Build output is ignored; deploy `dist` alongside
this repo or point `server.static_dir` at it. `tentacles serve` serves that directory with SPA fallback;
unknown `/api` paths stay JSON errors. A standalone Python wheel does not bundle the frontend: deploy
its separately built assets and configure `static_dir`.

Feed filters and selected detail ID are URL state. Pagination uses the API's offset/limit; query changes
start a new page sequence. Details render content as text (not raw HTML). Sources supports manual fetch
and invalidates source/feed/run/scheduler caches after success or failure. Themes are persisted per browser.

Noto Sans SC Variable is self-hosted with the application so Chinese text does not depend on system
font availability or an external font CDN. Fonts load by Unicode range; the distributed OFL notice is
in `public/fonts/OFL-NotoSansSC.txt`. Navigation icons are inline SVG, not a system icon font.

Timestamps use the server's non-sensitive `/api/settings` timezone metadata. No browser-local
fallback is used; an unsupported timezone displays labeled UTC with a warning. A late 401 from an
old token does not redirect after a new token has been saved.

Management pages include Pipelines, Reports, Runs and expanded Settings. Feed supports pipeline and
passed/rejected URL filters with interval-colored score badges, tags, reason and final stage. Reports
use react-markdown + remark-gfm for tables; raw HTML is skipped, unsafe links are not clickable,
and remote images become explicit no-referrer links (no automatic tracking-image fetch).
Runs/scheduler refresh every 5 seconds, preserving unknown costs. Model tests are manual and may
incur fees. Token save intentionally reloads Settings so mounted queries/local test results cannot
retain data from old credentials. Narrow navigation wraps rather than overflowing the page.
