"""Shared fixtures for forms tests.

Module-scoped engine + sessionmaker; per-test savepoint rollback.
Seed helpers return plain model objects with no FK assumptions beyond what
each test explicitly seeds.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.lot import LotDisposal, TaxLot
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment


@pytest.fixture(scope="module")
def frm_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(sync_url, future=True)


@pytest.fixture(scope="module")
def frm_sessionmaker(frm_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(frm_engine, expire_on_commit=False)


@pytest.fixture
def frm_db(frm_sessionmaker: sessionmaker[Session]) -> Generator[Session, None, None]:
    with frm_sessionmaker() as session:
        with session.begin():
            session.begin_nested()
            yield session
            session.rollback()


# ── seed helpers ──────────────────────────────────────────────────────────────


def seed_user(db: Session) -> User:
    u = User(clerk_user_id=f"frm_{uuid.uuid4().hex[:8]}", preferences={})
    db.add(u)
    db.flush()
    return u


def seed_entity(db: Session, user: User, name: str = "TestEntity") -> Entity:
    e = Entity(user_id=user.id, name=name, type="personal", default_basis_method="fifo")
    db.add(e)
    db.flush()
    return e


def seed_wallet(db: Session, user: User) -> Wallet:
    w = Wallet(
        user_id=user.id,
        chain="lemonchain",
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    db.add(w)
    db.flush()
    return w


def seed_assign(
    db: Session,
    wallet: Wallet,
    entity: Entity,
    from_date: date = date(2023, 1, 1),
) -> WalletEntityAssignment:
    a = WalletEntityAssignment(
        wallet_id=wallet.id,
        entity_id=entity.id,
        effective_from=from_date,
        effective_to=None,
        classification="initial-assignment",
    )
    db.add(a)
    db.flush()
    return a


def seed_token(db: Session, symbol: str = "LEMX") -> TokenRegistry:
    t = TokenRegistry(
        chain="lemonchain",
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol=symbol,
        name=f"{symbol} Token",
        decimals=18,
        tier=1,
        category="ecosystem-native",
    )
    db.add(t)
    db.flush()
    return t


def seed_ct(
    db: Session,
    *,
    wallet: Wallet,
    token: TokenRegistry,
    classification: str,
    amount: str,
    value_usd: str = "100",
    occurred_at: datetime | None = None,
) -> ClassifiedTransaction:
    ct = ClassifiedTransaction(
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash=f"0x{uuid.uuid4().hex}",
        event_seq=0,
        block_number=100,
        occurred_at=occurred_at or datetime(2024, 1, 1, tzinfo=UTC),
        classification=classification,
        token_id=token.id,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        amount=Decimal(amount),
        value_usd_at_event=Decimal(value_usd),
    )
    db.add(ct)
    db.flush()
    return ct


def seed_lot(
    db: Session,
    *,
    wallet: Wallet,
    entity: Entity,
    token: TokenRegistry,
    source_ct: ClassifiedTransaction,
    quantity: str,
    cost_basis_usd: str,
    acquired_at: datetime | None = None,
    acquisition_type: str = "buy",
    asset_class: str = "fungible",
) -> TaxLot:
    lot = TaxLot(
        wallet_id=wallet.id,
        entity_id=entity.id,
        acquired_token_id=token.id,
        source_classified_tx_id=source_ct.id,
        quantity=Decimal(quantity),
        quantity_remaining=Decimal(quantity),
        cost_basis_usd=Decimal(cost_basis_usd),
        acquired_at=acquired_at or datetime(2024, 1, 1, tzinfo=UTC),
        acquisition_type=acquisition_type,
        asset_class=asset_class,
    )
    db.add(lot)
    db.flush()
    return lot


def seed_disposal(
    db: Session,
    *,
    lot: TaxLot,
    disposal_ct: ClassifiedTransaction,
    quantity_consumed: str,
    proceeds_usd: str,
    basis_consumed_usd: str,
    gain_loss_usd: str,
    disposed_at: datetime | None = None,
    holding_period: str = "short",
    covered_status: str = "no-1099-da",
    asset_class: str = "fungible",
    adjustment_code: str | None = None,
    adjustment_usd: str | None = None,
) -> LotDisposal:
    d = LotDisposal(
        lot_id=lot.id,
        disposal_tx_id=disposal_ct.id,
        quantity_consumed=Decimal(quantity_consumed),
        proceeds_usd=Decimal(proceeds_usd),
        basis_consumed_usd=Decimal(basis_consumed_usd),
        gain_loss_usd=Decimal(gain_loss_usd),
        disposed_at=disposed_at or datetime(2025, 6, 1, tzinfo=UTC),
        holding_period=holding_period,
        covered_status=covered_status,
        asset_class=asset_class,
        adjustment_code=adjustment_code,
        adjustment_usd=Decimal(adjustment_usd) if adjustment_usd else None,
        selection_strategy="fifo",
        selected_at=disposed_at or datetime(2025, 6, 1, tzinfo=UTC),
    )
    db.add(d)
    db.flush()
    return d
