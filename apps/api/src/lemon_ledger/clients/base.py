from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from lemon_ledger.domain.chains import Chain


@runtime_checkable
class ChainClient(Protocol):
    """Structural interface satisfied by all chain explorer clients."""

    chain: Chain

    def get_latest_block(self) -> int: ...

    def get_transactions(
        self,
        address: str,
        *,
        start_block: int = ...,
        end_block: int | None = ...,
        sort: str = ...,
    ) -> Iterator[dict[str, str]]: ...

    def get_token_transfers(
        self,
        address: str,
        *,
        start_block: int = ...,
        end_block: int | None = ...,
        sort: str = ...,
    ) -> Iterator[dict[str, str]]: ...

    def get_internal_transactions(
        self,
        address: str,
        *,
        start_block: int = ...,
        end_block: int | None = ...,
        sort: str = ...,
    ) -> Iterator[dict[str, str]]: ...

    def get_logs(
        self,
        address: str,
        *,
        from_block: int,
        to_block: int | str = ...,
        topic0: str | None = ...,
    ) -> list[dict[str, str]]: ...

    def get_block_by_time(
        self,
        dt: datetime,
        closest: Literal["before", "after"] = ...,
    ) -> int: ...
