"""S1–S8 synthetic-fixture reconciliation harness.

Each test seeds the database to match its scenario, runs the forms pipeline,
and asserts against LITERAL CONSTANTS that were hand-computed and verified in
the Phase 1 review.  The expected values here are NEVER derived from engine
output — that is the anti-circularity invariant.

Scenarios:
  S1 — Simple buy then full sale, short-term
  S2 — Partial sale FIFO two lots (long + short)
  S3 — Long-term vs short-term split, same disposal date
  S4 — Reward income then sale (no double-count)
  S5 — Cross-entity cap-contribution (entity A = 0 rows, entity B = 1 long row)
  S6 — Related-party §267 loss disallowed (col(h) = $0)
  S7 — Gift-out to third party (no disposal row written)
  S8 — Gate-held pending CT (gate must report is_held=True)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

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
from lemon_ledger.domain.forms.form_8949 import build_8949
from lemon_ledger.domain.forms.gate import check_gate
from lemon_ledger.domain.forms.read_model import fetch_disposal_rows, fetch_reward_income
from lemon_ledger.domain.forms.reconcile import (
    BUILTIN_FIXTURES,
    S1,
    S2,
    S3,
    S4,
    S5_ENTITY_A,
    S5_ENTITY_B,
    S6,
    S7,
    S8,
    run_reconcile,
)
from lemon_ledger.domain.forms.schedule_1 import build_schedule_1
from lemon_ledger.domain.forms.schedule_d import build_schedule_d

TAX_YEAR = 2025

pytestmark = pytest.mark.usefixtures("frm_db")


# ── S1 ────────────────────────────────────────────────────────────────────────


def test_s1_simple_short_buy_sell(frm_db: Any) -> None:
    """Buy 100 @ $2 on 2025-01-01; sell all @ $5 on 2025-09-01 → short-term gain $300."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    buy_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="100",
        value_usd="200",
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    sell_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="100",
        value_usd="500",
        occurred_at=datetime(2025, 9, 1, tzinfo=UTC),
    )
    lot = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_ct,
        quantity="100",
        cost_basis_usd="200",
        acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=sell_ct,
        quantity_consumed="100",
        proceeds_usd="500",
        basis_consumed_usd="200",
        gain_loss_usd="300",
        disposed_at=datetime(2025, 9, 1, tzinfo=UTC),
        holding_period="short",
        covered_status="no-1099-da",
    )

    rows = fetch_disposal_rows(frm_db, entity.id, TAX_YEAR)
    reward = fetch_reward_income(frm_db, entity.id, TAX_YEAR)
    form = build_8949(rows, entity.id, TAX_YEAR)
    sched_d = build_schedule_d(form)
    sched_1 = build_schedule_1(reward)

    assert len(rows) == 1
    assert rows[0].holding_period == "short"
    assert rows[0].proceeds_usd == Decimal("500")
    assert rows[0].cost_basis_usd == Decimal("200")
    assert rows[0].adjustment_code is None
    assert rows[0].gain_loss_net == Decimal("300")

    assert len(form.boxes["C"].rows) == 1
    assert form.boxes["C"].total_proceeds == Decimal("500")
    assert form.boxes["C"].total_basis == Decimal("200")
    assert form.boxes["C"].total_gain_loss_net == Decimal("300")

    assert sched_d.short_term_net == Decimal("300")
    assert sched_d.long_term_net == Decimal("0")
    assert sched_d.total_net == Decimal("300")
    assert sched_1.line_8z_income == Decimal("0")

    result = run_reconcile(frm_db, entity.id, TAX_YEAR, S1)
    assert result.passed, result.summary_lines()


# ── S2 ────────────────────────────────────────────────────────────────────────


