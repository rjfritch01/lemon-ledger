"""PricingService — SOW §3.6 public interface.

Cascade ordering (per token class)
-----------------------------------
LUSD     → [stable_peg]
LEMX     → [lemx_oracle_crossval*, coingecko_lemon2, cmc_lemon, ?lkg]
             * current price only; FMV uses oracle_daily_avg directly
WLEMX    → [oracle_src, ?lkg]        # NO 1:1 LEMX proxy
else/L2  → [oracle_src, ?lkg]        # oracle is sole live source for the 19 L2s
             NOTE: HEXDEX re-inserts right after oracle_src when it launches.

?lkg appears only when allow_stale=True AND NOT fmv.

Consumer-aware staleness
------------------------
get_current_price: allow_stale=True  — MAY serve a flagged-stale LKG.
get_historical_price (same day): fmv=True — MUST NOT substitute stale; returns None.
get_historical_price (past day):  hits DB, then _historical_live_fallback → None (backfill PR).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from lemon_ledger.clients.coingecko import CoinGeckoClient
from lemon_ledger.clients.coinmarketcap import CoinMarketCapClient
from lemon_ledger.clients.oracle import OracleClient
from lemon_ledger.pricing import sources as src
from lemon_ledger.pricing.cache import PriceCache, PriceCacheProtocol, _NullCache
from lemon_ledger.pricing.external_ids import LEMX_COINGECKO_ID
from lemon_ledger.pricing.types import (
    PriceResult,
    PricingHealthReport,
    TokenInfo,
    TokenRegistryRepo,
    TokenRow,
)

log = logging.getLogger(__name__)

_HEALTH_CACHE_TTL = 30.0  # seconds


def _day_start(ts: float) -> date:
    """UTC calendar day that contains the given Unix timestamp."""
    return datetime.fromtimestamp(ts, tz=UTC).date()


class PricingService:
    """Declarative price cascade with three-tier Redis cache.

    Inject all external dependencies; this class owns NO database sessions
    and makes NO direct Redis calls (delegated to PriceCache).
    """

    def __init__(
        self,
        registry: TokenRegistryRepo,
        oracle: OracleClient,
        coingecko: CoinGeckoClient,
        cmc: CoinMarketCapClient | None = None,
        cache: PriceCache | None = None,
        historical_fallback: Callable[[str, str, date], Decimal | None] | None = None,
    ) -> None:
        self._registry = registry
        self._oracle = oracle
        self._cg = coingecko
        self._cmc = cmc
        self._cache: PriceCacheProtocol = cache if cache is not None else _NullCache()
        self._health_cache: PricingHealthReport | None = None
        self._health_cached_at: float = 0.0
        self._historical_fallback = historical_fallback

    # ── public interface ───────────────────────────────────────────────────────

    def get_current_price(self, chain: str, token_id: str) -> Decimal | None:
        """Current USD price. May serve a flagged-stale last-known-good."""
        return self._resolve(chain, token_id, allow_stale=True, fmv=False)

    def get_historical_price(self, chain: str, token_id: str, ts: float) -> Decimal | None:
        """FMV-safe historical price.

        Same-day requests resolve against live sources with fmv=True (no stale).
        Past-day requests hit the DB, then the live-fallback hook (→ None until
        the backfill PR wires it).
        """
        day = _day_start(ts)
        today = date.today()

        if day == today:
            # Same-day FMV: stale MUST NOT substitute
            return self._resolve(chain, token_id, allow_stale=False, fmv=True)

        # Past day: DB lookup
        db_price = self._registry.historical_price(chain, token_id, day)
        if db_price is not None:
            return db_price

        return self._historical_live_fallback(chain, token_id, day)

    def get_supported_tokens(self, chain: str) -> list[TokenInfo]:
        """Tier-1 tokens on this chain (no user_id filtering this phase)."""
        rows = self._registry.list_tier1_by_chain(chain)
        return [
            TokenInfo(
                token_id=row.token_id,
                chain=row.chain,
                symbol=row.symbol,
                category=row.category,
                tier=row.tier,
                is_priceable=self.is_priceable(row.chain, row.token_id),
            )
            for row in rows
        ]

    def is_priceable(self, chain: str, token_id: str) -> bool:
        """True if the token has at least one live price source."""
        row = self._registry.get_by_id(token_id)
        if row is None or chain == "bsc":
            return False
        # All Tier-1 Lemonchain tokens have at least the oracle source
        return row.tier == 1

    def health_check(self) -> PricingHealthReport:
        """Aggregate health of oracle and external sources. Cached ~30 s."""
        now = time.monotonic()
        if self._health_cache is not None and (now - self._health_cached_at) < _HEALTH_CACHE_TTL:
            return self._health_cache

        oracle_health = self._oracle.get_health()
        coingecko_ok = self._ping_coingecko()
        lemonchain_ok = bool(oracle_health.feeds_ok) and any(oracle_health.feeds_ok.values())
        rpc_ok = {"lemonchain": lemonchain_ok}

        report = PricingHealthReport(
            oracle_paused=oracle_health.paused,
            oracle_emergency=oracle_health.emergency,
            oracle_seeding_complete=oracle_health.seeding_complete,
            coingecko_ok=coingecko_ok,
            rpc_ok=rpc_ok,
        )
        self._health_cache = report
        self._health_cached_at = now
        return report

    # ── internals ─────────────────────────────────────────────────────────────

    def _sources_for(
        self,
        token: TokenRow,
        *,
        allow_stale: bool,
        fmv: bool,
    ) -> list[src.Source]:
        """Return the ordered source list for this token class and call context."""
        oracle_src = src.oracle_daily_avg(self._oracle) if fmv else src.oracle_spot(self._oracle)
        tail: list[src.Source] = (
            [] if (fmv or not allow_stale) else [src.last_known_good(self._cache)]
        )

        sym = token.symbol
        if sym == "LUSD":
            return [src.stable_peg(self._oracle)]
        if sym == "LEMX":
            # NOTE: HEXDEX re-inserts right after oracle_src when it launches.
            lemx_oracle: src.Source = (
                src.oracle_daily_avg(self._oracle)
                if fmv
                else src.lemx_oracle_crossval(self._oracle, self._cg)
            )
            return [
                lemx_oracle,
                src.coingecko_lemon2(self._cg),
                src.cmc_lemon(self._cmc),
                *tail,
            ]
        if sym == "WLEMX":
            # NO 1:1 LEMX proxy — oracle only
            return [oracle_src, *tail]
        # 19 L2 ecosystem tokens and everything else: oracle is the sole live source.
        return [oracle_src, *tail]

    def _resolve(
        self,
        chain: str,
        token_id: str,
        *,
        allow_stale: bool,
        fmv: bool,
    ) -> Decimal | None:
        # 1. Fresh cache hit
        fresh = self._cache.get_fresh(chain, token_id)
        if fresh is not None:
            return fresh.price_usd

        # 2. Negative cache hit — we already know there's no price
        if self._cache.get_negative(chain, token_id):
            return None

        # 3. Token lookup
        token = self._registry.get_by_id(token_id)
        if token is None:
            return None
        if chain == "bsc":
            raise NotImplementedError(f"bsc token pricing not implemented: {token_id!r}")

        # 4. Single-flight lock: one caller resolves; concurrent callers serve LKG.
        if not self._cache.acquire_lock(chain, token_id):
            lkg = self._cache.get_lkg(chain, token_id)
            return lkg.price_usd if lkg is not None else None

        try:
            srcs = self._sources_for(token, allow_stale=allow_stale, fmv=fmv)
            result: PriceResult | None = None
            for source in srcs:
                result = self._safe(source, token)
                if result is not None:
                    break

            if result is not None:
                if not result.stale:
                    self._cache.set(chain, token_id, result)
                return result.price_usd
            else:
                self._cache.set_negative(chain, token_id)
                return None
        finally:
            self._cache.release_lock(chain, token_id)

    def _safe(self, source: src.Source, token: TokenRow) -> PriceResult | None:
        """Call a source, absorbing any exception so one bad source never aborts the walk."""
        try:
            return source(token)
        except Exception:
            log.exception("Price source error for %s/%s", token.chain, token.symbol)
            return None

    def _historical_live_fallback(self, chain: str, token_id: str, day: date) -> Decimal | None:
        """On-demand event-log fetch when a past-day price is not in the DB.

        Wired at construction time via historical_fallback= (historical_backfill.fetch_day).
        Returns None if not wired (default for tests that don't need backfill).
        """
        if self._historical_fallback is None:
            return None
        return self._historical_fallback(chain, token_id, day)

    def _ping_coingecko(self) -> bool:
        """Quick CoinGecko connectivity check."""
        try:
            return self._cg.coin_price_usd(LEMX_COINGECKO_ID) is not None
        except Exception:
            return False
