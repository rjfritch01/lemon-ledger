"""Tests for the supply_snapshot job."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from lemon_ledger.jobs.supply_snapshot import check_completion, read_total_supply


def _make_token(
    symbol: str,
    contract_address: str,
    max_supply: Decimal,
    decimals: int = 18,
) -> MagicMock:
    tok = MagicMock()
    tok.id = MagicMock()
    tok.symbol = symbol
    tok.contract_address = contract_address
    tok.max_supply = max_supply
    tok.decimals = decimals
    return tok


def test_read_total_supply_decodes_hex() -> None:
    evm = MagicMock()
    # 100 * 10^18 as a padded hex uint256
    evm.eth_call.return_value = "0x" + hex(100 * 10**18)[2:].zfill(64)
    result = read_total_supply(evm, "0xcontract")
    assert result == 100 * 10**18


def test_read_total_supply_returns_none_on_empty() -> None:
    evm = MagicMock()
    evm.eth_call.return_value = "0x"
    assert read_total_supply(evm, "0xcontract") is None


def test_read_total_supply_returns_none_on_exception() -> None:
    evm = MagicMock()
    evm.eth_call.side_effect = Exception("rpc error")
    assert read_total_supply(evm, "0xcontract") is None


def test_check_completion_marks_token_complete() -> None:
    """Token at max_supply gets distribution_complete=True."""
    import uuid

    token_id = uuid.uuid4()
    cfg = MagicMock()
    cfg.token_id = token_id
    cfg.distribution_complete = False

    token = _make_token("LFLX", "0xcontract", max_supply=Decimal("1000000"), decimals=18)

    session = MagicMock()
    session.scalars.return_value.all.return_value = [cfg]
    session.get.return_value = token

    evm = MagicMock()
    # Supply exactly at max
    total_supply_raw = 1_000_000 * 10**18
    evm.eth_call.return_value = "0x" + hex(total_supply_raw)[2:].zfill(64)

    result = check_completion(session, evm)
    assert "LFLX" in result["updated"]
    assert cfg.distribution_complete is True
    session.commit.assert_called_once()


def test_check_completion_skips_already_complete() -> None:
    cfg = MagicMock()
    cfg.distribution_complete = True  # already done

    session = MagicMock()
    session.scalars.return_value.all.return_value = [cfg]
    session.get.return_value = MagicMock()  # won't be reached after early skip

    evm = MagicMock()
    check_completion(session, evm)
    evm.eth_call.assert_not_called()


def test_check_completion_dry_run_no_write() -> None:
    import uuid

    token_id = uuid.uuid4()
    cfg = MagicMock()
    cfg.token_id = token_id
    cfg.distribution_complete = False

    token = _make_token("LFLX", "0xcontract", max_supply=Decimal("1000"), decimals=18)

    session = MagicMock()
    session.scalars.return_value.all.return_value = [cfg]
    session.get.return_value = token

    evm = MagicMock()
    evm.eth_call.return_value = "0x" + hex(1000 * 10**18)[2:].zfill(64)

    check_completion(session, evm, dry_run=True)
    assert cfg.distribution_complete is False
    session.commit.assert_not_called()


def test_check_completion_skips_no_contract_address() -> None:
    import uuid

    token_id = uuid.uuid4()
    cfg = MagicMock()
    cfg.token_id = token_id
    cfg.distribution_complete = False

    token = MagicMock()
    token.contract_address = None  # no contract
    token.max_supply = Decimal("1000")

    session = MagicMock()
    session.scalars.return_value.all.return_value = [cfg]
    session.get.return_value = token

    evm = MagicMock()
    result = check_completion(session, evm)
    evm.eth_call.assert_not_called()
    assert str(token_id) in result["skipped"]
