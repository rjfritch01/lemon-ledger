"""Pure read-model for tax forms.

Fetch functions query the ledger and return frozen dataclasses.
No tax decisions are made here — every value is already materialized by Stages 1-4.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import extract, select
from sqlalchemy.orm import Session

from lemon_ledger.models.lot import LotDisposal, TaxLot
from lemon_ledger.models.token_registry import TokenRegistry


@dataclass(frozen=True)
class DisposalRow:
    """One row on Form 8949.  All values are exact ledger values; render rounds."""

    lot_id: uuid.UUID
    disposal_tx_id: uuid.UUID
    description: str  # col (a): "{qty_consumed.normalize()} {symbol}"
    acquired_at: date  # col (b)
    disposed_at: date  # col (c)
    proceeds_usd: Decimal  # col (d)
    cost_basis_usd: Decimal  # col (e) — basis_consumed_usd read directly (CORRECTION 1)
    adjustment_code: str | None  # col (f)
    adjustment_usd: Decimal | None  # col (g) positive; engine stores abs(loss)
    holding_period: str  # 'short' | 'long'
    covered_status: str  # 'no-1099-da' | 'covered-basis-reported' | ...
    asset_class: str  # 'fungible' | 'collectible'
    entity_id: uuid.UUID

    @property
    def gain_loss_net(self) -> Decimal:
        """Column (h) = (d) - (e) + (g).  adjustment_usd reduces a disallowed loss."""
        return self.proceeds_usd - self.cost_basis_usd + (self.adjustment_usd or Decimal(0))


@dataclass(frozen=True)
class RewardIncomeRow:
    """Aggregate for Schedule 1 Line 8z.  One row per entity+year query."""

    entity_id: uuid.UUID
    tax_year: int
    total_income_usd: Decimal  # SUM(cost_basis_usd) WHERE acquisition_type='reward'


def fetch_disposal_rows(
    session: Session,
    entity_id: uuid.UUID,
    tax_year: int,
) -> list[DisposalRow]:
    """Return all DisposalRows for entity+year, ordered by disposed_at.

    Entity attribution via tax_lots.entity_id — correct post-fix (entity_id
    is maintained by apply_relocation on cross-entity moves, PR #28).

    Symbol via acquired_token_id (CORRECTION 2): lot_disposals.lot_id
    -> tax_lots.acquired_token_id -> token_registry.symbol.
    """
    stmt = (
        select(
            LotDisposal,
            TaxLot.acquired_at,
            TaxLot.entity_id,
            TokenRegistry.symbol,
        )
        .join(TaxLot, TaxLot.id == LotDisposal.lot_id)
        .join(TokenRegistry, TokenRegistry.id == TaxLot.acquired_token_id)
        .where(
            TaxLot.entity_id == entity_id,
            extract("year", LotDisposal.disposed_at) == tax_year,
        )
        .order_by(LotDisposal.disposed_at, LotDisposal.lot_id)
    )

    rows: list[DisposalRow] = []
    for disposal, acquired_at, ent_id, symbol in session.execute(stmt):
        qty_str = str(disposal.quantity_consumed.normalize())
        rows.append(
            DisposalRow(
                lot_id=disposal.lot_id,
                disposal_tx_id=disposal.disposal_tx_id,
                description=f"{qty_str} {symbol}",
                acquired_at=(
                    acquired_at.date() if isinstance(acquired_at, datetime) else acquired_at
                ),
                disposed_at=disposal.disposed_at.date()
                if isinstance(disposal.disposed_at, datetime)
                else disposal.disposed_at,
                proceeds_usd=disposal.proceeds_usd,
                cost_basis_usd=disposal.basis_consumed_usd,
                adjustment_code=disposal.adjustment_code,
                adjustment_usd=disposal.adjustment_usd,
                holding_period=disposal.holding_period,
                covered_status=disposal.covered_status,
                asset_class=disposal.asset_class,
                entity_id=ent_id,
            )
        )
    return rows


def fetch_reward_income(
    session: Session,
    entity_id: uuid.UUID,
    tax_year: int,
) -> RewardIncomeRow:
    """Return SUM(cost_basis_usd) for reward lots acquired in tax_year.

    CORRECTION 5: acquisition_type='reward' covers staking rewards including
    Swap Credit earn (SCDT earned as staking reward → acquisition_type='reward').
    SC redemption ACQUIRE leg has acquisition_type='mint' and is excluded.
    """
    from sqlalchemy import func

    total = session.scalar(
        select(func.coalesce(func.sum(TaxLot.cost_basis_usd), Decimal(0))).where(
            TaxLot.entity_id == entity_id,
            TaxLot.acquisition_type == "reward",
            extract("year", TaxLot.acquired_at) == tax_year,
        )
    )
    return RewardIncomeRow(
        entity_id=entity_id,
        tax_year=tax_year,
        total_income_usd=total or Decimal(0),
    )
