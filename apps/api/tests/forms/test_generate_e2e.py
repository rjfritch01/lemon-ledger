"""E2E worked example: Form 8949 + Schedule D + Schedule 1.

Scenario:
  - Entity has 3 disposals:
      1. Short-term gain $100 (Box C)
      2. Long-term gain $200 (Box F)
      3. Long-term related-party loss −$150 with 'L' adjustment +$150 → col(h) = $0
  - Entity has 1 reward lot (staking), cost_basis_usd=$75
  - Gate is clear (no pending CTs)

Assertions:
  - 8949 totals per box correct
  - Schedule D total == sum of 8949 box subtotals (anti-drift)
  - Line 8z == $75 (reward basis sum)
  - L row nets col(h) to $0 exactly
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
from lemon_ledger.domain.forms.form_8949 import build_8949
from lemon_ledger.domain.forms.gate import check_gate
from lemon_ledger.domain.forms.read_model import fetch_disposal_rows, fetch_reward_income
from lemon_ledger.domain.forms.schedule_1 import build_schedule_1
from lemon_ledger.domain.forms.schedule_d import build_schedule_d


def test_worked_example_full_pipeline(frm_db: Session) -> None:
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user, "WorkedExample")
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)
    token = seed_token(frm_db, "LEMX")

    # ── Disposal 1: short-term gain $100 (Box C) ──────────────────────────────
    acq1 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="10",
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    lot1 = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=acq1,
        quantity="10",
        cost_basis_usd="100",
        acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    dis1 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        occurred_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot1,
        disposal_ct=dis1,
        quantity_consumed="10",
        proceeds_usd="200",
        basis_consumed_usd="100",
        gain_loss_usd="100",
        disposed_at=datetime(2025, 6, 1, tzinfo=UTC),
        holding_period="short",
        covered_status="no-1099-da",
    )

    # ── Disposal 2: long-term gain $200 (Box F) ────────────────────────────────
    acq2 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="20",
        occurred_at=datetime(2023, 12, 1, tzinfo=UTC),
    )
    lot2 = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=acq2,
        quantity="20",
        cost_basis_usd="300",
        acquired_at=datetime(2023, 12, 1, tzinfo=UTC),
    )
    dis2 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="20",
        occurred_at=datetime(2025, 7, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot2,
        disposal_ct=dis2,
        quantity_consumed="20",
        proceeds_usd="500",
        basis_consumed_usd="300",
        gain_loss_usd="200",
        disposed_at=datetime(2025, 7, 1, tzinfo=UTC),
        holding_period="long",
        covered_status="no-1099-da",
    )

    # ── Disposal 3: long-term related-party loss, L adjustment (Box F) ─────────
    # proceeds=$50, basis=$200 → gain_loss=-$150; adj_code='L', adj_usd=$150 → col(h)=$0
    acq3 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-in",
        amount="5",
        occurred_at=datetime(2023, 11, 1, tzinfo=UTC),
    )
    lot3 = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=acq3,
        quantity="5",
        cost_basis_usd="200",
        acquired_at=datetime(2023, 11, 1, tzinfo=UTC),
    )
    dis3 = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="5",
        occurred_at=datetime(2025, 8, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot3,
        disposal_ct=dis3,
        quantity_consumed="5",
        proceeds_usd="50",
        basis_consumed_usd="200",
        gain_loss_usd="-150",
        disposed_at=datetime(2025, 8, 1, tzinfo=UTC),
        holding_period="long",
        covered_status="no-1099-da",
        adjustment_code="L",
        adjustment_usd="150",  # abs(loss) added back → col(h) = 0
    )

    # ── Reward lot (Schedule 1 Line 8z) ────────────────────────────────────────
    ct_rwd = seed_ct(frm_db, wallet=wallet, token=token, classification="reward", amount="5")
    seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=ct_rwd,
        quantity="5",
        cost_basis_usd="75",
        acquired_at=datetime(2025, 2, 1, tzinfo=UTC),
        acquisition_type="reward",
    )

    # ── Gate check ─────────────────────────────────────────────────────────────
    gate = check_gate(frm_db, entity.id, 2025)
    assert gate.is_held is False, f"Gate held unexpectedly: {gate.blocker_rows}"

    # ── Fetch and build ────────────────────────────────────────────────────────
    disposal_rows = fetch_disposal_rows(frm_db, entity.id, 2025)
    reward_income = fetch_reward_income(frm_db, entity.id, 2025)

    assert len(disposal_rows) == 3

    form_8949 = build_8949(disposal_rows, entity.id, 2025)
    sched_d = build_schedule_d(form_8949)
    sched_1 = build_schedule_1(reward_income)

    # ── 8949 box assertions ────────────────────────────────────────────────────
    box_c = form_8949.boxes["C"]  # short-term no-1099-da
    assert box_c.total_proceeds == Decimal("200")
    assert box_c.total_basis == Decimal("100")
    assert box_c.total_gain_loss_net == Decimal("100")

    box_f = form_8949.boxes["F"]  # long-term no-1099-da
    assert box_f.total_proceeds == Decimal("550")  # 500 + 50
    assert box_f.total_basis == Decimal("500")  # 300 + 200
    # gain_loss_net = (500 + 50) - (300 + 200) + (0 + 150) = 550 - 500 + 150 = 200
    assert box_f.total_gain_loss_net == Decimal("200")

    # ── L-row col(h) explicitly = $0 ──────────────────────────────────────────
    l_row = next(r for r in disposal_rows if r.adjustment_code == "L")
    assert l_row.gain_loss_net == Decimal("0")

    # ── Schedule D anti-drift invariant ────────────────────────────────────────
    eight_nine_total = sum(b.total_gain_loss_net for b in form_8949.boxes.values())
    assert sched_d.total_net == eight_nine_total

    assert sched_d.short_term_net == Decimal("100")
    assert sched_d.long_term_net == Decimal("200")
    assert sched_d.total_net == Decimal("300")

    # ── Schedule 1 Line 8z ─────────────────────────────────────────────────────
    assert sched_1.line_8z_income == Decimal("75")
