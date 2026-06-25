"""Cross-entity transfer detection post-pass.

Runs after the per-wallet classifier pass.  For each unresolved transfer-out
across all of a user's wallets it determines one of four branches:

  1. Own-wallet, same entity  → auto-stamp relocate-internal (no pending row).
  2. Own-wallet, diff entity  → create pending_classifications row (cross-entity).
  3. Not own-wallet, outflow  → create pending_classifications row (external-outflow).
  4. Not own-wallet, inflow   → skip (outflow side writes the pending row).

Relocation stamp mirrors the 1.8 bridge convention exactly:
  OUTFLOW leg: transfer_resolution = 'relocate-internal'  (engine → NONE, no disposal)
  INFLOW  leg: transfer_resolution = 'relocate-internal'  (engine → RELOCATE)
               + relocation_source_event_id = outflow_ct.id

SCD (wallet_entity_assignments) uses a half-open [effective_from, effective_to)
interval.  effective_from and effective_to are DATE columns; comparison against
occurred_at (TIMESTAMPTZ) uses EXPLICIT CAST to avoid implicit timezone coercion.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, cast, or_, select
from sqlalchemy.orm import Session

from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.enums import (
    PendingClassificationKind,
    TransferResolution,
)
from lemon_ledger.models.logical_asset import LogicalAsset, TokenAssetMembership
from lemon_ledger.models.pending_classification import PendingClassification
from lemon_ledger.models.raw import RawTokenTransfer
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment


class NoEntityAssignment(ValueError):
    """Raised when no wallet_entity_assignment covers a wallet at the given timestamp."""


def make_logical_transfer_key(
    chain: str,
    tx_hash: str,
    wallet_id: uuid.UUID,
    event_seq: int,
) -> str:
    """Dedup key for a pending_classification row.

    Keyed on the OUTFLOW CT's (chain, tx_hash, wallet_id, event_seq) so
    re-sync from the same outflow perspective produces the same key.  The UNIQUE
    constraint on pending_classifications.logical_transfer_key enforces one row
    per outflow event.
    """
    return f"{chain.lower()}:{tx_hash.lower()}:{wallet_id}:{event_seq}"


def resolve_entity_at(
    session: Session,
    wallet_id: uuid.UUID,
    tx_ts: datetime,
) -> WalletEntityAssignment:
    """Return the WalletEntityAssignment that covers *wallet_id* at *tx_ts*.

    Uses explicit CAST(effective_from AS TIMESTAMP WITH TIME ZONE) to avoid
    implicit Postgres date→timestamptz coercion that could shift boundaries.

    Half-open interval: effective_from ≤ tx_ts < effective_to (NULLs → open end).

    Raises NoEntityAssignment if no covering row exists.
    """
    row = session.scalar(
        select(WalletEntityAssignment)
        .where(
            WalletEntityAssignment.wallet_id == wallet_id,
            cast(WalletEntityAssignment.effective_from, DateTime(timezone=True)) <= tx_ts,
            or_(
                WalletEntityAssignment.effective_to.is_(None),
                cast(WalletEntityAssignment.effective_to, DateTime(timezone=True)) > tx_ts,
            ),
        )
        .order_by(WalletEntityAssignment.effective_from.desc())
        .limit(1)
    )
    if row is None:
        raise NoEntityAssignment(
            f"No wallet_entity_assignment covers wallet {wallet_id} at {tx_ts.isoformat()}"
        )
    return row


def detect_for_user(
    session: Session,
    *,
    user_id: uuid.UUID,
    since: datetime | None = None,
) -> dict[str, int]:
    """Run the counterparty-detection post-pass for all wallets belonging to *user_id*.

    Returns ``{"auto_resolved": N, "pending_created": M}``.

    Idempotency: CTs already carrying transfer_resolution are skipped.
    Dedup: pending rows are only written when the logical_transfer_key is absent.
    """
    user_wallets = session.scalars(
        select(Wallet).where(
            Wallet.user_id == user_id,
            Wallet.is_active.is_(True),
        )
    ).all()

    if not user_wallets:
        return {"auto_resolved": 0, "pending_created": 0}

    wallet_ids: set[uuid.UUID] = {w.id for w in user_wallets}
    own_addresses: set[str] = {w.address.lower() for w in user_wallets}

    q = select(ClassifiedTransaction).where(
        ClassifiedTransaction.wallet_id.in_(wallet_ids),
        ClassifiedTransaction.classification == "transfer-out",
        ClassifiedTransaction.transfer_resolution.is_(None),
    )
    if since is not None:
        q = q.where(ClassifiedTransaction.occurred_at >= since)

    outflow_cts = session.scalars(q).all()

    auto_resolved = 0
    pending_created = 0

    for ct in outflow_cts:
        inflow_ct = _find_own_inflow(session, ct, wallet_ids)
        ltk = make_logical_transfer_key(ct.chain, ct.tx_hash, ct.wallet_id, ct.event_seq)

        if inflow_ct is not None:
            # Branch 1 or 2: own-wallet transfer.
            try:
                from_asgn = resolve_entity_at(session, ct.wallet_id, ct.occurred_at)
                to_asgn = resolve_entity_at(session, inflow_ct.wallet_id, ct.occurred_at)
            except NoEntityAssignment:
                continue

            if from_asgn.entity_id == to_asgn.entity_id:
                # Branch 1: same entity → auto-stamp relocation, no pending row.
                ct.transfer_resolution = TransferResolution.RELOCATE_INTERNAL
                inflow_ct.transfer_resolution = TransferResolution.RELOCATE_INTERNAL
                inflow_ct.relocation_source_event_id = ct.id
                auto_resolved += 1
            else:
                # Branch 2: different entities → pending classification needed.
                canonical = _canonical_asset(session, ct.token_id)
                created = _write_pending_if_absent(
                    session,
                    ltk=ltk,
                    kind=PendingClassificationKind.CROSS_ENTITY,
                    user_id=user_id,
                    chain=ct.chain,
                    tx_hash=ct.tx_hash,
                    transfer_index=ct.event_seq,
                    token_id=ct.token_id,
                    canonical_asset=canonical,
                    amount=ct.amount,
                    from_wallet_id=ct.wallet_id,
                    from_entity_id=from_asgn.entity_id,
                    to_wallet_id=inflow_ct.wallet_id,
                    to_entity_id=to_asgn.entity_id,
                    to_address=None,
                )
                if created:
                    pending_created += 1
        else:
            # Branch 3 / 4: no own inflow found.
            # Determine if the raw `to` address is an own wallet (shouldn't be
            # after classification, but guard against stale classification state).
            to_addr = _raw_to_address(session, ct)
            if to_addr is not None and to_addr in own_addresses:
                # Other wallet not yet classified — skip; will be caught after re-classify.
                continue

            # Branch 3: external outflow → pending classification.
            try:
                from_asgn = resolve_entity_at(session, ct.wallet_id, ct.occurred_at)
            except NoEntityAssignment:
                continue

            canonical = _canonical_asset(session, ct.token_id)
            created = _write_pending_if_absent(
                session,
                ltk=ltk,
                kind=PendingClassificationKind.EXTERNAL_OUTFLOW,
                user_id=user_id,
                chain=ct.chain,
                tx_hash=ct.tx_hash,
                transfer_index=ct.event_seq,
                token_id=ct.token_id,
                canonical_asset=canonical,
                amount=ct.amount,
                from_wallet_id=ct.wallet_id,
                from_entity_id=from_asgn.entity_id,
                to_wallet_id=None,
                to_entity_id=None,
                to_address=to_addr,
            )
            if created:
                pending_created += 1

    session.flush()
    return {"auto_resolved": auto_resolved, "pending_created": pending_created}


# ── private helpers ────────────────────────────────────────────────────────────


def _find_own_inflow(
    session: Session,
    outflow_ct: ClassifiedTransaction,
    wallet_ids: set[uuid.UUID],
) -> ClassifiedTransaction | None:
    """Find a matching inflow CT on another own wallet for the same transfer."""
    other_ids = wallet_ids - {outflow_ct.wallet_id}
    if not other_ids:
        return None
    return session.scalar(
        select(ClassifiedTransaction)
        .where(
            ClassifiedTransaction.wallet_id.in_(other_ids),
            ClassifiedTransaction.tx_hash == outflow_ct.tx_hash,
            ClassifiedTransaction.chain == outflow_ct.chain,
            ClassifiedTransaction.contract_address == outflow_ct.contract_address,
            ClassifiedTransaction.classification.in_(["transfer-in", "bridge-in"]),
        )
        .limit(1)
    )


def _raw_to_address(session: Session, ct: ClassifiedTransaction) -> str | None:
    """Extract the `to` address from the raw_token_transfers row for this CT."""
    raw = session.scalar(
        select(RawTokenTransfer)
        .where(
            RawTokenTransfer.wallet_id == ct.wallet_id,
            RawTokenTransfer.tx_hash == ct.tx_hash,
            RawTokenTransfer.contract_address == ct.contract_address,
        )
        .limit(1)
    )
    if raw is None:
        return None
    addr = raw.raw.get("to", "")
    return addr.lower() if addr else None


def _canonical_asset(session: Session, token_id: uuid.UUID | None) -> str:
    """Return the logical asset symbol for *token_id*, or the token symbol as fallback."""
    if token_id is None:
        return "UNKNOWN"
    membership = session.scalar(
        select(TokenAssetMembership).where(TokenAssetMembership.token_id == token_id)
    )
    if membership is not None:
        la = session.get(LogicalAsset, membership.logical_asset_id)
        if la is not None:
            return la.symbol
    token = session.get(TokenRegistry, token_id)
    return token.symbol if token is not None else "UNKNOWN"


def _write_pending_if_absent(
    session: Session,
    *,
    ltk: str,
    kind: PendingClassificationKind,
    user_id: uuid.UUID,
    chain: str,
    tx_hash: str,
    transfer_index: int,
    token_id: uuid.UUID | None,
    canonical_asset: str,
    amount: Decimal,
    from_wallet_id: uuid.UUID,
    from_entity_id: uuid.UUID,
    to_wallet_id: uuid.UUID | None,
    to_entity_id: uuid.UUID | None,
    to_address: str | None,
) -> bool:
    """Insert a PendingClassification row if none exists for *ltk*.

    Returns True if a new row was written, False if one already existed
    (covers both 'needs_classification' and terminal states — never reopens).
    """
    existing = session.scalar(
        select(PendingClassification).where(PendingClassification.logical_transfer_key == ltk)
    )
    if existing is not None:
        return False

    if token_id is None:
        return False

    pc = PendingClassification(
        user_id=user_id,
        kind=kind.value,
        logical_transfer_key=ltk,
        chain=chain,
        tx_hash=tx_hash,
        transfer_index=transfer_index,
        token_id=token_id,
        canonical_asset=canonical_asset,
        amount=amount,
        from_wallet_id=from_wallet_id,
        from_entity_id=from_entity_id,
        to_wallet_id=to_wallet_id,
        to_entity_id=to_entity_id,
        to_address=to_address,
    )
    session.add(pc)
    return True
