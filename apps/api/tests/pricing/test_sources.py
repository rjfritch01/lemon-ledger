"""Unit tests for individual price source factories."""

import logging
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from lemon_ledger.clients.oracle import OraclePriceStale, OracleTokenNotSupported
from lemon_ledger.pricing.sources import (
    cmc_lemon,
    coingecko_lemon2,
    last_known_good,
    lemx_oracle_crossval,
    oracle_daily_avg,
    oracle_spot,
    stable_peg,
)
from lemon_ledger.pricing.types import PriceResult, PriceSource, TokenRow


def _tok(symbol: str = "LEMX", category: str = "ecosystem-native") -> TokenRow:
    return TokenRow(
        token_id=f"{symbol.lower()}-id",
        symbol=symbol,
        category=category,
        contract_address="0x" + "a" * 40,
        chain="lemonchain",
        tier=1,
        decimals=18,
    )


def _oracle_mock(price: Decimal | None = None, *, stale: bool = False) -> MagicMock:
    oracle = MagicMock()
    if stale or price is None:
        oracle.get_spot_price.side_effect = OraclePriceStale("stale")
        oracle.get_daily_average.return_value = None
    else:
        oracle.get_spot_price.return_value = price
        oracle.get_daily_average.return_value = price
    return oracle


# ── oracle_spot ────────────────────────────────────────────────────────────────


def test_oracle_spot_returns_price_result() -> None:
    result = oracle_spot(_oracle_mock(Decimal("0.5")))(_tok())
    assert result is not None
    assert result.price_usd == Decimal("0.5")
    assert result.source == PriceSource.ORACLE
    assert result.stale is False


def test_oracle_spot_stale_returns_none() -> None:
    assert oracle_spot(_oracle_mock(stale=True))(_tok()) is None


def test_oracle_spot_not_supported_returns_none() -> None:
    oracle = MagicMock()
    oracle.get_spot_price.side_effect = OracleTokenNotSupported("no feed")
    assert oracle_spot(oracle)(_tok()) is None


# ── oracle_daily_avg ───────────────────────────────────────────────────────────


def test_oracle_daily_avg_returns_price_result() -> None:
    result = oracle_daily_avg(_oracle_mock(Decimal("1.23")))(_tok())
    assert result is not None
    assert result.price_usd == Decimal("1.23")
    assert result.source == PriceSource.ORACLE


def test_oracle_daily_avg_zero_price_returns_none() -> None:
    oracle = MagicMock()
    oracle.get_daily_average.return_value = None  # 0→None already applied
    assert oracle_daily_avg(oracle)(_tok()) is None


# ── lemx_oracle_crossval ───────────────────────────────────────────────────────


def test_lemx_crossval_returns_oracle_value_within_threshold() -> None:
    oracle = _oracle_mock(Decimal("0.100"))
    cg = MagicMock()
    cg.coin_price_usd.return_value = Decimal("0.103")  # 3% — within 5%
    result = lemx_oracle_crossval(oracle, cg)(_tok())
    assert result is not None
    assert result.price_usd == Decimal("0.100")
    assert result.source == PriceSource.ORACLE


def test_lemx_crossval_logs_warning_on_divergence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    oracle = _oracle_mock(Decimal("0.100"))
    cg = MagicMock()
    cg.coin_price_usd.return_value = Decimal("0.200")  # 100% — exceeds 5%
    with caplog.at_level(logging.WARNING):
        result = lemx_oracle_crossval(oracle, cg)(_tok())
    assert result is not None
    assert result.price_usd == Decimal("0.100")  # ORACLE value returned, not CoinGecko
    assert any("diverge" in r.getMessage().lower() for r in caplog.records)


def test_lemx_crossval_oracle_stale_returns_none_and_skips_cg() -> None:
    oracle = _oracle_mock(stale=True)
    cg = MagicMock()
    assert lemx_oracle_crossval(oracle, cg)(_tok()) is None
    cg.coin_price_usd.assert_not_called()


# ── coingecko_lemon2 ───────────────────────────────────────────────────────────


def test_coingecko_lemon2_happy_path() -> None:
    cg = MagicMock()
    cg.coin_price_usd.return_value = Decimal("0.042")
    result = coingecko_lemon2(cg)(_tok())
    assert result is not None
    assert result.price_usd == Decimal("0.042")
    assert result.source == PriceSource.COINGECKO


def test_coingecko_lemon2_none_propagates() -> None:
    cg = MagicMock()
    cg.coin_price_usd.return_value = None
    assert coingecko_lemon2(cg)(_tok()) is None


# ── cmc_lemon ─────────────────────────────────────────────────────────────────


def test_cmc_lemon_none_when_no_client() -> None:
    assert cmc_lemon(None)(_tok()) is None


def test_cmc_lemon_none_when_lemx_cmc_id_is_none() -> None:
    cmc = MagicMock()
    # LEMX_CMC_ID is currently None → short-circuits without calling CMC
    assert cmc_lemon(cmc)(_tok()) is None
    cmc.quote_usd.assert_not_called()


# ── stable_peg ────────────────────────────────────────────────────────────────


def test_stable_peg_returns_one_usd() -> None:
    oracle = MagicMock()
    oracle.get_spot_price.return_value = Decimal("1.00")
    result = stable_peg(oracle)(_tok("LUSD", "ecosystem-stablecoin"))
    assert result is not None
    assert result.price_usd == Decimal("1.00")
    assert result.source == PriceSource.STABLE_PEG


def test_stable_peg_logs_depeg_warning(caplog: pytest.LogCaptureFixture) -> None:
    oracle = MagicMock()
    oracle.get_spot_price.return_value = Decimal("0.95")  # 5% depeg
    with caplog.at_level(logging.WARNING):
        result = stable_peg(oracle)(_tok("LUSD", "ecosystem-stablecoin"))
    assert result is not None
    assert result.price_usd == Decimal("1.00")  # peg ALWAYS returned
    assert any("depeg" in r.getMessage().lower() for r in caplog.records)


def test_stable_peg_oracle_failure_still_returns_peg() -> None:
    oracle = MagicMock()
    oracle.get_spot_price.side_effect = Exception("RPC down")
    result = stable_peg(oracle)(_tok("LUSD"))
    assert result is not None
    assert result.price_usd == Decimal("1.00")


# ── last_known_good ────────────────────────────────────────────────────────────


def test_last_known_good_returns_lkg_from_cache() -> None:
    cache = MagicMock()
    expected = PriceResult(price_usd=Decimal("0.05"), source=PriceSource.ORACLE, stale=True)
    cache.get_lkg.return_value = expected
    token = _tok()
    result = last_known_good(cache)(token)
    assert result is expected
    cache.get_lkg.assert_called_once_with(token.chain, token.token_id)


def test_last_known_good_returns_none_when_cache_empty() -> None:
    cache = MagicMock()
    cache.get_lkg.return_value = None
    assert last_known_good(cache)(_tok()) is None
