"""Declarative price-source factories for the pricing cascade.

Each factory takes the required client(s) and returns a ``Source`` callable
``Callable[[TokenRow], PriceResult | None]`` that the cascade walks in order.

Conventions
-----------
- A source returns None on any failure (stale, unsupported, network error).
- Sources never raise — all errors are absorbed and logged.
- The oracle source for LEMX performs a cross-validation side-check against
  CoinGecko; it always RETURNS THE ORACLE VALUE (CG is informational only).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal

from lemon_ledger.clients.coingecko import CoinGeckoClient
from lemon_ledger.clients.coinmarketcap import CoinMarketCapClient
from lemon_ledger.clients.oracle import OracleClient, OraclePriceStale, OracleTokenNotSupported
from lemon_ledger.pricing.cache import PriceCacheProtocol
from lemon_ledger.pricing.external_ids import LEMX_CMC_ID, LEMX_COINGECKO_ID
from lemon_ledger.pricing.types import PriceResult, PriceSource, TokenRow

log = logging.getLogger(__name__)

Source = Callable[[TokenRow], PriceResult | None]

_LUSD_PEG = Decimal("1.00")
_DEPEG_THRESHOLD = Decimal("0.02")  # 2%
_LEMX_DIVERGENCE_THRESHOLD = Decimal("0.05")  # 5%


# ── Oracle sources ─────────────────────────────────────────────────────────────


def oracle_spot(oracle: OracleClient) -> Source:
    """Current spot price; returns None on stale or unsupported."""

    def _fn(token: TokenRow) -> PriceResult | None:
        try:
            price = oracle.get_spot_price(token)
        except (OraclePriceStale, OracleTokenNotSupported):
            return None
        return PriceResult(price_usd=price, source=PriceSource.ORACLE)

    return _fn


def oracle_daily_avg(oracle: OracleClient) -> Source:
    """FMV daily-average proxy; returns None if price is 0 or unsupported."""

    def _fn(token: TokenRow) -> PriceResult | None:
        price = oracle.get_daily_average(token)
        if price is None:
            return None
        return PriceResult(price_usd=price, source=PriceSource.ORACLE)

    return _fn


def lemx_oracle_crossval(oracle: OracleClient, cg: CoinGeckoClient) -> Source:
    """Oracle spot for LEMX with CoinGecko cross-validation side-check.

    If oracle and lemon-2 diverge >5%, logs a WARNING but always returns
    the oracle value.  CoinGecko divergence is informational only.
    """

    def _fn(token: TokenRow) -> PriceResult | None:
        try:
            oracle_price = oracle.get_spot_price(token)
        except (OraclePriceStale, OracleTokenNotSupported):
            return None

        # Cross-validate against CoinGecko (side-effect: warning only).
        try:
            cg_price = cg.coin_price_usd(LEMX_COINGECKO_ID)
            if cg_price is not None and oracle_price > Decimal(0):
                divergence = abs(oracle_price - cg_price) / oracle_price
                if divergence > _LEMX_DIVERGENCE_THRESHOLD:
                    log.warning(
                        "LEMX oracle/CoinGecko divergence: oracle=%s cg=%s (%.1f%%)",
                        oracle_price,
                        cg_price,
                        float(divergence * 100),
                    )
        except Exception:  # nosec B110 — CG cross-val must never surface to caller
            pass

        return PriceResult(price_usd=oracle_price, source=PriceSource.ORACLE)

    return _fn


# ── External sources ───────────────────────────────────────────────────────────


def coingecko_lemon2(cg: CoinGeckoClient) -> Source:
    """CoinGecko spot price for LEMX (coin_id="lemon-2")."""

    def _fn(token: TokenRow) -> PriceResult | None:
        price = cg.coin_price_usd(LEMX_COINGECKO_ID)
        if price is None:
            return None
        return PriceResult(price_usd=price, source=PriceSource.COINGECKO)

    return _fn


def cmc_lemon(cmc: CoinMarketCapClient | None) -> Source:
    """CoinMarketCap spot price for LEMX. Returns None if CMC is unconfigured."""

    def _fn(token: TokenRow) -> PriceResult | None:
        if cmc is None or LEMX_CMC_ID is None:
            return None
        price = cmc.quote_usd(LEMX_CMC_ID)
        if price is None:
            return None
        return PriceResult(price_usd=price, source=PriceSource.COINMARKETCAP)

    return _fn


def stable_peg(oracle: OracleClient) -> Source:
    """Always returns $1.00 for USD-pegged stablecoins (e.g. LUSD).

    The oracle is read as a depeg monitor: if the spot price diverges >2% from
    the peg, a WARNING is logged.  The peg value is returned regardless.
    """

    def _fn(token: TokenRow) -> PriceResult | None:
        try:
            oracle_price = oracle.get_spot_price(token)
            divergence = abs(oracle_price - _LUSD_PEG) / _LUSD_PEG
            if divergence > _DEPEG_THRESHOLD:
                log.warning(
                    "Depeg alert for %s: oracle=%s diverges %.1f%% from $1.00 peg",
                    token.symbol,
                    oracle_price,
                    float(divergence * 100),
                )
        except Exception:  # nosec B110 — oracle depeg check must never block peg return
            pass
        return PriceResult(price_usd=_LUSD_PEG, source=PriceSource.STABLE_PEG)

    return _fn


def last_known_good(cache: PriceCacheProtocol) -> Source:
    """Serve the last-known-good cached price (stale=True) as a tail fallback."""

    def _fn(token: TokenRow) -> PriceResult | None:
        return cache.get_lkg(token.chain, token.token_id)  # stale=True forced by get_lkg

    return _fn
