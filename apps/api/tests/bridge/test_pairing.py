"""Unit tests for the bridge pairing algorithm.

Tests _eligible, _amount_delta_bps, and the greedy pair_and_persist logic
using MagicMock sessions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from lemon_ledger.domain.bridge.candidates import CandidateLeg, LegDirection
from lemon_ledger.domain.bridge.pairing import (
    PAIRING_WINDOW,
    _amount_delta_bps,
    _eligible,
)

T0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
LA_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _leg(
    direction: LegDirection,
    chain: str = "lemonchain",
    amount: str = "100",
    occurred_at: datetime = T0,
    logical_asset_id: uuid.UUID | None = None,
) -> CandidateLeg:
    return CandidateLeg(
        classified_event_id=uuid.uuid4(),
        direction=direction,
        wallet_id=uuid.uuid4(),
        user_id=USER_ID,
        chain=chain,
        logical_asset_id=logical_asset_id or LA_ID,
        token_id=uuid.uuid4(),
        amount=Decimal(amount),
        value_usd=Decimal("500"),
        occurred_at=occurred_at,
        contract_address="0x" + "c" * 40,
    )


# ── _amount_delta_bps ─────────────────────────────────────────────────────────


def test_amount_delta_bps_exact_match() -> None:
    out = _leg(LegDirection.OUTFLOW, amount="100")
    inf = _leg(LegDirection.INFLOW, amount="100")
    assert _amount_delta_bps(out, inf) == 0


def test_amount_delta_bps_fee_deducted() -> None:
    out = _leg(LegDirection.OUTFLOW, amount="100")
    inf = _leg(LegDirection.INFLOW, amount="99")
    # -1/100 * 10000 = -100 bps
    assert _amount_delta_bps(out, inf) == -100


def test_amount_delta_bps_zero_outflow() -> None:
    out = _leg(LegDirection.OUTFLOW, amount="0")
    inf = _leg(LegDirection.INFLOW, amount="10")
    assert _amount_delta_bps(out, inf) == 0


# ── _eligible ─────────────────────────────────────────────────────────────────


def test_eligible_happy_path() -> None:
    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="bsc", occurred_at=T0 + timedelta(minutes=10))
    assert _eligible(out, inf)


def test_eligible_same_chain_rejected() -> None:
    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="lemonchain", occurred_at=T0 + timedelta(minutes=10))
    assert not _eligible(out, inf)


def test_eligible_wrong_directions() -> None:
    out = _leg(LegDirection.INFLOW, chain="lemonchain")
    inf = _leg(LegDirection.OUTFLOW, chain="bsc", occurred_at=T0 + timedelta(minutes=10))
    assert not _eligible(out, inf)


def test_eligible_different_logical_asset() -> None:
    other_la = uuid.uuid4()
    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="bsc", logical_asset_id=other_la)
    assert not _eligible(out, inf)


def test_eligible_outside_time_window() -> None:
    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="bsc", occurred_at=T0 + timedelta(hours=5))
    assert not _eligible(out, inf)


def test_eligible_at_window_boundary() -> None:
    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="bsc", occurred_at=T0 + PAIRING_WINDOW)
    assert _eligible(out, inf)


def test_eligible_amount_too_far() -> None:
    out = _leg(LegDirection.OUTFLOW, amount="100", chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, amount="90", chain="bsc")  # -1000 bps
    assert not _eligible(out, inf)


def test_eligible_amount_at_tolerance() -> None:
    # 500 bps = 5% fee → inflow = 95
    out = _leg(LegDirection.OUTFLOW, amount="100", chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, amount="95", chain="bsc")
    assert _eligible(out, inf)


# ── greedy assignment (unit, MagicMock session) ───────────────────────────────


def _mock_session_no_existing() -> MagicMock:
    """Session that reports no existing correlations."""
    session = MagicMock()
    session.scalar.return_value = None
    session.flush.return_value = None
    session.add.return_value = None
    session.delete.return_value = None
    return session


def test_pair_and_persist_creates_matched_pair() -> None:
    from lemon_ledger.domain.bridge.pairing import pair_and_persist

    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(
        LegDirection.INFLOW,
        chain="bsc",
        occurred_at=T0 + timedelta(minutes=5),
    )
    session = _mock_session_no_existing()

    written = pair_and_persist(session, [out, inf], user_id=USER_ID)
    # Two legs → one matched pair (no unmatched singletons).
    assert len(written) == 1
    assert session.add.called


def test_pair_and_persist_unmatched_singleton_for_leftover() -> None:
    from lemon_ledger.domain.bridge.pairing import pair_and_persist

    # Two outflows — only one can pair with one inflow; the other is unmatched.
    out1 = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    out2 = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="bsc", occurred_at=T0 + timedelta(minutes=5))
    session = _mock_session_no_existing()

    written = pair_and_persist(session, [out1, out2, inf], user_id=USER_ID)
    # One pair + one unmatched singleton.
    assert len(written) == 2


def test_pair_and_persist_no_cross_chain_no_pairs() -> None:
    from lemon_ledger.domain.bridge.pairing import pair_and_persist

    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="lemonchain", occurred_at=T0 + timedelta(minutes=5))
    session = _mock_session_no_existing()

    written = pair_and_persist(session, [out, inf], user_id=USER_ID)
    # No eligible cross-chain pair → two unmatched singletons.
    assert len(written) == 2


def test_pair_and_persist_never_clobbers_user_resolved() -> None:
    """User-resolved outflow should be skipped, inflow becomes unmatched."""
    from lemon_ledger.domain.bridge.pairing import pair_and_persist

    out = _leg(LegDirection.OUTFLOW, chain="lemonchain")
    inf = _leg(LegDirection.INFLOW, chain="bsc", occurred_at=T0 + timedelta(minutes=5))
    session = MagicMock()

    user_resolved_corr = MagicMock()
    user_resolved_corr.resolved_by = "user"

    def _scalar_side_effect(stmt: object) -> object:
        # First call: checking if outflow is user-resolved → return a user-resolved row.
        # All other calls → None.
        if not hasattr(_scalar_side_effect, "calls"):
            _scalar_side_effect.calls = 0  # type: ignore[attr-defined]
        _scalar_side_effect.calls += 1  # type: ignore[attr-defined]
        if _scalar_side_effect.calls == 1:
            return user_resolved_corr
        return None

    session.scalar.side_effect = _scalar_side_effect
    session.flush.return_value = None
    session.add.return_value = None

    pair_and_persist(session, [out, inf], user_id=USER_ID)
    # The user-resolved outflow prevents a pair; inflow becomes unmatched.
    # We just verify no error and that add was called for the inflow singleton.
    assert session.add.called
