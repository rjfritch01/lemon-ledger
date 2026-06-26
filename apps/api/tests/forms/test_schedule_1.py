"""Integration tests: Schedule 1 Line 8z — reward income.

DB required: tests seed tax_lots and read via fetch_reward_income.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from forms.conftest import (
    seed_assign,
    seed_ct,
    seed_entity,
    seed_lot,
    seed_token,
    seed_user,
    seed_wallet,
)
from lemon_ledger.domain.forms.read_model import fetch_reward_income
from lemon_ledger.domain.forms.schedule_1 import build_schedule_1


def test_line_8z_sums_reward_lots_in_year(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    ct1 = seed_ct(
        frm_db, wallet=wallet, token=token, classification="reward", amount="50", value_usd="60"
    )
    ct2 = seed_ct(
        frm_db, wallet=wallet, token=token, classification="reward", amount="30", value_usd="40"
    )

    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct1,
        quantity="50",
        cost_basis_usd="60",
        acquired_at=datetime(2025, 3, 1, tzinfo=UTC),
        acquisition_type="reward",
    )
    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct2,
        quantity="30",
        cost_basis_usd="40",
        acquired_at=datetime(2025, 8, 1, tzinfo=UTC),
        acquisition_type="reward",
    )

    income = fetch_reward_income(frm_db, entity.id, 2025)
    assert income.total_income_usd == Decimal("100")


def test_line_8z_excludes_mint_lots(frm_db: Session) -> None:
    """SC redemption acquire leg has acquisition_type='mint'; excluded from Line 8z."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    ct_reward = seed_ct(frm_db, wallet=wallet, token=token, classification="reward", amount="10")
    ct_mint = seed_ct(frm_db, wallet=wallet, token=token, classification="mint", amount="10")

    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct_reward,
        quantity="10",
        cost_basis_usd="50",
        acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
        acquisition_type="reward",
    )
    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct_mint,
        quantity="10",
        cost_basis_usd="999",
        acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
        acquisition_type="mint",
    )

    income = fetch_reward_income(frm_db, entity.id, 2025)
    assert income.total_income_usd == Decimal("50")


def test_line_8z_excludes_buy_lots(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    ct = seed_ct(frm_db, wallet=wallet, token=token, classification="transfer-in", amount="5")
    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct,
        quantity="5",
        cost_basis_usd="300",
        acquired_at=datetime(2025, 6, 1, tzinfo=UTC),
        acquisition_type="buy",
    )

    income = fetch_reward_income(frm_db, entity.id, 2025)
    assert income.total_income_usd == Decimal("0")


def test_line_8z_excludes_wrong_year(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    ct = seed_ct(frm_db, wallet=wallet, token=token, classification="reward", amount="10")
    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct,
        quantity="10",
        cost_basis_usd="200",
        acquired_at=datetime(2024, 6, 1, tzinfo=UTC),  # 2024, asking for 2025
        acquisition_type="reward",
    )

    income = fetch_reward_income(frm_db, entity.id, 2025)
    assert income.total_income_usd == Decimal("0")


def test_line_8z_excludes_other_entity(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity_a = seed_entity(frm_db, user, "A")
    entity_b = seed_entity(frm_db, user, "B")
    wallet_a = seed_wallet(frm_db, user)
    wallet_b = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet_a, entity_a)
    seed_assign(frm_db, wallet_b, entity_b)
    token = seed_token(frm_db)

    ct_a = seed_ct(frm_db, wallet=wallet_a, token=token, classification="reward", amount="10")
    seed_lot(
        frm_db,
        wallet=wallet_a,
        entity=entity_a,
        token=token,
        source_ct=ct_a,
        quantity="10",
        cost_basis_usd="100",
        acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
        acquisition_type="reward",
    )

    income = fetch_reward_income(frm_db, entity_b.id, 2025)
    assert income.total_income_usd == Decimal("0")


def test_build_schedule_1_result(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    ct = seed_ct(frm_db, wallet=wallet, token=token, classification="reward", amount="5")
    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct,
        quantity="5",
        cost_basis_usd="75",
        acquired_at=datetime(2025, 4, 1, tzinfo=UTC),
        acquisition_type="reward",
    )

    income = fetch_reward_income(frm_db, entity.id, 2025)
    result = build_schedule_1(income)
    assert result.line_8z_income == Decimal("75")
    assert result.tax_year == 2025
    assert result.entity_id == entity.id
    assert result.is_draft is False
