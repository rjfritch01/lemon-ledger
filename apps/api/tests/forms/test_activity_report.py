"""Tests for the activity / gain-loss report."""

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
from lemon_ledger.domain.forms.activity_report import build_activity_report, to_csv
from lemon_ledger.domain.forms.read_model import (
    fetch_acquisition_rows,
    fetch_disposal_rows,
    fetch_reward_income,
)

pytestmark = pytest.mark.usefixtures("frm_db")


def test_activity_report_aggregates(frm_db: Any) -> None:
    """build_activity_report totals match per-row sums."""
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
    )

    acquisitions = fetch_acquisition_rows(frm_db, entity.id, 2025)
    disposals = fetch_disposal_rows(frm_db, entity.id, 2025)
    reward = fetch_reward_income(frm_db, entity.id, 2025)

    report = build_activity_report(acquisitions, disposals, reward, entity.id, 2025)

    assert len(report.acquisitions) == 1
    assert len(report.disposals) == 1
    assert report.total_proceeds == Decimal("500")
    assert report.total_cost_basis_disposed == Decimal("200")
    assert report.total_gain_loss == Decimal("300")
    assert report.total_reward_income == Decimal("0")
    assert report.is_draft is False


def test_activity_report_reward_income(frm_db: Any) -> None:
    """Reward lot shows in Line 8z but NOT as a disposal."""
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
    seed_lot(
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

    acquisitions = fetch_acquisition_rows(frm_db, entity.id, 2025)
    disposals = fetch_disposal_rows(frm_db, entity.id, 2025)
    reward = fetch_reward_income(frm_db, entity.id, 2025)
    report = build_activity_report(acquisitions, disposals, reward, entity.id, 2025)

    assert len(report.acquisitions) == 1
    assert report.acquisitions[0].acquisition_type == "reward"
    assert len(report.disposals) == 0
    assert report.total_reward_income == Decimal("150")
    assert report.total_gain_loss == Decimal("0")


def test_to_csv_contains_sections(frm_db: Any) -> None:
    """CSV output includes acquisition and disposal section headers and disclaimer."""
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
        amount="10",
        value_usd="100",
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    sell_ct = seed_ct(
        frm_db,
        wallet=wallet,
        token=token,
        classification="transfer-out",
        amount="10",
        value_usd="150",
        occurred_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    lot = seed_lot(
        frm_db,
        wallet=wallet,
        entity=entity,
        token=token,
        source_ct=buy_ct,
        quantity="10",
        cost_basis_usd="100",
        acquired_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    seed_disposal(
        frm_db,
        lot=lot,
        disposal_ct=sell_ct,
        quantity_consumed="10",
        proceeds_usd="150",
        basis_consumed_usd="100",
        gain_loss_usd="50",
        disposed_at=datetime(2025, 6, 1, tzinfo=UTC),
        holding_period="short",
    )

    acqs = fetch_acquisition_rows(frm_db, entity.id, 2025)
    disps = fetch_disposal_rows(frm_db, entity.id, 2025)
    reward = fetch_reward_income(frm_db, entity.id, 2025)
    report = build_activity_report(acqs, disps, reward, entity.id, 2025)

    csv_text = to_csv(report)
    assert "ACQUISITIONS" in csv_text
    assert "DISPOSALS" in csv_text
    assert "SUMMARY" in csv_text
    assert "INFORMATIONAL ONLY" in csv_text
    assert "150" in csv_text  # proceeds
    assert "100" in csv_text  # basis


def test_activity_report_empty(frm_db: Any) -> None:
    """Empty entity produces all-zero report without errors."""
    user = seed_user(frm_db)
    entity = seed_entity(frm_db, user)
    wallet = seed_wallet(frm_db, user)
    seed_assign(frm_db, wallet, entity)

    acqs = fetch_acquisition_rows(frm_db, entity.id, 2025)
    disps = fetch_disposal_rows(frm_db, entity.id, 2025)
    reward = fetch_reward_income(frm_db, entity.id, 2025)
    report = build_activity_report(acqs, disps, reward, entity.id, 2025)

    assert len(report.acquisitions) == 0
    assert len(report.disposals) == 0
    assert report.total_proceeds == Decimal("0")
    assert report.total_gain_loss == Decimal("0")
    assert report.total_reward_income == Decimal("0")
