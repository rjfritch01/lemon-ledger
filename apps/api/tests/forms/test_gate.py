"""Gate guard tests: exit 2 / draft / clear behaviour.

Tests check_gate() against v_lot_gate using real DB with savepoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from forms.conftest import (
    seed_assign,
    seed_entity,
    seed_token,
    seed_user,
    seed_wallet,
)
from lemon_ledger.domain.forms.gate import check_gate


def test_gate_clear_no_blockers(frm_db: Session) -> None:
    """Entity with no CTs → gate is clear, is_held=False."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)

    result = check_gate(frm_db, entity.id, 2025)
    assert result.is_held is False
    assert result.blocker_rows == []
    assert wallet.id in result.entity_wallet_ids


def test_gate_no_wallets_is_clear(frm_db: Session) -> None:
    """Entity with no wallets at all → gate returns clear (nothing to block)."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)

    result = check_gate(frm_db, entity.id, 2025)
    assert result.is_held is False
    assert result.entity_wallet_ids == []


def test_gate_held_surfaces_pending_classification(frm_db: Session) -> None:
    """A pending-classification CT (needs_classification=true) blocks the gate.

    Seeds a CT with classification='pending' directly so v_lot_gate source (a)
    fires.  Source (a) fires on `classification = 'pending' OR needs_review = true`.
    """
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    from lemon_ledger.models.classified import ClassifiedTransaction

    ct = ClassifiedTransaction(
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash=f"0x{uuid.uuid4().hex}",
        event_seq=0,
        block_number=100,
        occurred_at=datetime(2025, 3, 1, tzinfo=UTC),
        classification="pending",
        token_id=token.id,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        amount=Decimal("10"),
        value_usd_at_event=Decimal("100"),
    )
    frm_db.add(ct)
    frm_db.flush()

    result = check_gate(frm_db, entity.id, 2025)
    assert result.is_held is True
    assert len(result.blocker_rows) >= 1
    assert any(r["reason"] == "pending" for r in result.blocker_rows)


def test_gate_held_blocks_prior_year(frm_db: Session) -> None:
    """Unresolved event in 2024 blocks generation of 2025 forms.

    Gate query uses EXTRACT(YEAR FROM occurred_at) <= :year (all years <= target).
    """
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    from lemon_ledger.models.classified import ClassifiedTransaction

    ct = ClassifiedTransaction(
        wallet_id=wallet.id,
        chain="lemonchain",
        tx_hash=f"0x{uuid.uuid4().hex}",
        event_seq=0,
        block_number=100,
        occurred_at=datetime(2024, 6, 15, tzinfo=UTC),  # 2024 event, blocking 2025
        classification="pending",
        token_id=token.id,
        contract_address=f"0x{uuid.uuid4().hex[:40]}",
        amount=Decimal("5"),
        value_usd_at_event=Decimal("50"),
    )
    frm_db.add(ct)
    frm_db.flush()

    result_2025 = check_gate(frm_db, entity.id, 2025)
    assert result_2025.is_held is True

    # Does NOT block 2023
    result_2023 = check_gate(frm_db, entity.id, 2023)
    assert result_2023.is_held is False


def test_gate_returns_wallet_ids(frm_db: Session) -> None:
    """check_gate always returns entity wallet IDs regardless of gate state."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    w1 = seed_wallet(frm_db, user)
    w2 = seed_wallet(frm_db, user)
    seed_assign(frm_db, w1, entity)
    seed_assign(frm_db, w2, entity)

    result = check_gate(frm_db, entity.id, 2025)
    assert w1.id in result.entity_wallet_ids
    assert w2.id in result.entity_wallet_ids
