"""Registry and ChainClient Protocol conformance tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from lemon_ledger.clients.base import ChainClient
from lemon_ledger.clients.blockscout import BlockscoutClient
from lemon_ledger.clients.rate_limit import NullRateLimiter
from lemon_ledger.clients.registry import build_chain_client
from lemon_ledger.config import Settings
from lemon_ledger.domain.chains import Chain
from lemon_ledger.worker import Resources


def _mock_resources() -> Resources:
    m = MagicMock()
    m.redis = MagicMock()
    m.http = httpx.Client()
    return m


# ── registry dispatch ─────────────────────────────────────────────────────────


@patch("lemon_ledger.clients.registry.RedisTokenBucket")
def test_build_chain_client_lemonchain_returns_blockscout(mock_bucket: Any) -> None:
    res = _mock_resources()
    client = build_chain_client(Chain.LEMONCHAIN, res, Settings())
    assert isinstance(client, BlockscoutClient)


def test_build_chain_client_bsc_raises_not_implemented() -> None:
    res = _mock_resources()
    with pytest.raises(NotImplementedError, match="BSC"):
        build_chain_client(Chain.BSC, res, Settings())


# ── Protocol conformance ──────────────────────────────────────────────────────


def test_blockscout_client_satisfies_chain_client_protocol() -> None:
    client = BlockscoutClient("http://test", http=httpx.Client(), rate_limiter=NullRateLimiter())
    assert isinstance(client, ChainClient)


def test_fake_chain_client_satisfies_protocol() -> None:
    """Inline minimal fake must satisfy the ChainClient Protocol."""

    class _MinimalFake:
        chain: Chain = Chain.LEMONCHAIN

        def get_latest_block(self) -> int:
            return 0

        def get_transactions(
            self,
            address: str,
            *,
            start_block: int = 0,
            end_block: int | None = None,
            sort: str = "asc",
        ) -> Iterator[dict[str, str]]:
            return iter([])

        def get_token_transfers(
            self,
            address: str,
            *,
            start_block: int = 0,
            end_block: int | None = None,
            sort: str = "asc",
        ) -> Iterator[dict[str, str]]:
            return iter([])

        def get_internal_transactions(
            self,
            address: str,
            *,
            start_block: int = 0,
            end_block: int | None = None,
            sort: str = "asc",
        ) -> Iterator[dict[str, str]]:
            return iter([])

        def get_logs(
            self,
            address: str,
            *,
            from_block: int,
            to_block: int | str = "latest",
            topic0: str | None = None,
        ) -> list[dict[str, str]]:
            return []

    assert isinstance(_MinimalFake(), ChainClient)


def test_chain_client_chain_attribute_lemonchain() -> None:
    client = BlockscoutClient("http://test", http=httpx.Client(), rate_limiter=NullRateLimiter())
    assert client.chain == Chain.LEMONCHAIN


# ── Static mypy conformance guard ─────────────────────────────────────────────
# mypy --strict checks this binding at import time; if BlockscoutClient ever
# diverges from ChainClient's Protocol, this function will produce a type error.


def _blockscout_satisfies_chain_client(client: BlockscoutClient) -> ChainClient:
    return client
