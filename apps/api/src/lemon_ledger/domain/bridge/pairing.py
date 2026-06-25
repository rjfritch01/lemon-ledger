"""Bridge pair hypothesis construction and persistence.

Algorithm: GLOBAL GREEDY over all eligible (outflow, inflow) pairs.

Eligibility (_eligible):
  - outflow.direction == OUTFLOW, inflow.direction == INFLOW
  - outflow.chain != inflow.chain
  - same logical_asset_id
  - |Δt| <= PAIRING_WINDOW (4 h)
  - |Δbps| <= PAIRING_AMOUNT_TOL_BPS (500 bps)

Greedy assignment:
  1. Compute all eligible pairs and rank by (|Δbps|, |Δt|) ascending.
  2. Greedily pick the best pair, mark both legs used, skip if either used.
  3. Write 'unmatched' singletons for any remaining legs.

Idempotency:
  - Never clobber a row already user-resolved (resolved_by='user').
  - On re-run, if both legs are already paired together, skip.
  - Upgrade an existing unmatched singleton in-place when its partner arrives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from lemon_ledger.domain.bridge.candidates import CandidateLeg, LegDirection
from lemon_ledger.models.bridge import BridgeCorrelation, BridgeStatus

PAIRING_WINDOW = timedelta(hours=4)
PAIRING_AMOUNT_TOL_BPS = 500


@dataclass
class PairHypothesis:
    outflow: CandidateLeg
    inflow: CandidateLeg
    time_delta_seconds: int  # signed: inflow - outflow
    amount_delta_bps: int  # signed: (inflow - outflow) / outflow * 10000


def _amount_delta_bps(outflow: CandidateLeg, inflow: CandidateLeg) -> int:
    if outflow.amount == Decimal(0):
        return 0
    delta = (inflow.amount - outflow.amount) / outflow.amount * Decimal("10000")
    return int(delta.to_integral_value())


def _eligible(out: CandidateLeg, inf: CandidateLeg) -> bool:
    if out.direction != LegDirection.OUTFLOW or inf.direction != LegDirection.INFLOW:
        return False
    if out.chain == inf.chain:
        return False
    if out.logical_asset_id != inf.logical_asset_id:
        return False
    dt_s = int((inf.occurred_at - out.occurred_at).total_seconds())
    if abs(dt_s) > int(PAIRING_WINDOW.total_seconds()):
        return False
    dbps = abs(_amount_delta_bps(out, inf))
    return dbps <= PAIRING_AMOUNT_TOL_BPS


def _build_hypotheses(legs: list[CandidateLeg]) -> list[PairHypothesis]:
    outflows = [lg for lg in legs if lg.direction == LegDirection.OUTFLOW]
    inflows = [lg for lg in legs if lg.direction == LegDirection.INFLOW]
    hyps: list[PairHypothesis] = []
    for out in outflows:
        for inf in inflows:
            if _eligible(out, inf):
                dt = int((inf.occurred_at - out.occurred_at).total_seconds())
                dbps = _amount_delta_bps(out, inf)
                hyps.append(
                    PairHypothesis(
                        outflow=out,
                        inflow=inf,
                        time_delta_seconds=dt,
                        amount_delta_bps=dbps,
                    )
                )
    hyps.sort(key=lambda h: (abs(h.amount_delta_bps), abs(h.time_delta_seconds)))
    return hyps


def _is_user_resolved(session: Session, event_id: uuid.UUID, direction: LegDirection) -> bool:
    """True if there is already a user-resolved row for this leg."""
    col = (
        BridgeCorrelation.outflow_classified_event_id
        if direction == LegDirection.OUTFLOW
        else BridgeCorrelation.inflow_classified_event_id
    )
    existing = session.scalar(
        select(BridgeCorrelation)
        .where(col == event_id, BridgeCorrelation.resolved_by == "user")
        .limit(1)
    )
    return existing is not None


def _existing_singleton(
    session: Session, event_id: uuid.UUID, direction: LegDirection
) -> BridgeCorrelation | None:
    col = (
        BridgeCorrelation.outflow_classified_event_id
        if direction == LegDirection.OUTFLOW
        else BridgeCorrelation.inflow_classified_event_id
    )
    other_col = (
        BridgeCorrelation.inflow_classified_event_id
        if direction == LegDirection.OUTFLOW
        else BridgeCorrelation.outflow_classified_event_id
    )
    return session.scalar(
        select(BridgeCorrelation)
        .where(
            col == event_id,
            other_col.is_(None),
            BridgeCorrelation.status == BridgeStatus.UNMATCHED,
            BridgeCorrelation.resolved_by.is_(None),
        )
        .limit(1)
    )


def _existing_pair(session: Session, hyp: PairHypothesis) -> BridgeCorrelation | None:
    return session.scalar(
        select(BridgeCorrelation)
        .where(
            BridgeCorrelation.outflow_classified_event_id == hyp.outflow.classified_event_id,
            BridgeCorrelation.inflow_classified_event_id == hyp.inflow.classified_event_id,
            BridgeCorrelation.status != BridgeStatus.REJECTED,
        )
        .limit(1)
    )


def pair_and_persist(
    session: Session,
    legs: list[CandidateLeg],
    *,
    user_id: uuid.UUID,
) -> list[BridgeCorrelation]:
    """Greedy pair assignment.  Returns all written/updated rows."""
    hyps = _build_hypotheses(legs)
    used: set[uuid.UUID] = set()
    written: list[BridgeCorrelation] = []

    for hyp in hyps:
        out_id = hyp.outflow.classified_event_id
        in_id = hyp.inflow.classified_event_id

        # Skip if either leg already assigned in this pass.
        if out_id in used or in_id in used:
            continue

        # Never clobber user-resolved rows.
        if _is_user_resolved(session, out_id, LegDirection.OUTFLOW):
            used.add(out_id)
            continue
        if _is_user_resolved(session, in_id, LegDirection.INFLOW):
            used.add(in_id)
            continue

        # Idempotency: skip if pair already exists (non-rejected).
        existing_pair = _existing_pair(session, hyp)
        if existing_pair is not None:
            used.add(out_id)
            used.add(in_id)
            written.append(existing_pair)
            continue

        # Upgrade singletons in-place if they exist; otherwise create new.
        out_singleton = _existing_singleton(session, out_id, LegDirection.OUTFLOW)
        in_singleton = _existing_singleton(session, in_id, LegDirection.INFLOW)

        if out_singleton is not None and in_singleton is not None:
            # Merge: keep one, delete the other.
            out_singleton.inflow_classified_event_id = in_id
            out_singleton.time_delta_seconds = hyp.time_delta_seconds
            out_singleton.amount_delta_bps = hyp.amount_delta_bps
            session.delete(in_singleton)
            session.flush()
            corr = out_singleton
        elif out_singleton is not None:
            out_singleton.inflow_classified_event_id = in_id
            out_singleton.time_delta_seconds = hyp.time_delta_seconds
            out_singleton.amount_delta_bps = hyp.amount_delta_bps
            corr = out_singleton
        elif in_singleton is not None:
            in_singleton.outflow_classified_event_id = out_id
            in_singleton.time_delta_seconds = hyp.time_delta_seconds
            in_singleton.amount_delta_bps = hyp.amount_delta_bps
            corr = in_singleton
        else:
            corr = BridgeCorrelation(
                user_id=user_id,
                logical_asset_id=hyp.outflow.logical_asset_id,
                outflow_classified_event_id=out_id,
                inflow_classified_event_id=in_id,
                status=BridgeStatus.NEEDS_CONFIRMATION,
                time_delta_seconds=hyp.time_delta_seconds,
                amount_delta_bps=hyp.amount_delta_bps,
            )
            session.add(corr)

        session.flush()
        written.append(corr)
        used.add(out_id)
        used.add(in_id)

    # Unmatched singletons for remaining legs.
    for leg in legs:
        if leg.classified_event_id in used:
            continue
        if _is_user_resolved(session, leg.classified_event_id, leg.direction):
            continue
        # Check if singleton already exists.
        singleton = _existing_singleton(session, leg.classified_event_id, leg.direction)
        if singleton is not None:
            written.append(singleton)
            continue
        leg_id = leg.classified_event_id
        s_out: uuid.UUID | None = leg_id if leg.direction == LegDirection.OUTFLOW else None
        s_in: uuid.UUID | None = leg_id if leg.direction == LegDirection.INFLOW else None
        corr = BridgeCorrelation(
            user_id=user_id,
            logical_asset_id=leg.logical_asset_id,
            outflow_classified_event_id=s_out,
            inflow_classified_event_id=s_in,
            status=BridgeStatus.UNMATCHED,
        )
        session.add(corr)
        session.flush()
        written.append(corr)

    return written
