"""Integration tests for domain.cross_entity.detection.

All tests run against a real Testcontainers Postgres instance (same container
and migration set used by the bridge/lot engine integration tests).

CRITICAL: The SCD half-open boundary tests at the bottom of this file are
the single most error-prone tests in Chat 1.9.  They verify that the explicit
CAST(effective_from AS TIMESTAMPTZ) comparison is correct on both sides of the
2024-03-15 00:00:00 UTC seam.  MagicMock cannot catch Postgres date casting bugs;
only a real DB query can.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.domain.cross_entity.detection import (
    NoEntityAssignment,
    detect_for_user,
    make_logical_transfer_key,
    resolve_entity_at,
)
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.pending_classification import PendingClassification
from lemon_ledger.models.raw import RawTokenTransfer
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

# ── Module-scoped sync engine ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def det_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(sync_url, future=True)


@pytest.fixture(scope="module")
def det_sessionmaker(det_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(det_engine, expire_on_commit=False)


@pytest.fixture
def det_session(det_sessionmaker: sessionmaker[Session]) -> Generator[Session, None, None]:
    with det_sessionmaker() as session:
        with session.begin():
            session.begin_nested()
            yield session
            session.rollback()


# ── Shared seed helpers ───────────────────────────────────────────────────────


def _make_user(session: Session) -> User:
    u = User(clerk_user_id=f"det_{uuid.uuid4().hex[:8]}", preferences={})
    session.add(u)
    session.flush()
    return u


def _make_entity(session: Session, user: User, name: str = "Test Entity") -> Entity:
    e = Entity(
        user_id=user.id,
        name=name,
        type="personal",
        default_basis_method="fifo",
        jurisdiction="US",
        bridge_treatment="relocate",
    )
    session.add(e)
    session.flush()
    return e


def _make_wallet(session: Session, user: User, *, chain: str = "lemonchain") -> Wallet:
    w = Wallet(
        user_id=user.id,
        chain=chain,
        address=f"0x{uuid.uuid4().hex[:40]}",
        role="live",
    )
    session.add(w)
    session.flush()
    return w


def _assign(
    session: Session,
    wallet: Wallet,
    entity: Entity,
    *,
    effective_from: date,
    effective_to: date | None = None,
) -> WalletEntityAssignment:
    asgn = WalletEntityAssignment(
        wallet_id=wallet.id,
        entity_id=entity.id,
        effective_from=effective_from,
        effective_to=effective_to,
        classification="initial-assignment",
    )
    session.add(asgn)
    session.flush()
    return asgn


def _make_token(session: Session, chain: str = "lemonchain") -> TokenRegistry:
    t = TokenRegistry(
        chain=chain,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol="LEMX",
        name="Native Lemon",
        decimals=18,
        tier=1,
        category="ecosystem-native",
    )
    session.add(t)
    session.flush()
    return t


def _make_ct(
    session: Session,
    *,
    wallet: Wallet,
    token: TokenRegistry,
    classification: str,
    tx_hash: str | None = None,
    event_seq: int = 0,
    occurred_at: datetime | None = None,
    amount: Decimal = Decimal("10"),
    transfer_resolution: str | None = None,
) -> ClassifiedTransaction:
    tx_hash = tx_hash or f"0x{uuid.uuid4().hex}"
    occurred_at = occurred_at or datetime(2024, 6, 1, tzinfo=UTC)
    ct = ClassifiedTransaction(
        wallet_id=wallet.id,
        chain=wallet.chain,
        tx_hash=tx_hash,
        event_seq=event_seq,
        block_number=100,
        occurred_at=occurred_at,
        classification=classification,
        token_id=token.id,
        contract_address=token.contract_address,
        amount=amount,
        value_usd_at_event=Decimal("100"),
        needs_review=False,
        manual_override=False,
        bridge_correlation_id=None,
        transfer_resolution=transfer_resolution,
    )
    session.add(ct)
    session.flush()
    return ct


def _make_raw_transfer(
    session: Session,
    *,
    wallet: Wallet,
    token: TokenRegistry,
    tx_hash: str,
    to_address: str,
    from_address: str = "0x" + "a" * 40,
    occurred_at: datetime | None = None,
) -> RawTokenTransfer:
    occurred_at = occurred_at or datetime(2024, 6, 1, tzinfo=UTC)
    rt = RawTokenTransfer(
        wallet_id=wallet.id,
        chain=wallet.chain,
        block_number=100,
        tx_hash=tx_hash,
        occurred_at=occurred_at,
        value=Decimal("10000000000000000000"),
        log_index=0,
        contract_address=token.contract_address,
        raw={"to": to_address, "from": from_address, "value": "10000000000000000000"},
    )
    session.add(rt)
    session.flush()
    return rt


# ── make_logical_transfer_key unit test ───────────────────────────────────────


def test_make_logical_transfer_key_is_stable() -> None:
    wid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    k = make_logical_transfer_key("lemonchain", "0xABCDEF", wid, 0)
    assert k == f"lemonchain:0xabcdef:{wid}:0"


def test_make_logical_transfer_key_chain_and_hash_lowercased() -> None:
    wid = uuid.uuid4()
    k1 = make_logical_transfer_key("LEMONCHAIN", "0xABC", wid, 1)
    k2 = make_logical_transfer_key("lemonchain", "0xabc", wid, 1)
    assert k1 == k2


# ── resolve_entity_at — real Postgres SCD boundary tests ─────────────────────


def test_resolve_entity_at_single_open_assignment(det_session: Session) -> None:
    """Present-day tx with effective_to=NULL → exactly one row matches."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet = _make_wallet(det_session, user)
    _assign(
        det_session,
        wallet,
        entity,
        effective_from=date(2023, 1, 1),
        effective_to=None,
    )
    tx_ts = datetime(2025, 6, 1, tzinfo=UTC)
    asgn = resolve_entity_at(det_session, wallet.id, tx_ts)
    assert asgn.entity_id == entity.id


