# #18 implementation audit

This maps the parent specification's user stories to the implemented child issues and observable
validation. Passing tests are evidence, not a guarantee of defect-free behavior.

| Parent stories | Implemented scope | Evidence |
| --- | --- | --- |
| 1–18 | YAML sources, RSS/Atom/conditional fetch, independent schedules, read-only incremental IMAP, selector/page-change/grounded LLM web sources, durable identity/dedup, isolation/manual fetch | #19/#20/#28/#30/#31; `test_config`, `test_fetch_rss`, `test_fetch_imap`, `test_fetch_web`, `test_web_extract`, `test_dedup`, `test_items` |
| 19–26 | Named lazy models, provider protocols, per-stage/explicit model selection, env keys, no-key startup, probes, retries and known usage/cost | #21/#25/#26/#31; `test_models`, `test_decision_filter`, `test_llm_filter`, source/processor failure and cancellation regressions |
| 27–39 | Versioned personas, stable tags/keyword editing, feedback, bounded evolution with optional feedback, read-only preview/apply CAS, immutable history/new-version rollback, multiple names | #22/#27/#35; `test_personas`, `test_persona_evolution`, `test_persona_web`, `test_persona_api`; browser dislike → preview → apply → rollback, 409 and draft preservation |
| 40–48 | Keyword/decision/LLM cascades, thresholds, stage traces, batch splitting, privacy preferences, versioned evaluation and retry | #23/#25/#26; `test_pipelines`, `test_decision_filter`, `test_llm_filter`; Feed pipeline/passed/score/tag/reason/stage views |
| 49–58 | Independent pipeline/filter scheduling, digest/deep/summarize/none, prompt/language overrides, files+DB publication, no repeated consumption/no empty reports | #23/#24/#29/#32; `test_pipelines`, `test_pipeline_scheduler`, `test_summarize`, `test_deep`, preserved clustering tests |
| 59–65 | Persistent schedules, cron enrollment/interval startup, fenced leases, tick ordering, status/runs, optional serving poller and graceful draining | #20/#24/#33; `test_schedule`, `test_scheduler`, `test_pipeline_scheduler`, `test_api`, `test_server_logs`; actual CLI subprocess and scheduler shutdown checks |
| 66–73 | Noninteractive CLI, one JSON stdout value, error exit mapping, item filters/detail, feedback/proposal pipe, report/source/pipeline actions, config checks and agent guide | `test_cli`, `docs/agent-cli.md`; new optional expected-version/no-feedback flags and `items feedback` preserve old defaults |
| 74–81 | Feed/Sources/Pipelines/Reports/Runs/Settings/Persona, optimistic feedback rollback, GFM reports, model/scheduler controls, CSS-variable dark/responsive UI | #33/#34/#35; frontend typecheck/build/type-generation parity; actual browser flows and narrow 390px iframe screenshots |
| 82–85 | Loopback default/token/explicit CORS, cross-site and rebinding guard, exposed-bind warning, combined serve/static SPA, legacy article migration without deleting history | #19/#33; config/API/logging/CLI and legacy SQLite fixture tests, installed-wheel smoke; `docs/http-api.md` deployment guidance |

## Important invariants

- Pipeline consumption is independent of persona version and reevaluation. Updating or rolling back a
  persona never clears consumption, source identity history, or existing reports.
- Feedback stays append-only; latest-vote reads do not delete evidence. Evolution retains the configured
  feedback window/count and the cutoff after the last LLM version.
- Persona version checks remain fenced after paid tag extraction. Stale UI saves/previews/rollback/apply
  fail with conflict rather than overwriting newer content. Existing CLI defaults remain compatible.
- Cancellation drains owned IO; expired owners cannot overwrite replacement cursors/state or reports.
  Known provider usage/cost is retained where the operation's completion metadata is available.
- Model/source credentials remain isolated. API errors/logs do not expose raw unexpected exceptions;
  Markdown raw HTML/automatic remote images and unsafe links are not executed.

## Scope and verification limits

- Tests and browser fixtures use fake HTTP/IMAP/clock boundaries. They do not verify a real mailbox,
  provider account, current external model availability, or actual charges. No paid model is needed for tests.
- Mobile evidence is a real narrow iframe viewport, not physical-device emulation. Foreground five-second
  polling timing was not independently observed through the background-tab browser tool; normal library
  visibility pausing and configured intervals are documented.
- Report exception rollback is covered. Cross-filesystem/SQLite crash atomicity under abrupt power loss
  is not promised or independently verified.
- The independent frontend is built/deployed separately; a Python wheel alone does not include its assets.
  Remote exposure requires deliberate token/HTTPS proxy configuration. OAuth2/XOAUTH2 IMAP, JS-rendered
  scraping, notifications, multi-user accounts, remote/object storage and background job queues remain
  out of scope exactly as specified.
- The parent's architecture overview uses a generic CRUD phrase for personas, but its explicit API/CLI
  lists, user stories and #22/#35 acceptance criteria specify create/edit/read/history/preview/apply/rollback,
  not destructive deletion. No history-delete operation was added; rollback creates a new immutable version.
