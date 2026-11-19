# Error Codes & `debug_context` Reference

## `error_code` lookup (`errors list` / `errors show`)

| `phase` | `error_code` | Meaning & action |
| :--- | :--- | :--- |
| `fetch_page` / `fetch_detail` | `http_status_error` | Upstream 401/403/404/429/5xx (`http_status` gives code). Check auth/headers or switch to `type: browser`. |
| `fetch_page` / `fetch_detail` | `http_redirect_blocked` | Static `web` hit 301/302. Replace config `url` with `debug_context.redirect_url`. |
| `fetch_page` / `fetch_detail` | `network_timeout` | Connect/read timeout (`retryable: true`). |
| `fetch_page` / `fetch_detail` / `imap_fetch` | `connection_failed` | DNS/TLS/socket failure (`retryable: true`). Verify reachability or proxy. |
| `fetch_page` | `feed_parse_error` | Unrecognized RSS/Atom body (often login/HTML error page). Inspect `debug_context.body_preview`. |
| `parse_entry` | `selector_no_match` | CSS selector matched 0 nodes, or items lacked `title`/`link`. Compare `configured_selectors` vs `html_skeleton` / `node_samples`. |
| `parse_entry` | `entry_unusable` | RSS/Atom entry lacked both title and `guid`/`link` (`severity: warning`). |
| `fetch_detail` | `empty_extracted_body` | Detail returned HTTP 200 with empty extracted body (SPA shell or anti-bot challenge). Switch to `type: browser`. |
| `browser_replay` | `playwright_selector_timeout` | Fixed script timed out; inspect `current_url`, `page_title`, `a11y_snapshot`, `traceback`. |
| `browser_replay` | `script_assertion_failed` | Script failed assertion, returned invalid contract, violated AST gate, or returned 0 items (`warning`). |
| `browser_heal` | `pi_heal_exhausted` | Self-healing exhausted 2 candidates (`last_failure` + `trajectory_path`). Inspect page snapshot and run `sources distill` or edit script manually. |
| `browser_distill` | `pi_rpc_failed` | Controlled `pi` subprocess failed (`pi` binary/model/creds/timeout/empty `task`). Verify `pi --model <model> --thinking <level> "hello"`. |
| `browser_distill` | `pi_script_rejected` | Distilled script failed AST gate or live trial after 1 correction (`last_failure` + `trajectory_path`). |
| `script_pr` | `git_or_pr_failed` | Script saved locally; branch push or `gh pr create` failed (`warning`). Fix `gh auth status` / remote and re-run `sources distill <ID>`. |
| `llm_extract` | `hallucinated_link_dropped` | Model returned URLs absent from page (`warning`, `dropped_urls`). |
| `llm_extract` | `invalid_json_schema` | Model violated strict JSON schema (`validation_errors`, `output_preview`). |
| `imap_fetch` | `imap_auth_failed` | IMAP login failed; check `IMAP_USERNAME` / `IMAP_PASSWORD` (Gmail requires App Password). |
| `imap_fetch` | `imap_message_parse_error` | Single bad message skipped (`folder`, `uidvalidity`, `uid`); cursor still advances. |
| `filter_stage` | `decision_item_error` | Decision model failed on item; retried next run. |
| `filter_stage` | `llm_batch_parse_error` | Chat batch JSON failed even after split retry; retried next run. |
| `filter_stage` | `regex_timeout` | Keyword regex timed out (>50ms); simplify regex. |
| `filter_stage` | `filter_stage_aborted` | Batch aborted by upstream error (`debug_context` shows progress). |
| `report_process` | `summarize_batch_unparsed` | Summarize batch fell into "未能解析" (`warning`, `item_ids`, `validation_errors`). |
| `report_process` | `citation_out_of_range` | Report cited invalid `[N]` index (`out_of_range`, `max_number`). |
| Any phase | `unexpected_exception` | Unclassified failure (`retryable: false`); check `exception_type` and traceback. |

## Key `debug_context` fields by scenario

- **Cloudflare / anti-bot**: `html_title` (`Just a moment...`), `response_headers.cf-mitigated: challenge`, `retry-after`.
- **Web selector drift**: `configured_selectors`, `html_title`, `html_skeleton`, or partial failure `matched_nodes` / `skipped_nodes` / `node_samples[].node_html_snippet`.
- **Browser replay / heal / distill**: `current_url`, `page_title`, `a11y_snapshot` (<= 4096 chars), `traceback`, `last_failure`, `trajectory_path`.
- **Redaction boundary**: Model keys, `Authorization`, `Cookie`, passwords, and sensitive query params are always replaced with `[REDACTED]`. `debug_context` is capped at 16 KB (marked `truncated: true` if trimmed).
