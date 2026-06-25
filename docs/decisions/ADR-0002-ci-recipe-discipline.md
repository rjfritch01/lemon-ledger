# ADR-0002: CI Recipe Discipline — Verification Commands Must Equal Justfile Recipes

**Date:** 2026-06-25
**Status:** Accepted

## Context

Two separate instances of local verification passing while CI failed on the same
commit, both caused by the same class of error: a hand-typed command covered a
different scope than the justfile recipe that CI actually runs.

**Instance 1 — ruff auto-fix drift (Chat 1.x):**
The pre-commit hook ran `ruff --fix` and rewrote code. The developer did not
re-run `ruff check` after the fix. CI's lint step (`ruff check` without `--fix`)
then rejected the auto-modified output. The local pass was on an intermediate
state that was never re-validated after the auto-fix applied.

**Instance 2 — mypy scope drift (Chat 1.9, Stage 1, PR #23):**
Local verification ran `uv run mypy src/` (82 source files, 0 errors).
CI runs `just api-typecheck` = `uv run mypy src/ tests/` (131 files).
All 39 errors were in `tests/`, which the local command never checked.
The Stage 1 report claimed "mypy --strict clean" — the claim was true for the
scope that was checked, but false for the scope CI uses.

Both instances share the same root cause: the developer (or agent) re-typed the
underlying shell command rather than invoking the justfile recipe, and the
re-typed command covered a narrower scope than the recipe.

## Decision

1. **Local verification always invokes `just <recipe>`.** Never re-type the
   underlying shell command. The justfile is the single source of truth for what
   each check covers.

2. **`[tool.mypy] files = [...]` is set in `pyproject.toml`** so that a bare
   `uv run mypy` with no path arguments covers the same files as
   `just api-typecheck`. This eliminates the specific scope mismatch for mypy.

3. **CLAUDE.md documents the canonical commands** in a "CI / pre-commit recipe
   discipline" section, including a running log of drift instances so the
   pattern is visible.

4. **Any agent or human verification step that runs a check must cite which
   justfile recipe it is equivalent to.** If no justfile recipe exists yet for
   a check, create one rather than documenting a raw command.

## Consequences

- `uv run mypy` (bare) and `just api-typecheck` now cover identical scope.
  Scope mismatch for mypy is structurally impossible going forward.
- New checks added to CI must also be added to the justfile before they can
  be declared "locally verified."
- The CLAUDE.md drift log provides a lightweight audit trail without requiring
  a new ADR for every future instance.
