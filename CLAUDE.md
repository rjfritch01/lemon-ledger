# Lemon Ledger — Project Conventions

## What this project is
Lemon Ledger is a **read-only** cryptocurrency tax record-keeping tool for the
LEMX ecosystem across Lemonchain (L2) and BNB Smart Chain (BSC). It generates
**informational drafts** of US tax forms (Form 8949, Schedule D, Schedule 1 Line 8z)
and an Activity / Gain-Loss Report for tax-preparation use by the user and their CPA.
It NEVER signs or sends transactions and NEVER claims to file taxes.
"Read-only" is a hard, build-enforced constraint. (ADR-0004)

## Locked architectural invariants (never violate)
- **NUMERIC, never float**, for any token or monetary value. Schema-wide law.
- **String columns + CHECK constraints, never native PostgreSQL enums.**
- **All schema changes via Alembic migrations only.** Never `create_all`.
  Migrations are reversible and additive. CI enforces a single migration head.
- **UUIDv7 primary keys, generated app-side**, via `uuid_utils.compat.uuid7`
  (Python 3.12 has no stdlib uuid7; do not use `uuid.uuid4`).
- **`timestamptz`, UTC, everywhere.**
- **`ON DELETE RESTRICT` is the FK default.** Soft-delete via `is_active`.
  Deviating to CASCADE requires an explicit, justified, per-FK decision.
- **No transaction signing anywhere.** Semgrep blocks `send_transaction`,
  `send_raw_transaction`, `.transact()`, `sign_transaction`.
- **Migrations run as a Railway release step, never at app startup.**
- **Schema changes via feature branch + PR.** Four CI checks (Lint, Type Check,
  Security Scan, Test) must pass before merge.

## Tech stack
- Python 3.12; FastAPI; SQLAlchemy 2.0 (async); Alembic; Celery; Redis 7;
  Postgres 16.
- Tooling: uv; Ruff (format + lint); mypy (strict); Bandit; Semgrep; pre-commit.
- Testing: Pytest; Testcontainers (real Postgres); real Redis for rate-limiter
  and lock tests (fakeredis is incompatible with Lua eval); 80% coverage gate.

## Repo layout
- `apps/api/src/lemon_ledger/` (src layout).
- `Base` lives in `lemon_ledger.db.base`.
- Domain logic in `lemon_ledger/domain/`.
- Table classes in `lemon_ledger/models/`, each registered in
  `models/__init__.py` so Alembic autogenerate sees them.

## How we work
- **Architecture-first, one decision at a time.** Recommend with rationale,
  confirm, then implement. Code without an accompanying actionable prompt is not
  actionable.
- **Serial-only (no parallel work) for:** tax math, lot engine logic, classifier
  semantics, schema changes, wallet authorization.
- **Parallel-safe** work uses git worktrees on isolated feature branches.
- **PR discipline:** all changes via feature branch + PR; four required CI checks;
  `enforce_admins: false`.

## CI / pre-commit recipe discipline
The canonical local verification commands are the `just` recipes, NOT hand-typed
re-implementations of the underlying commands:

| Check | Canonical command |
|---|---|
| Lint + format | `just api-lint` |
| Type check | `just api-typecheck` |
| Tests | `just api-test` |
| Full gate | run all three in order |

**Never run `uv run mypy src/` directly.** Always run `just api-typecheck`
(`uv run mypy src/ tests/`). A bare path-scoped command may silently exclude
files that CI covers.

Pre-commit vs CI drift — recorded instances:
- **ruff auto-fix drift** — pre-commit's `ruff --fix` rewrote code the CI lint
  check then rejected; always check with `--check` before committing.
- **mypy scope drift** — local `uv run mypy src/` passed (82 files) while CI
  `uv run mypy src/ tests/` failed with 39 errors in tests/; always run
  `just api-typecheck`, never a bare path-scoped mypy. (ADR-0002)

## Tax / domain rules (money-relevant — get these right)
- **Per-wallet lot pooling is mandatory** under Rev. Proc. 2024-28 (effective
  Jan 1, 2025). Pool key is `(wallet_id, canonical_asset)`. Per-entity pooling is
  WRONG and overridden.
- **Average Cost basis is not permitted** for US crypto holdings. It is
  informational-only and removed from `entities.default_basis_method`.
  HIFO / LIFO / Min-Tax are Specific Identification strategies and require
  `selection_strategy` and `selected_at` for audit defensibility.
- **Bridge taxability is unsettled.** Confirmed bridges are non-taxable
  relocations; unmatched/rejected pairs fall back to taxable. Per-entity
  `bridge_treatment` defaults to `relocate`; `jurisdiction` defaults to US.
