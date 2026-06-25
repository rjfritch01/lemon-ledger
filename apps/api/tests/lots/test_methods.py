"""Unit tests for lot ordering methods and consumption logic.

No DB; all objects built in-memory. disposed_at passed explicitly — no freezegun.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from lemon_ledger.domain.lots.engine import (
    build_lines,
    consume,
)
from lemon_ledger.domain.lots.methods import (
    Fifo,
    Hifo,
    InsufficientLotsError,
    Lifo,
    SpecificIdValidator,
)
from lemon_ledger.models.enums import HoldingPeriod, SelectionStrategy

# ── Helpers ───────────────────────────────────────────────────────────────────


def _lot(
    *,
    quantity: str,
    quantity_remaining: str,
    cost_basis_usd: str,
    acquired_at: datetime,
    lot_id: uuid.UUID | None = None,
    asset_class: str = "fungible",
) -> object:
    """Build a minimal TaxLot-like object for pure-Python tests."""
    from unittest.mock import MagicMock

    lot = MagicMock()
    lot.id = lot_id or uuid.uuid4()
    lot.quantity = Decimal(quantity)
    lot.quantity_remaining = Decimal(quantity_remaining)
    lot.cost_basis_usd = Decimal(cost_basis_usd)
    lot.acquired_at = acquired_at
    lot.asset_class = asset_class
    return lot


T0 = datetime(2024, 1, 1, tzinfo=UTC)
T1 = datetime(2024, 6, 1, tzinfo=UTC)
T2 = datetime(2025, 1, 1, tzinfo=UTC)
T3 = datetime(2025, 1, 2, tzinfo=UTC)
T_LEAP = datetime(2024, 2, 29, tzinfo=UTC)
T_AFTER_LEAP = datetime(2025, 3, 1, tzinfo=UTC)


# ── Ordering ──────────────────────────────────────────────────────────────────


def test_fifo_sorts_oldest_first() -> None:
    lots = [
        _lot(quantity="10", quantity_remaining="10", cost_basis_usd="100", acquired_at=T1),
        _lot(quantity="10", quantity_remaining="10", cost_basis_usd="100", acquired_at=T0),
    ]
    ordered = Fifo().order(lots)
    assert ordered[0].acquired_at == T0
    assert ordered[1].acquired_at == T1


def test_hifo_sorts_highest_unit_cost_first() -> None:
    # cheap=$10/unit; expensive=$50/unit
    cheap = _lot(quantity="10", quantity_remaining="10", cost_basis_usd="100", acquired_at=T0)
    expensive = _lot(quantity="10", quantity_remaining="10", cost_basis_usd="500", acquired_at=T1)
    ordered = Hifo().order([cheap, expensive])
    assert ordered[0].cost_basis_usd == Decimal("500")
    assert ordered[1].cost_basis_usd == Decimal("100")


def test_lifo_sorts_most_recent_first() -> None:
    lots = [
        _lot(quantity="10", quantity_remaining="10", cost_basis_usd="100", acquired_at=T0),
        _lot(quantity="10", quantity_remaining="10", cost_basis_usd="100", acquired_at=T1),
    ]
    ordered = Lifo().order(lots)
    assert ordered[0].acquired_at == T1
    assert ordered[1].acquired_at == T0


# ── Consume ───────────────────────────────────────────────────────────────────


def test_consume_single_lot_full() -> None:
    lot = _lot(quantity="100", quantity_remaining="100", cost_basis_usd="1000", acquired_at=T0)
    slices = consume(Fifo(), [lot], Decimal("100"))
    assert len(slices) == 1
    assert slices[0].quantity_consumed == Decimal("100")
    assert slices[0].basis_consumed_usd == Decimal("1000")


def test_consume_partial_lot() -> None:
    lot = _lot(quantity="100", quantity_remaining="100", cost_basis_usd="1000", acquired_at=T0)
    slices = consume(Fifo(), [lot], Decimal("40"))
    assert slices[0].quantity_consumed == Decimal("40")
    # 40/100 * 1000 = 400
    assert slices[0].basis_consumed_usd == Decimal("400")


def test_consume_spans_two_lots() -> None:
    lot_a = _lot(quantity="50", quantity_remaining="50", cost_basis_usd="500", acquired_at=T0)
    lot_b = _lot(quantity="100", quantity_remaining="100", cost_basis_usd="200", acquired_at=T1)
    slices = consume(Fifo(), [lot_a, lot_b], Decimal("80"))
    assert len(slices) == 2
    assert slices[0].quantity_consumed == Decimal("50")
    assert slices[1].quantity_consumed == Decimal("30")


def test_consume_insufficient_raises() -> None:
    lot = _lot(quantity="10", quantity_remaining="10", cost_basis_usd="100", acquired_at=T0)
    with pytest.raises(InsufficientLotsError) as exc_info:
        consume(Fifo(), [lot], Decimal("20"))
    assert exc_info.value.quantity_unmatched == Decimal("10")


def test_consume_no_lots_raises() -> None:
    with pytest.raises(InsufficientLotsError) as exc_info:
        consume(Fifo(), [], Decimal("1"))
    assert exc_info.value.quantity_unmatched == Decimal("1")


def test_exhaustion_basis_sweep() -> None:
    """Σ basis_consumed must equal cost_basis_usd exactly at qty_remaining=0."""
    lot = _lot(quantity="3", quantity_remaining="3", cost_basis_usd="1", acquired_at=T0)
    # Consume in two bites: first partial, then exhaust.
    slices_1 = consume(Fifo(), [lot], Decimal("1"))
    lot.quantity_remaining -= slices_1[0].quantity_consumed

    slices_2 = consume(Fifo(), [lot], Decimal("2"))
    total_basis = slices_1[0].basis_consumed_usd + slices_2[0].basis_consumed_usd
    assert total_basis == Decimal("1"), f"got {total_basis}"


def test_consume_no_float_in_result() -> None:
    lot = _lot(quantity="3", quantity_remaining="3", cost_basis_usd="10", acquired_at=T0)
    slices = consume(Fifo(), [lot], Decimal("1"))
    for s in slices:
        assert isinstance(s.quantity_consumed, Decimal)
        assert isinstance(s.basis_consumed_usd, Decimal)


# ── build_lines ───────────────────────────────────────────────────────────────


def test_build_lines_proceeds_sum_exact() -> None:
    """Σ proceeds == total_proceeds_usd across multi-lot disposal."""
    lot_a = _lot(quantity="3", quantity_remaining="3", cost_basis_usd="30", acquired_at=T0)
    lot_b = _lot(quantity="7", quantity_remaining="7", cost_basis_usd="70", acquired_at=T1)
    slices = consume(Fifo(), [lot_a, lot_b], Decimal("10"))
    disposed = datetime(2025, 6, 1, tzinfo=UTC)
    total = Decimal("1")
    lines = build_lines(slices, total, disposed, SelectionStrategy.FIFO)
    assert sum(ln.proceeds_usd for ln in lines) == total


def test_build_lines_proceeds_residual_two_slices() -> None:
    """Residual sweep: last slice absorbs rounding remainder."""
    lot_a = _lot(quantity="1", quantity_remaining="1", cost_basis_usd="10", acquired_at=T0)
    lot_b = _lot(quantity="2", quantity_remaining="2", cost_basis_usd="20", acquired_at=T1)
    slices = consume(Fifo(), [lot_a, lot_b], Decimal("3"))
    total = Decimal("10")  # $10 proceeds
    disposed = datetime(2025, 6, 1, tzinfo=UTC)
    lines = build_lines(slices, total, disposed, SelectionStrategy.FIFO)
    assert sum(ln.proceeds_usd for ln in lines) == total


def test_burn_produces_zero_proceeds() -> None:
    lot = _lot(quantity="100", quantity_remaining="100", cost_basis_usd="500", acquired_at=T0)
    slices = consume(Fifo(), [lot], Decimal("100"))
    disposed = datetime(2025, 6, 1, tzinfo=UTC)
    lines = build_lines(slices, Decimal("0"), disposed, SelectionStrategy.FIFO)
    assert len(lines) == 1
    assert lines[0].proceeds_usd == Decimal("0")
    assert lines[0].gain_loss_usd == -Decimal("500")


# ── Holding period ────────────────────────────────────────────────────────────


def test_holding_period_short_on_anniversary() -> None:
    """Jan 1 2024 acquired → Jan 1 2025 disposed is SHORT."""
    lot = _lot(quantity="1", quantity_remaining="1", cost_basis_usd="100", acquired_at=T0)
    slices = consume(Fifo(), [lot], Decimal("1"))
    lines = build_lines(slices, Decimal("100"), T2, SelectionStrategy.FIFO)
    assert lines[0].holding_period == HoldingPeriod.SHORT


def test_holding_period_long_day_after_anniversary() -> None:
    """Jan 1 2024 acquired → Jan 2 2025 disposed is LONG."""
    lot = _lot(quantity="1", quantity_remaining="1", cost_basis_usd="100", acquired_at=T0)
    slices = consume(Fifo(), [lot], Decimal("1"))
    lines = build_lines(slices, Decimal("100"), T3, SelectionStrategy.FIFO)
    assert lines[0].holding_period == HoldingPeriod.LONG


def test_holding_period_leap_year_acquisition() -> None:
    """Feb 29 2024 acquired → Mar 1 2025 is LONG (relativedelta handles leap)."""
    lot = _lot(quantity="1", quantity_remaining="1", cost_basis_usd="100", acquired_at=T_LEAP)
    slices = consume(Fifo(), [lot], Decimal("1"))
    lines = build_lines(slices, Decimal("100"), T_AFTER_LEAP, SelectionStrategy.FIFO)
    assert lines[0].holding_period == HoldingPeriod.LONG


def test_mixed_holding_period_two_lots() -> None:
    """One short lot + one long lot in same disposal → two lines with different holding periods."""
    # T0 (Jan 2024) → T2 (Jan 2025) = SHORT
    lot_short = _lot(quantity="1", quantity_remaining="1", cost_basis_usd="10", acquired_at=T0)
    # T0 minus 1 year would be long; use a much earlier date.
    lot_long = _lot(
        quantity="1",
        quantity_remaining="1",
        cost_basis_usd="10",
        acquired_at=datetime(2022, 1, 1, tzinfo=UTC),
    )
    slices = consume(Fifo(), [lot_long, lot_short], Decimal("2"))
    # T2 = Jan 1 2025 → lot_long (Jan 2022) is LONG; lot_short (Jan 2024) is SHORT
    lines = build_lines(slices, Decimal("20"), T2, SelectionStrategy.FIFO)
    periods = {ln.holding_period for ln in lines}
    assert HoldingPeriod.LONG in periods
    assert HoldingPeriod.SHORT in periods


# ── SpecificIdValidator ───────────────────────────────────────────────────────


def test_specific_id_valid_selection() -> None:
    wid = uuid.uuid4()
    lot_id = uuid.uuid4()
    lot = _lot(
        quantity="100",
        quantity_remaining="100",
        cost_basis_usd="1000",
        acquired_at=T0,
        lot_id=lot_id,
    )
    pairs = SpecificIdValidator().validate({lot_id: Decimal("50")}, [lot], Decimal("50"), wid)
    assert len(pairs) == 1
    assert pairs[0][1] == Decimal("50")


def test_specific_id_lot_not_in_pool() -> None:
    wid = uuid.uuid4()
    foreign_id = uuid.uuid4()
    lot_id = uuid.uuid4()
    lot = _lot(
        quantity="100",
        quantity_remaining="100",
        cost_basis_usd="1000",
        acquired_at=T0,
        lot_id=lot_id,
    )
    with pytest.raises(ValueError, match="not in the open pool"):
        SpecificIdValidator().validate({foreign_id: Decimal("10")}, [lot], Decimal("10"), wid)


def test_specific_id_exceeds_remaining() -> None:
    wid = uuid.uuid4()
    lot_id = uuid.uuid4()
    lot = _lot(
        quantity="100",
        quantity_remaining="30",
        cost_basis_usd="1000",
        acquired_at=T0,
        lot_id=lot_id,
    )
    with pytest.raises(ValueError, match="remaining"):
        SpecificIdValidator().validate({lot_id: Decimal("50")}, [lot], Decimal("50"), wid)


def test_specific_id_sum_mismatch() -> None:
    wid = uuid.uuid4()
    lot_id = uuid.uuid4()
    lot = _lot(
        quantity="100",
        quantity_remaining="100",
        cost_basis_usd="1000",
        acquired_at=T0,
        lot_id=lot_id,
    )
    with pytest.raises(ValueError, match="sum"):
        SpecificIdValidator().validate({lot_id: Decimal("40")}, [lot], Decimal("50"), wid)


# ── Decimal-only invariant ────────────────────────────────────────────────────


def test_all_build_lines_values_are_decimal() -> None:
    lot = _lot(quantity="7", quantity_remaining="7", cost_basis_usd="100", acquired_at=T0)
    slices = consume(Fifo(), [lot], Decimal("7"))
    disposed = datetime(2025, 6, 1, tzinfo=UTC)
    lines = build_lines(slices, Decimal("150"), disposed, SelectionStrategy.FIFO)
    for line in lines:
        assert isinstance(line.quantity_consumed, Decimal)
        assert isinstance(line.proceeds_usd, Decimal)
        assert isinstance(line.basis_consumed_usd, Decimal)
        assert isinstance(line.gain_loss_usd, Decimal)
