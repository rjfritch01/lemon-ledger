"""E2E bridge pipeline test: ingest → run_bridge_pass → lot apply.

Exercises the full path from classified events through bridge correlation
to lot engine treatment.

SKIPPED: curated custody seed data and real sponsor wallet captures are not
yet in the repo.  Once the bridge contract addresses are confirmed from the
sponsor wallet fixtures, remove the skip and fill in the real addresses and
amounts below.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "TODO: requires real sponsor bridge contract addresses in custody_addresses "
        "(Phase-1 curated seed) and real sponsor wallet classified_transaction fixtures. "
        "Remove skip once migration 0009 is updated with confirmed addresses and "
        "test fixtures are added under tests/fixtures/bridge/."
    )
)


def test_bridge_e2e_happy_path_no_8949_entry() -> None:
    """
    Confirmed bridge (outflow → inflow, relocate treatment) must produce
    ZERO Form 8949 entries (no LotDisposal rows for bridge legs).

    Placeholder — implement once:
      1. Real LEMX bridge contract address is in migration 0009 seed.
      2. Sponsor wallet CTs are available as fixtures in tests/fixtures/bridge/.
      3. The e2e session with full migrations is available.
    """
    pytest.skip("Sponsor wallet fixtures not yet available")


def test_bridge_e2e_fee_bearing_bridge_no_micro_disposal() -> None:
    """
    Fee-bearing bridge (outflow 100, inflow 99) must NOT produce a micro-disposal
    for the 1-unit fee.  The arrived units carry full consumed basis.
    """
    pytest.skip("Sponsor wallet fixtures not yet available")


def test_bridge_e2e_retroactive_confirm_reverses_fallback() -> None:
    """
    Retroactive confirm of an aged-out leg:
      1. CT was restored to 'transfer-out' (taxable fallback) after 7 days.
      2. User confirms the bridge pair.
      3. Scoped rebuild reverses the taxable disposal and produces relocation.
      4. Re-run produces identical state (idempotency).
    """
    pytest.skip("Sponsor wallet fixtures not yet available")


def test_bridge_e2e_wrap_unwrap_not_a_bridge_candidate() -> None:
    """
    WLEMX wrap/unwrap classified as 'wrap'/'unwrap' must NOT appear in
    find_candidate_legs output (classification guard drops them).
    """
    pytest.skip("Sponsor wallet fixtures not yet available")


def test_bridge_e2e_same_chain_transfer_stays_unmatched() -> None:
    """
    Same-chain transfer-out → unmatched singleton, never paired.
    """
    pytest.skip("Sponsor wallet fixtures not yet available")