def test_resolve_entity_at_no_covering_row_raises(det_session: Session) -> None:
    """Guard fires when no assignment covers the wallet at the given time."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet = _make_wallet(det_session, user)
    # Assignment starts in 2024; tx is in 2023.
    _assign(
        det_session,
        wallet,
        entity,
        effective_from=date(2024, 1, 1),
        effective_to=None,
    )
    tx_ts = datetime(2023, 12, 31, 23, 59, 59, tzinfo=UTC)
    with pytest.raises(NoEntityAssignment):
        resolve_entity_at(det_session, wallet.id, tx_ts)


# ── SCD BOUNDARY TESTS (CRITICAL — do not simplify or mock) ──────────────────
#
# These two tests verify the explicit CAST(DATE AS TIMESTAMPTZ) behavior on
# BOTH sides of the 2024-03-15 00:00:00 UTC seam.  A MagicMock session cannot
# catch Postgres date-casting bugs.  If the cast is implicit or session-tz-
# dependent, one of these tests will fail.


def test_scd_boundary_tx_at_seam_resolves_to_new_entity(det_session: Session) -> None:
    """tx at 2024-03-15 00:00:00 UTC → new entity (INCLUSIVE lower bound of new row)."""
    user = _make_user(det_session)
    entity_old = _make_entity(det_session, user, "Entity Old")
    entity_new = _make_entity(det_session, user, "Entity New")
    wallet = _make_wallet(det_session, user)

    # Old assignment: [2023-01-01, 2024-03-15)
    _assign(
        det_session,
        wallet,
        entity_old,
        effective_from=date(2023, 1, 1),
        effective_to=date(2024, 3, 15),
    )
    # New assignment: [2024-03-15, ∞)
    _assign(
        det_session,
        wallet,
        entity_new,
        effective_from=date(2024, 3, 15),
        effective_to=None,
    )

    tx_ts = datetime(2024, 3, 15, 0, 0, 0, tzinfo=UTC)  # exactly at the seam
    asgn = resolve_entity_at(det_session, wallet.id, tx_ts)
    assert asgn.entity_id == entity_new.id, (
        "tx AT effective_from boundary must resolve to the NEW entity "
        "(half-open [effective_from, effective_to) interval)"
    )


def test_scd_boundary_tx_before_seam_resolves_to_old_entity(det_session: Session) -> None:
    """tx at 2024-03-14 23:59:59 UTC → old entity (exclusive upper bound of old row)."""
    user = _make_user(det_session)
    entity_old = _make_entity(det_session, user, "Entity Old B")
    entity_new = _make_entity(det_session, user, "Entity New B")
    wallet = _make_wallet(det_session, user)

    _assign(
        det_session,
        wallet,
        entity_old,
        effective_from=date(2023, 1, 1),
        effective_to=date(2024, 3, 15),
    )
    _assign(
        det_session,
        wallet,
        entity_new,
        effective_from=date(2024, 3, 15),
        effective_to=None,
    )

    tx_ts = datetime(2024, 3, 14, 23, 59, 59, tzinfo=UTC)  # one second before seam
    asgn = resolve_entity_at(det_session, wallet.id, tx_ts)
    assert asgn.entity_id == entity_old.id, (
        "tx one second BEFORE effective_from boundary must resolve to the OLD entity"
    )


# ── Branch 1: own-wallet, same entity → auto-resolve ─────────────────────────


def test_branch1_same_entity_auto_stamps_relocation(det_session: Session) -> None:
    """Own-wallet transfer between two wallets of the same entity → relocate-internal."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet_a = _make_wallet(det_session, user)
    wallet_b = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity, effective_from=date(2023, 1, 1))
    _assign(det_session, wallet_b, entity, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    tx_hash = f"0x{uuid.uuid4().hex}"
    outflow = _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        tx_hash=tx_hash,
        event_seq=0,
    )
    inflow = _make_ct(
        det_session,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        tx_hash=tx_hash,
        event_seq=0,
    )

    result = detect_for_user(det_session, user_id=user.id)

    assert result["auto_resolved"] == 1
    assert result["pending_created"] == 0

    det_session.refresh(outflow)
    det_session.refresh(inflow)
    assert outflow.transfer_resolution == "relocate-internal"
    assert inflow.transfer_resolution == "relocate-internal"
    assert inflow.relocation_source_event_id == outflow.id


