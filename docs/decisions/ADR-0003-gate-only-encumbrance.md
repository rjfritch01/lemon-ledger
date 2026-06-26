# ADR-0003 — Gate-only encumbrance for unresolved cross-entity legs (v1)

**Date:** 2026-06-25
**Status:** Accepted
**Deciders:** Ryan Fritzsche

---

## Context

Stages 2 and 3 introduced cross-entity transfer detection and a resolve service.
When detection identifies an unresolved cross-entity or external-outflow leg
(Branches 2 and 3 in `detection.py`), it writes a `pending_classifications` row but
leaves the `ClassifiedTransaction` unchanged:

- `classification` stays `'transfer-out'`
- `transfer_resolution` stays `NULL`

The lot engine (`domain/lots/engine.py`) dispatches on `transfer_resolution` first
(Stage 4 addition) and falls back to `classification`.  An unresolved outflow has
`transfer_resolution=NULL`, so it falls back to `classification='transfer-out'` →
`LotTreatment.DISPOSE` — the same treatment as an ordinary taxable sale.

The engine is explicitly prohibited from reading `pending_classifications`
(architectural invariant: the engine materialises lots from CT signals only; it must
not couple to the classification state machine).

## Decision

**Encumbrance is enforced at the gate level, not the engine level, in v1.**

The pipeline ordering guarantees the property:

```
needs_classification → v_lot_gate source (e) blocks the wallet
                     → cross-entity pass precedes lot-apply
                     → generate-8949 refuses on a held gate
```

No sentinel value is added to `transfer_resolution`, and no new CHECK migration is
introduced for this purpose.  The engine is not changed to handle the NULL case
differently.

## Consequences

**Positive:**
- No additional migration or enum value required.
- Engine remains coupled only to `transfer_resolution` and `classification` — clean,
  auditable signal path.
- Gate blocking is already tested by `test_cross_entity_pending_appears_in_v_lot_gate`
  (Stage 2) and `test_t3_unresolved_cross_entity_blocks_in_gate` (Stage 4).

**Negative / known limitation:**
- Any caller that invokes `apply_event` directly on a wallet with unresolved legs
  (bypassing the gate) would incorrectly dispose those lots.  This is a footgun for
  future integrators.  The constraint is documented in `CLAUDE.md`.

## Deferred hardening option

Detection could stamp `transfer_resolution='pending-review'` (a new enum value) on
Branch 2/3 legs when it creates the pending row.  The engine would then route the
unresolved outflow to `LotTreatment.PENDING` → skip.  This approach requires:

1. Extending the `classified_transactions.transfer_resolution` CHECK constraint
   (new migration).
2. Updating `detection.py` Branches 2 and 3 to stamp the sentinel on the outflow CT.
3. Updating Stage 3 `resolve.py` to overwrite the sentinel with the real resolution
   value at resolve time (idempotent, since the resolve service already stamps the CT).

This is not pursued in v1 because the gate ordering guarantee is sufficient for the
current pipeline, and adding the sentinel increases the surface area of the detection
pass without a clear operational need.
