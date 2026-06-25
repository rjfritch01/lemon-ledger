"""Unit tests for custody address lookup and learning."""

from __future__ import annotations

from unittest.mock import MagicMock

from lemon_ledger.domain.bridge.custody import recognize_custody, strongest_custody
from lemon_ledger.models.bridge import CustodyRecognition

# ── recognize_custody ─────────────────────────────────────────────────────────


def _session_with_row(recognition: str | None) -> MagicMock:
    session = MagicMock()
    if recognition is None:
        session.scalar.return_value = None
    else:
        row = MagicMock()
        row.recognition = recognition
        session.scalar.return_value = row
    return session


def test_recognize_custody_recognized() -> None:
    session = _session_with_row("recognized")
    result = recognize_custody(session, address="0xabc", chain="lemonchain")
    assert result == CustodyRecognition.RECOGNIZED


def test_recognize_custody_inferred() -> None:
    session = _session_with_row("inferred")
    result = recognize_custody(session, address="0xabc", chain="lemonchain")
    assert result == CustodyRecognition.INFERRED


def test_recognize_custody_unknown_when_no_row() -> None:
    session = _session_with_row(None)
    result = recognize_custody(session, address="0xabc", chain="lemonchain")
    assert result == CustodyRecognition.UNKNOWN


# ── strongest_custody ─────────────────────────────────────────────────────────


def test_strongest_custody_recognized_wins() -> None:
    session = MagicMock()
    recognized_row = MagicMock()
    recognized_row.recognition = "recognized"
    session.scalar.return_value = recognized_row

    rec, addr = strongest_custody(
        session,
        outflow_contract="0xaaa",
        inflow_contract="0xbbb",
        outflow_chain="lemonchain",
        inflow_chain="bsc",
    )
    assert rec == CustodyRecognition.RECOGNIZED


def test_strongest_custody_both_unknown() -> None:
    session = MagicMock()
    session.scalar.return_value = None

    rec, addr = strongest_custody(
        session,
        outflow_contract="0xaaa",
        inflow_contract="0xbbb",
        outflow_chain="lemonchain",
        inflow_chain="bsc",
    )
    assert rec == CustodyRecognition.UNKNOWN
    assert addr is None


def test_strongest_custody_inferred_when_no_recognized() -> None:
    session = MagicMock()
    inferred_row = MagicMock()
    inferred_row.recognition = "inferred"

    call_count = [0]

    def scalar_side_effect(_: object) -> object:
        call_count[0] += 1
        if call_count[0] == 1:
            return None  # outflow: unknown
        return inferred_row  # inflow: inferred

    session.scalar.side_effect = scalar_side_effect

    rec, addr = strongest_custody(
        session,
        outflow_contract="0xaaa",
        inflow_contract="0xbbb",
        outflow_chain="lemonchain",
        inflow_chain="bsc",
    )
    assert rec == CustodyRecognition.INFERRED
    assert addr == "0xbbb"


def test_strongest_custody_none_contracts() -> None:
    session = MagicMock()
    rec, addr = strongest_custody(
        session,
        outflow_contract=None,
        inflow_contract=None,
        outflow_chain="lemonchain",
        inflow_chain="bsc",
    )
    assert rec == CustodyRecognition.UNKNOWN
    assert addr is None
    session.scalar.assert_not_called()
