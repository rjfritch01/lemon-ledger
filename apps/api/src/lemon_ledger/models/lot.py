"""Lot-tracking models: TaxLot, LotDisposal, LotRelocation, LotProcessingException.

All monetary values are NUMERIC(38,18); token quantities are NUMERIC(78,18).
UUIDv7 primary keys; FK default ON DELETE RESTRICT.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base, UUIDPrimaryKeyMixin
from lemon_ledger.models.enums import (
    AcquisitionType,
    AdjustmentCode,
    AssetClass,
    CoveredStatus,
    HoldingPeriod,
    LotExceptionReason,
    SelectionStrategy,
)

# CHECK constraint strings derived from enums — keeps DB and Python in sync.
_ASSET_CLASSES = ", ".join(f"'{k.value}'" for k in AssetClass)
_HOLDING_PERIODS = ", ".join(f"'{k.value}'" for k in HoldingPeriod)
_ACQ_TYPES = ", ".join(f"'{k.value}'" for k in AcquisitionType)
_SEL_STRATEGIES = ", ".join(f"'{k.value}'" for k in SelectionStrategy)
_EXCEPTION_REASONS = ", ".join(f"'{k.value}'" for k in LotExceptionReason)
_RELOCATION_REASONS = (
    "'wrap','unwrap','bridge','cap-contribution','gift','loan','internal','reassignment'"
)
_COVERED_STATUSES = ", ".join(f"'{k.value}'" for k in CoveredStatus)
_ADJ_CODES = ", ".join(f"'{k.value}'" for k in AdjustmentCode)


class TaxLot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tax_lots"
    __table_args__ = (
        UniqueConstraint("source_classified_tx_id", name="uq_tax_lots_source_classified_tx"),
        CheckConstraint(f"asset_class IN ({_ASSET_CLASSES})", name="ck_tax_lots_asset_class"),
        CheckConstraint(f"acquisition_type IN ({_ACQ_TYPES})", name="ck_tax_lots_acquisition_type"),
        CheckConstraint(
            "quantity_remaining >= 0 AND quantity_remaining <= quantity",
            name="ck_tax_lots_quantity_remaining",
        ),
        Index("ix_tax_lots_wallet_logical", "wallet_id", "logical_asset_id"),
        Index("ix_tax_lots_wallet_token", "wallet_id", "acquired_token_id"),
        Index("ix_tax_lots_acquired_at", "acquired_at"),
    )

    # Pool partition: per Rev. Proc. 2024-28, lots are pooled per (wallet, logical_asset).
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False
    )
    acquired_token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("token_registry.id", ondelete="RESTRICT"), nullable=False
    )
    logical_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("logical_assets.id", ondelete="RESTRICT"), nullable=True
    )
    # Denormalized: entity at acquisition time. NOT used for lot selection — use
    # wallet_entity_assignments SCD for report-time resolution.
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entities.id", ondelete="RESTRICT"), nullable=False
    )

    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acquisition_type: Mapped[str] = mapped_column(Text, nullable=False)
    asset_class: Mapped[str] = mapped_column(Text, nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    quantity_remaining: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    cost_basis_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

    source_classified_tx_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classified_transactions.id", ondelete="RESTRICT"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LotDisposal(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "lot_disposals"
    __table_args__ = (
        UniqueConstraint("disposal_tx_id", "lot_id", name="uq_lot_disposals_tx_lot"),
        CheckConstraint(
            f"holding_period IN ({_HOLDING_PERIODS})", name="ck_lot_disposals_holding_period"
        ),
        CheckConstraint(f"asset_class IN ({_ASSET_CLASSES})", name="ck_lot_disposals_asset_class"),
        CheckConstraint(
            f"selection_strategy IN ({_SEL_STRATEGIES})",
            name="ck_lot_disposals_selection_strategy",
        ),
        CheckConstraint(
            f"covered_status IN ({_COVERED_STATUSES})", name="ck_lot_disposals_covered_status"
        ),
        CheckConstraint(
            f"adjustment_code IS NULL OR adjustment_code IN ({_ADJ_CODES})",
            name="ck_lot_disposals_adjustment_code",
        ),
    )

    lot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tax_lots.id", ondelete="RESTRICT"), nullable=False
    )
    disposal_tx_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classified_transactions.id", ondelete="RESTRICT"), nullable=False
    )

    quantity_consumed: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    # DEFAULT 0 so burn disposals (proceeds=0) are clean without explicit NULL handling.
    proceeds_usd: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, server_default="0"
    )
    basis_consumed_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    gain_loss_usd: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

    holding_period: Mapped[str] = mapped_column(Text, nullable=False)
    asset_class: Mapped[str] = mapped_column(Text, nullable=False)
    selection_strategy: Mapped[str] = mapped_column(Text, nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 1.9: Form 8949 box / adjustment fields.
    covered_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="no-1099-da")
    adjustment_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjustment_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)


class LotRelocation(UUIDPrimaryKeyMixin, Base):
    """Append-only record of basis-preserving lot movements between wallets.

    Wraps and unwraps (same wallet, in-pool) create NO rows here.
    Bridges and other cross-wallet moves create a row; 1.8 wires bridge confirmation.
    """

    __tablename__ = "lot_relocations"
    __table_args__ = (
        CheckConstraint(f"reason IN ({_RELOCATION_REASONS})", name="ck_lot_relocations_reason"),
    )

    lot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tax_lots.id", ondelete="RESTRICT"), nullable=False
    )
    from_wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False
    )
    to_wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    classified_tx_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classified_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LotProcessingException(UUIDPrimaryKeyMixin, Base):
    """Records events the engine could not process, surfaced via v_lot_gate."""

    __tablename__ = "lot_processing_exceptions"
    __table_args__ = (
        CheckConstraint(f"reason IN ({_EXCEPTION_REASONS})", name="ck_lot_exc_reason"),
    )

    classified_tx_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classified_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    quantity_unmatched: Mapped[Decimal | None] = mapped_column(Numeric(78, 18), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
