# Contributing to Lemon Ledger

## Commit conventions

All commits must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`

**Scopes (optional):** `api`, `db`, `models`, `migrations`, `security`, `ci`

Examples:
- `feat(api): add wallet balance endpoint`
- `fix(models): enforce NUMERIC type on amount columns`
- `ci: add coverage gate to test job`

The `conventional-pre-commit` hook enforces this at commit time.

## PR flow

1. Branch from `main` — use `feat/`, `fix/`, `ci/`, or `chore/` prefixes.
2. Keep PRs focused; one logical change per PR.
3. Fill in the PR template — especially the checklist.
4. Four CI checks must be green before merge: **Lint**, **Type Check**, **Security Scan**, **Test**.
5. The Claude Adversarial Review posts a comment automatically — it is non-blocking.
6. Squash-merge into `main` with a Conventional Commit message.

## Local dev quick-start

```bash
just up          # start Postgres + Redis
just api-install # install Python deps
just api-test    # tests + coverage gate
just api-lint    # ruff
just api-typecheck   # mypy strict
just api-security    # bandit + semgrep
just precommit       # run all pre-commit hooks
```