# ── Branch 2: own-wallet, different entities → pending (cross-entity) ─────────


def test_branch2_different_entities_creates_pending_row(det_session: Session) -> None:
    """Own-wallet transfer between wallets of different entities → cross-entity pending."""
    user = _make_user(det_session)
    entity_a = _make_entity(det_session, user, "Entity A")
    entity_b = _make_entity(det_session, user, "Entity B")
    wallet_a = _make_wallet(det_session, user)
    wallet_b = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity_a, effective_from=date(2023, 1, 1))
    _assign(det_session, wallet_b, entity_b, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    tx_hash = f"0x{uuid.uuid4().hex}"
    outflow = _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        tx_hash=tx_hash,
        event_seq=0,
    )
    _make_ct(
        det_session,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        tx_hash=tx_hash,
        event_seq=0,
    )

    result = detect_for_user(det_session, user_id=user.id)

    assert result["auto_resolved"] == 0
    assert result["pending_created"] == 1

    pc = det_session.scalar(
        select(PendingClassification).where(PendingClassification.user_id == user.id)
    )
    assert pc is not None
    assert pc.kind == "cross-entity"
    assert pc.from_wallet_id == wallet_a.id
    assert pc.to_wallet_id == wallet_b.id
    assert pc.from_entity_id == entity_a.id
    assert pc.to_entity_id == entity_b.id
    assert pc.tx_hash == tx_hash
    assert pc.transfer_index == 0

    ltk = make_logical_transfer_key(outflow.chain, tx_hash, wallet_a.id, 0)
    assert pc.logical_transfer_key == ltk


# ── Branch 3: external outflow → pending (external-outflow) ───────────────────


