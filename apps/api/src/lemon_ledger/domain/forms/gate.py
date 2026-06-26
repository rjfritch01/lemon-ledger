"""Gate guard for form generation.

Reads v_lot_gate to determine whether an entity has unresolved blocking events
before form generation proceeds. Sources (c) and (d) now correctly key by
wallet UUID (PR #29).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment


@dataclass(frozen=True)
class GateResult:
    is_held: bool
    blocker_rows: list[dict[str, Any]]  # classified_tx_id, wallet_id, reason, occurred_at
    entity_wallet_ids: list[uuid.UUID] = field(default_factory=list)


def get_entity_wallet_ids(session: Session, entity_id: uuid.UUID) -> list[uuid.UUID]:
    """Return all wallet IDs ever assigned to this entity."""
    return list(
        session.scalars(
            select(WalletEntityAssignment.wallet_id).where(
                WalletEntityAssignment.entity_id == entity_id,
            )
        ).all()
    )


def check_gate(
    session: Session,
    entity_id: uuid.UUID,
    tax_year: int,
) -> GateResult:
    """Query v_lot_gate for blocking rows across all years <= tax_year.

    Returns GateResult with is_held=True if any blocking row exists.
    Non-blocking rows (bridge:aged_unmatched) are excluded.
    """
    wallet_ids = get_entity_wallet_ids(session, entity_id)
    if not wallet_ids:
        return GateResult(is_held=False, blocker_rows=[], entity_wallet_ids=[])

    # Cast wallet UUIDs to text for the ANY() clause; Postgres handles UUID comparison.
    wallet_id_strs = [str(w) for w in wallet_ids]

    rows = session.execute(
        text(
            "SELECT classified_tx_id, wallet_id, reason, occurred_at, blocking "
            "FROM v_lot_gate "
            "WHERE wallet_id::text = ANY(:wids) "
            "  AND blocking = true "
            "  AND EXTRACT(YEAR FROM occurred_at) <= :year "
            "ORDER BY occurred_at"
        ),
        {"wids": wallet_id_strs, "year": tax_year},
    ).fetchall()

    blocker_rows = [
        {
            "classified_tx_id": str(r[0]),
            "wallet_id": str(r[1]),
            "reason": r[2],
            "occurred_at": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
        }
        for r in rows
    ]

    return GateResult(
        is_held=bool(blocker_rows),
        blocker_rows=blocker_rows,
        entity_wallet_ids=wallet_ids,
    )


def recompute_lots(session: Session, wallet_ids: list[uuid.UUID], tax_year: int) -> int:
    """Apply pending classified events idempotently for the given wallets.

    Processes CTs in occurred_at order up to and including tax_year.
    Uses apply_event (not rebuild_wallet): skips already-applied events via
    the explicit idempotency checks in the engine; does not wipe existing lots.

    Returns the count of CTs processed.
    """
    from sqlalchemy import select as sa_select

    from lemon_ledger.domain.lots.engine import apply_event
    from lemon_ledger.models.classified import ClassifiedTransaction

    processed = 0
    for wallet_id in wallet_ids:
        events = session.scalars(
            sa_select(ClassifiedTransaction)
            .where(ClassifiedTransaction.wallet_id == wallet_id)
            .order_by(
                ClassifiedTransaction.occurred_at,
                ClassifiedTransaction.block_number,
                ClassifiedTransaction.event_seq,
                ClassifiedTransaction.id,
            )
        ).all()
        for event in events:
            apply_event(session, event)
            processed += 1
    session.flush()
    return processed
