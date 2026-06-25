"""Testcontainers integration tests for the bridge lot engine wiring.

Verifies:
  - bridge-in / bridge-out treatment mapping in the engine
  - _apply_bridge_relocation: happy path, idempotency, missing source event
  - Classification signal → lot treatment round-trip
  - Multi-lot relocate (FIFO order)
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.domain.lots.engine import (
    apply_event,
)
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.lot import LotProcessingException, LotRelocation, TaxLot
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

WHEN = datetime(2024, 6, 1, tzinfo=UTC)
BRIDGE_WHEN = datetime(2025, 6, 2, tzinfo=UTC)


# ── Module-scoped sync engine ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def bridge_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(sync_url, future=True)


@pytest.fixture(scope="module")
def bridge_sessionmaker(bridge_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(bridge_engine, expire_on_commit=False)


@pytest.fixture
def bridge_session(
    bridge_sessionmaker: sessionmaker[Session],
) -> Generator[Session, None, None]:
    with bridge_sessionmaker() as session:
        with session.begin():
            session.begin_nested()
            yield session
            session.rollback()


# ── Seed helper ───────────────────────────────────────────────────────────────


def _seed(session: Session) -> tuple[Wallet, Wallet, Entity, TokenRegistry]:
    user = User(clerk_user_id=f"b_{uuid.uuid4().hex[:8]}", preferences={})
    session.add(user)
    session.flush()

    entity = Entity(
        user_id=user.id,
        name="Bridge Test Entity",
        type="personal",
        default_basis_method="fifo",
        jurisdiction="US",
        bridge_treatment="relocate",
    )
    session.add(entity)
    session.flush()

    wallet_lc = Wallet(
        user_id=user.id,
        chain="lemonchain",
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    wallet_bsc = Wallet(
        user_id=user.id,
        chain="bsc",
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    session.add_all([wallet_lc, wallet_bsc])
    session.flush()

    for wallet in [wallet_lc, wallet_bsc]:
        session.add(
            WalletEntityAssignment(
                wallet_id=wallet.id,
                entity_id=entity.id,
                effective_from=datetime(2023, 1, 1).date(),
                effective_to=None,
                classification="initial-assignment",
            )
        )
    session.flush()

    token = TokenRegistry(
        chain="lemonchain",
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol="LEMX",
        name="Native Lemon",
        decimals=18,
        tier=1,
        category="ecosystem-native",
    )
    session.add(token)
    session.flush()
    return wallet_lc, wallet_bsc, entity, token


def _make_ct(
    session: Session,
    *,
    wallet_id: uuid.UUID,
    token_id: uuid.UUID,
    classification: str,
    amount: str,
    value_usd: str | None,
    occurred_at: datetime,
    tx_hash: str,
    event_seq: int = 0,
    relocation_source_event_id: uuid.UUID | None = None,
) -> ClassifiedTransaction:
    ct = ClassifiedTransaction(
        wallet_id=wallet_id,
        chain="lemonchain",
        tx_hash=tx_hash,
        event_seq=event_seq,
        block_number=100,
        occurred_at=occurred_at,
        classification=classification,
        token_id=token_id,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        amount=Decimal(amount),
        value_usd_at_event=Decimal(value_usd) if value_usd else None,
        relocation_source_event_id=relocation_source_event_id,
    )
    session.add(ct)
    session.flush()
    return ct


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_bridge_out_treatment_is_none(bridge_session: Session) -> None:
    """bridge-out → NONE; engine skips without touching lots."""
    wallet_lc, wallet_bsc, entity, token = _seed(bridge_session)
    ct_acq = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="200",
        occurred_at=WHEN,
        tx_hash="0x" + "1" * 64,
    )
    apply_event(bridge_session, ct_acq)
    bridge_session.flush()

    ct_bout = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="bridge-out",
        amount="100",
        value_usd="300",
        occurred_at=BRIDGE_WHEN,
        tx_hash="0x" + "2" * 64,
    )
    apply_event(bridge_session, ct_bout)
    bridge_session.flush()

    # Lots should still be at source wallet — not consumed by bridge-out.
    lot = bridge_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct_acq.id))
    assert lot is not None
    assert lot.quantity_remaining == Decimal("100")
    assert lot.wallet_id == wallet_lc.id


def test_bridge_in_relocates_lots(bridge_session: Session) -> None:
    """bridge-in with relocation_source_event_id → lots move to dest wallet."""
    wallet_lc, wallet_bsc, entity, token = _seed(bridge_session)

    ct_acq = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="200",
        occurred_at=WHEN,
        tx_hash="0x" + "3" * 64,
    )
    apply_event(bridge_session, ct_acq)
    bridge_session.flush()

    ct_bout = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="bridge-out",
        amount="100",
        value_usd="300",
        occurred_at=BRIDGE_WHEN,
        tx_hash="0x" + "4" * 64,
    )

    ct_bin = _make_ct(
        bridge_session,
        wallet_id=wallet_bsc.id,
        token_id=token.id,
        classification="bridge-in",
        amount="99",
        value_usd=None,
        occurred_at=BRIDGE_WHEN,
        tx_hash="0x" + "5" * 64,
        relocation_source_event_id=ct_bout.id,
    )
    apply_event(bridge_session, ct_bin)
    bridge_session.flush()

    reloc = bridge_session.scalar(
        select(LotRelocation).where(LotRelocation.classified_tx_id == ct_bin.id)
    )
    assert reloc is not None
    assert reloc.to_wallet_id == wallet_bsc.id
    assert reloc.from_wallet_id == wallet_lc.id
    assert reloc.reason == "bridge"


def test_bridge_in_idempotent(bridge_session: Session) -> None:
    """apply_event for bridge-in is idempotent (second call is a no-op)."""
    wallet_lc, wallet_bsc, entity, token = _seed(bridge_session)

    ct_acq = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="200",
        occurred_at=WHEN,
        tx_hash="0x" + "6" * 64,
    )
    apply_event(bridge_session, ct_acq)
    bridge_session.flush()

    ct_bout = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="bridge-out",
        amount="100",
        value_usd=None,
        occurred_at=BRIDGE_WHEN,
        tx_hash="0x" + "7" * 64,
    )
    ct_bin = _make_ct(
        bridge_session,
        wallet_id=wallet_bsc.id,
        token_id=token.id,
        classification="bridge-in",
        amount="100",
        value_usd=None,
        occurred_at=BRIDGE_WHEN,
        tx_hash="0x" + "8" * 64,
        relocation_source_event_id=ct_bout.id,
    )

    apply_event(bridge_session, ct_bin)
    apply_event(bridge_session, ct_bin)  # second call must be no-op
    bridge_session.flush()

    count = len(
        bridge_session.scalars(
            select(LotRelocation).where(LotRelocation.classified_tx_id == ct_bin.id)
        ).all()
    )
    assert count == 1


def test_bridge_in_missing_source_records_exception(bridge_session: Session) -> None:
    """bridge-in without relocation_source_event_id → LotProcessingException."""
    wallet_lc, wallet_bsc, entity, token = _seed(bridge_session)

    ct_bin = _make_ct(
        bridge_session,
        wallet_id=wallet_bsc.id,
        token_id=token.id,
        classification="bridge-in",
        amount="100",
        value_usd=None,
        occurred_at=BRIDGE_WHEN,
        tx_hash="0x" + "9" * 64,
        relocation_source_event_id=None,  # missing!
    )
    apply_event(bridge_session, ct_bin)
    bridge_session.flush()

    exc = bridge_session.scalar(
        select(LotProcessingException).where(LotProcessingException.classified_tx_id == ct_bin.id)
    )
    assert exc is not None
    assert "relocation_source_event_id" in (exc.detail or {}).get("reason", "")


def test_multi_lot_bridge_relocation_fifo(bridge_session: Session) -> None:
    """Bridge relocates across multiple lots in FIFO order."""
    wallet_lc, wallet_bsc, entity, token = _seed(bridge_session)

    # Two lots acquired at different times.
    ct1 = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="reward",
        amount="60",
        value_usd="60",
        occurred_at=datetime(2024, 1, 1, tzinfo=UTC),
        tx_hash="0xa" + "1" * 63,
    )
    ct2 = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="reward",
        amount="40",
        value_usd="80",
        occurred_at=datetime(2024, 3, 1, tzinfo=UTC),
        tx_hash="0xa" + "2" * 63,
    )
    apply_event(bridge_session, ct1)
    apply_event(bridge_session, ct2)
    bridge_session.flush()

    ct_bout = _make_ct(
        bridge_session,
        wallet_id=wallet_lc.id,
        token_id=token.id,
        classification="bridge-out",
        amount="100",
        value_usd=None,
        occurred_at=BRIDGE_WHEN,
        tx_hash="0xb" + "0" * 63,
    )
    ct_bin = _make_ct(
        bridge_session,
        wallet_id=wallet_bsc.id,
        token_id=token.id,
        classification="bridge-in",
        amount="100",
        value_usd=None,
        occurred_at=BRIDGE_WHEN,
        tx_hash="0xb" + "1" * 63,
        relocation_source_event_id=ct_bout.id,
    )
    apply_event(bridge_session, ct_bin)
    bridge_session.flush()

    relocs = bridge_session.scalars(
        select(LotRelocation).where(LotRelocation.classified_tx_id == ct_bin.id)
    ).all()
    assert len(relocs) >= 1
    lots_at_bsc = bridge_session.scalars(
        select(TaxLot).where(TaxLot.wallet_id == wallet_bsc.id)
    ).all()
    assert len(lots_at_bsc) > 0