def test_branch3_external_outflow_creates_pending_row(det_session: Session) -> None:
    """Outflow to an external (non-own) address → external-outflow pending."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet_a = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    tx_hash = f"0x{uuid.uuid4().hex}"
    external_addr = f"0x{uuid.uuid4().hex[:40]}"

    outflow = _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        tx_hash=tx_hash,
        event_seq=0,
    )
    # Provide raw transfer so detection can retrieve the `to` address.
    _make_raw_transfer(
        det_session,
        wallet=wallet_a,
        token=token,
        tx_hash=tx_hash,
        to_address=external_addr,
    )

    result = detect_for_user(det_session, user_id=user.id)

    assert result["auto_resolved"] == 0
    assert result["pending_created"] == 1

    pc = det_session.scalar(
        select(PendingClassification).where(PendingClassification.user_id == user.id)
    )
    assert pc is not None
    assert pc.kind == "external-outflow"
    assert pc.from_wallet_id == wallet_a.id
    assert pc.to_wallet_id is None
    assert pc.to_address == external_addr.lower()

    ltk = make_logical_transfer_key(outflow.chain, tx_hash, wallet_a.id, 0)
    assert pc.logical_transfer_key == ltk


# ── Branch 4: inflow only → no pending row created ────────────────────────────


def test_branch4_inflow_only_skipped(det_session: Session) -> None:
    """Inflow with no own-wallet outflow → no pending row (handled from outflow side)."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet_b = _make_wallet(det_session, user)
    _assign(det_session, wallet_b, entity, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    _make_ct(
        det_session,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
    )

    result = detect_for_user(det_session, user_id=user.id)

    assert result["auto_resolved"] == 0
    assert result["pending_created"] == 0

    count = det_session.scalar(
        select(PendingClassification).where(PendingClassification.user_id == user.id)
    )
    assert count is None


# ── Dedup: two detection runs produce one pending row ─────────────────────────


def test_dedup_two_runs_produce_one_pending_row(det_session: Session) -> None:
    """Running detect_for_user twice against the same unresolved outflow → one row."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet_a = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    tx_hash = f"0x{uuid.uuid4().hex}"
    _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        tx_hash=tx_hash,
        event_seq=0,
    )
    _make_raw_transfer(
        det_session,
        wallet=wallet_a,
        token=token,
        tx_hash=tx_hash,
        to_address=f"0x{uuid.uuid4().hex[:40]}",
    )

    r1 = detect_for_user(det_session, user_id=user.id)
    r2 = detect_for_user(det_session, user_id=user.id)

    assert r1["pending_created"] == 1
    assert r2["pending_created"] == 0

    rows = det_session.scalars(
        select(PendingClassification).where(PendingClassification.user_id == user.id)
    ).all()
    assert len(rows) == 1


# ── Idempotency: already-resolved CTs are not reprocessed ─────────────────────


def test_idempotency_resolved_ct_not_reprocessed(det_session: Session) -> None:
    """CT with transfer_resolution already set → skipped entirely on re-run."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet_a = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        transfer_resolution="relocate-internal",  # already resolved
    )

    result = detect_for_user(det_session, user_id=user.id)

    assert result["auto_resolved"] == 0
    assert result["pending_created"] == 0


# ── Idempotency: classified/applied pending row not reopened ───────────────────


def test_idempotency_classified_pending_not_reopened(det_session: Session) -> None:
    """Pending row in 'classified' state → second run creates no new rows."""
    user = _make_user(det_session)
    entity_a = _make_entity(det_session, user, "EntA")
    entity_b = _make_entity(det_session, user, "EntB")
    wallet_a = _make_wallet(det_session, user)
    wallet_b = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity_a, effective_from=date(2023, 1, 1))
    _assign(det_session, wallet_b, entity_b, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    tx_hash = f"0x{uuid.uuid4().hex}"
    outflow = _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        tx_hash=tx_hash,
        event_seq=0,
    )
    _make_ct(
        det_session,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        tx_hash=tx_hash,
        event_seq=0,
    )

    # First run creates the pending row.
    r1 = detect_for_user(det_session, user_id=user.id)
    assert r1["pending_created"] == 1

    # Simulate user classifying the row (state transitions, but transfer_resolution not yet set).
    pc = det_session.scalar(
        select(PendingClassification).where(PendingClassification.user_id == user.id)
    )
    assert pc is not None
    pc.state = "classified"
    pc.chosen_classification = "sale"
    det_session.flush()

    # Second run: outflow CT still has transfer_resolution=NULL (Stage 3 not yet run).
    r2 = detect_for_user(det_session, user_id=user.id)
    assert r2["pending_created"] == 0, (
        "Existing pending row in 'classified' state must NOT be reopened or duplicated"
    )

    rows = det_session.scalars(
        select(PendingClassification).where(PendingClassification.user_id == user.id)
    ).all()
    assert len(rows) == 1
    # Verify state was not reset.
    det_session.refresh(rows[0])
    assert rows[0].state == "classified"

    # outflow CT transfer_resolution still NULL (Stage 3's job to stamp it)
    det_session.refresh(outflow)
    assert outflow.transfer_resolution is None


# ── since= parameter filters by occurred_at ───────────────────────────────────


def test_since_filters_old_cts(det_session: Session) -> None:
    """CTs older than `since` are not processed."""
    user = _make_user(det_session)
    entity = _make_entity(det_session, user)
    wallet_a = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity, effective_from=date(2020, 1, 1))
    token = _make_token(det_session)

    _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        occurred_at=datetime(2022, 1, 1, tzinfo=UTC),
    )

    result = detect_for_user(
        det_session,
        user_id=user.id,
        since=datetime(2023, 1, 1, tzinfo=UTC),
    )
    assert result["auto_resolved"] == 0
    assert result["pending_created"] == 0


