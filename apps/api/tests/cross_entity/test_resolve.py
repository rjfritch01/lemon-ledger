"""Integration tests for domain.cross_entity.resolve.

Risk-first ordering:
  GROUP A — validity rejection atomicity (InvalidChoice + no partial mutation)
  GROUP B — lazy application (tax_lots/lot_disposals row counts unchanged)
  Then: leg stamping, audit log shape, reclassify, dismiss, idempotency, rules hook.

All tests run against a real Testcontainers Postgres (same module-scoped container
as the rest of the integration suite, migrated to head c0d1e2f3a4b5).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from lemon_ledger.domain.cross_entity.resolve import (
    ALLOWED,
    InvalidChoice,
    InvalidState,
    UserResolvedGuard,
    dismiss,
    reclassify,
    resolve_classification,
)
from lemon_ledger.models.classification_audit import ClassificationAuditLog
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.models.entity import Entity
from lemon_ledger.models.enums import (
    ChosenClassification,
    PendingClassificationKind,
    PendingClassificationState,
    TransferResolution,
)
from lemon_ledger.models.lot import LotDisposal, TaxLot
from lemon_ledger.models.pending_classification import PendingClassification
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.models.user import User
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.models.wallet_entity_assignment import WalletEntityAssignment

# ── Engine (module-scoped, shares pg_container fixture from conftest) ──────────


@pytest.fixture(scope="module")
def res_engine(pg_container: PostgresContainer) -> Any:
    raw_url = pg_container.get_connection_url()
    if "+psycopg2" in raw_url:
        sync_url = raw_url.replace("+psycopg2", "+psycopg")
    else:
        sync_url = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(sync_url, future=True)


@pytest.fixture(scope="module")
def res_sessionmaker(res_engine: Any) -> sessionmaker[Session]:
    return sessionmaker(res_engine, expire_on_commit=False)


@pytest.fixture
def db(res_sessionmaker: sessionmaker[Session]) -> Generator[Session, None, None]:
    with res_sessionmaker() as session:
        with session.begin():
            session.begin_nested()
            yield session
            session.rollback()


# ── Seed helpers ──────────────────────────────────────────────────────────────


def _user(session: Session) -> User:
    u = User(clerk_user_id=f"res_{uuid.uuid4().hex[:8]}", preferences={})
    session.add(u)
    session.flush()
    return u


def _entity(session: Session, user: User, name: str = "E") -> Entity:
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


def _wallet(session: Session, user: User, *, chain: str = "lemonchain") -> Wallet:
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
    effective_from: date = date(2024, 1, 1),
) -> WalletEntityAssignment:
    a = WalletEntityAssignment(
        wallet_id=wallet.id,
        entity_id=entity.id,
        effective_from=effective_from,
        effective_to=None,
        classification="initial-assignment",
    )
    session.add(a)
    session.flush()
    return a


def _token(session: Session) -> TokenRegistry:
    t = TokenRegistry(
        chain="lemonchain",
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        symbol="LEMX",
        name="Lemon",
        decimals=18,
        tier=1,
        category="ecosystem-native",
    )
    session.add(t)
    session.flush()
    return t


def _ct(
    session: Session,
    *,
    wallet: Wallet,
    token: TokenRegistry,
    classification: str,
    tx_hash: str,
    event_seq: int = 0,
) -> ClassifiedTransaction:
    ct = ClassifiedTransaction(
        wallet_id=wallet.id,
        chain=wallet.chain,
        tx_hash=tx_hash,
        event_seq=event_seq,
        block_number=100,
        occurred_at=datetime(2024, 6, 1, tzinfo=UTC),
        classification=classification,
        token_id=token.id,
        contract_address=token.contract_address,
        amount=Decimal("10"),
        value_usd_at_event=Decimal("100"),
        needs_review=False,
        manual_override=False,
    )
    session.add(ct)
    session.flush()
    return ct


def _pending_cross_entity(
    session: Session,
    *,
    user: User,
    token: TokenRegistry,
    from_wallet: Wallet,
    from_entity: Entity,
    to_wallet: Wallet,
    to_entity: Entity,
    tx_hash: str,
) -> PendingClassification:
    ltk = f"{from_wallet.chain}:{tx_hash}:{from_wallet.id}:0"
    pc = PendingClassification(
        user_id=user.id,
        kind=PendingClassificationKind.CROSS_ENTITY.value,
        logical_transfer_key=ltk,
        chain="lemonchain",
        tx_hash=tx_hash,
        transfer_index=0,
        token_id=token.id,
        canonical_asset="LEMX",
        amount=Decimal("10"),
        from_wallet_id=from_wallet.id,
        from_entity_id=from_entity.id,
        to_wallet_id=to_wallet.id,
        to_entity_id=to_entity.id,
    )
    session.add(pc)
    session.flush()
    return pc


def _pending_external(
    session: Session,
    *,
    user: User,
    token: TokenRegistry,
    from_wallet: Wallet,
    from_entity: Entity,
    tx_hash: str,
) -> PendingClassification:
    ltk = f"{from_wallet.chain}:{tx_hash}:{from_wallet.id}:0"
    pc = PendingClassification(
        user_id=user.id,
        kind=PendingClassificationKind.EXTERNAL_OUTFLOW.value,
        logical_transfer_key=ltk,
        chain="lemonchain",
        tx_hash=tx_hash,
        transfer_index=0,
        token_id=token.id,
        canonical_asset="LEMX",
        amount=Decimal("10"),
        from_wallet_id=from_wallet.id,
        from_entity_id=from_entity.id,
        to_wallet_id=None,
        to_entity_id=None,
        to_address="0x" + "d" * 40,
    )
    session.add(pc)
    session.flush()
    return pc


# ── Cross-entity fixture with both CT legs ────────────────────────────────────


def _cross_entity_fixture(
    session: Session,
) -> tuple[PendingClassification, ClassifiedTransaction, ClassifiedTransaction]:
    """Return (pc, outflow_ct, inflow_ct) for a cross-entity scenario."""
    u = _user(session)
    e1 = _entity(session, u, "Entity-A")
    e2 = _entity(session, u, "Entity-B")
    w1 = _wallet(session, u)
    w2 = _wallet(session, u)
    _assign(session, w1, e1)
    _assign(session, w2, e2)
    tok = _token(session)
    tx = f"0x{uuid.uuid4().hex}"

    outflow = _ct(
        session, wallet=w1, token=tok, classification="transfer-out", tx_hash=tx, event_seq=0
    )
    inflow = _ct(
        session, wallet=w2, token=tok, classification="transfer-in", tx_hash=tx, event_seq=1
    )
    pc = _pending_cross_entity(
        session,
        user=u,
        token=tok,
        from_wallet=w1,
        from_entity=e1,
        to_wallet=w2,
        to_entity=e2,
        tx_hash=tx,
    )
    return pc, outflow, inflow


def _external_fixture(
    session: Session,
) -> tuple[PendingClassification, ClassifiedTransaction]:
    """Return (pc, outflow_ct) for an external-outflow scenario."""
    u = _user(session)
    e = _entity(session, u)
    w = _wallet(session, u)
    _assign(session, w, e)
    tok = _token(session)
    tx = f"0x{uuid.uuid4().hex}"

    outflow = _ct(
        session, wallet=w, token=tok, classification="transfer-out", tx_hash=tx, event_seq=0
    )
    pc = _pending_external(session, user=u, token=tok, from_wallet=w, from_entity=e, tx_hash=tx)
    return pc, outflow


# ══════════════════════════════════════════════════════════════════════════════
# GROUP A — Validity rejection: must raise BEFORE any state change or audit write
# ══════════════════════════════════════════════════════════════════════════════


def test_invalid_choice_external_outflow_capital_contribution(db: Session) -> None:
    """external-outflow + 'capital-contribution' raises InvalidChoice."""
    pc, _ = _external_fixture(db)
    with pytest.raises(InvalidChoice):
        resolve_classification(db, pc.id, "capital-contribution", actor="user-1")


def test_invalid_choice_cross_entity_payment(db: Session) -> None:
    """cross-entity + 'payment' raises InvalidChoice."""
    pc, _, _ = _cross_entity_fixture(db)
    with pytest.raises(InvalidChoice):
        resolve_classification(db, pc.id, "payment", actor="user-1")


def test_invalid_choice_no_state_change(db: Session) -> None:
    """Rejected call must not mutate pending row state."""
    pc, _ = _external_fixture(db)
    original_state = pc.state
    original_choice = pc.chosen_classification

    with pytest.raises(InvalidChoice):
        resolve_classification(db, pc.id, "capital-contribution", actor="user-1")

    db.refresh(pc)
    assert pc.state == original_state
    assert pc.chosen_classification == original_choice


def test_invalid_choice_no_audit_row(db: Session) -> None:
    """Rejected call must not write any ClassificationAuditLog row."""
    pc, _ = _external_fixture(db)
    audit_before = db.scalar(
        select(func.count())
        .select_from(ClassificationAuditLog)
        .where(ClassificationAuditLog.pending_id == pc.id)
    )

    with pytest.raises(InvalidChoice):
        resolve_classification(db, pc.id, "capital-contribution", actor="user-1")

    audit_after = db.scalar(
        select(func.count())
        .select_from(ClassificationAuditLog)
        .where(ClassificationAuditLog.pending_id == pc.id)
    )
    assert audit_before == audit_after  # zero before, still zero after


# ══════════════════════════════════════════════════════════════════════════════
# GROUP B — Lazy application: tax_lots and lot_disposals untouched after resolve
# ══════════════════════════════════════════════════════════════════════════════


def test_resolve_does_not_touch_tax_lots(db: Session) -> None:
    """After resolve, tax_lots row count must be unchanged."""
    pc, _, _ = _cross_entity_fixture(db)

    lots_before = db.scalar(select(func.count()).select_from(TaxLot))
    resolve_classification(db, pc.id, "capital-contribution", actor="user-1")
    lots_after = db.scalar(select(func.count()).select_from(TaxLot))

    assert lots_before == lots_after


def test_resolve_does_not_touch_lot_disposals(db: Session) -> None:
    """After resolve, lot_disposals row count must be unchanged."""
    pc, _, _ = _cross_entity_fixture(db)

    disposals_before = db.scalar(select(func.count()).select_from(LotDisposal))
    resolve_classification(db, pc.id, "capital-contribution", actor="user-1")
    disposals_after = db.scalar(select(func.count()).select_from(LotDisposal))

    assert disposals_before == disposals_after


# ══════════════════════════════════════════════════════════════════════════════
# Worked example — cross-entity capital-contribution
# ══════════════════════════════════════════════════════════════════════════════


def test_worked_example_capital_contribution(db: Session) -> None:
    """Resolve cross-entity as capital-contribution: both legs stamped, inflow links to outflow,
    tax_lots / lot_disposals untouched."""
    pc, outflow, inflow = _cross_entity_fixture(db)

    lots_before = db.scalar(select(func.count()).select_from(TaxLot))
    disposals_before = db.scalar(select(func.count()).select_from(LotDisposal))

    resolve_classification(db, pc.id, "capital-contribution", actor="user-1")

    db.refresh(outflow)
    db.refresh(inflow)
    db.refresh(pc)

    # Both legs stamped with relocate-contribution.
    assert outflow.transfer_resolution == TransferResolution.RELOCATE_CONTRIBUTION
    assert inflow.transfer_resolution == TransferResolution.RELOCATE_CONTRIBUTION
    # Inflow links to outflow (same as Stage 2 Branch 1 shape).
    assert inflow.relocation_source_event_id == outflow.id
    # Pending row transitions to 'classified'.
    assert pc.state == PendingClassificationState.CLASSIFIED.value
    assert pc.chosen_classification == ChosenClassification.CAPITAL_CONTRIBUTION.value
    # Tax lot tables untouched.
    assert db.scalar(select(func.count()).select_from(TaxLot)) == lots_before
    assert db.scalar(select(func.count()).select_from(LotDisposal)) == disposals_before


# ══════════════════════════════════════════════════════════════════════════════
# Leg stamping — all 8 choice × kind combinations
# ══════════════════════════════════════════════════════════════════════════════


def test_stamp_reassignment_both_legs(db: Session) -> None:
    pc, outflow, inflow = _cross_entity_fixture(db)
    resolve_classification(db, pc.id, "reassignment", actor="u")
    db.refresh(outflow)
    db.refresh(inflow)
    assert outflow.transfer_resolution == TransferResolution.RELOCATE_REASSIGNMENT
    assert inflow.transfer_resolution == TransferResolution.RELOCATE_REASSIGNMENT
    assert inflow.relocation_source_event_id == outflow.id


def test_stamp_cross_entity_gift_both_legs(db: Session) -> None:
    pc, outflow, inflow = _cross_entity_fixture(db)
    resolve_classification(db, pc.id, "gift", actor="u")
    db.refresh(outflow)
    db.refresh(inflow)
    assert outflow.transfer_resolution == TransferResolution.RELOCATE_GIFT
    assert inflow.transfer_resolution == TransferResolution.RELOCATE_GIFT
    assert inflow.relocation_source_event_id == outflow.id


def test_stamp_cross_entity_sale_outflow_only(db: Session) -> None:
    """cross-entity sale → disposal-related-party on outflow; inflow stays unresolved."""
    pc, outflow, inflow = _cross_entity_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="u")
    db.refresh(outflow)
    db.refresh(inflow)
    assert outflow.transfer_resolution == TransferResolution.DISPOSAL_RELATED_PARTY
    assert inflow.transfer_resolution is None
    assert inflow.relocation_source_event_id is None


def test_stamp_loan_outflow_only(db: Session) -> None:
    pc, outflow, inflow = _cross_entity_fixture(db)
    resolve_classification(db, pc.id, "loan", actor="u")
    db.refresh(outflow)
    db.refresh(inflow)
    assert outflow.transfer_resolution == TransferResolution.NO_OP_LOAN
    assert inflow.transfer_resolution is None


def test_stamp_external_sale(db: Session) -> None:
    pc, outflow = _external_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="u")
    db.refresh(outflow)
    assert outflow.transfer_resolution == TransferResolution.DISPOSAL


def test_stamp_external_payment(db: Session) -> None:
    pc, outflow = _external_fixture(db)
    resolve_classification(db, pc.id, "payment", actor="u")
    db.refresh(outflow)
    assert outflow.transfer_resolution == TransferResolution.DISPOSAL


def test_stamp_external_gift(db: Session) -> None:
    pc, outflow = _external_fixture(db)
    resolve_classification(db, pc.id, "gift", actor="u")
    db.refresh(outflow)
    assert outflow.transfer_resolution == TransferResolution.GIFT_OUT


# ══════════════════════════════════════════════════════════════════════════════
# Audit log shape
# ══════════════════════════════════════════════════════════════════════════════


def test_audit_written_on_resolve(db: Session) -> None:
    pc, _ = _external_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="alice", note="confirmed by user")

    entries = db.scalars(
        select(ClassificationAuditLog).where(ClassificationAuditLog.pending_id == pc.id)
    ).all()
    assert len(entries) == 1
    e = entries[0]
    assert e.actor == "alice"
    assert e.action == "resolve:sale"
    assert e.note == "confirmed by user"
    assert e.before_state is not None
    assert e.after_state is not None
    assert e.before_state["state"] == "needs_classification"
    assert e.after_state["state"] == "classified"
    assert e.after_state["chosen_classification"] == "sale"
    assert e.rule_id is None


def test_audit_written_on_reclassify(db: Session) -> None:
    pc, _ = _external_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="alice")
    reclassify(db, pc.id, "gift", actor="alice", note="oops")

    entries = db.scalars(
        select(ClassificationAuditLog)
        .where(ClassificationAuditLog.pending_id == pc.id)
        .order_by(ClassificationAuditLog.created_at)
    ).all()
    assert len(entries) == 2
    assert entries[0].action == "resolve:sale"
    assert entries[1].action == "reclassify:gift"
    assert entries[1].before_state is not None
    assert entries[1].after_state is not None
    assert entries[1].before_state["chosen_classification"] == "sale"
    assert entries[1].after_state["chosen_classification"] == "gift"


def test_audit_written_on_dismiss(db: Session) -> None:
    pc, _ = _external_fixture(db)
    dismiss(db, pc.id, "not a taxable event", actor="alice")

    entries = db.scalars(
        select(ClassificationAuditLog).where(ClassificationAuditLog.pending_id == pc.id)
    ).all()
    assert len(entries) == 1
    assert entries[0].action == "dismiss"
    assert entries[0].after_state is not None
    assert entries[0].after_state["state"] == "dismissed"


# ══════════════════════════════════════════════════════════════════════════════
# State transitions
# ══════════════════════════════════════════════════════════════════════════════


def test_state_transitions_to_classified(db: Session) -> None:
    pc, _ = _external_fixture(db)
    assert pc.state == "needs_classification"
    resolve_classification(db, pc.id, "sale", actor="u")
    db.refresh(pc)
    assert pc.state == "classified"
    assert pc.resolved_by == "user"
    assert pc.resolved_at is not None


def test_reclassify_from_applied_resets_to_classified(db: Session) -> None:
    """If state was 'applied', reclassify must reset it to 'classified'."""
    pc, _ = _external_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="u")
    # Simulate Stage 4 marking as applied.
    db.execute(
        text("UPDATE pending_classifications SET state='applied' WHERE id=:id"),
        {"id": pc.id},
    )
    db.flush()
    db.expire_all()  # force ORM to re-read from DB; raw SQL bypassed identity map

    reclassify(db, pc.id, "gift", actor="u")
    db.refresh(pc)
    assert pc.state == "classified"
    assert pc.chosen_classification == "gift"


def test_reclassify_automation_cannot_reopen_user_resolved(db: Session) -> None:
    """resolved_by='user' row must not be reopened by automation actor='rule'."""
    pc, _ = _external_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="u")
    db.refresh(pc)
    assert pc.resolved_by == "user"

    with pytest.raises(UserResolvedGuard):
        reclassify(db, pc.id, "gift", actor="rule")


def test_reclassify_overwrites_prior_stamp(db: Session) -> None:
    """Reclassify must overwrite prior transfer_resolution on outflow CT."""
    pc, outflow = _external_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="u")
    db.refresh(outflow)
    assert outflow.transfer_resolution == TransferResolution.DISPOSAL

    reclassify(db, pc.id, "gift", actor="u")
    db.refresh(outflow)
    assert outflow.transfer_resolution == TransferResolution.GIFT_OUT


# ══════════════════════════════════════════════════════════════════════════════
# Dismiss + dismiss-with-redirect
# ══════════════════════════════════════════════════════════════════════════════


def test_dismiss_sets_state(db: Session) -> None:
    pc, _ = _external_fixture(db)
    result = dismiss(db, pc.id, "definitely external", actor="u")
    db.refresh(pc)
    assert pc.state == "dismissed"
    assert pc.dismiss_reason == "definitely external"
    assert result == {"dismissed": True}


def test_dismiss_redirect_sets_reason_and_returns_signal(db: Session) -> None:
    pc, _, _ = _cross_entity_fixture(db)
    result = dismiss(db, pc.id, "actually my wallet", actor="u", redirect=True)
    db.refresh(pc)
    assert pc.state == "dismissed"
    assert pc.dismiss_reason == "reclassified-internal"
    assert result == {"dismissed": True, "redirect": "add_wallet"}


def test_dismiss_does_not_stamp_legs(db: Session) -> None:
    pc, outflow = _external_fixture(db)
    dismiss(db, pc.id, "not taxable", actor="u")
    db.refresh(outflow)
    assert outflow.transfer_resolution is None


# ══════════════════════════════════════════════════════════════════════════════
# Idempotency
# ══════════════════════════════════════════════════════════════════════════════


def test_idempotent_same_choice_no_duplicate_audit(db: Session) -> None:
    """Resolving with the same choice when already classified → no duplicate audit row."""
    pc, _ = _external_fixture(db)
    resolve_classification(db, pc.id, "sale", actor="u")
    resolve_classification(db, pc.id, "sale", actor="u")

    count = db.scalar(
        select(func.count())
        .select_from(ClassificationAuditLog)
        .where(ClassificationAuditLog.pending_id == pc.id)
    )
    assert count == 1


# ══════════════════════════════════════════════════════════════════════════════
# Rules hook
# ══════════════════════════════════════════════════════════════════════════════


def test_rules_hook_resolved_by_rule(db: Session) -> None:
    """resolve with rule_id sets resolved_by='rule' and audits with rule_id."""
    pc, _ = _external_fixture(db)
    rule_id = uuid.uuid4()
    resolve_classification(db, pc.id, "sale", actor="rule-engine", rule_id=rule_id)

    db.refresh(pc)
    assert pc.resolved_by == "rule"
    assert pc.resolved_rule_id == rule_id

    entry = db.scalar(
        select(ClassificationAuditLog).where(ClassificationAuditLog.pending_id == pc.id)
    )
    assert entry is not None
    assert entry.rule_id == rule_id
    assert entry.actor == "rule-engine"


# ══════════════════════════════════════════════════════════════════════════════
# ALLOWED set contract
# ══════════════════════════════════════════════════════════════════════════════


def test_allowed_sets_match_spec() -> None:
    """Verify ALLOWED matches the spec: no drift between code and docs."""
    assert ALLOWED["cross-entity"] == frozenset(
        {"capital-contribution", "sale", "gift", "loan", "reassignment"}
    )
    assert ALLOWED["external-outflow"] == frozenset({"sale", "gift", "payment"})


def test_invalid_state_dismissed_cannot_resolve(db: Session) -> None:
    """dismissed row cannot be re-resolved."""
    pc, _ = _external_fixture(db)
    dismiss(db, pc.id, "done", actor="u")
    with pytest.raises(InvalidState):
        resolve_classification(db, pc.id, "sale", actor="u")