- **BSC endpoint is `api.etherscan.io/v2/api` with `chainid=56`.**
  `api.bscscan.com` was deprecated December 2025 — never use it.
- **WLEMX must be in `token_asset_membership`** under the LEMX logical asset so
  wrap/unwrap is a genuine no-op.
- **`InsufficientLotsError` surfaces hard** to the gate. Never fabricate phantom
  basis.
- **Conservative defaults on financially-consequential classifications:** flag
  `pending` rather than guessing. Unresolved fees flag `pending`, never zero-basis.
- **Burn addresses never auto-book a capital loss.** Candidate burn addresses
  require trust-gated discovery.
- **Two parallel audit tables by deliberate design:** `bridge_audit_log` (bridge
  domain) and `classification_audit_log` (cross-entity pending_classifications).
  Identical column shape. Disjoint domains. A future chat may unify them but must
  not break either. Do NOT unify without an explicit decision.
- **Resolve service mirrors bridge workflow pattern:** `resolve_classification`
  stamps `transfer_resolution` + writes `classification_audit_log` + sets
  state='classified'. It writes ZERO rows to tax_lots/lot_disposals — lot
  materialization is Stage 4's job, full stop.
- **App-layer kind↔choice validity:** `ALLOWED[kind]` in
  `domain/cross_entity/resolve.py` enforces which `ChosenClassification` values
  are valid per `PendingClassificationKind`. Intentionally NOT a DB cross-column
  CHECK constraint — the DB only validates that each column value is in-set.
- **⚠ KNOWN LIMITATION — Cross-entity/external encumbrance is GATE-LEVEL, not
  engine-level, in v1.** The lot engine (`domain/lots/engine.py`) cannot distinguish
  an unresolved cross-entity/external leg (`transfer_resolution NULL`,
  `classification 'transfer-out'`) from an ordinary taxable transfer-out, because
  the distinguishing signal lives in `pending_classifications`, which the engine must
  not read (1.3 invariant: engine never reads pending_classifications). The
  phantom-disposal property is preserved UPSTREAM: `needs_classification` →
  `v_lot_gate` blocks the wallet → cross-entity pass precedes lot apply →
  `generate-8949` refuses on a held gate. **CONSEQUENCE:** any new caller wiring
  work into the lot engine MUST run downstream of the gate / cross-entity pass;
  calling `apply_event` directly on a wallet with unresolved legs would dispose
  them. Tracked in ADR-0003. A future hardening option is for detection.py to stamp
  an engine-visible PENDING marker on Branch 2/3 legs (deferred — would need a
  `transfer_resolution` CHECK extension).

## Stage 6 additions (ADRs 0004-0006)
- **Product is informational record-keeping, not tax filing.** Every PDF and CSV
  carries a mandatory disclaimer. Claims use "informational draft" language. (ADR-0004)
- **Mandatory disclaimer text** (all forms, every page): "INFORMATIONAL DRAFT —
  NOT FILED TAX ADVICE. Generated by Lemon Ledger for tax-preparation and
  record-keeping. Crypto-to-crypto trades are taxable disposals under US law.
  Review with a licensed tax professional before filing. Lemon Ledger and its
  author are not tax advisors." (ADR-0006)
- **Synthetic-fixture gate (ADR-0005):** Merge of form-generation code requires
  S1-S8 reconcile harness (`tests/forms/test_reconcile.py`) to pass. Expected
  values are LITERAL CONSTANTS, never derived from engine output (anti-circularity).
  $5 per-figure tolerance. S8 specifically tests gate-guard (`is_held=True`).
- **Activity / Gain-Loss Report** (`domain/forms/activity_report.py`): builds from
  `fetch_acquisition_rows` + `fetch_disposal_rows` + `fetch_reward_income`. CSV and
  PDF output. CLI: `lemon-ledger forms activity-report --year --entity --user`.
- **Celery pipeline ordering:** `run_cross_entity_detection_task` MUST run after
  `classify_wallet` and BEFORE `apply_lots_for_wallet`. `run_user_lot_pipeline_task`
  in `tasks/sync.py` enforces this. Never call `apply_event` directly on a wallet
  with unresolved cross-entity legs.
- **`surface_aged_unmatched` sync-watermark gate:** now gated on
  `wallet.last_synced_at ≥ occurred_at + 24 h` in addition to the 7-day raw clock.
  Prevents false-positive taxable-fallback on an un-ingested counterparty chain.
- **`reconcile` CLI:** `lemon-ledger forms reconcile --entity --year --expected <ID|path>`
  compares actual form output against a fixture (S1-S8 built-in or JSON file).
  Exit 0 = PASS, 2 = FAIL. `--waivers` demotes figure mismatches to warnings.
