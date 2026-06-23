"""Cache tests — fakeredis-backed, no Docker required."""

from decimal import Decimal

import fakeredis

from lemon_ledger.pricing.cache import PriceCache
from lemon_ledger.pricing.types import PriceResult, PriceSource


def _cache() -> PriceCache:
    return PriceCache(fakeredis.FakeRedis())


CHAIN = "lemonchain"
TID = "lemx-id"


# ── round-trip ─────────────────────────────────────────────────────────────────


def test_cache_round_trip_preserves_decimal_exactly() -> None:
    """str(Decimal) → cache → Decimal(...) must not introduce float drift."""
    cache = _cache()
    original = PriceResult(
        price_usd=Decimal("0.04200000000000000123"),
        source=PriceSource.ORACLE,
    )
    cache.set(CHAIN, TID, original)

    fresh = cache.get_fresh(CHAIN, TID)
    assert fresh is not None
    assert fresh.price_usd == original.price_usd
    assert isinstance(fresh.price_usd, Decimal)
    assert str(fresh.price_usd) == str(original.price_usd)


def test_set_writes_both_fresh_and_lkg() -> None:
    cache = _cache()
    result = PriceResult(price_usd=Decimal("1.00"), source=PriceSource.STABLE_PEG)
    cache.set(CHAIN, TID, result)

    assert cache.get_fresh(CHAIN, TID) is not None
    assert cache.get_lkg(CHAIN, TID) is not None


def test_get_fresh_returns_stale_false() -> None:
    cache = _cache()
    cache.set(CHAIN, TID, PriceResult(price_usd=Decimal("1"), source=PriceSource.ORACLE))
    fresh = cache.get_fresh(CHAIN, TID)
    assert fresh is not None
    assert fresh.stale is False


def test_get_lkg_forces_stale_true() -> None:
    cache = _cache()
    cache.set(CHAIN, TID, PriceResult(price_usd=Decimal("2"), source=PriceSource.ORACLE))
    lkg = cache.get_lkg(CHAIN, TID)
    assert lkg is not None
    assert lkg.stale is True


def test_get_fresh_miss_returns_none() -> None:
    cache = _cache()
    assert cache.get_fresh(CHAIN, "unknown") is None


def test_get_lkg_miss_returns_none() -> None:
    cache = _cache()
    assert cache.get_lkg(CHAIN, "unknown") is None


# ── negative cache ─────────────────────────────────────────────────────────────


def test_negative_cache_round_trip() -> None:
    cache = _cache()
    assert cache.get_negative(CHAIN, TID) is False
    cache.set_negative(CHAIN, TID)
    assert cache.get_negative(CHAIN, TID) is True


# ── single-flight lock ─────────────────────────────────────────────────────────


def test_single_flight_lock_acquired_once() -> None:
    cache = _cache()
    assert cache.acquire_lock(CHAIN, TID) is True


def test_single_flight_lock_second_acquire_fails() -> None:
    """Second acquire must fail while lock is held — prevents CoinGecko stampede."""
    cache = _cache()
    assert cache.acquire_lock(CHAIN, TID) is True
    assert cache.acquire_lock(CHAIN, TID) is False  # already held


def test_single_flight_lock_released_allows_re_acquire() -> None:
    cache = _cache()
    cache.acquire_lock(CHAIN, TID)
    cache.release_lock(CHAIN, TID)
    assert cache.acquire_lock(CHAIN, TID) is True


# ── source enum round-trip ─────────────────────────────────────────────────────


def test_cache_round_trip_all_sources() -> None:
    for source in PriceSource:
        cache = _cache()
        result = PriceResult(price_usd=Decimal("1"), source=source)
        cache.set(CHAIN, f"tok-{source}", result)
        fresh = cache.get_fresh(CHAIN, f"tok-{source}")
        assert fresh is not None
        assert fresh.source == source
