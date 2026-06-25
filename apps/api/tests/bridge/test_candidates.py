"""Unit tests for find_candidate_legs predicate logic.

Uses MagicMock rows to avoid DB dependency.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from lemon_ledger.domain.bridge.candidates import LegDirection, find_candidate_legs


def _make_row(
    classification: str,
    chain: str = "lemonchain",
    value_usd: str | None = "100",
    occurred_at: datetime | None = None,
) -> tuple[MagicMock, uuid.UUID, uuid.UUID]:
    ct = MagicMock()
    ct.id = uuid.uuid4()
    ct.wallet_id = uuid.uuid4()
    ct.chain = chain
    ct.classification = classification
    ct.amount = Decimal("10")
    ct.value_usd_at_event = Decimal(value_usd) if value_usd else None
    ct.occurred_at = occurred_at or datetime(2024, 6, 1, tzinfo=UTC)
    ct.contract_address = "0x" + "b" * 40
    ct.token_id = uuid.uuid4()
    logical_asset_id = uuid.uuid4()
    user_id = uuid.uuid4()
    return ct, logical_asset_id, user_id


def _mock_session(rows: list) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.all.return_value = rows
    return session


def test_transfer_out_yields_outflow_leg() -> None:
    ct, la_id, uid = _make_row("transfer-out")
    session = _mock_session([(ct, la_id, uid)])
    legs = find_candidate_legs(session, user_id=uid)
    assert len(legs) == 1
    assert legs[0].direction == LegDirection.OUTFLOW


def test_transfer_in_yields_inflow_leg() -> None:
    ct, la_id, uid = _make_row("transfer-in")
    session = _mock_session([(ct, la_id, uid)])
    legs = find_candidate_legs(session, user_id=uid)
    assert len(legs) == 1
    assert legs[0].direction == LegDirection.INFLOW


def test_materiality_filter_drops_below_threshold() -> None:
    ct, la_id, uid = _make_row("transfer-out", value_usd="0.50")
    session = _mock_session([(ct, la_id, uid)])
    legs = find_candidate_legs(session, user_id=uid, materiality_usd=Decimal("1.00"))
    assert len(legs) == 0


def test_materiality_filter_keeps_null_value() -> None:
    ct, la_id, uid = _make_row("transfer-out", value_usd=None)
    session = _mock_session([(ct, la_id, uid)])
    legs = find_candidate_legs(session, user_id=uid, materiality_usd=Decimal("1.00"))
    assert len(legs) == 1


def test_no_token_id_drops_leg() -> None:
    ct, la_id, uid = _make_row("transfer-out")
    ct.token_id = None
    session = _mock_session([(ct, la_id, uid)])
    legs = find_candidate_legs(session, user_id=uid)
    assert len(legs) == 0


def test_candidate_fields_populated() -> None:
    ct, la_id, uid = _make_row("transfer-in", chain="bsc")
    session = _mock_session([(ct, la_id, uid)])
    legs = find_candidate_legs(session, user_id=uid)
    leg = legs[0]
    assert leg.chain == "bsc"
    assert leg.logical_asset_id == la_id
    assert leg.amount == Decimal("10")
    assert leg.classified_event_id == ct.id
