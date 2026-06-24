"""Shared types for the classification layer.

TxBundle      — immutable snapshot of one transaction's raw rows.
ClassifiedEvent — a decoded economic event within a bundle.
ClaimSet      — tracks which raw rows have been consumed by a decoder.
EventKind     — internal signal; maps to ClassificationKind at persist time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from lemon_ledger.domain.chains import Chain
from lemon_ledger.models.enums import ClassificationKind
from lemon_ledger.models.raw import RawInternalTx, RawTokenTransfer, RawTransaction


class EventKind(StrEnum):
    REWARD = "reward"
    REWARD_LOW_CONFIDENCE = "reward-low-confidence"
    MINT = "mint"
    STAKE = "stake"
    UNSTAKE = "unstake"
    TRANSFER_IN = "transfer-in"
    TRANSFER_OUT = "transfer-out"


@dataclass(frozen=True)
class TxBundle:
    """Immutable snapshot of one tx's raw rows, ready for classification."""

    wallet_id: uuid.UUID
    chain: Chain
    tx_hash: str
    block_number: int
    occurred_at: datetime
    envelope: RawTransaction | None
    transfers: list[RawTokenTransfer] = field(default_factory=list)
    internals: list[RawInternalTx] = field(default_factory=list)


@dataclass
class ClassifiedEvent:
    """A single economic event decoded from a TxBundle.

    _order_hint is the raw log_index (or -1 for native/internal events) used
    to produce a deterministic event_seq within the tx.
    """

    classification: ClassificationKind
    contract_address: str
    token_id: uuid.UUID | None
    amount: Decimal
    value_usd_at_event: Decimal | None
    needs_review: bool = False
    notes: str | None = None
    _order_hint: int = 0


class ClaimSet:
    """Tracks which raw rows a decoder has consumed.

    Key scheme:
      RawTokenTransfer  → ("transfer", str(log_index))
      RawInternalTx     → ("internal", str(trace_id))
    """

    def __init__(self) -> None:
        self._claimed: set[tuple[str, str]] = set()

    @staticmethod
    def _key(row: RawTokenTransfer | RawInternalTx) -> tuple[str, str]:
        if isinstance(row, RawTokenTransfer):
            return ("transfer", str(row.log_index))
        return ("internal", str(row.trace_id))

    def add(self, row: RawTokenTransfer | RawInternalTx) -> None:
        self._claimed.add(self._key(row))

    def has(self, row: RawTokenTransfer | RawInternalTx) -> bool:
        return self._key(row) in self._claimed