def test_s2_partial_sale_fifo_two_lots(frm_db: Any) -> None:
    """FIFO 150 out of 200.  Lot A (2024) → LONG $300; Lot B (2025) → SHORT $50."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    buy_a = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="100",
        value_usd="200",
        occurred_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    buy_b = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="100",
        value_usd="400",
        occurred_at=datetime(2025, 3, 1, tzinfo=UTC),
    )
    sell_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="150",
        value_usd="750",
        occurred_at=datetime(2025, 7, 1, tzinfo=UTC),
    )
    lot_a = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_a,
        quantity="100",
        cost_basis_usd="200",
        acquired_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    lot_b = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_b,
        quantity="100",
        cost_basis_usd="400",
        acquired_at=datetime(2025, 3, 1, tzinfo=UTC),
    )
    # Lot A: 100 units LONG — proceeds = 100/150*750 = 500 (pro-rata first, exact here)
    seed_disposal(
        frm_db,
        lot=lot_a,
        disposal_ct=sell_ct,
        quantity_consumed="100",
        proceeds_usd="500",
        basis_consumed_usd="200",
        gain_loss_usd="300",
        disposed_at=datetime(2025, 7, 1, tzinfo=UTC),
        holding_period="long",
        covered_status="no-1099-da",
    )
    # Lot B: 50 units SHORT — proceeds = 750 - 500 = 250 (residual sweep)
    seed_disposal(
        frm_db,
        lot=lot_b,
        disposal_ct=sell_ct,
        quantity_consumed="50",
        proceeds_usd="250",
        basis_consumed_usd="200",
        gain_loss_usd="50",
        disposed_at=datetime(2025, 7, 1, tzinfo=UTC),
        holding_period="short",
        covered_status="no-1099-da",
    )

    rows = fetch_disposal_rows(frm_db, entity.id, TAX_YEAR)
    reward = fetch_reward_income(frm_db, entity.id, TAX_YEAR)
    form = build_8949(rows, entity.id, TAX_YEAR)
    sched_d = build_schedule_d(form)
    sched_1 = build_schedule_1(reward)

    assert len(rows) == 2

    box_f = form.boxes["F"]
    assert len(box_f.rows) == 1
    assert box_f.total_proceeds == Decimal("500")
    assert box_f.total_basis == Decimal("200")
    assert box_f.total_gain_loss_net == Decimal("300")

    box_c = form.boxes["C"]
    assert len(box_c.rows) == 1
    assert box_c.total_proceeds == Decimal("250")
    assert box_c.total_basis == Decimal("200")
    assert box_c.total_gain_loss_net == Decimal("50")

    assert sched_d.short_term_net == Decimal("50")
    assert sched_d.long_term_net == Decimal("300")
    assert sched_d.total_net == Decimal("350")
    assert sched_1.line_8z_income == Decimal("0")

    result = run_reconcile(frm_db, entity.id, TAX_YEAR, S2)
    assert result.passed, result.summary_lines()


# ── S3 ────────────────────────────────────────────────────────────────────────


def test_s3_long_short_split_same_date(frm_db: Any) -> None:
    """Two lots disposed 2025-01-15.  Lot A (2023) → LONG $250; Lot B (2024) → SHORT $100."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    buy_a = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="50",
        value_usd="150",
        occurred_at=datetime(2023, 6, 1, tzinfo=UTC),
    )
    buy_b = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="50",
        value_usd="300",
        occurred_at=datetime(2024, 9, 1, tzinfo=UTC),
    )
    sell_a = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="50",
        value_usd="400",
        occurred_at=datetime(2025, 1, 15, tzinfo=UTC),
    )
    sell_b = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="50",
        value_usd="400",
        occurred_at=datetime(2025, 1, 15, tzinfo=UTC),
    )
    lot_a = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_a,
        quantity="50",
        cost_basis_usd="150",
        acquired_at=datetime(2023, 6, 1, tzinfo=UTC),
    )
    lot_b = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_b,
        quantity="50",
        cost_basis_usd="300",
        acquired_at=datetime(2024, 9, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot_a,
        disposal_ct=sell_a,
        quantity_consumed="50",
        proceeds_usd="400",
        basis_consumed_usd="150",
        gain_loss_usd="250",
        disposed_at=datetime(2025, 1, 15, tzinfo=UTC),
        holding_period="long",
        covered_status="no-1099-da",
    )
    seed_disposal(
        frm_db,
        lot=lot_b,
        disposal_ct=sell_b,
        quantity_consumed="50",
        proceeds_usd="400",
        basis_consumed_usd="300",
        gain_loss_usd="100",
        disposed_at=datetime(2025, 1, 15, tzinfo=UTC),
        holding_period="short",
        covered_status="no-1099-da",
    )

    rows = fetch_disposal_rows(frm_db, entity.id, TAX_YEAR)
    form = build_8949(rows, entity.id, TAX_YEAR)
    sched_d = build_schedule_d(form)
    sched_1 = build_schedule_1(fetch_reward_income(frm_db, entity.id, TAX_YEAR))

    assert len(rows) == 2
    assert form.boxes["F"].total_proceeds == Decimal("400")
    assert form.boxes["F"].total_basis == Decimal("150")
    assert form.boxes["F"].total_gain_loss_net == Decimal("250")
    assert form.boxes["C"].total_proceeds == Decimal("400")
    assert form.boxes["C"].total_basis == Decimal("300")
    assert form.boxes["C"].total_gain_loss_net == Decimal("100")
    assert sched_d.short_term_net == Decimal("100")
    assert sched_d.long_term_net == Decimal("250")
    assert sched_d.total_net == Decimal("350")
    assert sched_1.line_8z_income == Decimal("0")

    result = run_reconcile(frm_db, entity.id, TAX_YEAR, S3)
    assert result.passed, result.summary_lines()


