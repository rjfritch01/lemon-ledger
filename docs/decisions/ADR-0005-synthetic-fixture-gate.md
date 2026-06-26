# ADR-0005 — Synthetic-Fixture Gate for Form Generation

**Status**: Accepted (Stage 6, 2026-06-26)

## Context

Before merging any form-generation change, we need a way to verify that the forms pipeline produces the right numbers. Running the engine against real blockchain data is insufficient for verification because:
1. The expected values must come from somewhere independent of the engine to detect regressions.
2. Manual inspection of real-chain outputs is slow and error-prone.

## Decision

A **synthetic-fixture reconciliation harness** (S1-S8) gates form-generation changes.

### Anti-circularity rule (load-bearing)

Expected values in `domain/forms/reconcile.py::BUILTIN_FIXTURES` are **literal constants hand-computed from first principles**. They are never derived by calling engine code. If expected values were computed by the engine, the harness would pass trivially on any engine bug — defeating the purpose.

### Gate definition

Merge is permitted only when **all** of:
1. S1-S8 reconcile: `test_reconcile.py` passes (all 9 test functions).
2. Gate-guard S8: `check_gate()` reports `is_held=True` for a pending CT.
3. CI green: lint + typecheck + tests ≥ 80% coverage.

### Fixture format

Eight scenarios cover the major code paths:
- S1: Simple short-term gain
- S2: Partial FIFO sale across two lots (mixed long/short)
- S3: Long vs short split on same disposal date
- S4: Reward income (no double-count with capital gain)
- S5-A/B: Cross-entity cap-contribution (entity A zero rows, entity B LONG gain)
- S6: §267 related-party loss disallowed (col h = $0)
- S7: Gift-out (no disposal row written)
- S8: Gate-held pending CT (exit code 2 without --draft)

### Per-figure tolerance

$5 absolute delta per figure. This covers whole-dollar rounding at render time without masking real errors.

## Consequences

- Any change that breaks S1-S8 is a regression: fix the engine or the fixture, not the test assertion.
- New forms code paths (e.g., collectibles, wash sales) must add new fixtures before merge.
- The `reconcile` CLI command exposes the same logic for operator validation of production entities.
