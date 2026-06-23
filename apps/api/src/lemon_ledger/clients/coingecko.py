"""CoinGecko price client — primary external price source for LEMX.

Scope: prices LEMX (coin_id "lemon-2") and, when a platform mapping exists,
Tier-2 tokens by contract address.  The 19 L2 ecosystem tokens are unlisted
and have no CoinGecko entry; callers must NOT pass them here.

token_price_usd is built but dormant this phase: there is no Lemonchain
platform on CoinGecko, so any lemonchain contract lookup returns None cleanly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

_BASE = "https://api.coingecko.com/api/v3"

# Map our internal chain name to CoinGecko's platform identifier.
# Lemonchain is intentionally absent — no CoinGecko platform exists yet.
_PLATFORM_IDS: dict[str, str] = {
    "bsc": "binance-smart-chain",
}


class PricerTransientError(Exception):
    """Retryable transient error from the CoinGecko API (5xx, timeout)."""


class PricerRateLimited(PricerTransientError):
    """HTTP 429 from the CoinGecko API."""


class CoinGeckoClient:
    """Read-only CoinGecko REST client.

    Callers own the httpx.Client so connection pooling and timeouts are
    configured centrally.  Pass api_key to use the Demo tier; omit for
    the public (lower-rate) tier.
    """

    def __init__(
        self,
        *,
        http: httpx.Client,
        api_key: str | None = None,
        max_retries: int = 5,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._max_retries = max_retries

    # ── internals ─────────────────────────────────────────────────────────────

    def _request(self, path: str, params: dict[str, str]) -> Any:
        """Single HTTP GET — maps status codes to exceptions or parsed JSON."""
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key

        try:
            resp = self._http.get(f"{_BASE}{path}", params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise PricerTransientError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise PricerTransientError(str(exc)) from exc

        if resp.status_code == 429:
            raise PricerRateLimited("rate limited (HTTP 429)")
        if resp.status_code >= 500:
            raise PricerTransientError(f"server error HTTP {resp.status_code}")
        if resp.status_code >= 400:
            return None
        return resp.json()

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """HTTP GET with exponential-backoff+jitter retry on transient errors."""
        _params: dict[str, str] = params or {}
        for attempt in Retrying(
            retry=retry_if_exception_type(PricerTransientError),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(initial=1, exp_base=2, max=60),
            reraise=True,
        ):
            with attempt:
                return self._request(path, _params)
        raise AssertionError("unreachable")  # pragma: no cover

    # ── public API ────────────────────────────────────────────────────────────

    def coin_price_usd(self, coin_id: str) -> Decimal | None:
        """Current USD price for a CoinGecko coin_id.

        Returns None if the coin_id is unknown or absent from the response.
        All arithmetic uses Decimal(str(...)) — never float.
        """
        data = self._get("/simple/price", {"ids": coin_id, "vs_currencies": "usd"})
        if not isinstance(data, dict):
            return None
        entry = data.get(coin_id)
        if not isinstance(entry, dict):
            return None
        usd = entry.get("usd")
        if usd is None:
            return None
        return Decimal(str(usd))

    def coin_history_usd(self, coin_id: str, day: date) -> Decimal | None:
        """Historical USD price for a CoinGecko coin_id on a given calendar day.

        CoinGecko expects the date in DD-MM-YYYY format.
        Returns None if market_data is absent (e.g. coin existed but had no price).
        """
        date_str = day.strftime("%d-%m-%Y")
        data = self._get(f"/coins/{coin_id}/history", {"date": date_str})
        if not isinstance(data, dict):
            return None
        try:
            usd = data["market_data"]["current_price"]["usd"]
        except (KeyError, TypeError):
            return None
        return Decimal(str(usd))

    def token_price_usd(self, platform: str, contract: str) -> Decimal | None:
        """USD price for a token by contract address on a given platform.

        DORMANT for Lemonchain: there is no 'lemonchain' entry in _PLATFORM_IDS,
        so any lemonchain contract lookup returns None immediately without an
        API call.  This method becomes live when BSC unparks.
        """
        cg_platform = _PLATFORM_IDS.get(platform)
        if cg_platform is None:
            return None
        data = self._get(
            f"/simple/token_price/{cg_platform}",
            {"contract_addresses": contract.lower(), "vs_currencies": "usd"},
        )
        if not isinstance(data, dict):
            return None
        entry = data.get(contract.lower())
        if not isinstance(entry, dict):
            return None
        usd = entry.get("usd")
        if usd is None:
            return None
        return Decimal(str(usd))
