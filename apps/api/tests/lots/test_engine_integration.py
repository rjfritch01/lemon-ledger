"""Testcontainers integration tests for the lot engine.

Uses the same session-scoped pg_container + apply_migrations from tests/conftest.py.
Each test wraps operations in a savepoint that rolls back, so tests are isolated.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.domain.lots.engine import (
    apply_event,
    apply_relocation,
    rebuild_wallet,
)
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.lot import (
    LotDisposal,
    LotProcessingException,
    TaxLot,
)
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

# ── shared constants ──────────────────────────────────────────────────────────

ADDR = "0x" + "a" * 40
TOKEN_ADDR = "0x" + "c" * 40
TX1 = "0x" + "1" * 64
TX2 = "0x" + "2" * 64
BLOCK = 100
WHEN = datetime(2024, 6, 1, tzinfo=UTC)
DISPOSE_WHEN = datetime(2025, 6, 2, tzinfo=UTC)  # > 1 year → LONG


# ── module-scoped sync engine ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def lot_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(sync_url, future=True)


@pytest.fixture(scope="module")
def lot_sessionmaker(lot_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(lot_engine, expire_on_commit=False)


@pytest.fixture
def lot_session(
    lot_sessionmaker: sessionmaker[Session],
) -> Generator[Session, None, None]:
    """Each test gets a savepoint; rolled back afterward."""
    with lot_sessionmaker() as session:
        with session.begin():
            session.begin_nested()
            yield session
            session.rollback()


# ── Fixtures: seed minimal entities ──────────────────────────────────────────


def _seed_wallet_and_entity(session: Session) -> tuple[Wallet, Entity, TokenRegistry]:
    user = User(clerk_user_id=f"test_{uuid.uuid4().hex[:8]}", preferences={})
    session.add(user)
    session.flush()

    entity = Entity(
        user_id=user.id,
        name="Test Entity",
        type="personal",
        default_basis_method="fifo",
    )
    session.add(entity)
    session.flush()

    wallet = Wallet(
        user_id=user.id,
        chain="lemonchain",
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    session.add(wallet)
    session.flush()

    assignment = WalletEntityAssignment(
        wallet_id=wallet.id,
        entity_id=entity.id,
        effective_from=datetime(2023, 1, 1).date(),
        effective_to=None,
        classification="initial-assignment",
    )
    session.add(assignment)
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

    return wallet, entity, token


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
    notes: str | None = None,
) -> ClassifiedTransaction:
    ct = ClassifiedTransaction(
        wallet_id=wallet_id,
        chain="lemonchain",
        tx_hash=tx_hash,
        event_seq=event_seq,
        block_number=BLOCK,
        occurred_at=occurred_at,
        classification=classification,
        token_id=token_id,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        amount=Decimal(amount),
        value_usd_at_event=Decimal(value_usd) if value_usd else None,
        notes=notes,
    )
    session.add(ct)
    session.flush()
    return ct


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_acquire_creates_tax_lot(lot_session: Session) -> None:
    wallet, entity, token = _seed_wallet_and_entity(lot_session)
    ct = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="50",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct)
    lot_session.flush()

    lot = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct.id))
    assert lot is not None
    assert lot.quantity == Decimal("100")
    assert lot.quantity_remaining == Decimal("100")
    assert lot.cost_basis_usd == Decimal("50")
    assert lot.wallet_id == wallet.id
    assert lot.asset_class == "fungible"


def test_idempotent_reapply(lot_session: Session) -> None:
    wallet, entity, token = _seed_wallet_and_entity(lot_session)
    ct = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="reward",
        amount="50",
        value_usd="100",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct)
    apply_event(lot_session, ct)  # second call must be no-op
    lot_session.flush()

    count = len(
        lot_session.scalars(select(TaxLot).where(TaxLot.source_classified_tx_id == ct.id)).all()
    )
    assert count == 1


def test_dispose_creates_disposal_rows(lot_session: Session) -> None:
    wallet, entity, token = _seed_wallet_and_entity(lot_session)

    # Acquire 100 units
    ct_acq = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="200",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct_acq)
    lot_session.flush()

    # Dispose 60 units (> 1 year later → LONG)
    ct_dis = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="transfer-out",
        amount="60",
        value_usd="300",
        occurred_at=DISPOSE_WHEN,
        tx_hash=TX2,
    )
    apply_event(lot_session, ct_dis)
    lot_session.flush()

    disposal = lot_session.scalar(
        select(LotDisposal).where(LotDisposal.disposal_tx_id == ct_dis.id)
    )
    assert disposal is not None
    assert disposal.quantity_consumed == Decimal("60")
    assert disposal.proceeds_usd == Decimal("300")
    assert disposal.holding_period == "long"

    lot = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct_acq.id))
    assert lot is not None
    assert lot.quantity_remaining == Decimal("40")


def test_per_wallet_scoping_regression(lot_session: Session) -> None:
    """Wallet A's lots must NEVER be consumed by wallet B's disposal."""
    wallet_a, _, token = _seed_wallet_and_entity(lot_session)
    user_b = User(clerk_user_id=f"test_{uuid.uuid4().hex[:8]}", preferences={})
    lot_session.add(user_b)
    lot_session.flush()

    entity_b = Entity(user_id=user_b.id, name="B", type="personal", default_basis_method="fifo")
    lot_session.add(entity_b)
    lot_session.flush()

    wallet_b = Wallet(
        user_id=user_b.id,
        chain="lemonchain",
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    lot_session.add(wallet_b)
    lot_session.flush()

    lot_session.add(
        WalletEntityAssignment(
            wallet_id=wallet_b.id,
            entity_id=entity_b.id,
            effective_from=datetime(2023, 1, 1).date(),
            effective_to=None,
            classification="initial-assignment",
        )
    )
    lot_session.flush()

    # Wallet A acquires 100 units
    ct_a = _make_ct(
        lot_session,
        wallet_id=wallet_a.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="100",
        occurred_at=WHEN,
        tx_hash=f"0x{'1' * 64}",
    )
    apply_event(lot_session, ct_a)
    lot_session.flush()

    # Wallet B tries to dispose the same token — should create an InsufficientLotsError
    ct_b_dispose = _make_ct(
        lot_session,
        wallet_id=wallet_b.id,
        token_id=token.id,
        classification="transfer-out",
        amount="50",
        value_usd="50",
        occurred_at=DISPOSE_WHEN,
        tx_hash=f"0x{'2' * 64}",
    )
    apply_event(lot_session, ct_b_dispose)
    lot_session.flush()

    # Wallet A's lot must be untouched
    lot_a = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct_a.id))
    assert lot_a is not None
    assert lot_a.quantity_remaining == Decimal("100")  # unchanged

    # Wallet B's disposal creates an exception (not a disposal of A's lots)
    exc = lot_session.scalar(
        select(LotProcessingException).where(
            LotProcessingException.classified_tx_id == ct_b_dispose.id
        )
    )
    assert exc is not None
    assert exc.reason == "insufficient_lots"


def test_insufficient_lots_creates_exception(lot_session: Session) -> None:
    wallet, entity, token = _seed_wallet_and_entity(lot_session)
    ct = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="transfer-out",
        amount="999",
        value_usd="100",
        occurred_at=DISPOSE_WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct)
    lot_session.flush()

    exc = lot_session.scalar(
        select(LotProcessingException).where(LotProcessingException.classified_tx_id == ct.id)
    )
    assert exc is not None
    assert exc.reason == "insufficient_lots"
    assert exc.quantity_unmatched == Decimal("999")


def test_missing_basis_creates_exception(lot_session: Session) -> None:
    """Acquisition with NULL value_usd_at_event → exception, not a zero-basis lot."""
    wallet, entity, token = _seed_wallet_and_entity(lot_session)
    ct = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="reward",
        amount="10",
        value_usd=None,  # no FMV
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct)
    lot_session.flush()

    # No lot created
    lot = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct.id))
    assert lot is None

    # Exception recorded
    exc = lot_session.scalar(
        select(LotProcessingException).where(LotProcessingException.classified_tx_id == ct.id)
    )
    assert exc is not None
    assert exc.reason == "missing_basis"

    # v_lot_gate shows this event
    rows = lot_session.execute(
        text("SELECT classified_tx_id FROM v_lot_gate WHERE wallet_id = :wid"),
        {"wid": str(wallet.id)},
    ).fetchall()
    ct_ids = {r[0] for r in rows}
    assert str(ct.id) in ct_ids or ct.id in ct_ids


def test_burn_creates_zero_proceeds_disposal(lot_session: Session) -> None:
    wallet, entity, token = _seed_wallet_and_entity(lot_session)
    ct_acq = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="500",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct_acq)
    lot_session.flush()

    ct_burn = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="burn",
        amount="100",
        value_usd=None,
        occurred_at=DISPOSE_WHEN,
        tx_hash=TX2,
    )
    apply_event(lot_session, ct_burn)
    lot_session.flush()

    disposal = lot_session.scalar(
        select(LotDisposal).where(LotDisposal.disposal_tx_id == ct_burn.id)
    )
    assert disposal is not None
    assert disposal.proceeds_usd == Decimal("0")
    assert disposal.gain_loss_usd == -Decimal("500")


def test_rebuild_wallet_determinism(lot_session: Session) -> None:
    """rebuild_wallet replays to the same ledger state."""
    wallet, entity, token = _seed_wallet_and_entity(lot_session)

    ct_acq = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="100",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    ct_dis = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="transfer-out",
        amount="40",
        value_usd="80",
        occurred_at=DISPOSE_WHEN,
        tx_hash=TX2,
    )
    apply_event(lot_session, ct_acq)
    apply_event(lot_session, ct_dis)
    lot_session.flush()

    lot_before = lot_session.scalar(
        select(TaxLot).where(TaxLot.source_classified_tx_id == ct_acq.id)
    )
    assert lot_before is not None
    remaining_before = lot_before.quantity_remaining

    # Rebuild
    rebuild_wallet(lot_session, wallet.id)
    lot_session.flush()

    lot_after = lot_session.scalar(
        select(TaxLot).where(TaxLot.source_classified_tx_id == ct_acq.id)
    )
    assert lot_after is not None
    assert lot_after.quantity_remaining == remaining_before


def test_scdt_value_threading(lot_session: Session) -> None:
    """L2 NFT lot basis equals the paired SCDT disposal proceeds."""
    wallet, entity, scdt_token = _seed_wallet_and_entity(lot_session)
    # Make scdt_token a 0-decimal NFT-like token
    scdt_token.decimals = 0
    lot_session.flush()

    nft_token = TokenRegistry(
        chain="lemonchain",
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol="LQST",
        name="LemQuest",
        decimals=0,
        tier=2,
        category="ecosystem-l2",
    )
    lot_session.add(nft_token)
    lot_session.flush()

    # SCDT must exist as a lot first (acquire it)
    ct_acquire_scdt = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=scdt_token.id,
        classification="mint",
        amount="1",
        value_usd="50",
        occurred_at=WHEN,
        tx_hash="0x" + "a" * 64,
    )
    apply_event(lot_session, ct_acquire_scdt)
    lot_session.flush()

    tx_hash_redeem = "0x" + "b" * 64

    # SC leg (disposal of SCDT)
    ct_sc = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=scdt_token.id,
        classification="swap-credit-redemption",
        amount="1",
        value_usd="75",
        occurred_at=DISPOSE_WHEN,
        tx_hash=tx_hash_redeem,
        event_seq=0,
        notes="scdt-out: SCDT NFT redeemed",
    )
    apply_event(lot_session, ct_sc)
    lot_session.flush()

    # NFT leg (acquire L2 NFT; basis should be threaded from SC leg)
    ct_nft = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=nft_token.id,
        classification="swap-credit-redemption",
        amount="1",
        value_usd=None,  # NFT leg has no own FMV — threaded from SC
        occurred_at=DISPOSE_WHEN,
        tx_hash=tx_hash_redeem,
        event_seq=1,
        notes="l2-nft-in: acquired via SCDT redemption",
    )
    apply_event(lot_session, ct_nft)
    lot_session.flush()

    nft_lot = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct_nft.id))
    assert nft_lot is not None
    # NFT lot basis == SC disposal proceeds (value-threaded)
    assert nft_lot.cost_basis_usd == Decimal("75")
    assert nft_lot.asset_class == "collectible"


def test_asset_class_nft_mint_is_collectible(lot_session: Session) -> None:
    wallet, entity, token = _seed_wallet_and_entity(lot_session)
    token.decimals = 0  # NFT signal
    lot_session.flush()

    ct = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="mint",
        amount="1",
        value_usd="10",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct)
    lot_session.flush()

    lot = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct.id))
    assert lot is not None
    assert lot.asset_class == "collectible"


def test_erc20_reward_is_fungible(lot_session: Session) -> None:
    wallet, entity, token = _seed_wallet_and_entity(lot_session)
    token.decimals = 18  # ERC-20
    lot_session.flush()

    ct = _make_ct(
        lot_session,
        wallet_id=wallet.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="50",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct)
    lot_session.flush()

    lot = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct.id))
    assert lot is not None
    assert lot.asset_class == "fungible"


def test_apply_relocation_changes_wallet_id(lot_session: Session) -> None:
    wallet_a, entity, token = _seed_wallet_and_entity(lot_session)
    wallet_b, entity_b, _ = _seed_wallet_and_entity(lot_session)

    ct_acq = _make_ct(
        lot_session,
        wallet_id=wallet_a.id,
        token_id=token.id,
        classification="reward",
        amount="100",
        value_usd="100",
        occurred_at=WHEN,
        tx_hash=TX1,
    )
    apply_event(lot_session, ct_acq)
    lot_session.flush()

    ct_rel = _make_ct(
        lot_session,
        wallet_id=wallet_a.id,
        token_id=token.id,
        classification="transfer-out",
        amount="100",
        value_usd=None,
        occurred_at=DISPOSE_WHEN,
        tx_hash=TX2,
    )
    apply_relocation(lot_session, ct_rel, wallet_a.id, wallet_b.id, "bridge")
    lot_session.flush()

    lot = lot_session.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == ct_acq.id))
    assert lot is not None
    assert lot.wallet_id == wallet_b.id
    # acquired_at and cost_basis preserved
    assert lot.cost_basis_usd == Decimal("100")
