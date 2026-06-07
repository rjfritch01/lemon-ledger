import time
from collections.abc import Iterator
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from lemon_ledger.clients.envelope import parse_list_envelope
from lemon_ledger.clients.exceptions import (
    BlockscoutResponseError,
    BlockscoutTransientError,
    BlockscoutWindowExceeded,
)
from lemon_ledger.clients.rate_limit import RateLimiter
from lemon_ledger.config import Settings

_ETHERSCAN_MAX_RESULTS = 10_000


class BlockscoutClient:
    """Read-only Etherscan-compatible Blockscout API client.

    Pass a pre-constructed httpx.Client so callers control connection pooling
    and timeouts.  The rate_limiter is shared across instances for the same
    host so chains have independent buckets.
    """

    def __init__(
        self,
        base_url: str,
        *,
        http: httpx.Client,
        rate_limiter: RateLimiter,
        api_key: str | None = None,
        page_size: int = 1000,
        max_retries: int = 5,
    ) -> None:
        self._base_url = base_url
        self._http = http
        self._rate_limiter = rate_limiter
        self._api_key = api_key
        self._page_size = page_size
        self._max_retries = max_retries

    # ── internal ──────────────────────────────────────────────────────────────

    def _fetch_raw(self, params: dict[str, str]) -> Any:
        """Single HTTP GET — maps transport/HTTP errors to the exception hierarchy."""
        self._rate_limiter.acquire()
        if self._api_key:
            params = {**params, "apikey": self._api_key}
        try:
            resp = self._http.get(self._base_url, params=params)
        except httpx.TimeoutException as exc:
            raise BlockscoutTransientError(f"Request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise BlockscoutTransientError(f"Transport error: {exc}") from exc

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    time.sleep(float(retry_after))
                except ValueError:
                    pass
            raise BlockscoutTransientError("Rate limited (HTTP 429)")

        if resp.status_code >= 500:
            raise BlockscoutTransientError(f"Server error: HTTP {resp.status_code}")

        if resp.status_code >= 400:
            raise BlockscoutResponseError(f"Client error: HTTP {resp.status_code}")

        return resp.json()

    def _get(self, params: dict[str, str]) -> Any:
        """HTTP GET with exponential-backoff+jitter retry on transient errors."""
        for attempt in Retrying(
            retry=retry_if_exception_type(BlockscoutTransientError),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(initial=1, exp_base=2, max=60),
            reraise=True,
        ):
            with attempt:
                return self._fetch_raw(params)
        raise AssertionError("unreachable — Retrying with reraise=True always raises on exhaustion")

    def _paginate(
        self,
        base_params: dict[str, str],
        start_block: int,
        end_block: int | None,
        sort: str,
    ) -> Iterator[dict[str, str]]:
        """Page/offset walk over the Etherscan-compatible list endpoints.

        Stops when a short page is returned.  Raises BlockscoutWindowExceeded
        before requesting a page that would exceed the 10k result window.
        """
        page = 1
        while True:
            if page * self._page_size > _ETHERSCAN_MAX_RESULTS:
                raise BlockscoutWindowExceeded(
                    f"Result window ({_ETHERSCAN_MAX_RESULTS}) exceeded at page {page} "
                    f"(page_size={self._page_size}). Narrow the block range."
                )
            params: dict[str, str] = {
                **base_params,
                "startblock": str(start_block),
                "endblock": str(end_block) if end_block is not None else "99999999",
                "sort": sort,
                "page": str(page),
                "offset": str(self._page_size),
            }
            payload = self._get(params)
            rows = parse_list_envelope(payload)
            yield from rows
            if len(rows) < self._page_size:
                break
            page += 1

    # ── public API ────────────────────────────────────────────────────────────

    def get_latest_block(self) -> int:
        """Return the current chain head as an integer block number."""
        payload = self._get({"module": "block", "action": "eth_block_number"})
        if not isinstance(payload, dict):
            raise BlockscoutResponseError(
                f"Expected dict for eth_block_number, got {type(payload).__name__}"
            )
        result = payload.get("result")
        if not isinstance(result, str):
            raise BlockscoutResponseError(
                f"Expected hex string for block number, got {type(result).__name__}"
            )
        return int(result, 16)

    def get_transactions(
        self,
        address: str,
        *,
        start_block: int = 0,
        end_block: int | None = None,
        sort: str = "asc",
    ) -> Iterator[dict[str, str]]:
        return self._paginate(
            {"module": "account", "action": "txlist", "address": address.lower()},
            start_block,
            end_block,
            sort,
        )

    def get_token_transfers(
        self,
        address: str,
        *,
        start_block: int = 0,
        end_block: int | None = None,
        sort: str = "asc",
    ) -> Iterator[dict[str, str]]:
        return self._paginate(
            {"module": "account", "action": "tokentx", "address": address.lower()},
            start_block,
            end_block,
            sort,
        )

    def get_internal_transactions(
        self,
        address: str,
        *,
        start_block: int = 0,
        end_block: int | None = None,
        sort: str = "asc",
    ) -> Iterator[dict[str, str]]:
        return self._paginate(
            {"module": "account", "action": "txlistinternal", "address": address.lower()},
            start_block,
            end_block,
            sort,
        )

    def get_logs(
        self,
        address: str,
        *,
        from_block: int,
        to_block: int | str = "latest",
        topic0: str | None = None,
    ) -> list[dict[str, str]]:
        """Single-call log fetch (no pagination — caller must narrow block range)."""
        params: dict[str, str] = {
            "module": "logs",
            "action": "getLogs",
            "address": address.lower(),
            "fromBlock": str(from_block),
            "toBlock": str(to_block),
        }
        if topic0 is not None:
            params["topic0"] = topic0
        payload = self._get(params)
        return parse_list_envelope(payload)


def build_blockscout_client(
    chain: str,
    settings: Settings,
    *,
    http: httpx.Client,
    rate_limiter: RateLimiter,
) -> BlockscoutClient:
    """Resolve chain → base_url and construct a BlockscoutClient.

    api_key is intentionally None for Lemonchain (no key required).
    """
    if chain == "lemonchain":
        base_url = settings.explorer_lemonchain_url
    elif chain == "lemonchain-testnet":
        base_url = settings.explorer_lemonchain_testnet_url
    else:
        raise ValueError(f"Unsupported chain for Blockscout client: {chain!r}")

    return BlockscoutClient(
        base_url,
        http=http,
        rate_limiter=rate_limiter,
        api_key=None,
        page_size=settings.explorer_page_size,
        max_retries=settings.explorer_max_retries,
    )
