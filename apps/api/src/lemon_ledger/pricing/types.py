from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class PriceSource(StrEnum):
    ORACLE = "oracle"
    COINGECKO = "coingecko"
    COINMARKETCAP = "coinmarketcap"
    STABLE_PEG = "stable_peg"
    LAST_KNOWN_GOOD = "last_known_good"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class PriceResult:
    price_usd: Decimal
    source: PriceSource
    stale: bool = False


@dataclass
class PricingHealthReport:
    oracle_paused: bool
    oracle_emergency: bool
    oracle_seeding_complete: bool
    coingecko_ok: bool
    rpc_ok: dict[str, bool]


@dataclass
class TokenInfo:
    token_id: str
    chain: str
    symbol: str
    category: str
    tier: int
    is_priceable: bool


@dataclass
class TokenRow:
    """Lightweight projection of TokenRegistry used by the pricing layer.

    The token_registry repository converts ORM rows to this dataclass so the
    pricing layer stays decoupled from SQLAlchemy.
    """

    token_id: str
    symbol: str
    category: str
    contract_address: str | None
    chain: str
    tier: int
    decimals: int


class TokenRegistryRepo(Protocol):
    """Injectable repository — pricing layer never queries the DB directly."""

    def get_by_id(self, token_id: str) -> TokenRow | None: ...

    def historical_price(self, chain: str, token_id: str, day: date) -> Decimal | None: ...

    def list_tier1_by_chain(self, chain: str) -> list[TokenRow]: ...

    def id_for_address(self, chain: str, contract_address: str) -> str | None:
        """Return the token_id for a given contract address, or None if unknown.

        The zero address maps to the chain's native LEMX token.
        """
        ...

    def tier1_lemonchain(self) -> list[TokenRow]:
        """Convenience alias for list_tier1_by_chain('lemonchain')."""
        ...
