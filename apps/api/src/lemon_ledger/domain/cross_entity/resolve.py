"""Cross-entity classification resolve service.

State machine for pending_classifications rows:
  needs_classification -> classified -> applied | dismissed

Entry points:
  resolve_classification  — first-time or idempotent same-choice resolve
  reclassify              — change an already-classified row
  dismiss                 — abandon the row (with optional redirect signal)

Rules hook:
  resolve accepts resolved_by='rule' + rule_id; the actual matcher returning a
  rule_id is a no-op stub (_match_rule) — seam only, logic is Phase 2.

Stamping mirrors the 1.8 bridge convention:
  - relocate-* and cross-entity gift: both legs stamped; inflow.relocation_source_event_id
    = outflow.id (inflow links to outflow, same as detection.py Branch 1).
  - disposal / disposal-related-party / gift-out / no-op-loan: outflow leg only.

ALLOWED sets (kind ↔ choice — app-layer, no DB cross-column CHECK):
  cross-entity     -> capital-contribution, sale, gift, loan, reassignment
  external-outflow -> sale, gift, payment
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from lemon_ledger.models.classification_audit import ClassificationAuditLog
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.enums import (
    ChosenClassification,
    PendingClassificationKind,
    PendingClassificationState,
    TransferResolution,
)
from lemon_ledger.models.pending_classification import PendingClassification

# ── Constants ─────────────────────────────────────────────────────────────────

ALLOWED: dict[str, frozenset[str]] = {
    PendingClassificationKind.CROSS_ENTITY.value: frozenset(
        {
            ChosenClassification.CAPITAL_CONTRIBUTION.value,
            ChosenClassification.SALE.value,
            ChosenClassification.GIFT.value,
            ChosenClassification.LOAN.value,
            ChosenClassification.REASSIGNMENT.value,
        }
    ),
    PendingClassificationKind.EXTERNAL_OUTFLOW.value: frozenset(
        {
            ChosenClassification.SALE.value,
            ChosenClassification.GIFT.value,
            ChosenClassification.PAYMENT.value,
        }
    ),
}

# Short aliases for the long enum name pair used as dict keys.
_CE = PendingClassificationKind.CROSS_ENTITY.value
_EO = PendingClassificationKind.EXTERNAL_OUTFLOW.value
_C = ChosenClassification

# Maps (kind, choice) -> TransferResolution value for the outflow leg.
_OUTFLOW_RESOLUTION: dict[tuple[str, str], TransferResolution] = {
    (_CE, _C.CAPITAL_CONTRIBUTION.value): TransferResolution.RELOCATE_CONTRIBUTION,
    (_CE, _C.REASSIGNMENT.value): TransferResolution.RELOCATE_REASSIGNMENT,
    (_CE, _C.GIFT.value): TransferResolution.RELOCATE_GIFT,
    (_CE, _C.SALE.value): TransferResolution.DISPOSAL_RELATED_PARTY,
    (_CE, _C.LOAN.value): TransferResolution.NO_OP_LOAN,
    (_EO, _C.SALE.value): TransferResolution.DISPOSAL,
    (_EO, _C.PAYMENT.value): TransferResolution.DISPOSAL,
    (_EO, _C.GIFT.value): TransferResolution.GIFT_OUT,
}

# Choices where the inflow leg also gets stamped + relocation_source_event_id set.
_STAMP_INFLOW: frozenset[str] = frozenset(
    {
        ChosenClassification.CAPITAL_CONTRIBUTION.value,
        ChosenClassification.REASSIGNMENT.value,
        ChosenClassification.GIFT.value,  # cross-entity gift only
    }
)


# ── Public exceptions ──────────────────────────────────────────────────────────


class InvalidChoice(ValueError):
    """choice is not in ALLOWED[kind]."""


class InvalidState(ValueError):
    """Pending row is in a state that does not permit the requested action."""


class UserResolvedGuard(ValueError):
    """Automation attempted to reopen a row already resolved by a user."""


# ── Public entry points ────────────────────────────────────────────────────────


def resolve_classification(
    session: Session,
    pending_id: uuid.UUID,
    choice: str,
    *,
    actor: str,
    note: str | None = None,
    rule_id: uuid.UUID | None = None,
) -> PendingClassification:
    """Resolve a pending row for the first time (or idempotently with the same choice).

    resolved_by is derived from actor/rule_id:
      - rule_id provided -> resolved_by='rule'
      - otherwise        -> resolved_by='user'
    """
    pc = _load(session, pending_id)

    allowed_states = {
        PendingClassificationState.NEEDS_CLASSIFICATION.value,
        PendingClassificationState.CLASSIFIED.value,
        PendingClassificationState.APPLIED.value,
    }
    if pc.state not in allowed_states:
        raise InvalidState(f"Cannot resolve from state '{pc.state}' (id={pending_id})")

    _validate_choice(pc.kind, choice)

    # Idempotency: same choice already set, same state — return without duplicate audit.
    if (
        pc.chosen_classification == choice
        and pc.state == PendingClassificationState.CLASSIFIED.value
    ):
        return pc

    before = _snapshot(pc)
    resolved_by: Literal["user", "rule"] = "rule" if rule_id is not None else "user"

    pc.chosen_classification = choice
    pc.resolved_by = resolved_by
    pc.resolved_at = datetime.now(UTC)
    pc.state = PendingClassificationState.CLASSIFIED.value
    pc.note = note
    if rule_id is not None:
        pc.resolved_rule_id = rule_id

    session.flush()

    _stamp_legs(session, pc, choice)
    session.flush()

    after = _snapshot(pc)
    _write_audit(
        session,
        pending_id=pending_id,
        actor=actor,
        action=f"resolve:{choice}",
        before=before,
        after=after,
        note=note,
        rule_id=rule_id,
    )
    session.flush()

    return pc


def reclassify(
    session: Session,
    pending_id: uuid.UUID,
    new_choice: str,
    *,
    actor: str,
    note: str | None = None,
) -> PendingClassification:
    """Change the classification of an already-classified (or applied) row.

    - Automation (actor != 'user' caller, resolved_by='rule') may NEVER reopen a
      row whose existing resolved_by='user'.
    - If state was 'applied', resets to 'classified' so Stage 4 re-materializes.
    """
    pc = _load(session, pending_id)

    allowed_states = {
        PendingClassificationState.CLASSIFIED.value,
        PendingClassificationState.APPLIED.value,
    }
    if pc.state not in allowed_states:
        raise InvalidState(f"Cannot reclassify from state '{pc.state}' (id={pending_id})")

    _validate_choice(pc.kind, new_choice)

    # Guard: automation cannot reopen a user-resolved row.
    # We detect "automation" as: note contains 'rule' marker OR caller explicitly
    # passes resolved_by context. The simplest seam: reclassify() never passes
    # rule_id, so we check the existing resolved_by instead of the caller.
    # Callers that ARE rules should call resolve_classification with rule_id instead.
    if pc.resolved_by == "user" and actor == "rule":
        raise UserResolvedGuard(f"Automation cannot reopen a user-resolved row (id={pending_id})")

    before = _snapshot(pc)
    was_applied = pc.state == PendingClassificationState.APPLIED.value

    pc.chosen_classification = new_choice
    pc.resolved_at = datetime.now(UTC)
    pc.note = note
    if was_applied:
        pc.state = PendingClassificationState.CLASSIFIED.value

    session.flush()

    _stamp_legs(session, pc, new_choice)
    session.flush()

    after = _snapshot(pc)
    _write_audit(
        session,
        pending_id=pending_id,
        actor=actor,
        action=f"reclassify:{new_choice}",
        before=before,
        after=after,
        note=note,
        rule_id=None,
    )
    session.flush()

    return pc


def dismiss(
    session: Session,
    pending_id: uuid.UUID,
    reason: str,
    *,
    actor: str,
    redirect: bool = False,
) -> dict[str, object]:
    """Dismiss the pending row.

    redirect=True: sets dismiss_reason='reclassified-internal' and returns
    {'dismissed': True, 'redirect': 'add_wallet'} so the UI can prompt the user
    to add the counterparty wallet.  Does NOT add a ChosenClassification value.
    """
    pc = _load(session, pending_id)

    before = _snapshot(pc)

    actual_reason = "reclassified-internal" if redirect else reason
    pc.state = PendingClassificationState.DISMISSED.value
    pc.dismiss_reason = actual_reason
    pc.resolved_at = datetime.now(UTC)
    pc.resolved_by = "user"

    session.flush()

    after = _snapshot(pc)
    _write_audit(
        session,
        pending_id=pending_id,
        actor=actor,
        action="dismiss" if not redirect else "dismiss:redirect",
        before=before,
        after=after,
        note=reason if not redirect else None,
        rule_id=None,
    )
    session.flush()

    result: dict[str, object] = {"dismissed": True}
    if redirect:
        result["redirect"] = "add_wallet"
    return result


# ── Rules-hook stub ────────────────────────────────────────────────────────────


def _match_rule(
    pc: PendingClassification,
) -> tuple[str, uuid.UUID] | None:
    """Stub rule matcher — returns (choice, rule_id) or None.

    Phase 2 replaces this with real rule evaluation.  Returning None means
    no automatic rule applies; the row stays in needs_classification.
    """
    return None


# ── Private helpers ────────────────────────────────────────────────────────────


def _load(session: Session, pending_id: uuid.UUID) -> PendingClassification:
    pc = session.get(PendingClassification, pending_id)
    if pc is None:
        raise ValueError(f"PendingClassification {pending_id} not found")
    return pc


def _validate_choice(kind: str, choice: str) -> None:
    allowed = ALLOWED.get(kind)
    if allowed is None or choice not in allowed:
        raise InvalidChoice(
            f"'{choice}' is not a valid choice for kind='{kind}'. "
            f"Allowed: {sorted(allowed) if allowed else 'none'}"
        )


def _locate_outflow_ct(
    session: Session,
    pc: PendingClassification,
) -> ClassifiedTransaction | None:
    """Find the outflow CT using the v_lot_gate join keys."""
    return session.scalar(
        select(ClassifiedTransaction).where(
            ClassifiedTransaction.wallet_id == pc.from_wallet_id,
            ClassifiedTransaction.tx_hash == pc.tx_hash,
            ClassifiedTransaction.event_seq == pc.transfer_index,
        )
    )


def _locate_inflow_ct(
    session: Session,
    pc: PendingClassification,
    outflow_ct: ClassifiedTransaction,
) -> ClassifiedTransaction | None:
    """Find the inflow CT on the to_wallet_id side (cross-entity only)."""
    if pc.to_wallet_id is None:
        return None
    return session.scalar(
        select(ClassifiedTransaction)
        .where(
            ClassifiedTransaction.wallet_id == pc.to_wallet_id,
            ClassifiedTransaction.tx_hash == pc.tx_hash,
            ClassifiedTransaction.chain == pc.chain,
            ClassifiedTransaction.contract_address == outflow_ct.contract_address,
            ClassifiedTransaction.classification.in_(["transfer-in", "bridge-in"]),
        )
        .limit(1)
    )


def _stamp_legs(
    session: Session,
    pc: PendingClassification,
    choice: str,
) -> None:
    """Stamp transfer_resolution (and relocation_source_event_id where applicable) onto CTs."""
    outflow_ct = _locate_outflow_ct(session, pc)
    if outflow_ct is None:
        return

    resolution = _OUTFLOW_RESOLUTION[(pc.kind, choice)]
    outflow_ct.transfer_resolution = resolution

    if choice in _STAMP_INFLOW and pc.kind == PendingClassificationKind.CROSS_ENTITY.value:
        inflow_ct = _locate_inflow_ct(session, pc, outflow_ct)
        if inflow_ct is not None:
            inflow_ct.transfer_resolution = resolution
            inflow_ct.relocation_source_event_id = outflow_ct.id

    # For cross-entity sale: inflow is an FMV acquisition — no transfer_resolution stamp,
    # no relocation link.  The inflow CT stays as 'transfer-in' with no resolution signal.


def _snapshot(pc: PendingClassification) -> dict[str, object]:
    return {
        "state": pc.state,
        "chosen_classification": pc.chosen_classification,
        "resolved_by": pc.resolved_by,
        "resolved_at": pc.resolved_at.isoformat() if pc.resolved_at else None,
        "dismiss_reason": pc.dismiss_reason,
        "note": pc.note,
    }


def _write_audit(
    session: Session,
    pending_id: uuid.UUID,
    *,
    actor: str,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    note: str | None,
    rule_id: uuid.UUID | None,
) -> None:
    entry = ClassificationAuditLog(
        pending_id=pending_id,
        actor=actor,
        action=action,
        rule_id=rule_id,
        note=note,
        before_state=before,
        after_state=after,
    )
    session.add(entry)
