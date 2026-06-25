"""Lot ordering methods for cost basis calculation.

Protocol:
  LotMethod.order(lots) -> list[TaxLot]   (sorted for consumption)

Implementations:
  Fifo  — oldest acquired_at first (default)
  Hifo  — highest unit cost first (specific-id sub-strategy)
  Lifo  — most recent acquired_at first (specific-id sub-strategy)

SpecificIdValidator — validates caller-supplied {lot_id: qty} map before
a manual disposal is applied.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable

from lemon_ledger.models.lot import TaxLot


class InsufficientLotsError(Exception):
    """Raised when open lots cannot cover the disposal quantity."""

    def __init__(self, quantity_unmatched: Decimal) -> None:
        super().__init__(f"Insufficient lots: {quantity_unmatched} unmatched")
        self.quantity_unmatched = quantity_unmatched


@runtime_checkable
class LotMethod(Protocol):
    """Ordering protocol for lot selection algorithms."""

    def order(self, lots: list[TaxLot]) -> list[TaxLot]: ...


class Fifo:
    """First-in, first-out: oldest acquired_at exhausted first."""

    def order(self, lots: list[TaxLot]) -> list[TaxLot]:
        return sorted(lots, key=lambda lot: (lot.acquired_at, lot.id))


class Hifo:
    """Highest unit cost first (minimises near-term gain)."""

    def order(self, lots: list[TaxLot]) -> list[TaxLot]:
        def _key(lot: TaxLot) -> tuple[Decimal, object, uuid.UUID]:
            try:
                unit = lot.cost_basis_usd / lot.quantity
            except InvalidOperation:
                unit = Decimal(0)
            return (-unit, lot.acquired_at, lot.id)

        return sorted(lots, key=_key)


class Lifo:
    """Last-in, first-out: most recent acquired_at exhausted first."""

    def order(self, lots: list[TaxLot]) -> list[TaxLot]:
        return sorted(lots, key=lambda lot: (-lot.acquired_at.timestamp(), lot.id))


class SpecificIdValidator:
    """Validates a caller-supplied {lot_id: quantity} selection map.

    Raises ValueError for any of:
      - lot_id not in the open pool for this wallet
      - lot's quantity_remaining < requested quantity
      - selection quantities do not sum to the disposal quantity
    """

    def validate(
        self,
        selection: dict[uuid.UUID, Decimal],
        open_lots: list[TaxLot],
        disposal_quantity: Decimal,
        wallet_id: uuid.UUID,
    ) -> list[tuple[TaxLot, Decimal]]:
        lot_map = {lot.id: lot for lot in open_lots}
        pairs: list[tuple[TaxLot, Decimal]] = []
        total = Decimal(0)

        for lot_id, qty in selection.items():
            if lot_id not in lot_map:
                raise ValueError(f"Lot {lot_id} is not in the open pool for wallet {wallet_id}")
            lot = lot_map[lot_id]
            if qty <= 0:
                raise ValueError(f"Lot {lot_id}: selected quantity must be positive")
            if qty > lot.quantity_remaining:
                raise ValueError(
                    f"Lot {lot_id}: selected {qty} > remaining {lot.quantity_remaining}"
                )
            pairs.append((lot, qty))
            total += qty

        if total != disposal_quantity:
            raise ValueError(f"Selection sum {total} != disposal quantity {disposal_quantity}")

        return pairs
