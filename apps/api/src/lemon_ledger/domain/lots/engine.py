"""Lot tracking and tax math engine.

Public API:
  canonical_pool_key(session, token_id) -> tuple[str, UUID]
  open_lots(session, wallet_id, token_id) -> list[TaxLot]
  consume(method, lots, quantity) -> list[ConsumptionSlice]
  build_lines(slices, total_proceeds_usd, disposed_at, strategy) -> list[DisposalLine]
  apply_event(session, event) -> None
  apply_relocation(session, event) -> None
  rebuild_wallet(session, wallet_id) -> None
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from typing import Any

from dateutil.relativedelta import relativedelta
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from lemon_ledger.domain.lots.methods import (
    Fifo,
    Hifo,
    InsufficientLotsError,
    LotMethod,
)
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.enums import (
    AcquisitionType,
    AdjustmentCode,
    AssetClass,
    BasisMethod,
    HoldingPeriod,
    LotExceptionReason,
    LotTreatment,
    SelectionStrategy,
    TransferResolution,
)
from lemon_ledger.models.logical_asset import LogicalAsset, TokenAssetMembership
from lemon_ledger.models.lot import (
    LotDisposal,
    LotProcessingException,
    LotRelocation,
    TaxLot,
)
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

_SCALE18 = Decimal("0.000000000000000001")  # 1e-18


# ── Pool key ──────────────────────────────────────────────────────────────────


def canonical_pool_key(session: Session, token_id: uuid.UUID) -> tuple[str, uuid.UUID]:
    """Return ('logical', logical_asset_id) or ('token', token_id)."""
    membership = session.scalar(
        select(TokenAssetMembership).where(TokenAssetMembership.token_id == token_id)
    )
    if membership is not None:
        return ("logical", membership.logical_asset_id)
    return ("token", token_id)


# ── Open lots query ───────────────────────────────────────────────────────────


def open_lots(
    session: Session,
    wallet_id: uuid.UUID,
    token_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> list[TaxLot]:
    """Return open lots for the canonical pool, never scoped by entity_id."""
    pool_kind, pool_id = canonical_pool_key(session, token_id)

    stmt = select(TaxLot).where(
        TaxLot.wallet_id == wallet_id,
        TaxLot.quantity_remaining > Decimal(0),
    )

    if pool_kind == "logical":
        stmt = stmt.where(TaxLot.logical_asset_id == pool_id)
    else:
        stmt = stmt.where(
            TaxLot.acquired_token_id == pool_id,
            TaxLot.logical_asset_id.is_(None),
        )

    if for_update:
        # Deterministic lock order prevents deadlocks across concurrent sessions.
        stmt = stmt.order_by(TaxLot.acquired_at, TaxLot.id).with_for_update()
    else:
        stmt = stmt.order_by(TaxLot.acquired_at, TaxLot.id)

    return list(session.scalars(stmt).all())


# ── Consumption ───────────────────────────────────────────────────────────────


@dataclass
class ConsumptionSlice:
    lot: TaxLot
    quantity_consumed: Decimal
    basis_consumed_usd: Decimal


def consume(
    method: LotMethod,
    lots: list[TaxLot],
    quantity: Decimal,
) -> list[ConsumptionSlice]:
    """Walk ordered lots and allocate *quantity* across them.

    Basis for non-exhausting slices: unit_basis * quantity_consumed (18 dp).
    Basis for the exhausting slice: anchored to cost_basis_usd * qty_remaining / qty
    to prevent accumulated rounding from exceeding the lot's total basis.

    Raises InsufficientLotsError if open lots cannot cover *quantity*.
    """
    ordered = method.order(lots)
    slices: list[ConsumptionSlice] = []
    outstanding = quantity

    for lot in ordered:
        if outstanding <= Decimal(0):
            break
        take = min(lot.quantity_remaining, outstanding)
        unit_basis = lot.cost_basis_usd / lot.quantity

        if take == lot.quantity_remaining:
            # Exhausting slice: anchor to original lot parameters.
            basis = (lot.cost_basis_usd * lot.quantity_remaining / lot.quantity).quantize(
                _SCALE18, rounding=ROUND_HALF_EVEN
            )
        else:
            basis = (unit_basis * take).quantize(_SCALE18, rounding=ROUND_HALF_EVEN)

        slices.append(ConsumptionSlice(lot=lot, quantity_consumed=take, basis_consumed_usd=basis))
        outstanding -= take

    if outstanding > Decimal(0):
        raise InsufficientLotsError(quantity_unmatched=outstanding)

    return slices


# ── Disposal lines ────────────────────────────────────────────────────────────


@dataclass
class DisposalLine:
    lot: TaxLot
    quantity_consumed: Decimal
    proceeds_usd: Decimal
    basis_consumed_usd: Decimal
    gain_loss_usd: Decimal
    holding_period: HoldingPeriod
    asset_class: AssetClass
    selection_strategy: SelectionStrategy
    selected_at: datetime


def build_lines(
    slices: list[ConsumptionSlice],
    total_proceeds_usd: Decimal,
    disposed_at: datetime,
    selection_strategy: SelectionStrategy,
) -> list[DisposalLine]:
    """Allocate proceeds pro-rata across slices with a residual sweep on the last.

    Ensures Σ proceeds == total_proceeds_usd exactly (no rounding leak).
    gain_loss = proceeds - basis_consumed (burn → -basis falls out naturally).
    """
    if not slices:
        return []

    total_qty = sum(s.quantity_consumed for s in slices)
    selected_at = datetime.now(UTC)
    allocated_proceeds = Decimal(0)
    lines: list[DisposalLine] = []

    for i, s in enumerate(slices):
        if i == len(slices) - 1:
            slice_proceeds = total_proceeds_usd - allocated_proceeds
        else:
            slice_proceeds = (s.quantity_consumed / total_qty * total_proceeds_usd).quantize(
                _SCALE18, rounding=ROUND_DOWN
            )
            allocated_proceeds += slice_proceeds

        holding_period = _holding_period(s.lot.acquired_at, disposed_at)
        gain_loss = slice_proceeds - s.basis_consumed_usd

        lines.append(
            DisposalLine(
                lot=s.lot,
                quantity_consumed=s.quantity_consumed,
                proceeds_usd=slice_proceeds,
                basis_consumed_usd=s.basis_consumed_usd,
                gain_loss_usd=gain_loss,
                holding_period=holding_period,
                asset_class=AssetClass(s.lot.asset_class),
                selection_strategy=selection_strategy,
                selected_at=selected_at,
            )
        )

    return lines


def _holding_period(acquired_at: datetime, disposed_at: datetime) -> HoldingPeriod:
    """Long iff disposed_at.date() > acquired_at.date() + 1 year (anniversary is SHORT)."""
    anniversary = acquired_at.date() + relativedelta(years=1)
    if disposed_at.date() > anniversary:
        return HoldingPeriod.LONG
    return HoldingPeriod.SHORT


# ── Treatment mapping ─────────────────────────────────────────────────────────

_RELOCATE_RESOLUTIONS = frozenset(
    {
        TransferResolution.RELOCATE_INTERNAL.value,
        TransferResolution.RELOCATE_CONTRIBUTION.value,
        TransferResolution.RELOCATE_GIFT.value,
        TransferResolution.RELOCATE_REASSIGNMENT.value,
    }
)


def _treatment_from_resolution(event: ClassifiedTransaction) -> LotTreatment:
    """Map transfer_resolution to a LotTreatment.

    Relocate-* outflow legs (no relocation_source_event_id) → NONE.
    Relocate-* inflow legs (relocation_source_event_id set) → RELOCATE.
    """
    res = event.transfer_resolution
    if res in _RELOCATE_RESOLUTIONS:
        if event.relocation_source_event_id is not None:
            return LotTreatment.RELOCATE
        return LotTreatment.NONE
    if res in (
        TransferResolution.DISPOSAL.value,
        TransferResolution.DISPOSAL_RELATED_PARTY.value,
    ):
        return LotTreatment.DISPOSE
    if res == TransferResolution.GIFT_OUT.value:
        return LotTreatment.GIFT_OUT
    if res == TransferResolution.NO_OP_LOAN.value:
        return LotTreatment.NO_OP_LOAN
    return LotTreatment.PENDING


def _derive_treatment(event: ClassifiedTransaction) -> LotTreatment:
    # Stage 4: transfer_resolution takes precedence over classification.
    if event.transfer_resolution is not None:
        return _treatment_from_resolution(event)

    k = event.classification
    if k in ("reward", "mint", "transfer-in"):
        return LotTreatment.ACQUIRE
    if k == "swap-credit-redemption":
        notes = event.notes or ""
        if notes.startswith("scdt-out:"):
            return LotTreatment.DISPOSE
        if notes.startswith("l2-nft-in:"):
            return LotTreatment.ACQUIRE
        return LotTreatment.PENDING
    if k == "transfer-out":
        return LotTreatment.DISPOSE
    if k == "burn":
        return LotTreatment.DISPOSE
    if k in ("stake", "unstake", "wrap", "unwrap"):
        return LotTreatment.NONE
    # 1.8 bridge signals: bridge-in = basis-preserving relocation; bridge-out = NONE
    if k == "bridge-in":
        return LotTreatment.RELOCATE
    if k == "bridge-out":
        return LotTreatment.NONE
    # pending, unclassified, and anything else → PENDING
    return LotTreatment.PENDING


def _acquisition_type(event: ClassifiedTransaction) -> AcquisitionType:
    k = event.classification
    if k == "reward":
        return AcquisitionType.REWARD
    if k == "mint":
        return AcquisitionType.MINT
    if k == "swap-credit-redemption":
        return AcquisitionType.MINT
    return AcquisitionType.BUY


def _asset_class(session: Session, event: ClassifiedTransaction) -> AssetClass:
    """Derive AssetClass: COLLECTIBLE for NFT tokens; FUNGIBLE otherwise."""
    # Explicit NFT classifications are always collectible.
    if event.classification in ("mint", "swap-credit-redemption"):
        token_id = event.token_id
        if token_id is not None:
            token = session.get(TokenRegistry, token_id)
            if token is not None and token.decimals == 0:
                return AssetClass.COLLECTIBLE
        # Fall through if we can't confirm; use logical_asset
    if event.token_id is not None:
        membership = session.scalar(
            select(TokenAssetMembership).where(TokenAssetMembership.token_id == event.token_id)
        )
        if membership is not None:
            logical = session.get(LogicalAsset, membership.logical_asset_id)
            if logical is not None and logical.asset_kind == "nft":
                return AssetClass.COLLECTIBLE
    return AssetClass.FUNGIBLE


# ── Entity resolution ─────────────────────────────────────────────────────────


def _resolve_entity_id(session: Session, event: ClassifiedTransaction) -> uuid.UUID | None:
    """Find the entity owning this wallet at event time via the SCD table."""
    assignment = session.scalar(
        select(WalletEntityAssignment)
        .where(
            WalletEntityAssignment.wallet_id == event.wallet_id,
            WalletEntityAssignment.effective_from <= event.occurred_at.date(),
            or_(
                WalletEntityAssignment.effective_to.is_(None),
                WalletEntityAssignment.effective_to >= event.occurred_at.date(),
            ),
        )
        .limit(1)
    )
    return assignment.entity_id if assignment else None


# ── Basis method resolution ───────────────────────────────────────────────────


def _resolve_basis_method(
    session: Session, event: ClassifiedTransaction
) -> tuple[BasisMethod, SelectionStrategy]:
    """Return (BasisMethod, SelectionStrategy) for this disposal.

    Resolves the entity at disposal date via the SCD, reads default_basis_method,
    maps to the automatic SelectionStrategy (HIFO for specific_id).
    """
    entity_id = _resolve_entity_id(session, event)
    if entity_id is None:
        return BasisMethod.FIFO, SelectionStrategy.FIFO

    entity = session.get(Entity, entity_id)
    if entity is None:
        return BasisMethod.FIFO, SelectionStrategy.FIFO

    method_str = entity.default_basis_method
    if method_str == BasisMethod.SPECIFIC_ID:
        return BasisMethod.SPECIFIC_ID, SelectionStrategy.HIFO
    return BasisMethod.FIFO, SelectionStrategy.FIFO


def _method_for(basis: BasisMethod) -> LotMethod:
    if basis == BasisMethod.SPECIFIC_ID:
        return Hifo()
    return Fifo()


# ── apply_event ───────────────────────────────────────────────────────────────


def _record_exception(
    session: Session,
    event: ClassifiedTransaction,
    reason: LotExceptionReason,
    detail: dict[str, Any] | None = None,
    quantity_unmatched: Decimal | None = None,
) -> None:
    exc = LotProcessingException(
        classified_tx_id=event.id,
        reason=reason.value,
        detail=detail,
        quantity_unmatched=quantity_unmatched,
    )
    session.add(exc)


def apply_event(session: Session, event: ClassifiedTransaction) -> None:
    """Process one ClassifiedTransaction, writing lots/disposals idempotently."""
    treatment = _derive_treatment(event)

    if treatment == LotTreatment.NONE:
        return

    if treatment == LotTreatment.PENDING:
        return

    if treatment == LotTreatment.ACQUIRE:
        _apply_acquire(session, event)
    elif treatment == LotTreatment.DISPOSE:
        _apply_dispose(session, event)
    elif treatment == LotTreatment.RELOCATE:
        if event.transfer_resolution is not None:
            _apply_cross_entity_relocation(session, event)
        else:
            _apply_bridge_relocation(session, event)
    elif treatment == LotTreatment.GIFT_OUT:
        _apply_gift_out(session, event)
    elif treatment == LotTreatment.NO_OP_LOAN:
        event.needs_review = True


def _apply_acquire(session: Session, event: ClassifiedTransaction) -> None:
    # Idempotency: skip if lot already recorded for this classified tx.
    existing = session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == event.id))
    if existing is not None:
        return

    # Basis validation: PENDING classifications or NULL value → exception, not a $0 lot.
    basis_usd = event.value_usd_at_event
    if basis_usd is None:
        # Special case: l2-nft-in (swap-credit-redemption) threads value from sc leg.
        if event.classification == "swap-credit-redemption" and (event.notes or "").startswith(
            "l2-nft-in:"
        ):
            basis_usd = _thread_scdt_value(session, event)

        if basis_usd is None:
            _record_exception(
                session,
                event,
                LotExceptionReason.MISSING_BASIS,
                detail={"classification": event.classification, "notes": event.notes},
            )
            return

    entity_id = _resolve_entity_id(session, event)
    if entity_id is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={"reason": "no_wallet_entity_assignment"},
        )
        return

    token_id = event.token_id
    if token_id is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={"reason": "no_token_id_on_classified_tx"},
        )
        return

    pool_kind, pool_id = canonical_pool_key(session, token_id)
    logical_asset_id = pool_id if pool_kind == "logical" else None

    acq_type = _acquisition_type(event)
    asset_cls = _asset_class(session, event)

    lot = TaxLot(
        wallet_id=event.wallet_id,
        acquired_token_id=token_id,
        logical_asset_id=logical_asset_id,
        entity_id=entity_id,
        acquired_at=event.occurred_at,
        acquisition_type=acq_type.value,
        asset_class=asset_cls.value,
        quantity=event.amount,
        quantity_remaining=event.amount,
        cost_basis_usd=basis_usd,
        source_classified_tx_id=event.id,
    )
    session.add(lot)


def _thread_scdt_value(session: Session, nft_event: ClassifiedTransaction) -> Decimal | None:
    """For the l2-nft-in leg: return the proceeds from the paired scdt-out disposal."""
    sc_leg = session.scalar(
        select(ClassifiedTransaction)
        .where(
            ClassifiedTransaction.wallet_id == nft_event.wallet_id,
            ClassifiedTransaction.tx_hash == nft_event.tx_hash,
            ClassifiedTransaction.classification == "swap-credit-redemption",
            ClassifiedTransaction.notes.like("scdt-out:%"),
        )
        .limit(1)
    )
    if sc_leg is None:
        return None
    return sc_leg.value_usd_at_event


def _apply_dispose(session: Session, event: ClassifiedTransaction) -> None:
    # Idempotency: skip if disposals already recorded for this event.
    existing = session.scalar(
        select(LotDisposal).where(LotDisposal.disposal_tx_id == event.id).limit(1)
    )
    if existing is not None:
        return

    token_id = event.token_id
    if token_id is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={"reason": "no_token_id_on_classified_tx"},
        )
        return

    lots = open_lots(session, event.wallet_id, token_id, for_update=True)

    proceeds = (
        Decimal(0) if event.classification == "burn" else (event.value_usd_at_event or Decimal(0))
    )

    basis_method, selection_strategy = _resolve_basis_method(session, event)
    method = _method_for(basis_method)

    try:
        slices = consume(method, lots, event.amount)
    except InsufficientLotsError as exc:
        _record_exception(
            session,
            event,
            LotExceptionReason.INSUFFICIENT_LOTS,
            detail={"classification": event.classification},
            quantity_unmatched=exc.quantity_unmatched,
        )
        return

    lines = build_lines(slices, proceeds, event.occurred_at, selection_strategy)
    is_related_party = event.transfer_resolution == TransferResolution.DISPOSAL_RELATED_PARTY.value

    for line in lines:
        # Decrement quantity_remaining on the lot (atomic with the disposal insert).
        line.lot.quantity_remaining -= line.quantity_consumed
        session.add(line.lot)

        # §267 related-party: disallow the loss; gains pass through unchanged.
        adjustment_code: str | None = None
        adjustment_usd: Decimal | None = None
        if is_related_party and line.gain_loss_usd < Decimal(0):
            adjustment_code = AdjustmentCode.L.value
            adjustment_usd = abs(line.gain_loss_usd)

        disposal = LotDisposal(
            lot_id=line.lot.id,
            disposal_tx_id=event.id,
            quantity_consumed=line.quantity_consumed,
            proceeds_usd=line.proceeds_usd,
            basis_consumed_usd=line.basis_consumed_usd,
            gain_loss_usd=line.gain_loss_usd,
            holding_period=line.holding_period.value,
            asset_class=line.asset_class.value,
            selection_strategy=line.selection_strategy.value,
            selected_at=line.selected_at,
            disposed_at=event.occurred_at,
            adjustment_code=adjustment_code,
            adjustment_usd=adjustment_usd,
        )
        session.add(disposal)


# ── bridge relocation (engine-side; never reads bridge_correlations) ──────────


def _apply_bridge_relocation(session: Session, event: ClassifiedTransaction) -> None:
    """Drive a basis-preserving relocation for a confirmed bridge-in event.

    The bridge module stamps relocation_source_event_id onto the inflow CT so
    the engine can derive the source wallet without reading bridge_correlations.

    Fee fold: outflow - inflow difference stays in source wallet as unrelocated
    lots (future CPA queue item). The inflow quantity carries the full pro-rata
    basis of the lots consumed at the source.
    """
    # Idempotency: skip if relocation already recorded for this event.
    from sqlalchemy import select as _select

    existing = session.scalar(
        _select(LotRelocation).where(LotRelocation.classified_tx_id == event.id).limit(1)
    )
    if existing is not None:
        return

    source_event_id = event.relocation_source_event_id
    if source_event_id is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={"reason": "bridge-in_missing_relocation_source_event_id"},
        )
        return

    outflow_event = session.get(ClassifiedTransaction, source_event_id)
    if outflow_event is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={
                "reason": "bridge-in_outflow_event_not_found",
                "source_event_id": str(source_event_id),
            },
        )
        return

    apply_relocation(
        session,
        event,
        from_wallet_id=outflow_event.wallet_id,
        to_wallet_id=event.wallet_id,
        reason="bridge",
    )


# ── apply_relocation ──────────────────────────────────────────────────────────


def apply_relocation(
    session: Session,
    event: ClassifiedTransaction,
    from_wallet_id: uuid.UUID,
    to_wallet_id: uuid.UUID,
    reason: str,
) -> None:
    """Basis-preserving lot move. Writes a lot_relocations row; updates wallet_id on lots.

    1.8 will wire bridge confirmation events to this function. Built now for unit tests.
    """
    token_id = event.token_id
    if token_id is None:
        return

    lots_to_move = open_lots(session, from_wallet_id, token_id, for_update=True)
    method = Fifo()
    slices = consume(method, lots_to_move, event.amount)

    for s in slices:
        s.lot.wallet_id = to_wallet_id
        session.add(s.lot)

        relocation = LotRelocation(
            lot_id=s.lot.id,
            from_wallet_id=from_wallet_id,
            to_wallet_id=to_wallet_id,
            reason=reason,
            classified_tx_id=event.id,
            occurred_at=event.occurred_at,
        )
        session.add(relocation)


# ── cross-entity relocation (transfer_resolution-driven) ──────────────────────

_RELOCATION_REASON: dict[str, str] = {
    TransferResolution.RELOCATE_INTERNAL.value: "internal",
    TransferResolution.RELOCATE_CONTRIBUTION.value: "cap-contribution",
    TransferResolution.RELOCATE_GIFT.value: "gift",
    TransferResolution.RELOCATE_REASSIGNMENT.value: "reassignment",
}

_RELOCATION_ACQ_TYPE: dict[str, AcquisitionType] = {
    TransferResolution.RELOCATE_CONTRIBUTION.value: AcquisitionType.CAP_CONTRIBUTION,
    TransferResolution.RELOCATE_GIFT.value: AcquisitionType.GIFT,
}


def _apply_cross_entity_relocation(session: Session, event: ClassifiedTransaction) -> None:
    """Basis-preserving relocation for resolved cross-entity and internal transfers.

    The inflow CT carries relocation_source_event_id pointing to the outflow CT,
    matching the 1.8 bridge convention.  The outflow leg is treated as NONE.

    Post-relocation, acquisition_type is set per the transfer_resolution:
      relocate-contribution → 'cap-contribution'
      relocate-gift         → 'gift' + needs_review=True (Form 709)
      relocate-internal     → preserved (no change)
      relocate-reassignment → preserved (no change)
    """
    existing = session.scalar(
        select(LotRelocation).where(LotRelocation.classified_tx_id == event.id).limit(1)
    )
    if existing is not None:
        return

    source_event_id = event.relocation_source_event_id
    if source_event_id is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={"reason": "cross-entity_relocation_missing_source_event_id"},
        )
        return

    outflow_event = session.get(ClassifiedTransaction, source_event_id)
    if outflow_event is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={
                "reason": "cross-entity_relocation_outflow_not_found",
                "source_event_id": str(source_event_id),
            },
        )
        return

    res = event.transfer_resolution or ""
    reason = _RELOCATION_REASON.get(res, "bridge")
    apply_relocation(
        session,
        event,
        from_wallet_id=outflow_event.wallet_id,
        to_wallet_id=event.wallet_id,
        reason=reason,
    )

    # Set acquisition_type on relocated lots if the resolution requires it.
    new_acq_type = _RELOCATION_ACQ_TYPE.get(res)
    if new_acq_type is not None:
        relocated_lot_ids = list(
            session.scalars(
                select(LotRelocation.lot_id).where(LotRelocation.classified_tx_id == event.id)
            ).all()
        )
        for lot_id in relocated_lot_ids:
            lot = session.get(TaxLot, lot_id)
            if lot is not None:
                lot.acquisition_type = new_acq_type.value
                session.add(lot)

    if res == TransferResolution.RELOCATE_GIFT.value:
        event.needs_review = True


# ── gift-out (third-party gift; no disposal, no gain/loss, Form 709) ──────────


def _apply_gift_out(session: Session, event: ClassifiedTransaction) -> None:
    """Consume lots for a gift-out.  No LotDisposal is written.

    A LotRelocation with reason='gift' and from==to serves as the idempotency
    anchor (no FK or CHECK violation; semantics: lot exited the pool as a gift).
    needs_review=True flags the event for Form 709 review.
    """
    existing = session.scalar(
        select(LotRelocation).where(LotRelocation.classified_tx_id == event.id).limit(1)
    )
    if existing is not None:
        return

    token_id = event.token_id
    if token_id is None:
        _record_exception(
            session,
            event,
            LotExceptionReason.MISSING_BASIS,
            detail={"reason": "no_token_id_on_classified_tx"},
        )
        return

    lots = open_lots(session, event.wallet_id, token_id, for_update=True)
    basis_method, _ = _resolve_basis_method(session, event)
    method = _method_for(basis_method)

    try:
        slices = consume(method, lots, event.amount)
    except InsufficientLotsError as exc:
        _record_exception(
            session,
            event,
            LotExceptionReason.INSUFFICIENT_LOTS,
            detail={"classification": event.classification},
            quantity_unmatched=exc.quantity_unmatched,
        )
        return

    for s in slices:
        s.lot.quantity_remaining -= s.quantity_consumed
        session.add(s.lot)
        session.add(
            LotRelocation(
                lot_id=s.lot.id,
                from_wallet_id=event.wallet_id,
                to_wallet_id=event.wallet_id,  # self-ref: idempotency anchor for gift-out
                reason="gift",
                classified_tx_id=event.id,
                occurred_at=event.occurred_at,
            )
        )

    event.needs_review = True


# ── rebuild_wallet ────────────────────────────────────────────────────────────


def rebuild_wallet(session: Session, wallet_id: uuid.UUID) -> None:
    """Wipe and replay all classified events for *wallet_id* in canonical order.

    Canonical order: occurred_at, block_number, event_seq, id.
    Used on basis-method change or reclassification. Idempotent (replay is safe).
    """
    from sqlalchemy import delete

    # Wipe in reverse FK order: disposals → exceptions → relocations → lots.
    lot_ids_q = select(TaxLot.id).where(TaxLot.wallet_id == wallet_id)
    session.execute(delete(LotDisposal).where(LotDisposal.lot_id.in_(lot_ids_q)))
    session.execute(delete(LotRelocation).where(LotRelocation.lot_id.in_(lot_ids_q)))
    ct_ids_q = select(ClassifiedTransaction.id).where(ClassifiedTransaction.wallet_id == wallet_id)
    session.execute(
        delete(LotProcessingException).where(LotProcessingException.classified_tx_id.in_(ct_ids_q))
    )
    session.execute(delete(TaxLot).where(TaxLot.wallet_id == wallet_id))
    session.flush()

    # Replay events in canonical order.
    events = session.scalars(
        select(ClassifiedTransaction)
        .where(ClassifiedTransaction.wallet_id == wallet_id)
        .order_by(
            ClassifiedTransaction.occurred_at,
            ClassifiedTransaction.block_number,
            ClassifiedTransaction.event_seq,
            ClassifiedTransaction.id,
        )
    ).all()

    for evt in events:
        apply_event(session, evt)

    session.flush()
