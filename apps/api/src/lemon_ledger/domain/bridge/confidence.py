"""Confidence scoring for bridge pair hypotheses.

Tier thresholds (strict; custody recognition caps the tier):
  HIGH:   |Δbps| <= 50,  |Δt| <= 30 min, custody = recognized
  MEDIUM: |Δbps| <= 200, |Δt| <= 2 h,    custody = recognized OR inferred
  LOW:    |Δbps| <= 500, |Δt| <= 4 h,    custody = any (including unknown)

Custody caps: if the strongest custody across both legs is 'unknown', the
tier is capped at LOW regardless of the numeric tolerances.
"""

from __future__ import annotations

from decimal import Decimal

from lemon_ledger.domain.bridge.pairing import PairHypothesis
from lemon_ledger.models.bridge import BridgeStatus, ConfidenceLevel, CustodyRecognition

# Thresholds
_HIGH_BPS = 50
_HIGH_SECS = 30 * 60
_MED_BPS = 200
_MED_SECS = 2 * 60 * 60
_LOW_BPS = 500
_LOW_SECS = 4 * 60 * 60

# Representative confidence scores per tier (stored for display / sort purposes).
_SCORE_HIGH = Decimal("0.95")
_SCORE_MED = Decimal("0.65")
_SCORE_LOW = Decimal("0.35")


def score_pair(
    hyp: PairHypothesis,
    custody: CustodyRecognition,
) -> tuple[ConfidenceLevel, Decimal]:
    """Return (level, score) for *hyp* given the strongest custody recognition.

    Tier is the highest tier whose numeric thresholds are met AND whose custody
    requirement is satisfied.  Returns the first (best) tier that passes both;
    no tier passes → LOW with unknown custody (always satisfies LOW threshold).
    """
    abs_bps = abs(hyp.amount_delta_bps)
    abs_dt = abs(hyp.time_delta_seconds)

    # HIGH: requires recognized custody.
    if abs_bps <= _HIGH_BPS and abs_dt <= _HIGH_SECS and custody == CustodyRecognition.RECOGNIZED:
        return ConfidenceLevel.HIGH, _SCORE_HIGH

    # MEDIUM: requires recognized or inferred custody.
    if (
        abs_bps <= _MED_BPS
        and abs_dt <= _MED_SECS
        and custody in (CustodyRecognition.RECOGNIZED, CustodyRecognition.INFERRED)
    ):
        return ConfidenceLevel.MEDIUM, _SCORE_MED

    # LOW: any custody.
    if abs_bps <= _LOW_BPS and abs_dt <= _LOW_SECS:
        return ConfidenceLevel.LOW, _SCORE_LOW

    # Outside all windows — this pair should not have been generated; treat as LOW.
    return ConfidenceLevel.LOW, Decimal("0.10")


def status_for_level(level: ConfidenceLevel) -> BridgeStatus:
    """Map confidence level to the initial bridge status."""
    if level == ConfidenceLevel.HIGH:
        return BridgeStatus.CONFIRMED
    return BridgeStatus.NEEDS_CONFIRMATION
