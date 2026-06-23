"""Three-tier Redis price cache.

Tiers
-----
fresh   — live price, short TTL (60 s). Get returns stale=False.
lkg     — last-known-good, long TTL (7 days). Get forces stale=True.
neg     — negative sentinel (no price found), very short TTL (30 s).

Single-flight lock
------------------
acquire_lock / release_lock use Redis SETNX + EX to guarantee that only one
caller does source resolution for a given (chain, token_id) pair at a time.
Concurrent callers that lose the race receive the LKG or None immediately.

Cache encoding
--------------
Each entry is stored as "price_decimal|source_name", where the price is the
str() of a Decimal (no float).  Reads reconstruct via Decimal(...) — zero
float drift through the cache boundary.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from lemon_ledger.pricing.types import PriceResult, PriceSource

_SEP = "|"
_FRESH = "fresh"
_LKG = "lkg"
_NEG = "neg"
_LOCK = "lock"


class PriceCacheProtocol(Protocol):
    """Interface satisfied by PriceCache and _NullCache."""

    def get_fresh(self, chain: str, token_id: str) -> PriceResult | None: ...
    def get_lkg(self, chain: str, token_id: str) -> PriceResult | None: ...
    def set(self, chain: str, token_id: str, result: PriceResult) -> None: ...
    def get_negative(self, chain: str, token_id: str) -> bool: ...
    def set_negative(self, chain: str, token_id: str) -> None: ...
    def acquire_lock(self, chain: str, token_id: str) -> bool: ...
    def release_lock(self, chain: str, token_id: str) -> None: ...


class _Redis(Protocol):
    """Minimal Redis interface the cache needs (compatible with redis-py)."""

    def get(self, name: str) -> bytes | None: ...
    def set(
        self,
        name: str,
        value: str | bytes | int | float,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None: ...
    def delete(self, *names: str) -> int: ...


def _key(kind: str, chain: str, token_id: str) -> str:
    return f"price:{kind}:{chain}:{token_id}"


def _encode(result: PriceResult) -> str:
    return f"{result.price_usd}{_SEP}{result.source}"


def _decode(raw: bytes, *, stale: bool) -> PriceResult:
    text = raw.decode()
    price_str, source_str = text.rsplit(_SEP, 1)
    return PriceResult(
        price_usd=Decimal(price_str),
        source=PriceSource(source_str),
        stale=stale,
    )


class PriceCache:
    """Redis-backed three-tier price cache.

    Prices are stored as str(Decimal) — reads are Decimal(...), never float.
    """

    FRESH_TTL: int = 60
    LKG_TTL: int = 7 * 86_400
    NEGATIVE_TTL: int = 30
    LOCK_TTL: int = 10

    def __init__(self, redis: Any) -> None:  # redis-py Redis[Any]
        self._r: _Redis = redis

    def get_fresh(self, chain: str, token_id: str) -> PriceResult | None:
        raw = self._r.get(_key(_FRESH, chain, token_id))
        return _decode(raw, stale=False) if raw is not None else None

    def get_lkg(self, chain: str, token_id: str) -> PriceResult | None:
        raw = self._r.get(_key(_LKG, chain, token_id))
        return _decode(raw, stale=True) if raw is not None else None

    def set(self, chain: str, token_id: str, result: PriceResult) -> None:
        """Write price to BOTH fresh (short TTL) and lkg (long TTL)."""
        encoded = _encode(result)
        self._r.set(_key(_FRESH, chain, token_id), encoded, ex=self.FRESH_TTL)
        self._r.set(_key(_LKG, chain, token_id), encoded, ex=self.LKG_TTL)

    def get_negative(self, chain: str, token_id: str) -> bool:
        return self._r.get(_key(_NEG, chain, token_id)) is not None

    def set_negative(self, chain: str, token_id: str) -> None:
        self._r.set(_key(_NEG, chain, token_id), "1", ex=self.NEGATIVE_TTL)

    def acquire_lock(self, chain: str, token_id: str) -> bool:
        """Try to acquire the single-flight lock. Returns True if acquired."""
        result = self._r.set(_key(_LOCK, chain, token_id), "1", nx=True, ex=self.LOCK_TTL)
        return bool(result)

    def release_lock(self, chain: str, token_id: str) -> None:
        self._r.delete(_key(_LOCK, chain, token_id))


class _NullCache:
    """No-op cache for use when no Redis is available (tests, dev)."""

    def get_fresh(self, chain: str, token_id: str) -> PriceResult | None:
        return None

    def get_lkg(self, chain: str, token_id: str) -> PriceResult | None:
        return None

    def set(self, chain: str, token_id: str, result: PriceResult) -> None:
        pass

    def get_negative(self, chain: str, token_id: str) -> bool:
        return False

    def set_negative(self, chain: str, token_id: str) -> None:
        pass

    def acquire_lock(self, chain: str, token_id: str) -> bool:
        return True

    def release_lock(self, chain: str, token_id: str) -> None:
        pass
