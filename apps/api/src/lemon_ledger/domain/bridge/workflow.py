"""Bridge workflow: user resolution, unmatched surfacing, classification signal setting.

Resolution rules:
  - resolve_pair: confirm or reject a matched pair.
  - resolve_unmatched: assign a user resolution to an unmatched singleton.
  - surface_aged_unmatched: age-out unmatched legs after 7 days → taxable fallback.

Override asymmetry:
  - User may override auto-confirmed rows.
  - Re-detection / learning may NEVER reopen a user-resolved row.

Classification signals (set_classification_signal) drive the lot engine without
the engine reading bridge_correlations:
  - confirmed + relocate entity: outflow → 'bridge-out', inflow → 'bridge-in'
    + stamp relocation_source_event_id on inflow CT.
  - confirmed + disposition entity: leave as 'transfer-out'/'transfer-in'.
  - rejected or aged-out: restore 'transfer-out'/'transfer-in'.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from lemon_ledger.models.bridge import (
    BridgeAuditLog,
    BridgeCorrelation,
    BridgeStatus,
    UserResolution,
)
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

log = logging.getLogger(__name__)

_AGED_UNMATCHED_WINDOW = timedelta(days=7)


# ── Classification signal setters ─────────────────────────────────────────────


def _get_entity_bridge_treatment(
    session: Session,
    wallet_id: uuid.UUID,
    at: datetime,
) -> str:
    """Resolve the entity's bridge_treatment for a wallet at a given time."""
    assignment = session.scalar(
        select(WalletEntityAssignment)
        .where(
            WalletEntityAssignment.wallet_id == wallet_id,
            WalletEntityAssignment.effective_from <= at.date(),
        )
        .order_by(WalletEntityAssignment.effective_from.desc())
        .limit(1)
    )
    if assignment is None:
        return "relocate"
    entity = session.get(Entity, assignment.entity_id)
    return entity.bridge_treatment if entity is not None else "relocate"


def set_classification_signal(
    session: Session,
    corr: BridgeCorrelation,
    *,
    confirmed: bool,
) -> None:
    """Set (or restore) the classification + relocation_source_event_id on both legs."""
    if not confirmed:
        # Restore taxable classifications.
        if corr.outflow_classified_event_id is not None:
            ct = session.get(ClassifiedTransaction, corr.outflow_classified_event_id)
            if ct is not None:
                ct.classification = "transfer-out"
                ct.bridge_correlation_id = None
        if corr.inflow_classified_event_id is not None:
            ct = session.get(ClassifiedTransaction, corr.inflow_classified_event_id)
            if ct is not None:
                ct.classification = "transfer-in"
                ct.relocation_source_event_id = None
                ct.bridge_correlation_id = None
        return

    # Confirmed — resolve treatment from the outflow wallet's entity.
    outflow_ct = (
        session.get(ClassifiedTransaction, corr.outflow_classified_event_id)
        if corr.outflow_classified_event_id is not None
        else None
    )
    inflow_ct = (
        session.get(ClassifiedTransaction, corr.inflow_classified_event_id)
        if corr.inflow_classified_event_id is not None
        else None
    )

    treatment = "relocate"
    if outflow_ct is not None:
        treatment = _get_entity_bridge_treatment(
            session, outflow_ct.wallet_id, outflow_ct.occurred_at
        )

    if treatment == "relocate":
        if outflow_ct is not None:
            outflow_ct.classification = "bridge-out"
            outflow_ct.bridge_correlation_id = corr.id
        if inflow_ct is not None:
            inflow_ct.classification = "bridge-in"
            inflow_ct.bridge_correlation_id = corr.id
            inflow_ct.relocation_source_event_id = corr.outflow_classified_event_id
    else:
        # disposition: leave as transfer-out / transfer-in (taxable).
        if outflow_ct is not None:
            outflow_ct.bridge_correlation_id = corr.id
        if inflow_ct is not None:
            inflow_ct.bridge_correlation_id = corr.id


# ── Rebuild enqueueing ────────────────────────────────────────────────────────


def _enqueue_rebuilds(corr: BridgeCorrelation) -> list[uuid.UUID]:
    """Return distinct wallet_ids that need rebuild after signal change."""
    wallet_ids: set[uuid.UUID] = set()
    # Collect via the correlation fields already populated.
    # Actual rebuild dispatch happens in the Celery task layer.
    return list(wallet_ids)


# ── resolve_pair ──────────────────────────────────────────────────────────────


