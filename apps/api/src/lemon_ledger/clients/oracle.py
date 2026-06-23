from __future__ import annotations

from decimal import Decimal

from lemon_ledger.clients.evm.provider import EVMProvider

# ABI selector for latestRoundData() — keccak256 first 4 bytes
_LATEST_ROUND_DATA = "0xfeaf968c"


class PriceDataFeed:
    """Read-only wrapper around a Chainlink AggregatorV3-compatible oracle.

    Calls latestRoundData() on-chain and returns the answer as a Decimal
    scaled by the oracle's published decimal precision.
    """

    def __init__(
        self,
        provider: EVMProvider,
        contract_address: str,
        *,
        decimals: int = 8,
    ) -> None:
        self._provider = provider
        self._contract = contract_address
        self._decimals = decimals

    def latest_price(self) -> Decimal:
        """Return the latest oracle price as a human-scale Decimal."""
        raw_hex = self._provider.eth_call(self._contract, _LATEST_ROUND_DATA)
        # latestRoundData returns (roundId, answer, startedAt, updatedAt, answeredInRound)
        # answer is the second 32-byte ABI slot.
        payload = bytes.fromhex(raw_hex[2:])  # strip 0x prefix
        answer = int.from_bytes(payload[32:64], "big", signed=True)
        return Decimal(answer).scaleb(-self._decimals)
