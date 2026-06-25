# ADR-0002: WLEMX Mapped Into the LEMX Logical Asset (Canonical Pool)

**Status:** Accepted
**Date:** 2026-06-24
**Supersedes:** 1.3 tier-1 seed (which left WLEMX standalone)

## Context

Native LEMX (ERC-20 zero address on Lemonchain) and Wrapped LEMX (WLEMX,
`0x84862e65ebf37af91a8b85283b58505de3352588`) are economically 1:1 equivalents.
Wrapping native LEMX deposits it into the WLEMX contract and mints an equal amount
of WLEMX; unwrapping burns WLEMX and returns native LEMX. No gain or loss realises
on this operation under current IRS guidance for like-kind exchanges within a single
chain.

## Decision

WLEMX is mapped into the **LEMX canonical logical asset** via a row in
`token_asset_memberships`, alongside native LEMX. Both tokens share a single
cost-basis pool keyed by `(wallet_id, logical_asset_id)` per Rev. Proc. 2024-28.

The lot engine's `canonical_pool_key()` resolves WLEMX to the LEMX pool, so
wrap and unwrap events (classified as `wrap`/`unwrap` by the 1.6 `WrapRecognizer`)
generate `LotTreatment.NONE` — no lot is opened or consumed, and no gain/loss is
recorded.

## Consequences

- Wrap/unwrap are true no-ops from a tax-lot perspective: basis date and cost
  carry through unchanged.
- Any future BEP-20 LEMX representation on BSC may be added to the same logical
  asset in a later migration without engine changes.
- The 1.7 migration (`a2b3c4d5e6f7`) seeds the `logical_assets` row and both
  memberships; it fails loudly if either token_registry row is missing.
