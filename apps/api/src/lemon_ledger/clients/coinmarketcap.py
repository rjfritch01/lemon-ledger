"""CoinMarketCap price client — secondary external price source for LEMX.

Scope: prices LEMX and Tier-2 tokens by CMC numeric ID.  The 19 L2
ecosystem tokens are unlisted and have no CMC entry; callers must NOT
pass them here.

This client requires a valid CMC_API_KEY.  Use it as a fallback when
CoinGecko is unavailable or stale.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

_BASE = "https://pro-api.coinmarketcap.com"


class PricerTransientError(Exception):
    """Retryable transient error from the CMC API (5xx, timeout)."""


class PricerRateLimited(PricerTransientError):
    """HTTP 429 from the CMC API."""


class CoinMarketCapClient:
    """Read-only CoinMarketCap REST client (secondary price source).

    Callers own the httpx.Client so connection pooling and timeouts are
    configured centrally.
    """

    def __init__(
        self,
        *,
        http: httpx.Client,
        api_key: str,
        max_retries: int = 5,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._max_retries = max_retries

    # ── internals ─────────────────────────────────────────────────────────────

    def _request(self, path: str, params: dict[str, str]) -> Any:
        """Single HTTP GET — maps status codes to exceptions or parsed JSON."""
        headers: dict[str, str] = {"X-CMC_PRO_API_KEY": self._api_key}
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

    def quote_usd(self, cmc_id: int | str) -> Decimal | None:
        """Current USD price for a CMC numeric ID.

        Returns None if the ID is unknown or absent from the response.
        All arithmetic uses Decimal(str(...)) — never float.
        """
        data = self._get(
            "/v2/cryptocurrency/quotes/latest",
            {"id": str(cmc_id)},
        )
        if not isinstance(data, dict):
            return None
        try:
            price = data["data"][str(cmc_id)]["quote"]["USD"]["price"]
        except (KeyError, TypeError):
            return None
        return Decimal(str(price))
