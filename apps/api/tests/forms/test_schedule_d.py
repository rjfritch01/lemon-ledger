"""Unit test: Schedule D anti-drift invariant.

Schedule D reads ONLY Form8949Result box subtotals — never the ledger.
The anti-drift assertion: schedule_d.total_net == sum of all 8949 box (h) subtotals.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from lemon_ledger.domain.forms.form_8949 import build_8949
from lemon_ledger.domain.forms.read_model import DisposalRow
from lemon_ledger.domain.forms.schedule_d import build_schedule_d

_ENT = uuid.uuid4()


def _row(proceeds: str, basis: str, holding: str, covered: str = "no-1099-da") -> DisposalRow:
    return DisposalRow(
        lot_id=uuid.uuid4(),
        disposal_tx_id=uuid.uuid4(),
        description="1 LEMX",
        acquired_at=date(2024, 1, 1),
        disposed_at=date(2025, 6, 1),
        proceeds_usd=Decimal(proceeds),
        cost_basis_usd=Decimal(basis),
        adjustment_code=None,
        adjustment_usd=None,
        holding_period=holding,
        covered_status=covered,
        asset_class="fungible",
        entity_id=_ENT,
    )


def test_schedule_d_anti_drift_total_equals_8949_box_sum() -> None:
    """Core invariant: Schedule D total_net == sum(box.total_gain_loss_net)."""
    rows = [
        _row("200", "100", "short"),  # Box C: +100
        _row("150", "200", "short"),  # Box C: -50
        _row("500", "300", "long"),  # Box F: +200
        _row("100", "150", "long"),  # Box F: -50
    ]
    form_8949 = build_8949(rows, _ENT, 2025)
    sched_d = build_schedule_d(form_8949)

    expected_total = sum(b.total_gain_loss_net for b in form_8949.boxes.values())
    assert sched_d.total_net == expected_total


def test_schedule_d_parts() -> None:
    rows = [
        _row("300", "100", "short"),  # +200 short
        _row("500", "400", "long"),  # +100 long
    ]
    form_8949 = build_8949(rows, _ENT, 2025)
    sched_d = build_schedule_d(form_8949)

    assert sched_d.short_term_net == Decimal("200")
    assert sched_d.long_term_net == Decimal("100")
    assert sched_d.total_net == Decimal("300")
    assert sched_d.total_net == sched_d.short_term_net + sched_d.long_term_net


def test_schedule_d_entity_and_year_passthrough() -> None:
    form_8949 = build_8949([], _ENT, 2025)
    sched_d = build_schedule_d(form_8949)
    assert sched_d.entity_id == _ENT
    assert sched_d.tax_year == 2025


def test_schedule_d_net_loss_case() -> None:
    rows = [
        _row("100", "500", "long"),  # -400 long
    ]
    form_8949 = build_8949(rows, _ENT, 2025)
    sched_d = build_schedule_d(form_8949)
    assert sched_d.long_term_net == Decimal("-400")
    assert sched_d.total_net == Decimal("-400")


def test_schedule_d_draft_propagates_from_8949() -> None:
    form_8949 = build_8949([], _ENT, 2025, is_draft=True)
    sched_d = build_schedule_d(form_8949)
    assert sched_d.is_draft is True
