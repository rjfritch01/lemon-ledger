# ADR-0001: Monorepo Foundation and Local Dev Stack

**Date:** 2026-06-05
**Status:** Accepted

## Context

Lemon Ledger is a read-only crypto portfolio and tax platform being built from scratch.
We need to pick a repository structure, language/framework set, and local infrastructure
stack before any application code is written.

Key constraints:
- A single engineer (initially) who needs fast iteration loops.
- A clear seam between the React frontend and the Python API so they can evolve independently.
- Secrets must never be committed; production config must be operator-managed.
- The stack should be conventional enough that contributors can onboard quickly.

## Decision

**Monorepo layout** — pnpm workspaces with `apps/*` and `packages/*`. Keeps the frontend
and API in one repository with shared tooling, while preserving clear module boundaries.

**API** — Python 3.12 + FastAPI. Python 3.12 is pinned via `.python-version` (pyenv-compatible).
FastAPI is async-first and generates OpenAPI docs automatically.

**Web** — React (scaffolded in a later step). Placeholder at `apps/web`.

**Data stores** — Postgres 16 (primary persistence) and Redis 7 (caching / task queues),
both running locally via Docker Compose with health checks and named volumes.

**Task runner** — `just`. Single binary, cross-platform, self-documenting (`just --list`).

**Secret management** — `.env` for local dev (gitignored); Doppler for production and CI.
`.env.example` documents every variable; no defaults are committed as real secrets.

**Node/Python version pins** — `.nvmrc` (Node 22 LTS) and `.python-version` (3.12)
ensure consistent environments across machines and CI.

## Consequences

- All contributors must have Docker, just, pnpm, and Python 3.12 installed locally.
- Docker Compose manages local infrastructure; `just up` / `just down` are the single UX for it.
- Adding a new shared package means creating a directory under `packages/` and registering
  it as a pnpm workspace — low overhead.
- Production secrets flow through Doppler; the platform team controls access, not git history.