# ── S4 ────────────────────────────────────────────────────────────────────────


def test_s4_reward_income_then_sale(frm_db: Any) -> None:
    """Staking reward acquired 2025-02-01 at FMV $150; sold at $250 → gain $100, Line 8z $150."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    reward_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="reward",
        amount="50",
        value_usd="150",
        occurred_at=datetime(2025, 2, 1, tzinfo=UTC),
    )
    sell_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="50",
        value_usd="250",
        occurred_at=datetime(2025, 9, 1, tzinfo=UTC),
    )
    lot = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=reward_ct,
        quantity="50",
        cost_basis_usd="150",
        acquired_at=datetime(2025, 2, 1, tzinfo=UTC),
        acquisition_type="reward",
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=sell_ct,
        quantity_consumed="50",
        proceeds_usd="250",
        basis_consumed_usd="150",
        gain_loss_usd="100",
        disposed_at=datetime(2025, 9, 1, tzinfo=UTC),
        holding_period="short",
        covered_status="no-1099-da",
    )

    rows = fetch_disposal_rows(frm_db, entity.id, TAX_YEAR)
    reward = fetch_reward_income(frm_db, entity.id, TAX_YEAR)
    form = build_8949(rows, entity.id, TAX_YEAR)
    sched_d = build_schedule_d(form)
    sched_1 = build_schedule_1(reward)

    assert len(rows) == 1
    assert rows[0].holding_period == "short"
    assert rows[0].proceeds_usd == Decimal("250")
    assert rows[0].cost_basis_usd == Decimal("150")
    assert rows[0].gain_loss_net == Decimal("100")
    assert form.boxes["C"].total_gain_loss_net == Decimal("100")
    assert sched_d.short_term_net == Decimal("100")
    assert sched_d.long_term_net == Decimal("0")
    assert sched_d.total_net == Decimal("100")
    assert sched_1.line_8z_income == Decimal("150")

    result = run_reconcile(frm_db, entity.id, TAX_YEAR, S4)
    assert result.passed, result.summary_lines()


# ── S5 ────────────────────────────────────────────────────────────────────────


def test_s5_cross_entity_contribution(frm_db: Any) -> None:
    """Lot acquired by Entity A; relocated to Entity B; disposed by Entity B.

    Entity A 8949: 0 rows.  Entity B 8949: 1 LONG row, gain $300.
    Seed post-relocation state: lot.entity_id = entity_b (apply_relocation updated it).
    """
    user = seed_user(frm_db)
    entity_a = seed_entity(frm_db, user, name="EntityA")
    entity_b = seed_entity(frm_db, user, name="EntityB")
    wallet_a = seed_wallet(frm_db, user)
    wallet_b = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet_a, entity_a)
    seed_assign(frm_db, wallet_b, entity_b)
    token = seed_token(frm_db)

    buy_ct = seed_ct(
        frm_db,
        wallet=wallet_a,
        token=token,
        classification="transfer-in",
        amount="100",
        value_usd="200",
        occurred_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    sell_ct = seed_ct(
        frm_db,
        wallet=wallet_b,
        token=token,
        classification="transfer-out",
        amount="100",
        value_usd="500",
        occurred_at=datetime(2025, 7, 1, tzinfo=UTC),
    )

    # Post-relocation: lot lives in wallet_b / entity_b, acquired_at preserved
    lot = seed_lot(
        frm_db,
        wallet=wallet_b,
        entity=entity_b,
        token=token,
        source_ct=buy_ct,
        quantity="100",
        cost_basis_usd="200",
        acquired_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=sell_ct,
        quantity_consumed="100",
        proceeds_usd="500",
        basis_consumed_usd="200",
        gain_loss_usd="300",
        disposed_at=datetime(2025, 7, 1, tzinfo=UTC),
        holding_period="long",
        covered_status="no-1099-da",
    )

    # Entity A: zero rows
    rows_a = fetch_disposal_rows(frm_db, entity_a.id, TAX_YEAR)
    assert len(rows_a) == 0
    form_a = build_8949(rows_a, entity_a.id, TAX_YEAR)
    assert form_a.total_gain_loss_net == Decimal("0")
    result_a = run_reconcile(frm_db, entity_a.id, TAX_YEAR, S5_ENTITY_A)
    assert result_a.passed, result_a.summary_lines()

    # Entity B: one LONG row, Box F
    rows_b = fetch_disposal_rows(frm_db, entity_b.id, TAX_YEAR)
    assert len(rows_b) == 1
    assert rows_b[0].holding_period == "long"
    assert rows_b[0].proceeds_usd == Decimal("500")
    assert rows_b[0].cost_basis_usd == Decimal("200")
    assert rows_b[0].gain_loss_net == Decimal("300")

    form_b = build_8949(rows_b, entity_b.id, TAX_YEAR)
    sched_d_b = build_schedule_d(form_b)
    assert form_b.boxes["F"].total_gain_loss_net == Decimal("300")
    assert sched_d_b.long_term_net == Decimal("300")
    assert sched_d_b.short_term_net == Decimal("0")
    result_b = run_reconcile(frm_db, entity_b.id, TAX_YEAR, S5_ENTITY_B)
    assert result_b.passed, result_b.summary_lines()


# ── S6 ────────────────────────────────────────────────────────────────────────


def test_s6_related_party_loss_disallowed(frm_db: Any) -> None:
    """§267 loss disallowed: adj_code='L', adj_usd=$200, col(h)=$0."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    buy_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="100",
        value_usd="500",
        occurred_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    sell_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="100",
        value_usd="300",
        occurred_at=datetime(2025, 6, 15, tzinfo=UTC),
    )
    lot = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_ct,
        quantity="100",
        cost_basis_usd="500",
        acquired_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=sell_ct,
        quantity_consumed="100",
        proceeds_usd="300",
        basis_consumed_usd="500",
        gain_loss_usd="-200",
        disposed_at=datetime(2025, 6, 15, tzinfo=UTC),
        holding_period="long",
        covered_status="no-1099-da",
        adjustment_code="L",
        adjustment_usd="200",
    )

    rows = fetch_disposal_rows(frm_db, entity.id, TAX_YEAR)
    form = build_8949(rows, entity.id, TAX_YEAR)
    sched_d = build_schedule_d(form)
    sched_1 = build_schedule_1(fetch_reward_income(frm_db, entity.id, TAX_YEAR))

    assert len(rows) == 1
    assert rows[0].holding_period == "long"
    assert rows[0].proceeds_usd == Decimal("300")
    assert rows[0].cost_basis_usd == Decimal("500")
    assert rows[0].adjustment_code == "L"
    assert rows[0].adjustment_usd == Decimal("200")
    assert rows[0].gain_loss_net == Decimal("0")

    box_f = form.boxes["F"]
    assert len(box_f.rows) == 1
    assert box_f.total_proceeds == Decimal("300")
    assert box_f.total_basis == Decimal("500")
    assert box_f.total_gain_loss_net == Decimal("0")

    assert sched_d.short_term_net == Decimal("0")
    assert sched_d.long_term_net == Decimal("0")
    assert sched_d.total_net == Decimal("0")
    assert sched_1.line_8z_income == Decimal("0")

    result = run_reconcile(frm_db, entity.id, TAX_YEAR, S6)
    assert result.passed, result.summary_lines()


