# Phase 1 Ground-Truth Audit — 2026-06-17

Auditor: Claude (read-only, no source files modified)
Branch audited: `feat/1.2b-sync-worker-ingestion-cli`
Latest commit: `81151f9` — feat(worker): sync worker, ingestion engine, and CLI (Chat 1.2b)

---

## 1. Deliverable Status Table

| # | Deliverable | Status | Evidence / Notes |
|---|---|---|---|
| 1 | Monorepo scaffold (pnpm workspaces, justfile, Docker Compose PG16 + Redis7) | **DONE** | `pnpm-workspace.yaml` (apps/*, packages/*); `justfile` (15 recipes); `docker-compose.yml` (postgres:16-alpine, redis:7-alpine) |
| 2 | FastAPI skeleton + health endpoints, SQLAlchemy 2.0 async, connection pooling | **DONE** | `apps/api/src/lemon_ledger/main.py`; `GET /health/live`, `GET /health/ready`; `create_async_engine` + `async_sessionmaker`; `pool_size=5, max_overflow=10, pool_pre_ping=True` |
| 3 | Alembic initialized; reversible additive migrations; single-head gate | **DONE** | `apps/api/alembic.ini`; two migrations, both have downgrade functions; linear chain (no branching): `None → 294f76baacc3 → 351ece0cc2cf` |
| 4 | Core schema tables: users, entities, wallets, wallet_entity_assignments, token_registry, raw_transactions, raw_token_transfers, raw_internal_txs, raw_logs | **DONE** | All 9 tables present across 2 migrations and 7 model files; lot/tax/bridge tables absent (expected at this frontier) |
| 5 | CI: 4 required checks + adversarial review Action | **DONE** | `.github/workflows/ci.yml`: jobs `lint`, `typecheck`, `security`, `test`; `.github/workflows/claude-review.yml`: Claude Opus 4.8 adversarial review on PR open/sync |
| 6 | Semgrep read-only rule | **DONE** | `tools/semgrep/no-transaction-sending.yml`: blocks `send_transaction`, `send_raw_transaction`, `.transact()`, `sign_transaction` with `severity: ERROR --error` flag |
| 6 | Bandit | **DONE** | `pyproject.toml [tool.bandit]`; run in CI (`just api-security`) and pre-commit |
| 6 | mypy strict | **DONE** | `pyproject.toml [tool.mypy] strict = true`; run in CI (`just api-typecheck`) targeting `src/ tests/` |
| 6 | Ruff | **DONE** | `pyproject.toml [tool.ruff]`; `E,F,I,UP,B` rules; run in CI (`just api-lint`) and pre-commit with `--fix` |
| 6 | pre-commit hooks | **DONE** | `.pre-commit-config.yaml`: 12 hooks — trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-added-large-files, check-merge-conflict, detect-private-key, ruff, ruff-format, bandit, semgrep-no-transaction-sending, conventional-pre-commit |
| 7 | Blockscout (Lemonchain) client + rate limit + retry/backoff | **DONE** | `src/lemon_ledger/clients/blockscout.py`; `RedisTokenBucket` (4 RPS, token bucket via Lua); tenacity `wait_exponential_jitter(initial=1, exp_base=2, max=60)`, 5 max attempts; 429/Retry-After honored |
| 8 | Raw tables + Celery sync worker + incremental sync + idempotency | **DONE** | Migration `351ece0cc2cf` creates all 4 raw tables; `worker.py` + `tasks/sync.py` (Celery); `ingestion/sync.py` (`last_synced_block` cursor, chunk-based, cursor written after commit); `ON CONFLICT DO NOTHING` with unique constraints |
| 9 | Token registry seed (Lemonchain Tier-1) | **ABSENT** | No seed migration, script, or data file anywhere; `token_registry` has zero rows; WLEMX not present in codebase |
| 10 | CLI: `lemon-ledger sync --wallet <addr> --chain lemonchain` | **DONE** | `src/lemon_ledger/cli.py`; entry point `lemon-ledger = "lemon_ledger.cli:app"`; `sync` command with `--wallet`, `--chain`, `--local/--remote`, `--full/--incremental`; `wallet add` command also present |
| 11 | BSC Etherscan V2 client + chain abstraction | **ABSENT** | `build_blockscout_client()` raises `ValueError` for any chain outside `lemonchain`/`lemonchain-testnet`; no BSC client file; `bsc` exists only as an allowed CHECK string in schema |
| 12 | Pricing / oracle integration | **ABSENT** | `token_registry` has nullable `pricing_source_primary/fallback` text columns (schema placeholder only); no pricing client or market-data fetch |
| 13 | Per-L2 decoders | **ABSENT** | Raw data stored as JSONB blobs; mappers do minimal field extraction only; no ABI decoding |
| 14 | Bridge correlation | **ABSENT** | `bridge` appears only as allowed wallet `role` value; no correlation or cross-chain linkage code |
| 15 | Lot engine (FIFO/HIFO/SpecID, per-wallet pooling) | **ABSENT** | `entity.default_basis_method` stores preference string; no lot computation or cost-basis engine |
| 16 | Form 8949 / Schedule D / Schedule 1 generation | **ABSENT** | No tax report, 8949 formatter, or reporting module exists |

---

## 2. Confirmed Frontier

**Current frontier: Chat 1.2b fully merged.**

| Chat prompt | Content | State |
|---|---|---|
| Chat 1.1 | Monorepo scaffold, CI, security tooling, pre-commit, Docker Compose | **Merged** |
| Chat 1.2a | Blockscout client, raw ingestion tables (Migration 2) | **Merged** (commit `09856f4`) |
| Chat 1.2b | Sync worker, ingestion engine, Celery tasks, CLI | **Merged** (commit `81151f9`) |
| Chat 1.2c (queued?) | Token registry seed — Lemonchain Tier-1 tokens, WLEMX | **Not merged** |
| Chat 1.3+ | BSC client, chain abstraction, pricing, decoders, bridge, lot engine, 8949 | **Not started** |

The user's prior belief ("Chat 1.2 prompts 1-2 merged, prompt 3 queued, prompt 4 pending") is **correct but slightly off on numbering**. The frontier is after 1.2b (sync worker + CLI), with token registry seed as the next unmerged item. All downstream deliverables (items 11-16) are confirmed absent.

---

## 3. Gaps & Risks

### 3a. Invariant Violations

| Invariant | Required | Actual | Risk |
|---|---|---|---|
| FK `ON DELETE` | RESTRICT | **CASCADE** on all FK constraints | High — in a tax accounting system, cascading wallet or user deletes could silently destroy raw transaction data; should be RESTRICT with explicit soft-delete or archive pattern |
| Primary key type | UUIDv7 (time-ordered) | **UUID4** (random, `uuid.uuid4()`) | Medium — random UUIDs cause B-tree index fragmentation under insert load; UUIDv7 preserves insertion order. All 9 tables affected. |
| CLAUDE.md | Should exist | **Absent** | Low — no conventions file for Claude Code agents working in this repo |

### 3b. Coverage Configuration Mismatch

`[tool.coverage.run] source = ["src/lemon_ledger"]` in `pyproject.toml` uses a filesystem path, not the installed package name (`lemon_ledger`). Coverage.py resolves `source` as an importable name; `src/lemon_ledger` will not resolve correctly after `uv` installs the package under its canonical name. The `--cov=src/lemon_ledger` CLI flag (also a path) partially compensates, but the `[tool.coverage.run]` stanza may be silently inert. Fix: change `source = ["lemon_ledger"]`.

### 3c. apps/web Stub

`apps/web/` contains only `package.json` and no source. Not a risk for backend work, but noted for completeness.

### 3d. packages/ Empty

`packages/` contains only `.gitkeep`. No shared packages exist. Noted for completeness.

### 3e. No Single-Head CI Enforcement

The single-head state is currently correct (verified), but there is no CI step that runs `alembic heads` and asserts count == 1. This would catch accidental branching. Currently enforced only by convention.

### 3f. Schema Note: `wallet_entity_assignments` Composite Unique Constraint

Migration `294f76baacc3` creates a **partial unique index** `uq_wea_wallet_current` on `wallet_entity_assignments(wallet_id) WHERE effective_to IS NULL` — enforcing only one active assignment per wallet. This is intentional design (allows historical assignments) and not a violation, but worth documenting for lot engine work later.

---

## 4. Open Inputs

1. **Token registry seed content**: How many Tier-1 Lemonchain tokens should the seed migration insert? What are the canonical contract addresses, decimals, and `pricing_source_primary` values? Is WLEMX the wrapped native or a separate ERC-20?

2. **ON DELETE intent**: Were all FK constraints intentionally set to CASCADE, or was RESTRICT the design goal? The audit found CASCADE everywhere; if RESTRICT was intended, the migration DDL needs to change before any prod data exists.

3. **UUIDv7 migration path**: Was UUID4 an intentional choice (simpler, no library dependency) or an oversight? If UUIDv7 is still the target, it requires a new library (`uuid6` or Python 3.13+ `uuid.uuid7()`) and updated `default=` on all PK columns — easiest to fix now before data exists.

4. **Single-head CI gate**: Should `alembic heads --resolve-dependencies | wc -l` be added as a CI step, or is convention + pre-commit sufficient?

---

## Appendix: Track-by-Track Source References

| Track | Key files audited |
|---|---|
| A — Tooling | `pnpm-workspace.yaml`, `package.json`, `justfile`, `docker-compose.yml`, `apps/api/pyproject.toml`, `.pre-commit-config.yaml` |
| B — CI/Security | `.github/workflows/ci.yml`, `claude-review.yml`, `claude-code-review.yml`, `claude.yml`, `tools/semgrep/no-transaction-sending.yml` |
| C — Schema | `apps/api/alembic.ini`, `migrations/versions/20260605_2212_294f76baacc3_name_initial_schema.py`, `migrations/versions/20260607_1323_351ece0cc2cf_name_raw_ingestion_tables.py`, `src/lemon_ledger/models/*.py`, `src/lemon_ledger/main.py`, `src/lemon_ledger/db/session.py` |
| D — Ingestion | `src/lemon_ledger/clients/blockscout.py`, `src/lemon_ledger/worker.py`, `src/lemon_ledger/tasks/sync.py`, `src/lemon_ledger/ingestion/sync.py`, `src/lemon_ledger/ingestion/mappers.py`, `src/lemon_ledger/cli.py` |
| E — Tests | `apps/api/tests/conftest.py`, `tests/test_*.py`, `tests/clients/test_*.py`, `tests/ingestion/test_*.py`, `tests/tasks/test_sync_task.py` |
