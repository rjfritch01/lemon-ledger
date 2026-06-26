"""Schedule 1 Line 8z — Other Income: cryptocurrency reward income.

Pure transformation: takes RewardIncomeRow, returns Schedule1Result.
The DB query lives in read_model.fetch_reward_income.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from lemon_ledger.domain.forms.read_model import RewardIncomeRow


@dataclass(frozen=True)
class Schedule1Result:
    entity_id: uuid.UUID
    tax_year: int
    line_8z_income: Decimal  # SUM(cost_basis_usd) WHERE acquisition_type='reward'
    is_draft: bool
    generated_at: datetime


def build_schedule_1(
    reward_income: RewardIncomeRow,
    *,
    is_draft: bool = False,
    generated_at: datetime | None = None,
) -> Schedule1Result:
    return Schedule1Result(
        entity_id=reward_income.entity_id,
        tax_year=reward_income.tax_year,
        line_8z_income=reward_income.total_income_usd,
        is_draft=is_draft,
        generated_at=generated_at or datetime.utcnow(),
    )
