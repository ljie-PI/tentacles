# Implementation Plans

Design and implementation plans for features, architectural changes, and documentation refactors live in **`docs/plans/`**.

## Conventions

- **Directory**: `docs/plans/` (local-only scratchpad, ignored via `.gitignore` — indexed locally by `docs/plans/README.md`).
- **Naming**: `<NN>-<kebab-case-slug>.md` (ordered within an initiative) or `<YYYY-MM-DD>-<slug>.md` for standalone plans.
- **Structure**: Every plan file states:
  1. **Goal & Scope** — observable behavior, non-goals, and constraints.
  2. **Current State & Gaps** — exact files, schemas, and invariants affected.
  3. **Design & Contracts** — YAML config changes, SQLite schema/migrations, Python/CLI/API models, error/retry boundaries.
  4. **Implementation Steps & Doc Sync** — ordered, commit-sized file changes, verification checks, **and explicit updates to `README.md`, `docs/api.md`, `skills/`, and `config/tentacles.yaml` examples** whenever user-facing config, behavior, schemas, or CLI commands change.
- **Lifecycle & Relationship to GitHub Issues / ADRs**:
  - `docs/plans/` is **gitignored** because finalized plans are published to GitHub Issues (see `docs/agents/issue-tracker.md`) rather than synced to git; tracked repository docs must never link into `docs/plans/`.
  - Durable domain vocabulary still belongs in `GLOSSARY.md` and architectural invariants in `docs/adr/` (see `docs/agents/domain.md`).
