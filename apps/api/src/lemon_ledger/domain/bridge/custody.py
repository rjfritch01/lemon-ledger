"""Custody address recognition and learning.

recognize_custody: row lookup — curated beats learned, absence = unknown.
learn_custody_addresses: cross-user aggregate; promote confirmed address clusters
    to 'inferred' / source='learned' once >= threshold unique users confirm them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from lemon_ledger.models.bridge import (
    BridgeCorrelation,
    BridgeStatus,
    CustodyAddress,
    CustodyRecognition,
)

log = logging.getLogger(__name__)


def recognize_custody(
    session: Session,
    *,
    address: str,
    chain: str,
) -> CustodyRecognition:
    """Look up *address* on *chain* in custody_addresses.

    curated beats learned — if both exist for the same (chain, address) that's
    a data-quality bug (UNIQUE constraint prevents it), so we just return the row.
    Absence of any row → UNKNOWN.
    """
    row = session.scalar(
        select(CustodyAddress).where(
            CustodyAddress.chain == chain.lower(),
            CustodyAddress.address == address.lower(),
        )
    )
    if row is None:
        return CustodyRecognition.UNKNOWN
    return CustodyRecognition(row.recognition)


def strongest_custody(
    session: Session,
    *,
    outflow_contract: str | None,
    inflow_contract: str | None,
    outflow_chain: str,
    inflow_chain: str,
) -> tuple[CustodyRecognition, str | None]:
    """Return (strongest recognition, winning address) across both legs.

    Priority: recognized > inferred > unknown.
    The winning address is stored on the BridgeCorrelation row.
    """
    best = CustodyRecognition.UNKNOWN
    winning_addr: str | None = None

    for addr, chain in [
        (outflow_contract, outflow_chain),
        (inflow_contract, inflow_chain),
    ]:
        if addr is None:
            continue
        rec = recognize_custody(session, address=addr, chain=chain)
        if rec == CustodyRecognition.RECOGNIZED:
            return CustodyRecognition.RECOGNIZED, addr.lower()
        if rec == CustodyRecognition.INFERRED and best == CustodyRecognition.UNKNOWN:
            best = CustodyRecognition.INFERRED
            winning_addr = addr.lower()

    return best, winning_addr


def learn_custody_addresses(
    session: Session,
    *,
    threshold: int = 5,
) -> list[CustodyAddress]:
    """Promote candidate custody addresses to 'inferred' / source='learned'.

    Aggregates CONFIRMED bridge_correlations; counts DISTINCT users per
    matched_custody_address + the chain derived from the outflow leg.
    Any address reaching *threshold* unique users is promoted.

    Promotion-only in v1 — no demotion.
    User-resolved rows are the only learning signal.
    """
    from lemon_ledger.models.classified import ClassifiedTransaction

    # Aggregate: confirmed pairs → custody address frequency per chain.
    stmt = (
        select(
            BridgeCorrelation.matched_custody_address,
            ClassifiedTransaction.chain,
            func.count(distinct(BridgeCorrelation.user_id)).label("unique_users"),
            func.count(BridgeCorrelation.id).label("pair_count"),
        )
        .join(
            ClassifiedTransaction,
            ClassifiedTransaction.id == BridgeCorrelation.outflow_classified_event_id,
        )
        .where(
            BridgeCorrelation.status == BridgeStatus.CONFIRMED,
            BridgeCorrelation.matched_custody_address.isnot(None),
        )
        .group_by(BridgeCorrelation.matched_custody_address, ClassifiedTransaction.chain)
        .having(func.count(distinct(BridgeCorrelation.user_id)) >= threshold)
    )

    rows = session.execute(stmt).all()
    promoted: list[CustodyAddress] = []

    for custody_addr, chain, unique_users, pair_count in rows:
        if custody_addr is None:
            continue
        addr_lower = custody_addr.lower()

        # Skip if already curated (curated > learned; never demote).
        existing = session.scalar(
            select(CustodyAddress).where(
                CustodyAddress.chain == chain,
                CustodyAddress.address == addr_lower,
            )
        )
        if existing is not None and existing.source == "curated":
            continue

        if existing is not None:
            # Update counters on existing learned row.
            existing.confirmed_pair_count = pair_count
            existing.unique_user_count = unique_users
            session.flush()
            promoted.append(existing)
        else:
            # Insert new learned row.
            new_row = CustodyAddress(
                chain=chain,
                address=addr_lower,
                recognition="inferred",
                source="learned",
                confirmed_pair_count=pair_count,
                unique_user_count=unique_users,
                promoted_at=datetime.now(UTC),
            )
            session.add(new_row)
            session.flush()
            promoted.append(new_row)
            log.info(
                "custody_promoted",
                extra={
                    "chain": chain,
                    "address": addr_lower,
                    "unique_users": unique_users,
                    "pair_count": pair_count,
                },
            )

    return promoted
