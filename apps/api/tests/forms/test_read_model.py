"""Integration tests: DisposalRow fetch from DB.

Verifies:
- Symbol comes from acquired_token_id FK (CORRECTION 2)
- entity_id filter (CORRECTION 3)
- basis_consumed_usd read directly (CORRECTION 1)
- Year filter
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from forms.conftest import (
    seed_assign,
    seed_ct,
    seed_disposal,
    seed_entity,
    seed_lot,
    seed_token,
    seed_user,
    seed_wallet,
)
from lemon_ledger.domain.forms.read_model import fetch_disposal_rows


def test_fetch_disposal_rows_returns_correct_disposal(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db, "LEMX")

    acq_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="10",
        value_usd="100",
    )
    lot = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=acq_ct,
        quantity="10",
        cost_basis_usd="100",
        acquired_at=datetime(2024, 1, 1, tzinfo=UTC),
    )

    dis_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        value_usd="150",
        occurred_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=dis_ct,
        quantity_consumed="10",
        proceeds_usd="150",
        basis_consumed_usd="100",
        gain_loss_usd="50",
        disposed_at=datetime(2025, 6, 1, tzinfo=UTC),
    )

    rows = fetch_disposal_rows(frm_db, entity.id, 2025)
    assert len(rows) == 1
    row = rows[0]
    assert row.proceeds_usd == Decimal("150")
    assert row.cost_basis_usd == Decimal("100")  # CORRECTION 1: read directly
    assert "LEMX" in row.description  # CORRECTION 2: via acquired_token_id
    assert row.gain_loss_net == Decimal("50")


def test_fetch_disposal_rows_symbol_from_acquired_token(frm_db: Session) -> None:
    """Description uses token symbol from acquired_token_id (CORRECTION 2)."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db, "WLEMX")

    acq_ct = seed_ct(frm_db, wallet=wallet, token=token, classification="transfer-in", amount="5")
    lot = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=acq_ct,
        quantity="5",
        cost_basis_usd="50",
    )

    dis_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="5",
        occurred_at=datetime(2025, 3, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=dis_ct,
        quantity_consumed="5",
        proceeds_usd="80",
        basis_consumed_usd="50",
        gain_loss_usd="30",
        disposed_at=datetime(2025, 3, 1, tzinfo=UTC),
    )

    rows = fetch_disposal_rows(frm_db, entity.id, 2025)
    assert any("WLEMX" in r.description for r in rows)


def test_fetch_disposal_rows_year_filter(frm_db: Session) -> None:
    """Disposals from different years are isolated by year filter."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    acq_ct = seed_ct(frm_db, wallet=wallet, token=token, classification="transfer-in", amount="20")
    lot = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=acq_ct,
        quantity="20",
        cost_basis_usd="200",
    )

    # 2024 disposal
    dis_2024 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        occurred_at=datetime(2024, 12, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=dis_2024,
        quantity_consumed="10",
        proceeds_usd="100",
        basis_consumed_usd="100",
        gain_loss_usd="0",
        disposed_at=datetime(2024, 12, 1, tzinfo=UTC),
    )

    # 2025 disposal
    dis_2025 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        occurred_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=dis_2025,
        quantity_consumed="10",
        proceeds_usd="120",
        basis_consumed_usd="100",
        gain_loss_usd="20",
        disposed_at=datetime(2025, 6, 1, tzinfo=UTC),
    )

    rows_2025 = fetch_disposal_rows(frm_db, entity.id, 2025)
    assert len(rows_2025) == 1
    assert rows_2025[0].proceeds_usd == Decimal("120")

    rows_2024 = fetch_disposal_rows(frm_db, entity.id, 2024)
    assert len(rows_2024) == 1
    assert rows_2024[0].proceeds_usd == Decimal("100")


def test_fetch_disposal_rows_entity_filter(frm_db: Session) -> None:
    """CORRECTION 3: filter via tax_lots.entity_id — only returns entity's disposals."""
    user = seed_user(frm_db)
    entity_a = seed_entity(frm_db, user, "A")
    entity_b = seed_entity(frm_db, user, "B")
    wallet_a = seed_wallet(frm_db, user)
    wallet_b = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet_a, entity_a)
    seed_assign(frm_db, wallet_b, entity_b)
    token = seed_token(frm_db)

    acq_a = seed_ct(frm_db, wallet=wallet_a, token=token, classification="transfer-in", amount="10")
    lot_a = seed_lot(
        frm_db,
        wallet=wallet_a,
        entity=entity_a,
        token=token,
        source_ct=acq_a,
        quantity="10",
        cost_basis_usd="100",
    )

    dis_a = seed_ct(
        frm_db,
        wallet=wallet_a,
        token=token,
        classification="transfer-out",
        amount="10",
        occurred_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot_a,
        disposal_ct=dis_a,
        quantity_consumed="10",
        proceeds_usd="120",
        basis_consumed_usd="100",
        gain_loss_usd="20",
        disposed_at=datetime(2025, 6, 1, tzinfo=UTC),
    )

    # Entity B has no disposals
    rows_b = fetch_disposal_rows(frm_db, entity_b.id, 2025)
    assert rows_b == []

    rows_a = fetch_disposal_rows(frm_db, entity_a.id, 2025)
    assert len(rows_a) == 1


def test_fetch_disposal_rows_empty_when_none(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    rows = fetch_disposal_rows(frm_db, entity.id, 2025)
    assert rows == []