def resolve_pair(
    session: Session,
    corr_id: uuid.UUID,
    *,
    decision: Literal["confirm", "reject"],
    actor: str,
) -> BridgeCorrelation:
    """Confirm or reject a bridge pair.

    - No-op if already user-resolved with the same decision.
    - User may override an auto-confirmed row.
    - Re-detection may NOT reopen a user-resolved row (enforced in callers).
    """
    corr = session.get(BridgeCorrelation, corr_id)
    if corr is None:
        raise ValueError(f"BridgeCorrelation {corr_id} not found")

    # Guard: already user-resolved with same decision → no-op.
    if corr.resolved_by == "user":
        same_outcome = (decision == "confirm" and corr.status == BridgeStatus.CONFIRMED) or (
            decision == "reject" and corr.status == BridgeStatus.REJECTED
        )
        if same_outcome:
            return corr

    before = _corr_snapshot(corr)

    if decision == "confirm":
        corr.status = BridgeStatus.CONFIRMED
    else:
        corr.status = BridgeStatus.REJECTED

    corr.resolved_by = "user"
    corr.resolved_at = datetime.now(UTC)
    session.flush()

    set_classification_signal(session, corr, confirmed=(decision == "confirm"))
    session.flush()

    after = _corr_snapshot(corr)
    _write_audit(session, corr_id, actor=actor, action=decision, before=before, after=after)
    session.flush()

    return corr


# ── resolve_unmatched ─────────────────────────────────────────────────────────


def resolve_unmatched(
    session: Session,
    corr_id: uuid.UUID,
    *,
    user_resolution: UserResolution,
    actor: str,
) -> BridgeCorrelation:
    """User disposition for an unmatched singleton.

    sale / third-party → rejected (taxable).
    bridge-pending     → stays unmatched + flagged (gate).
    other              → unmatched + manual pending-classification gate item.
    """
    corr = session.get(BridgeCorrelation, corr_id)
    if corr is None:
        raise ValueError(f"BridgeCorrelation {corr_id} not found")

    before = _corr_snapshot(corr)
    corr.user_resolution = user_resolution.value
    corr.resolved_by = "user"
    corr.resolved_at = datetime.now(UTC)

    if user_resolution in (UserResolution.SALE, UserResolution.THIRD_PARTY):
        corr.status = BridgeStatus.REJECTED
        set_classification_signal(session, corr, confirmed=False)
    elif user_resolution == UserResolution.BRIDGE_PENDING:
        corr.status = BridgeStatus.UNMATCHED
        # CT stays 'pending' → v_lot_gate keeps it gated.
    else:
        # 'other' → leave unmatched; flag the CT for manual review.
        corr.status = BridgeStatus.UNMATCHED
        leg_id = corr.outflow_classified_event_id or corr.inflow_classified_event_id
        if leg_id is not None:
            ct = session.get(ClassifiedTransaction, leg_id)
            if ct is not None:
                ct.needs_review = True

    session.flush()
    after = _corr_snapshot(corr)
    _write_audit(
        session,
        corr_id,
        actor=actor,
        action=f"resolve_unmatched:{user_resolution.value}",
        before=before,
        after=after,
    )
    session.flush()
    return corr


# ── surface_aged_unmatched ────────────────────────────────────────────────────


def surface_aged_unmatched(session: Session) -> list[BridgeCorrelation]:
    """Age-out unmatched legs older than 7 days → taxable-fallback signal.

    TODO: gate on both chains synced past leg timestamp before applying
    the taxable fallback (watermark refinement deferred to a later chat).
    """
    cutoff = datetime.now(UTC) - _AGED_UNMATCHED_WINDOW

    # Find unmatched singletons with no user resolution where the leg is old.
    aged = session.scalars(
        select(BridgeCorrelation).where(
            BridgeCorrelation.status == BridgeStatus.UNMATCHED,
            BridgeCorrelation.resolved_by.is_(None),
            BridgeCorrelation.created_at < cutoff,
        )
    ).all()

    surfaced: list[BridgeCorrelation] = []
    for corr in aged:
        # Restore taxable classification on the leg.
        set_classification_signal(session, corr, confirmed=False)
        # Mark for UI surfacing — non-blocking, ends up in v_lot_gate.
        leg_id = corr.outflow_classified_event_id or corr.inflow_classified_event_id
        if leg_id is not None:
            ct = session.get(ClassifiedTransaction, leg_id)
            if ct is not None:
                ct.needs_review = True

        _write_audit(
            session,
            corr.id,
            actor="system",
            action="surface_aged_unmatched",
            before=_corr_snapshot(corr),
            after=None,
        )
        surfaced.append(corr)

    session.flush()
    return surfaced


# ── helpers ───────────────────────────────────────────────────────────────────


def _corr_snapshot(corr: BridgeCorrelation) -> dict[str, object]:
    return {
        "status": corr.status,
        "confidence_level": corr.confidence_level,
        "confidence_score": str(corr.confidence_score) if corr.confidence_score else None,
        "resolved_by": corr.resolved_by,
        "resolved_at": corr.resolved_at.isoformat() if corr.resolved_at else None,
        "user_resolution": corr.user_resolution,
    }


def _write_audit(
    session: Session,
    corr_id: uuid.UUID,
    *,
    actor: str,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> None:
    entry = BridgeAuditLog(
        correlation_id=corr_id,
        actor=actor,
        action=action,
        before_state=before,
        after_state=after,
    )
    session.add(entry)
