"""Schedule D — Capital Gains and Losses.

Pure transformation: consumes Form8949Result subtotals only.
Never reads lot_disposals or tax_lots directly — that is the anti-drift guarantee.
If Schedule D were to re-read the ledger, a bug in the generator could produce
a Schedule D total that disagrees with the 8949 already sent to the IRS.

Part I = short-term (boxes A + B + C)
Part II = long-term  (boxes D + E + F)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from lemon_ledger.domain.forms.form_8949 import Form8949Result


@dataclass(frozen=True)
class ScheduleDResult:
    entity_id: uuid.UUID
    tax_year: int
    short_term_net: Decimal  # Part I: sum of 8949 boxes A + B + C col (h)
    long_term_net: Decimal  # Part II: sum of 8949 boxes D + E + F col (h)
    total_net: Decimal  # line 16: short + long
    is_draft: bool
    generated_at: datetime


def build_schedule_d(form_8949: Form8949Result) -> ScheduleDResult:
    """Build Schedule D from 8949 box subtotals.

    Anti-drift invariant (enforced by test):
        schedule_d.total_net == sum(box.total_gain_loss_net for box in form_8949.boxes.values())
    """
    short = form_8949.short_term_net
    long_ = form_8949.long_term_net
    return ScheduleDResult(
        entity_id=form_8949.entity_id,
        tax_year=form_8949.tax_year,
        short_term_net=short,
        long_term_net=long_,
        total_net=short + long_,
        is_draft=form_8949.is_draft,
        generated_at=form_8949.generated_at,
    )
