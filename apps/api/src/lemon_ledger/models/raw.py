import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lemon_ledger.db.base import Base
from lemon_ledger.models._constraints import CHAIN_SQL


class RawRecordMixin:
    """Columns shared by all four raw ingestion tables."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    raw: Mapped[Any] = mapped_column(JSONB, nullable=False)


class RawTransaction(RawRecordMixin, Base):
    __tablename__ = "raw_transactions"
    __table_args__ = (
        UniqueConstraint("wallet_id", "tx_hash", name="uq_raw_transactions_wallet_tx"),
        Index("ix_raw_transactions_wallet_block", "wallet_id", "block_number"),
        CheckConstraint(CHAIN_SQL, name="ck_raw_transactions_chain"),
    )

    value: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)


class RawTokenTransfer(RawRecordMixin, Base):
    __tablename__ = "raw_token_transfers"
    __table_args__ = (
        UniqueConstraint(
            "wallet_id", "tx_hash", "log_index", name="uq_raw_token_transfers_wallet_tx_log"
        ),
        Index("ix_raw_token_transfers_wallet_block", "wallet_id", "block_number"),
        CheckConstraint(CHAIN_SQL, name="ck_raw_token_transfers_chain"),
    )

    value: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)


class RawInternalTx(RawRecordMixin, Base):
    __tablename__ = "raw_internal_txs"
    __table_args__ = (
        UniqueConstraint(
            "wallet_id", "tx_hash", "trace_id", name="uq_raw_internal_txs_wallet_tx_trace"
        ),
        Index("ix_raw_internal_txs_wallet_block", "wallet_id", "block_number"),
        CheckConstraint(CHAIN_SQL, name="ck_raw_internal_txs_chain"),
    )

    value: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)


class RawLog(RawRecordMixin, Base):
    __tablename__ = "raw_logs"
    __table_args__ = (
        UniqueConstraint("wallet_id", "tx_hash", "log_index", name="uq_raw_logs_wallet_tx_log"),
        Index("ix_raw_logs_wallet_block", "wallet_id", "block_number"),
        CheckConstraint(CHAIN_SQL, name="ck_raw_logs_chain"),
    )

    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False, index=True)
    topic0: Mapped[str | None] = mapped_column(String(66), nullable=True, index=True)
