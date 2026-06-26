"""Activity / gain-loss report — informational summary for entity + year.

Aggregates acquisitions, disposals, and reward income into a single report
suitable for a CPA review packet.  CSV and PDF output are both supported.

No tax decisions are made here: all values are read from the materialized ledger.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from lemon_ledger.domain.forms.read_model import (
    AcquisitionRow,
    DisposalRow,
    RewardIncomeRow,
)


@dataclass(frozen=True)
class ActivityReport:
    entity_id: uuid.UUID
    tax_year: int
    acquisitions: list[AcquisitionRow]
    disposals: list[DisposalRow]
    total_proceeds: Decimal
    total_cost_basis_disposed: Decimal
    total_gain_loss: Decimal
    total_reward_income: Decimal
    is_draft: bool
    generated_at: datetime


def build_activity_report(
    acquisitions: list[AcquisitionRow],
    disposals: list[DisposalRow],
    reward_income: RewardIncomeRow,
    entity_id: uuid.UUID,
    tax_year: int,
    *,
    is_draft: bool = False,
) -> ActivityReport:
    total_proceeds = sum((r.proceeds_usd for r in disposals), Decimal(0))
    total_basis = sum((r.cost_basis_usd for r in disposals), Decimal(0))
    total_gain = sum((r.gain_loss_net for r in disposals), Decimal(0))
    return ActivityReport(
        entity_id=entity_id,
        tax_year=tax_year,
        acquisitions=acquisitions,
        disposals=disposals,
        total_proceeds=total_proceeds,
        total_cost_basis_disposed=total_basis,
        total_gain_loss=total_gain,
        total_reward_income=reward_income.total_income_usd,
        is_draft=is_draft,
        generated_at=datetime.utcnow(),
    )


def to_csv(report: ActivityReport) -> str:
    """Return the activity report as a UTF-8 CSV string (two sections)."""
    buf = io.StringIO()
    w = csv.writer(buf)

    draft_flag = " [DRAFT]" if report.is_draft else ""
    w.writerow([f"# Lemon Ledger — Activity Report{draft_flag}"])
    w.writerow([f"# Entity: {report.entity_id}  |  Tax Year: {report.tax_year}"])
    w.writerow(
        ["# INFORMATIONAL ONLY — NOT FILED TAX ADVICE. Review with a licensed tax professional."]
    )
    w.writerow([])

    w.writerow(["## ACQUISITIONS"])
    w.writerow(
        ["date", "description", "quantity", "cost_basis_usd", "acquisition_type", "asset_class"]
    )
    for acq in report.acquisitions:
        w.writerow(
            [
                acq.acquired_at.isoformat(),
                acq.description,
                str(acq.quantity.normalize()),
                str(acq.cost_basis_usd),
                acq.acquisition_type,
                acq.asset_class,
            ]
        )
    w.writerow([])

    w.writerow(["## DISPOSALS"])
    w.writerow(
        [
            "date",
            "description",
            "proceeds_usd",
            "basis_usd",
            "gain_loss_net",
            "holding_period",
            "adj_code",
            "adj_usd",
        ]
    )
    for disp in report.disposals:
        w.writerow(
            [
                disp.disposed_at.isoformat(),
                disp.description,
                str(disp.proceeds_usd),
                str(disp.cost_basis_usd),
                str(disp.gain_loss_net),
                disp.holding_period,
                disp.adjustment_code or "",
                str(disp.adjustment_usd) if disp.adjustment_usd is not None else "",
            ]
        )
    w.writerow([])

    w.writerow(["## SUMMARY"])
    w.writerow(["total_proceeds", str(report.total_proceeds)])
    w.writerow(["total_cost_basis_disposed", str(report.total_cost_basis_disposed)])
    w.writerow(["total_gain_loss", str(report.total_gain_loss)])
    w.writerow(["total_reward_income_line_8z", str(report.total_reward_income)])

    return buf.getvalue()