# ── S7 ────────────────────────────────────────────────────────────────────────


def test_s7_gift_out_no_disposal(frm_db: Any) -> None:
    """Gift-out writes no LotDisposal.  fetch_disposal_rows returns 0 rows."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    buy_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="100",
        value_usd="400",
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    # Lot with quantity_remaining=0 (consumed by gift); no disposal row
    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_ct,
        quantity="100",
        cost_basis_usd="400",
        acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    # No seed_disposal call — gift-out never writes one

    rows = fetch_disposal_rows(frm_db, entity.id, TAX_YEAR)
    form = build_8949(rows, entity.id, TAX_YEAR)
    sched_d = build_schedule_d(form)
    sched_1 = build_schedule_1(fetch_reward_income(frm_db, entity.id, TAX_YEAR))

    assert len(rows) == 0
    assert form.total_gain_loss_net == Decimal("0")
    assert sched_d.total_net == Decimal("0")
    assert sched_1.line_8z_income == Decimal("0")

    result = run_reconcile(frm_db, entity.id, TAX_YEAR, S7)
    assert result.passed, result.summary_lines()


# ── S8 ────────────────────────────────────────────────────────────────────────


def test_s8_gate_held_pending_ct(frm_db: Any) -> None:
    """CT with classification='pending' → gate is held (blocking=True).

    With is_draft=False: run_reconcile returns gate_verdict=False (harness cannot run).
    With is_draft=True:  run_reconcile proceeds; fixture expects gate_held so verdict=True.
    """
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db)

    seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="pending",
        amount="100",
        value_usd="0",
        occurred_at=datetime(2025, 3, 15, tzinfo=UTC),
    )

    gate = check_gate(frm_db, entity.id, TAX_YEAR)
    assert gate.is_held is True
    assert any(r["reason"] == "pending" for r in gate.blocker_rows)

    # S8 fixture expects gate_held=True: reconcile verdict checks gate.is_held
    result = run_reconcile(frm_db, entity.id, TAX_YEAR, S8)
    assert result.passed, result.summary_lines()


# ── Fixture registry sanity ───────────────────────────────────────────────────


def test_builtin_fixtures_complete() -> None:
    """All 8 canonical scenario IDs are registered (S5 has two sub-fixtures)."""
    expected_ids = {"S1", "S2", "S3", "S4", "S5-A", "S5-B", "S6", "S7", "S8"}
    assert set(BUILTIN_FIXTURES) == expected_ids
