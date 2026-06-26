"""Integration test: v_lot_gate sources (c) and (d) key by wallet UUID.

Regression test for the 1.8 latent bug where sources (c) and (d) emitted
w.user_id AS wallet_id instead of the wallet UUID.  A gate guard filtering
by wallet_id would silently miss bridge blockers from those sources.

After migration e2f3a4b5c6d7 both sources emit the actual wallet UUID, so
filtering WHERE wallet_id = :wallet_uuid finds the rows correctly.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.models.bridge import BridgeCorrelation
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.logical_asset import LogicalAsset
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet

# ── module-scoped sync engine (same pattern as test_engine_transfer_resolution) ─


@pytest.fixture(scope="module")
def gk_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(sync_url, future=True)


@pytest.fixture(scope="module")
def gk_sessionmaker(gk_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(gk_engine, expire_on_commit=False)


@pytest.fixture
def gk_db(gk_sessionmaker: sessionmaker[Session]) -> Generator[Session, None, None]:
    with gk_sessionmaker() as session:
        with session.begin():
            session.begin_nested()
            yield session
            session.rollback()


# ── seed helpers ───────────────────────────────────────────────────────────────

_AT = datetime(2025, 6, 1, tzinfo=UTC)


def _user(db: Session) -> User:
    u = User(clerk_user_id=f"gk_{uuid.uuid4().hex[:8]}", preferences={})
    db.add(u)
    db.flush()
    return u


def _wallet(db: Session, user: User) -> Wallet:
    w = Wallet(
        user_id=user.id,
        chain="lemonchain",
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    db.add(w)
    db.flush()
    return w


def _logical_asset(db: Session) -> LogicalAsset:
    sym = f"TST_{uuid.uuid4().hex[:6]}"
    la = LogicalAsset(symbol=sym, name=sym, asset_kind="fungible")
    db.add(la)
    db.flush()
    return la


def _token(db: Session) -> TokenRegistry:
    t = TokenRegistry(
        chain="lemonchain",
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol="TST",
        name="Test Token",
        decimals=18,
        tier=1,
        category="ecosystem-native",
    )
    db.add(t)
    db.flush()
    return t


def _ct(
    db: Session, wallet: Wallet, token: TokenRegistry, classification: str
) -> ClassifiedTransaction:
    ct = ClassifiedTransaction(
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash=f"0x{uuid.uuid4().hex}",
        event_seq=0,
        block_number=1000,
        occurred_at=_AT,
        classification=classification,
        token_id=token.id,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        amount=Decimal("10"),
        value_usd_at_event=Decimal("100"),
    )
    db.add(ct)
    db.flush()
    return ct


def _bridge_corr(
    db: Session,
    user: User,
    la: LogicalAsset,
    *,
    out_ct: ClassifiedTransaction,
    in_ct: ClassifiedTransaction | None = None,
    status: str,
) -> BridgeCorrelation:
    corr = BridgeCorrelation(
        user_id=user.id,
        logical_asset_id=la.id,
        outflow_classified_event_id=out_ct.id,
        inflow_classified_event_id=in_ct.id if in_ct else None,
        status=status,
        confidence_level="high" if status == "needs_confirmation" else None,
        confidence_score=Decimal("0.9500") if status == "needs_confirmation" else None,
    )
    db.add(corr)
    db.flush()
    return corr


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_bridge_needs_confirmation_surfaces_by_wallet_uuid(gk_db: Session) -> None:
    """Source (c): bridge:needs_confirmation row must appear when filtering by wallet UUID.

    Before the fix, w.user_id was emitted as wallet_id.  Filtering by the actual
    wallet UUID returned zero rows, so a bridge blocker was invisible to the gate guard.
    """
    user = _user(gk_db)
    wallet_out = _wallet(gk_db, user)
    wallet_in = _wallet(gk_db, user)
    token = _token(gk_db)
    la = _logical_asset(gk_db)

    out_ct = _ct(gk_db, wallet_out, token, "bridge-out")
    in_ct = _ct(gk_db, wallet_in, token, "bridge-in")

    _bridge_corr(gk_db, user, la, out_ct=out_ct, in_ct=in_ct, status="needs_confirmation")
    gk_db.flush()

    rows = gk_db.execute(
        text(
            "SELECT classified_tx_id, wallet_id, reason, blocking "
            "FROM v_lot_gate "
            "WHERE wallet_id = :wid AND reason = 'bridge:needs_confirmation'"
        ),
        {"wid": str(wallet_out.id)},
    ).fetchall()

    assert len(rows) == 1, (
        f"Expected 1 bridge:needs_confirmation row for wallet {wallet_out.id!s}, "
        f"got {len(rows)}. "
        "If 0: source (c) is still emitting user_id instead of wallet_id."
    )
    row = rows[0]
    assert str(row[0]) == str(out_ct.id), "classified_tx_id must be the outflow CT"
    assert str(row[1]) == str(wallet_out.id), "wallet_id must be the wallet UUID, not user_id"
    assert row[2] == "bridge:needs_confirmation"
    assert row[3] is True, "bridge:needs_confirmation must be blocking=true"


def test_bridge_aged_unmatched_surfaces_by_wallet_uuid(gk_db: Session) -> None:
    """Source (d): bridge:aged_unmatched row must appear when filtering by wallet UUID.

    Before the fix, w.user_id was emitted as wallet_id.  Non-blocking aged legs
    were also invisible to a wallet-filtered gate query.
    """
    user = _user(gk_db)
    wallet_out = _wallet(gk_db, user)
    token = _token(gk_db)
    la = _logical_asset(gk_db)

    out_ct = _ct(gk_db, wallet_out, token, "bridge-out")

    _bridge_corr(gk_db, user, la, out_ct=out_ct, status="unmatched")
    gk_db.flush()

    rows = gk_db.execute(
        text(
            "SELECT classified_tx_id, wallet_id, reason, blocking "
            "FROM v_lot_gate "
            "WHERE wallet_id = :wid AND reason = 'bridge:aged_unmatched'"
        ),
        {"wid": str(wallet_out.id)},
    ).fetchall()

    assert len(rows) == 1, (
        f"Expected 1 bridge:aged_unmatched row for wallet {wallet_out.id!s}, "
        f"got {len(rows)}. "
        "If 0: source (d) is still emitting user_id instead of wallet_id."
    )
    row = rows[0]
    assert str(row[0]) == str(out_ct.id), "classified_tx_id must be the outflow CT"
    assert str(row[1]) == str(wallet_out.id), "wallet_id must be the wallet UUID, not user_id"
    assert row[2] == "bridge:aged_unmatched"
    assert row[3] is False, "bridge:aged_unmatched must be blocking=false"
