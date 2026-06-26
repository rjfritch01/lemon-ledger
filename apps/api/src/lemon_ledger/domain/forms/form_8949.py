"""Form 8949 — Sales and Other Dispositions of Capital Assets.

Pure transformation: takes list[DisposalRow], returns Form8949Result.
No DB access; no tax derivation. Every value is read from the ledger.

Box mapping (CoveredStatus × HoldingPeriod):
  Short-term: covered-basis-reported → A | covered-basis-not-reported → B | no-1099-da → C
  Long-term:  covered-basis-reported → D | covered-basis-not-reported → E | no-1099-da → F

Schedule D reads Box subtotals from Form8949Result — never the raw ledger.
This is the anti-drift invariant: Schedule D total == sum of 8949 box totals.

NOTE (Phase 4): collectible asset_class (LemQuest NFTs, SC NFTs) is subject
to 28% max rate under IRC §1(h)(4), not the standard LTCG rate schedule.
Phase 1 reports collectibles on the same 8949/Schedule D as fungibles without
rate differentiation. Tax computation at the correct rate is deferred to Phase 4.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from lemon_ledger.domain.forms.read_model import DisposalRow

Box = Literal["A", "B", "C", "D", "E", "F"]

_SHORT_BOX: dict[str, Box] = {
    "covered-basis-reported": "A",
    "covered-basis-not-reported": "B",
    "no-1099-da": "C",
}
_LONG_BOX: dict[str, Box] = {
    "covered-basis-reported": "D",
    "covered-basis-not-reported": "E",
    "no-1099-da": "F",
}


def _box_for(row: DisposalRow) -> Box:
    if row.holding_period == "short":
        return _SHORT_BOX[row.covered_status]
    return _LONG_BOX[row.covered_status]


@dataclass(frozen=True)
class BoxSubtotals:
    box: Box
    holding_period: Literal["short", "long"]
    total_proceeds: Decimal
    total_basis: Decimal
    total_adjustment: Decimal
    total_gain_loss_net: Decimal  # col (h) sum; Schedule D reads this, not the ledger
    rows: list[DisposalRow] = field(default_factory=list)


@dataclass(frozen=True)
class Form8949Result:
    entity_id: uuid.UUID
    tax_year: int
    boxes: dict[Box, BoxSubtotals]
    is_draft: bool
    generated_at: datetime

    @property
    def total_gain_loss_net(self) -> Decimal:
        return sum((b.total_gain_loss_net for b in self.boxes.values()), Decimal(0))

    @property
    def short_term_net(self) -> Decimal:
        return sum(
            (b.total_gain_loss_net for k, b in self.boxes.items() if k in ("A", "B", "C")),
            Decimal(0),
        )

    @property
    def long_term_net(self) -> Decimal:
        return sum(
            (b.total_gain_loss_net for k, b in self.boxes.items() if k in ("D", "E", "F")),
            Decimal(0),
        )


def build_8949(
    rows: list[DisposalRow],
    entity_id: uuid.UUID,
    tax_year: int,
    *,
    is_draft: bool = False,
) -> Form8949Result:
    """Partition rows into boxes A-F and compute subtotals.

    Boxes with zero rows are still included with zero subtotals so Schedule D
    can safely read all six without key-error guards.
    """
    by_box: dict[Box, list[DisposalRow]] = {b: [] for b in ("A", "B", "C", "D", "E", "F")}  # type: ignore[misc]
    for row in rows:
        by_box[_box_for(row)].append(row)

    holding: dict[Box, Literal["short", "long"]] = {
        "A": "short",
        "B": "short",
        "C": "short",
        "D": "long",
        "E": "long",
        "F": "long",
    }

    boxes: dict[Box, BoxSubtotals] = {}
    for box, box_rows in by_box.items():
        boxes[box] = BoxSubtotals(
            box=box,
            holding_period=holding[box],
            total_proceeds=sum((r.proceeds_usd for r in box_rows), Decimal(0)),
            total_basis=sum((r.cost_basis_usd for r in box_rows), Decimal(0)),
            total_adjustment=sum((r.adjustment_usd or Decimal(0) for r in box_rows), Decimal(0)),
            total_gain_loss_net=sum((r.gain_loss_net for r in box_rows), Decimal(0)),
            rows=box_rows,
        )

    return Form8949Result(
        entity_id=entity_id,
        tax_year=tax_year,
        boxes=boxes,
        is_draft=is_draft,
        generated_at=datetime.utcnow(),
    )
