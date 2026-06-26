"""Unit tests: Form 8949 box split, (h) column, L adjustment.

No database required — tests work on DisposalRow instances directly.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from lemon_ledger.domain.forms.form_8949 import build_8949
from lemon_ledger.domain.forms.read_model import DisposalRow

_ENT = uuid.uuid4()
_ACQ = date(2024, 1, 1)
_DIS = date(2025, 7, 1)  # 18 months → long


def _row(
    *,
    proceeds: str,
    basis: str,
    holding: str = "short",
    covered: str = "no-1099-da",
    adj_code: str | None = None,
    adj_usd: str | None = None,
    asset_class: str = "fungible",
) -> DisposalRow:
    proceeds_d = Decimal(proceeds)
    basis_d = Decimal(basis)
    adj_d = Decimal(adj_usd) if adj_usd else None
    return DisposalRow(
        lot_id=uuid.uuid4(),
        disposal_tx_id=uuid.uuid4(),
        description="1 LEMX",
        acquired_at=_ACQ,
        disposed_at=_DIS,
        proceeds_usd=proceeds_d,
        cost_basis_usd=basis_d,
        adjustment_code=adj_code,
        adjustment_usd=adj_d,
        holding_period=holding,
        covered_status=covered,
        asset_class=asset_class,
        entity_id=_ENT,
    )


# ── box selection ──────────────────────────────────────────────────────────────


def test_short_covered_basis_reported_maps_to_box_a() -> None:
    row = _row(proceeds="100", basis="80", holding="short", covered="covered-basis-reported")
    result = build_8949([row], _ENT, 2025)
    assert len(result.boxes["A"].rows) == 1
    assert result.boxes["B"].rows == []
    assert result.boxes["C"].rows == []


def test_short_no_1099_maps_to_box_c() -> None:
    row = _row(proceeds="100", basis="80", holding="short", covered="no-1099-da")
    result = build_8949([row], _ENT, 2025)
    assert len(result.boxes["C"].rows) == 1


def test_long_no_1099_maps_to_box_f() -> None:
    row = _row(proceeds="300", basis="200", holding="long", covered="no-1099-da")
    result = build_8949([row], _ENT, 2025)
    assert len(result.boxes["F"].rows) == 1
    assert result.boxes["C"].rows == []


def test_long_covered_basis_reported_maps_to_box_d() -> None:
    row = _row(proceeds="300", basis="200", holding="long", covered="covered-basis-reported")
    result = build_8949([row], _ENT, 2025)
    assert len(result.boxes["D"].rows) == 1


# ── column (h) = (d) - (e) + (g) ─────────────────────────────────────────────


def test_gain_loss_net_no_adjustment() -> None:
    row = _row(proceeds="200", basis="100")
    assert row.gain_loss_net == Decimal("100")


def test_gain_loss_net_with_L_adjustment_nets_to_zero() -> None:
    """§267 related-party loss fully disallowed: adj_usd = abs(loss), net = 0."""
    row = _row(proceeds="100", basis="200", adj_code="L", adj_usd="100")
    # gain_loss_usd = 100 - 200 = -100; adjustment_usd = +100; net = 0
    assert row.gain_loss_net == Decimal("0")


def test_gain_loss_net_partial_adjustment() -> None:
    row = _row(proceeds="100", basis="300", adj_code="L", adj_usd="150")
    # gain_loss_usd = -200; adjustment = +150; net = -50
    assert row.gain_loss_net == Decimal("-50")


# ── box subtotals ──────────────────────────────────────────────────────────────


def test_box_subtotals_aggregate_correctly() -> None:
    rows = [
        _row(proceeds="200", basis="100", holding="short"),  # gain $100
        _row(proceeds="150", basis="200", holding="short"),  # loss -$50
    ]
    result = build_8949(rows, _ENT, 2025)
    box_c = result.boxes["C"]
    assert box_c.total_proceeds == Decimal("350")
    assert box_c.total_basis == Decimal("300")
    assert box_c.total_adjustment == Decimal("0")
    assert box_c.total_gain_loss_net == Decimal("50")


def test_empty_rows_returns_zero_subtotals_for_all_boxes() -> None:
    result = build_8949([], _ENT, 2025)
    assert len(result.boxes) == 6
    for box, sub in result.boxes.items():
        assert sub.total_gain_loss_net == Decimal("0"), f"Box {box} should be zero"
        assert sub.rows == []


def test_multi_box_partition() -> None:
    rows = [
        _row(proceeds="200", basis="100", holding="short", covered="no-1099-da"),  # Box C
        _row(proceeds="300", basis="250", holding="long", covered="no-1099-da"),  # Box F
    ]
    result = build_8949(rows, _ENT, 2025)
    assert len(result.boxes["C"].rows) == 1
    assert len(result.boxes["F"].rows) == 1
    assert result.boxes["C"].total_gain_loss_net == Decimal("100")
    assert result.boxes["F"].total_gain_loss_net == Decimal("50")


def test_short_term_and_long_term_net_properties() -> None:
    rows = [
        _row(proceeds="200", basis="100", holding="short"),  # Box C +100
        _row(proceeds="300", basis="250", holding="long"),  # Box F +50
    ]
    result = build_8949(rows, _ENT, 2025)
    assert result.short_term_net == Decimal("100")
    assert result.long_term_net == Decimal("50")
    assert result.total_gain_loss_net == Decimal("150")


# ── is_draft flag ─────────────────────────────────────────────────────────────


def test_draft_flag_propagates() -> None:
    result = build_8949([], _ENT, 2025, is_draft=True)
    assert result.is_draft is True
