"""Unit tests for bridge confidence scoring.

Verifies strict tier thresholds and custody recognition cap.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from lemon_ledger.domain.bridge.candidates import CandidateLeg, LegDirection
from lemon_ledger.domain.bridge.confidence import score_pair, status_for_level
from lemon_ledger.domain.bridge.pairing import PairHypothesis
from lemon_ledger.models.bridge import BridgeStatus, ConfidenceLevel, CustodyRecognition

T0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
LA_ID = uuid.uuid4()


def _hyp(bps: int, dt_secs: int) -> PairHypothesis:
    out = CandidateLeg(
        classified_event_id=uuid.uuid4(),
        direction=LegDirection.OUTFLOW,
        wallet_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        chain="lemonchain",
        logical_asset_id=LA_ID,
        token_id=uuid.uuid4(),
        amount=Decimal("100"),
        value_usd=Decimal("500"),
        occurred_at=T0,
        contract_address="0x" + "a" * 40,
    )
    inf = CandidateLeg(
        classified_event_id=uuid.uuid4(),
        direction=LegDirection.INFLOW,
        wallet_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        chain="bsc",
        logical_asset_id=LA_ID,
        token_id=uuid.uuid4(),
        amount=Decimal("100") + Decimal(bps) * Decimal("100") / Decimal("10000"),
        value_usd=Decimal("500"),
        occurred_at=T0 + timedelta(seconds=dt_secs),
        contract_address="0x" + "b" * 40,
    )
    return PairHypothesis(outflow=out, inflow=inf, time_delta_seconds=dt_secs, amount_delta_bps=bps)


# ── Tier thresholds ───────────────────────────────────────────────────────────


def test_high_tier_with_recognized_custody() -> None:
    hyp = _hyp(bps=10, dt_secs=60 * 5)  # 10 bps, 5 min
    level, score = score_pair(hyp, CustodyRecognition.RECOGNIZED)
    assert level == ConfidenceLevel.HIGH
    assert score > Decimal("0.9")


def test_high_tier_blocked_by_inferred_custody() -> None:
    hyp = _hyp(bps=10, dt_secs=60 * 5)  # HIGH numeric thresholds
    level, score = score_pair(hyp, CustodyRecognition.INFERRED)
    # Inferred custody caps at MEDIUM.
    assert level == ConfidenceLevel.MEDIUM


def test_high_tier_blocked_by_unknown_custody() -> None:
    hyp = _hyp(bps=10, dt_secs=60 * 5)
    level, score = score_pair(hyp, CustodyRecognition.UNKNOWN)
    assert level == ConfidenceLevel.LOW


def test_medium_tier_with_inferred_custody() -> None:
    hyp = _hyp(bps=150, dt_secs=60 * 60)  # 150 bps, 1 h
    level, score = score_pair(hyp, CustodyRecognition.INFERRED)
    assert level == ConfidenceLevel.MEDIUM


def test_medium_tier_blocked_by_unknown_custody() -> None:
    hyp = _hyp(bps=150, dt_secs=60 * 60)
    level, score = score_pair(hyp, CustodyRecognition.UNKNOWN)
    assert level == ConfidenceLevel.LOW


def test_low_tier_any_custody() -> None:
    hyp = _hyp(bps=400, dt_secs=3 * 60 * 60)  # 400 bps, 3 h
    for cust in CustodyRecognition:
        level, _ = score_pair(hyp, cust)
        assert level == ConfidenceLevel.LOW


def test_status_for_high_is_confirmed() -> None:
    assert status_for_level(ConfidenceLevel.HIGH) == BridgeStatus.CONFIRMED


def test_status_for_medium_is_needs_confirmation() -> None:
    assert status_for_level(ConfidenceLevel.MEDIUM) == BridgeStatus.NEEDS_CONFIRMATION


def test_status_for_low_is_needs_confirmation() -> None:
    assert status_for_level(ConfidenceLevel.LOW) == BridgeStatus.NEEDS_CONFIRMATION


# ── Score ordering ────────────────────────────────────────────────────────────


def test_scores_decrease_with_tier() -> None:
    h_hyp = _hyp(bps=10, dt_secs=60)
    m_hyp = _hyp(bps=150, dt_secs=60 * 60)
    l_hyp = _hyp(bps=400, dt_secs=3 * 60 * 60)
    _, h_score = score_pair(h_hyp, CustodyRecognition.RECOGNIZED)
    _, m_score = score_pair(m_hyp, CustodyRecognition.INFERRED)
    _, l_score = score_pair(l_hyp, CustodyRecognition.UNKNOWN)
    assert h_score > m_score > l_score
