"""Stage 4: lot engine executes transfer_resolution signals.

T1 — relocate-contribution: 18-month lot tacks → LONG at disposal, $0 adj
T2 — disposal-related-party: loss → adjustment_code='L'; gain → no adjustment
T3 — encumbrance (GATE test): unresolved cross-entity leg blocks in v_lot_gate;
     ordinary transfer-out with NULL transfer_resolution disposes normally.
     The two NULL cases are distinguished by gate-row presence, not CT fields.
T4 — gift-out: qty_remaining reduced, no disposal row, 709 flag
T5 — no-op-loan: lots untouched, no disposal, CPA flag
T6 — relocate-reassignment: destination lot preserves source acquisition_type
T7 — relocate variants carryover: relocate-internal (preserve) and
     relocate-gift (→ 'gift' + 709); no disposal for any relocate-* inflow
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

from lemon_ledger.domain.cross_entity.detection import detect_for_user
from lemon_ledger.domain.lots.engine import apply_event
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.lot import LotDisposal, LotRelocation, TaxLot
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

# ── sync engine for this module ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def tr_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(sync_url, future=True)


@pytest.fixture(scope="module")
def tr_sessionmaker(tr_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(tr_engine, expire_on_commit=False)


@pytest.fixture
def db(tr_sessionmaker: sessionmaker[Session]) -> Generator[Session, None, None]:
    with tr_sessionmaker() as session:
        with session.begin():
            session.begin_nested()
            yield session
            session.rollback()


# ── seed helpers ──────────────────────────────────────────────────────────────

_ACQUIRE_AT = datetime(2024, 1, 1, tzinfo=UTC)  # acquisition date
_DISPOSE_AT = datetime(2025, 7, 1, tzinfo=UTC)  # 18 months later → LONG
_CROSS_AT = datetime(2025, 3, 1, tzinfo=UTC)  # relocation date (14 months in)


def _user(db: Session) -> User:
    u = User(clerk_user_id=f"t_{uuid.uuid4().hex[:8]}", preferences={})
    db.add(u)
    db.flush()
    return u


def _entity(db: Session, user: User, name: str = "E") -> Entity:
    e = Entity(user_id=user.id, name=name, type="personal", default_basis_method="fifo")
    db.add(e)
    db.flush()
    return e


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


def _assign(
    db: Session,
    wallet: Wallet,
    entity: Entity,
    from_date: datetime = datetime(2023, 1, 1),
) -> None:
    db.add(
        WalletEntityAssignment(
            wallet_id=wallet.id,
            entity_id=entity.id,
            effective_from=from_date.date(),
            effective_to=None,
            classification="initial-assignment",
        )
    )
    db.flush()


def _token(db: Session) -> TokenRegistry:
    t = TokenRegistry(
        chain="lemonchain",
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol="LEMX",
        name="Native Lemon",
        decimals=18,
        tier=1,
        category="ecosystem-native",
    )
    db.add(t)
    db.flush()
    return t


def _ct(
    db: Session,
    *,
    wallet: Wallet,
    token: TokenRegistry,
    classification: str,
    amount: str,
    value_usd: str | None = None,
    occurred_at: datetime = _ACQUIRE_AT,
    tx_hash: str | None = None,
    event_seq: int = 0,
    transfer_resolution: str | None = None,
    relocation_source_event_id: uuid.UUID | None = None,
) -> ClassifiedTransaction:
    ct = ClassifiedTransaction(
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash=tx_hash or f"0x{uuid.uuid4().hex}",
        event_seq=event_seq,
        block_number=100,
        occurred_at=occurred_at,
        classification=classification,
        token_id=token.id,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        amount=Decimal(amount),
        value_usd_at_event=Decimal(value_usd) if value_usd else None,
        transfer_resolution=transfer_resolution,
        relocation_source_event_id=relocation_source_event_id,
    )
    db.add(ct)
    db.flush()
    return ct


# ── T1: relocate-contribution — 18-month tack → LONG at disposal ─────────────


def test_t1_relocate_contribution_tacks_holding_period(db: Session) -> None:
    user = _user(db)
    ent_a = _entity(db, user, "A")
    ent_b = _entity(db, user, "B")
    wallet_a = _wallet(db, user)
    wallet_b = _wallet(db, user)
    _assign(db, wallet_a, ent_a)
    _assign(db, wallet_b, ent_b)
    token = _token(db)

    # Acquire 10 LEMX in wallet_a, $100 basis, Jan 1 2024
    acq = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    tx = f"0x{uuid.uuid4().hex}"
    # Outflow: relocate-contribution, no relocation_source (→ NONE)
    out = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        amount="10",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=0,
        transfer_resolution="relocate-contribution",
    )
    # Inflow: relocate-contribution with relocation pointer (→ RELOCATE)
    inflow = _ct(
        db,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        amount="10",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=1,
        transfer_resolution="relocate-contribution",
        relocation_source_event_id=out.id,
    )

    apply_event(db, out)  # NONE — no lots consumed
    apply_event(db, inflow)  # RELOCATE — move lot to wallet_b
    db.flush()

    # No disposal row from the relocation
    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == out.id)) is None
    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == inflow.id)) is None

    lot = db.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == acq.id))
    assert lot is not None
    assert lot.wallet_id == wallet_b.id  # lot is now in dest wallet
    assert lot.acquired_at == _ACQUIRE_AT  # acquisition date preserved (tacking)
    assert lot.cost_basis_usd == Decimal("100")  # basis preserved
    assert lot.acquisition_type == "cap-contribution"

    # Dispose 18 months after acquisition → LONG
    disposal_ct = _ct(
        db,
        wallet=wallet_b,
        token=token,
        classification="transfer-out",
        amount="10",
        value_usd="150",
        occurred_at=_DISPOSE_AT,
    )
    apply_event(db, disposal_ct)
    db.flush()

    disposal = db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == disposal_ct.id))
    assert disposal is not None
    assert disposal.holding_period == "long"
    assert disposal.basis_consumed_usd == Decimal("100")
    assert disposal.proceeds_usd == Decimal("150")
    assert disposal.adjustment_code is None


# ── T2: disposal-related-party — §267 loss zeroing ───────────────────────────


def test_t2_disposal_related_party_loss_zeroed(db: Session) -> None:
    user = _user(db)
    ent = _entity(db, user)
    wallet = _wallet(db, user)
    _assign(db, wallet, ent)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    # Related-party disposal at a LOSS: proceeds=$80, basis=$100 → gain=-$20
    dis = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        value_usd="80",
        occurred_at=_DISPOSE_AT,
        transfer_resolution="disposal-related-party",
    )
    apply_event(db, dis)
    db.flush()

    disposal = db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == dis.id))
    assert disposal is not None
    assert disposal.gain_loss_usd == Decimal("-20")
    assert disposal.adjustment_code == "L"
    assert disposal.adjustment_usd == Decimal("20")


def test_t2_disposal_related_party_gain_no_adjustment(db: Session) -> None:
    user = _user(db)
    ent = _entity(db, user)
    wallet = _wallet(db, user)
    _assign(db, wallet, ent)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    # Related-party disposal at a GAIN: proceeds=$120 → gain=$20, no §267 adjustment
    dis = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        value_usd="120",
        occurred_at=_DISPOSE_AT,
        transfer_resolution="disposal-related-party",
    )
    apply_event(db, dis)
    db.flush()

    disposal = db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == dis.id))
    assert disposal is not None
    assert disposal.gain_loss_usd == Decimal("20")
    assert disposal.adjustment_code is None
    assert disposal.adjustment_usd is None


# ── T3: encumbrance — GATE test (not engine test) ────────────────────────────
#
# Detection leaves Branch-2/3 CTs as ordinary 'transfer-out' + NULL transfer_resolution.
# The engine cannot distinguish them from plain taxable disposals (see P4 pre-flight).
# Encumbrance is enforced UPSTREAM:
#   needs_classification → v_lot_gate blocks the wallet → cross-entity pass precedes lot
#   apply → generate-8949 refuses on a held gate.
#
# T3 proves the two-sided gate property:
#   Side A: unresolved cross-entity outflow → blocking row in v_lot_gate.
#   Side B: ordinary transfer-out (no pending row, NULL transfer_resolution) → disposes
#           normally in the engine. The two NULL cases are distinguished ONLY by gate-row
#           presence, not by anything on the CT itself.


def test_t3_unresolved_cross_entity_blocks_in_gate(db: Session) -> None:
    """Branch-2 detection → needs_classification → v_lot_gate blocking=true for that wallet."""
    user = _user(db)
    ent_a = _entity(db, user, "GateA")
    ent_b = _entity(db, user, "GateB")
    wallet_a = _wallet(db, user)
    wallet_b = _wallet(db, user)
    _assign(db, wallet_a, ent_a)
    _assign(db, wallet_b, ent_b)
    token = _token(db)

    tx = f"0x{uuid.uuid4().hex}"
    outflow = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        amount="5",
        occurred_at=_ACQUIRE_AT,
        tx_hash=tx,
        event_seq=0,
    )
    _ct(
        db,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        amount="5",
        occurred_at=_ACQUIRE_AT,
        tx_hash=tx,
        event_seq=0,
    )

    detect_for_user(db, user_id=user.id)

    # Side A: unresolved leg must appear in v_lot_gate with blocking=true.
    gate_rows = db.execute(
        text(
            "SELECT classified_tx_id, blocking, reason "
            "FROM v_lot_gate WHERE classified_tx_id = :ct_id"
        ),
        {"ct_id": str(outflow.id)},
    ).fetchall()

    assert len(gate_rows) >= 1, (
        "Unresolved cross-entity outflow must appear in v_lot_gate — "
        "this is the encumbrance mechanism preventing lot finalization"
    )
    blocking_rows = [r for r in gate_rows if r.blocking]
    assert len(blocking_rows) >= 1, "Cross-entity pending row must be blocking=true in v_lot_gate"
    assert any("needs_classification" in r.reason for r in blocking_rows)


def test_t3_ordinary_transfer_out_disposes_normally(db: Session) -> None:
    """Side B: plain transfer-out with NULL transfer_resolution and NO pending row disposes."""
    user = _user(db)
    ent = _entity(db, user)
    wallet = _wallet(db, user)
    _assign(db, wallet, ent)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    # Ordinary transfer-out: transfer_resolution=NULL, no pending row → plain disposal.
    dis = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        value_usd="200",
        occurred_at=_DISPOSE_AT,
    )
    apply_event(db, dis)
    db.flush()

    disposal = db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == dis.id))
    assert disposal is not None, (
        "Ordinary transfer-out (NULL transfer_resolution, no pending row) must create a disposal. "
        "The NULL case is distinguished from an unresolved leg ONLY by the absence of a gate row."
    )
    assert disposal.gain_loss_usd == Decimal("100")


# ── T4: gift-out — qty_remaining reduced, no disposal, 709 flag ──────────────


def test_t4_gift_out_consumes_lots_no_disposal(db: Session) -> None:
    user = _user(db)
    ent = _entity(db, user)
    wallet = _wallet(db, user)
    _assign(db, wallet, ent)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    gift = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="6",
        occurred_at=_DISPOSE_AT,
        transfer_resolution="gift-out",
    )
    apply_event(db, gift)
    db.flush()

    lot = db.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == acq.id))
    assert lot is not None
    assert lot.quantity_remaining == Decimal("4")  # 6 consumed

    # No disposal row
    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == gift.id)) is None

    # 709 flag
    db.refresh(gift)
    assert gift.needs_review is True


def test_t4_gift_out_idempotent(db: Session) -> None:
    user = _user(db)
    ent = _entity(db, user)
    wallet = _wallet(db, user)
    _assign(db, wallet, ent)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    gift = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        occurred_at=_DISPOSE_AT,
        transfer_resolution="gift-out",
    )
    apply_event(db, gift)
    db.flush()
    apply_event(db, gift)  # second call — must not double-consume
    db.flush()

    lot = db.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == acq.id))
    assert lot is not None
    assert lot.quantity_remaining == Decimal("0")


# ── T5: no-op-loan — lots untouched, CPA flag ────────────────────────────────


def test_t5_no_op_loan_leaves_lots_intact(db: Session) -> None:
    user = _user(db)
    ent = _entity(db, user)
    wallet = _wallet(db, user)
    _assign(db, wallet, ent)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    loan = _ct(
        db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="5",
        occurred_at=_DISPOSE_AT,
        transfer_resolution="no-op-loan",
    )
    apply_event(db, loan)
    db.flush()

    lot = db.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == acq.id))
    assert lot is not None
    assert lot.quantity_remaining == Decimal("10")  # untouched

    # No disposal
    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == loan.id)) is None
    # No relocation
    assert db.scalar(select(LotRelocation).where(LotRelocation.classified_tx_id == loan.id)) is None

    # CPA flag
    db.refresh(loan)
    assert loan.needs_review is True


# ── T6: relocate-reassignment — destination preserves source acquisition_type ─


def test_t6_relocate_reassignment_preserves_acquisition_type(db: Session) -> None:
    user = _user(db)
    ent_a = _entity(db, user, "ReA")
    ent_b = _entity(db, user, "ReB")
    wallet_a = _wallet(db, user)
    wallet_b = _wallet(db, user)
    _assign(db, wallet_a, ent_a)
    _assign(db, wallet_b, ent_b)
    token = _token(db)

    # Acquire as 'reward' → acquisition_type='reward'
    acq = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="reward",
        amount="10",
        value_usd="100",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    lot = db.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == acq.id))
    assert lot is not None
    assert lot.acquisition_type == "reward"

    tx = f"0x{uuid.uuid4().hex}"
    out = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        amount="10",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=0,
        transfer_resolution="relocate-reassignment",
    )
    inflow = _ct(
        db,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        amount="10",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=1,
        transfer_resolution="relocate-reassignment",
        relocation_source_event_id=out.id,
    )

    apply_event(db, out)
    apply_event(db, inflow)
    db.flush()

    db.refresh(lot)
    assert lot.wallet_id == wallet_b.id
    assert lot.acquisition_type == "reward"  # PRESERVED — not overwritten
    assert lot.acquired_at == _ACQUIRE_AT
    assert lot.cost_basis_usd == Decimal("100")
    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == inflow.id)) is None


# ── T7: relocate-internal + relocate-gift carryover ──────────────────────────


def test_t7_relocate_internal_preserves_acquisition_type(db: Session) -> None:
    user = _user(db)
    ent = _entity(db, user, "Same")  # same entity on both wallets
    wallet_a = _wallet(db, user)
    wallet_b = _wallet(db, user)
    _assign(db, wallet_a, ent)
    _assign(db, wallet_b, ent)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="mint",
        amount="5",
        value_usd="50",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    lot = db.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == acq.id))
    assert lot is not None
    assert lot.acquisition_type == "mint"

    tx = f"0x{uuid.uuid4().hex}"
    out = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        amount="5",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=0,
        transfer_resolution="relocate-internal",
    )
    inflow = _ct(
        db,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        amount="5",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=1,
        transfer_resolution="relocate-internal",
        relocation_source_event_id=out.id,
    )

    apply_event(db, out)
    apply_event(db, inflow)
    db.flush()

    db.refresh(lot)
    assert lot.wallet_id == wallet_b.id
    assert lot.acquisition_type == "mint"  # PRESERVED
    assert lot.acquired_at == _ACQUIRE_AT
    assert lot.cost_basis_usd == Decimal("50")
    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == out.id)) is None
    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == inflow.id)) is None


def test_t7_relocate_gift_sets_gift_type_and_709(db: Session) -> None:
    user = _user(db)
    ent_a = _entity(db, user, "GiftA")
    ent_b = _entity(db, user, "GiftB")
    wallet_a = _wallet(db, user)
    wallet_b = _wallet(db, user)
    _assign(db, wallet_a, ent_a)
    _assign(db, wallet_b, ent_b)
    token = _token(db)

    acq = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="reward",
        amount="8",
        value_usd="80",
        occurred_at=_ACQUIRE_AT,
    )
    apply_event(db, acq)
    db.flush()

    tx = f"0x{uuid.uuid4().hex}"
    out = _ct(
        db,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        amount="8",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=0,
        transfer_resolution="relocate-gift",
    )
    inflow = _ct(
        db,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        amount="8",
        occurred_at=_CROSS_AT,
        tx_hash=tx,
        event_seq=1,
        transfer_resolution="relocate-gift",
        relocation_source_event_id=out.id,
    )

    apply_event(db, out)
    apply_event(db, inflow)
    db.flush()

    lot = db.scalar(select(TaxLot).where(TaxLot.source_classified_tx_id == acq.id))
    assert lot is not None
    assert lot.wallet_id == wallet_b.id
    assert lot.acquisition_type == "gift"
    assert lot.acquired_at == _ACQUIRE_AT  # tacking preserved
    assert lot.cost_basis_usd == Decimal("80")

    assert db.scalar(select(LotDisposal).where(LotDisposal.disposal_tx_id == inflow.id)) is None

    # 709 flag set on the INFLOW ct (the relocation trigger event)
    db.refresh(inflow)
    assert inflow.needs_review is True
