"""ORM models for the classification layer.

ClassifiedTransaction  — one row per economic event within a tx bundle.
L2DecoderConfig        — per-L2-token decoder configuration; updated by
                         Option-C discovery and the nightly supply check.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid_utils.compat import uuid7

from lemon_ledger.db.base import Base
from lemon_ledger.models._constraints import CHAIN_SQL
from lemon_ledger.models.enums import ClassificationKind, TransferResolution

# CHECK constraints derived from enums so model and DB never drift.
_KINDS_SQL = ", ".join(f"'{k.value}'" for k in ClassificationKind)
_TRANSFER_RES_SQL = ", ".join(f"'{r.value}'" for r in TransferResolution)


class ClassifiedTransaction(Base):
    __tablename__ = "classified_transactions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    event_seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("token_registry.id", ondelete="RESTRICT"), nullable=True
    )
    contract_address: Mapped[str] = mapped_column(String(42), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(78, 18), nullable=False)
    value_usd_at_event: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 1.7: will be populated when lot engine runs; stays NULL until then.
    related_lots: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True
    )
    # 1.8: FK constraint added in migration 0008; column already in DB from initial schema.
    bridge_correlation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    # 1.8: stamped by bridge module so engine can relocate without reading bridge_correlations.
    relocation_source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    # 1.9: stamped by the resolve service; engine reads this, never pending_classifications.
    transfer_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "wallet_id",
            "tx_hash",
            "event_seq",
            name="uq_classified_wallet_tx_seq",
        ),
        Index("ix_classified_wallet_block", "wallet_id", "block_number"),
        CheckConstraint(f"classification IN ({_KINDS_SQL})", name="ck_classification_kind"),
        CheckConstraint(CHAIN_SQL, name="ck_classified_chain"),
        CheckConstraint(
            f"transfer_resolution IS NULL OR transfer_resolution IN ({_TRANSFER_RES_SQL})",
            name="ck_classified_transfer_resolution",
        ),
    )


class L2DecoderConfig(Base):
    __tablename__ = "l2_decoder_config"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    token_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("token_registry.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    decoder_class: Mapped[str] = mapped_column(String(64), nullable=False)

    nft_contract: Mapped[str | None] = mapped_column(String(42), nullable=True)
    staking_contract: Mapped[str | None] = mapped_column(String(42), nullable=True)
    mint_contract: Mapped[str | None] = mapped_column(String(42), nullable=True)
    nft_contract_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    staking_contract_status: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False
    )

    mint_fee_wei: Mapped[Decimal | None] = mapped_column(Numeric(78, 0), nullable=True)
    reward_event_topic0: Mapped[str | None] = mapped_column(String(66), nullable=True)
    burn_and_mint: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    distribution_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 1.6: deflationary tokenomics (separate from burn_and_mint mint-mechanics)
    deflationary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    buy_burn_wallet: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(CHAIN_SQL, name="ck_l2config_chain"),
        CheckConstraint(
            "nft_contract_status IN ('unknown','discovered','confirmed')",
            name="ck_l2config_nft_status",
        ),
        CheckConstraint(
            "staking_contract_status IN ('unknown','discovered','confirmed')",
            name="ck_l2config_staking_status",
        ),
    )