# ── Dedup: both wallet perspectives, one pending row (SQL COUNT) ───────────────
#
# The earlier dedup test only covered re-run dedup (one wallet, same outflow
# processed twice).  This test covers the real "both perspectives" scenario:
# wallet_a sees the OUTFLOW, wallet_b sees the INFLOW, detection runs once
# across both wallets, and the result must be exactly one pending row — proven
# with a raw SQL COUNT(*) on pending_classifications, not an ORM .all() call.


def test_dedup_both_perspectives_sql_count(det_session: Session) -> None:
    """Outflow from wallet_a + inflow at wallet_b → COUNT(*) = 1, not 2."""
    user = _make_user(det_session)
    entity_a = _make_entity(det_session, user, "CrossA")
    entity_b = _make_entity(det_session, user, "CrossB")
    wallet_a = _make_wallet(det_session, user)
    wallet_b = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity_a, effective_from=date(2023, 1, 1))
    _assign(det_session, wallet_b, entity_b, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    tx_hash = f"0x{uuid.uuid4().hex}"
    outflow = _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        tx_hash=tx_hash,
        event_seq=0,
    )
    _make_ct(
        det_session,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        tx_hash=tx_hash,
        event_seq=0,
    )

    result = detect_for_user(det_session, user_id=user.id)

    ltk = make_logical_transfer_key(outflow.chain, tx_hash, wallet_a.id, 0)

    # Raw SQL COUNT — not ORM, proves the DB constraint, not just the Python list.
    count = det_session.execute(
        text("SELECT count(*) FROM pending_classifications WHERE logical_transfer_key = :ltk"),
        {"ltk": ltk},
    ).scalar_one()

    assert result["pending_created"] == 1
    assert count == 1, (
        f"Expected exactly 1 pending_classifications row for ltk={ltk!r}, got {count}"
    )


# ── v_lot_gate gate check ─────────────────────────────────────────────────────
#
# Confirms that a cross-entity pending row surfaces as blocking=true in v_lot_gate.
# The view source (e) joins on: ct.wallet_id=pc.from_wallet_id, ct.tx_hash=pc.tx_hash,
# ct.event_seq=pc.transfer_index.  If any of those keys misalign, the row vanishes
# from the gate silently.  This is the cheapest proof that the join keys align.


def test_cross_entity_pending_appears_in_v_lot_gate(det_session: Session) -> None:
    """pending_classifications row for branch-2 appears in v_lot_gate as blocking."""
    user = _make_user(det_session)
    entity_a = _make_entity(det_session, user, "GateA")
    entity_b = _make_entity(det_session, user, "GateB")
    wallet_a = _make_wallet(det_session, user)
    wallet_b = _make_wallet(det_session, user)
    _assign(det_session, wallet_a, entity_a, effective_from=date(2023, 1, 1))
    _assign(det_session, wallet_b, entity_b, effective_from=date(2023, 1, 1))
    token = _make_token(det_session)

    tx_hash = f"0x{uuid.uuid4().hex}"
    outflow = _make_ct(
        det_session,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        tx_hash=tx_hash,
        event_seq=0,
    )
    _make_ct(
        det_session,
        wallet=wallet_b,
        token=token,
        classification="transfer-in",
        tx_hash=tx_hash,
        event_seq=0,
    )

    detect_for_user(det_session, user_id=user.id)

    # Query v_lot_gate for the outflow CT's ID.
    gate_rows = det_session.execute(
        text(
            "SELECT classified_tx_id, wallet_id, reason, blocking "
            "FROM v_lot_gate "
            "WHERE classified_tx_id = :ct_id"
        ),
        {"ct_id": str(outflow.id)},
    ).fetchall()

    assert len(gate_rows) >= 1, (
        f"outflow CT {outflow.id} not found in v_lot_gate — "
        "join keys (wallet_id/tx_hash/event_seq) may be misaligned"
    )

    blocking_rows = [r for r in gate_rows if r.blocking]
    assert len(blocking_rows) >= 1, (
        f"CT {outflow.id} appears in v_lot_gate but blocking=false — "
        "cross-entity pending must be blocking"
    )

    reasons = [r.reason for r in blocking_rows]
    assert any("needs_classification" in r for r in reasons), (
        f"Expected reason containing 'needs_classification', got {reasons!r}"
    )
