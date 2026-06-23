"""Integration tests for PricingService — all 9 mandatory spec scenarios."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple
from unittest.mock import MagicMock

import fakeredis
import pytest

from lemon_ledger.clients.oracle import OraclePriceStale
from lemon_ledger.pricing.cache import PriceCache, _key
from lemon_ledger.pricing.service import PricingService
from lemon_ledger.pricing.types import PriceResult, PriceSource, TokenRow

# ── helpers ────────────────────────────────────────────────────────────────────


def _tok(
    symbol: str = "LEMX",
    tier: int = 1,
    chain: str = "lemonchain",
    category: str = "ecosystem-native",
) -> TokenRow:
    return TokenRow(
        token_id=f"{symbol.lower()}-id",
        symbol=symbol,
        category=category,
        contract_address="0x" + "a" * 40,
        chain=chain,
        tier=tier,
        decimals=18,
    )


class _F(NamedTuple):
    """Service + raw mocks so tests can make mock assertions without casts."""

    svc: PricingService
    oracle: MagicMock
    cg: MagicMock


def _make(
    token: TokenRow | None = None,
    oracle_price: Decimal | None = Decimal("0.042"),
    *,
    cg_price: Decimal | None = None,
    oracle_stale: bool = False,
    with_cache: bool = False,
    db_price: Decimal | None = None,
) -> _F:
    tok = token or _tok()

    registry = MagicMock()
    registry.get_by_id.return_value = tok
    registry.list_tier1_by_chain.return_value = [tok]
    registry.historical_price.return_value = db_price

    oracle: MagicMock = MagicMock()
    if oracle_stale:
        oracle.get_spot_price.side_effect = OraclePriceStale("stale")
        oracle.get_daily_average.return_value = None
    else:
        oracle.get_spot_price.return_value = oracle_price
        oracle.get_daily_average.return_value = oracle_price
    oracle.get_health.return_value = MagicMock(
        paused=False, emergency=False, seeding_complete=True, feeds_ok={"lemx": True}
    )

    cg: MagicMock = MagicMock()
    cg.coin_price_usd.return_value = cg_price

    cache = PriceCache(fakeredis.FakeRedis()) if with_cache else None
    svc = PricingService(registry=registry, oracle=oracle, coingecko=cg, cache=cache)
    return _F(svc=svc, oracle=oracle, cg=cg)


# ── Spec scenario 1: LEMX current price served from fresh cache ────────────────


def test_lemx_current_price_cache_hit_skips_sources() -> None:
    """Fresh cache hit must be returned without calling oracle or CG."""
    f = _make(with_cache=True)
    assert isinstance(f.svc._cache, PriceCache)
    f.svc._cache.set(
        "lemonchain",
        "lemx-id",
        PriceResult(price_usd=Decimal("0.099"), source=PriceSource.ORACLE),
    )
    price = f.svc.get_current_price("lemonchain", "lemx-id")
    assert price == Decimal("0.099")
    f.oracle.get_spot_price.assert_not_called()


# ── Spec scenario 2: LEMX current price, cache miss → oracle primary ──────────


def test_lemx_current_price_resolves_via_oracle() -> None:
    price = _make(oracle_price=Decimal("0.042")).svc.get_current_price("lemonchain", "lemx-id")
    assert price == Decimal("0.042")


# ── Spec scenario 3: LEMX stale oracle → CoinGecko fallback ──────────────────


def test_lemx_stale_oracle_falls_back_to_coingecko() -> None:
    price = _make(oracle_stale=True, cg_price=Decimal("0.040")).svc.get_current_price(
        "lemonchain", "lemx-id"
    )
    assert price == Decimal("0.040")


# ── Spec scenario 4: stale result served for current-price, blocked for FMV ──


def test_lkg_served_for_current_price_not_for_fmv() -> None:
    """
    When all live sources fail, current_price serves LKG (stale=True).
    The same call via get_historical_price (same-day FMV) must return None.
    """
    f = _make(oracle_stale=True, cg_price=None, with_cache=True)
    assert isinstance(f.svc._cache, PriceCache)

    # Manually plant an LKG entry
    f.svc._cache._r.set(
        _key("lkg", "lemonchain", "lemx-id"),
        f"0.035|{PriceSource.ORACLE}",
        ex=PriceCache.LKG_TTL,
    )

    current = f.svc.get_current_price("lemonchain", "lemx-id")
    assert current == Decimal("0.035")

    # Same-day FMV: stale MUST NOT be substituted
    now_ts = datetime.now(UTC).timestamp()
    fmv = f.svc.get_historical_price("lemonchain", "lemx-id", now_ts)
    assert fmv is None


# ── Spec scenario 5: LUSD always returns $1.00 ────────────────────────────────


def test_lusd_always_returns_peg() -> None:
    lusd = _tok("LUSD", category="ecosystem-stablecoin")
    price = _make(token=lusd, oracle_price=Decimal("0.97")).svc.get_current_price(
        "lemonchain", "lusd-id"
    )
    assert price == Decimal("1.00")


# ── Spec scenario 6: historical price returns DB value for past day ───────────


def test_historical_price_past_day_returns_db_price() -> None:
    past_ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    price = _make(db_price=Decimal("0.031")).svc.get_historical_price(
        "lemonchain", "lemx-id", past_ts
    )
    assert price == Decimal("0.031")


# ── Spec scenario 7: historical price returns None when DB misses ─────────────


def test_historical_price_past_day_db_miss_returns_none() -> None:
    past_ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    price = _make(db_price=None).svc.get_historical_price("lemonchain", "lemx-id", past_ts)
    assert price is None


# ── Spec scenario 8: bsc chain raises NotImplementedError ─────────────────────


def test_bsc_chain_raises_not_implemented() -> None:
    f = _make(token=_tok("LEMX", chain="bsc"))
    with pytest.raises(NotImplementedError, match="bsc"):
        f.svc.get_current_price("bsc", "lemx-id")


# ── Spec scenario 9: negative cache suppresses source calls ───────────────────


def test_negative_cache_suppresses_source_resolution() -> None:
    """Once a negative entry is cached, sources must not be called again."""
    f = _make(oracle_price=None, oracle_stale=True, cg_price=None, with_cache=True)

    # First call: sources exhausted → negative cached
    assert f.svc.get_current_price("lemonchain", "lemx-id") is None

    f.oracle.get_spot_price.reset_mock()
    f.cg.coin_price_usd.reset_mock()

    # Second call: negative cache hit → no source calls
    assert f.svc.get_current_price("lemonchain", "lemx-id") is None
    f.oracle.get_spot_price.assert_not_called()
    f.cg.coin_price_usd.assert_not_called()


# ── Additional: is_priceable ──────────────────────────────────────────────────


def test_is_priceable_tier1_lemonchain() -> None:
    assert _make().svc.is_priceable("lemonchain", "lemx-id") is True


def test_is_priceable_bsc_always_false() -> None:
    assert _make().svc.is_priceable("bsc", "lemx-id") is False


def test_is_priceable_unknown_token_false() -> None:
    registry = MagicMock()
    registry.get_by_id.return_value = None
    oracle: MagicMock = MagicMock()
    cg: MagicMock = MagicMock()
    svc = PricingService(registry=registry, oracle=oracle, coingecko=cg)
    assert svc.is_priceable("lemonchain", "no-such-token") is False


# ── Additional: health_check ──────────────────────────────────────────────────


def test_health_check_returns_report() -> None:
    report = _make(cg_price=Decimal("0.042")).svc.health_check()
    assert report.oracle_paused is False
    assert report.oracle_seeding_complete is True
    assert report.coingecko_ok is True
