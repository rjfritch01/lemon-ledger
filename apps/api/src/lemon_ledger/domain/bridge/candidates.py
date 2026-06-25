"""Bridge candidate leg discovery.

Reads classified_transactions to find transfer-in / transfer-out events
that are eligible for cross-chain bridge correlation.

Eligibility gate:
  - classification IN ('transfer-out','transfer-in')
  - INNER JOIN token_asset_memberships on token_id (drops single-chain tokens)
  - value_usd_at_event IS NULL OR value_usd_at_event >= materiality_usd
  - occurred_at >= since (if provided)

TODO: exclude counterparty addresses that are the user's own wallets on the
same chain.  Requires adding a counterparty_address column to
classified_transactions or joining back to raw_token_transfers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.logical_asset import TokenAssetMembership
from lemon_ledger.models.wallet import Wallet


class LegDirection(StrEnum):
    OUTFLOW = "outflow"
    INFLOW = "inflow"


@dataclass(frozen=True, slots=True)
class CandidateLeg:
    classified_event_id: uuid.UUID
    direction: LegDirection
    wallet_id: uuid.UUID
    user_id: uuid.UUID
    chain: str
    logical_asset_id: uuid.UUID
    token_id: uuid.UUID
    amount: Decimal
    value_usd: Decimal | None
    occurred_at: datetime
    contract_address: str


def find_candidate_legs(
    session: Session,
    *,
    user_id: uuid.UUID,
    since: datetime | None = None,
    materiality_usd: Decimal = Decimal("1.00"),
) -> list[CandidateLeg]:
    """Return all bridge-eligible classified events for *user_id*.

    Only tokens that have a logical_asset membership are included — this drops
    single-chain tokens that cannot be bridged within the LEMX ecosystem.
    """
    stmt = (
        select(
            ClassifiedTransaction,
            TokenAssetMembership.logical_asset_id,
            Wallet.user_id,
        )
        .join(Wallet, Wallet.id == ClassifiedTransaction.wallet_id)
        .join(
            TokenAssetMembership,
            TokenAssetMembership.token_id == ClassifiedTransaction.token_id,
        )
        .where(
            Wallet.user_id == user_id,
            ClassifiedTransaction.classification.in_(["transfer-out", "transfer-in"]),
        )
    )

    if since is not None:
        stmt = stmt.where(ClassifiedTransaction.occurred_at >= since)

    rows = session.execute(stmt).all()

    legs: list[CandidateLeg] = []
    for ct, logical_asset_id, wuser_id in rows:
        # Materiality filter: if USD value is NULL we keep the leg (value unknown);
        # if known, it must meet or exceed the threshold.
        if ct.value_usd_at_event is not None and ct.value_usd_at_event < materiality_usd:
            continue
        if ct.token_id is None:
            continue

        direction = (
            LegDirection.OUTFLOW if ct.classification == "transfer-out" else LegDirection.INFLOW
        )
        legs.append(
            CandidateLeg(
                classified_event_id=ct.id,
                direction=direction,
                wallet_id=ct.wallet_id,
                user_id=wuser_id,
                chain=ct.chain,
                logical_asset_id=logical_asset_id,
                token_id=ct.token_id,
                amount=ct.amount,
                value_usd=ct.value_usd_at_event,
                occurred_at=ct.occurred_at,
                contract_address=ct.contract_address,
            )
        )

    return legs
