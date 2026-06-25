"""Celery task: apply_lots_for_wallet.

Processes all not-yet-applied classified events for a wallet in canonical order.
Chained after classify_wallet in the pipeline. Idempotent.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import sqlalchemy.exc
from sqlalchemy import select
from sqlalchemy.orm import Session

from lemon_ledger.domain.lots.engine import apply_event
from lemon_ledger.models.classified import ClassifiedTransaction
from lemon_ledger.worker import celery_app, resources

log = logging.getLogger(__name__)


def process_wallet_lots(session: Session, wallet_id: uuid.UUID) -> dict[str, Any]:
    """Process all classified events for *wallet_id* in canonical order.

    apply_event is idempotent (checks for existing lots/disposals), so re-running
    is safe and processes only events not yet applied.
    """
    events = session.scalars(
        select(ClassifiedTransaction)
        .where(ClassifiedTransaction.wallet_id == wallet_id)
        .order_by(
            ClassifiedTransaction.occurred_at,
            ClassifiedTransaction.block_number,
            ClassifiedTransaction.event_seq,
            ClassifiedTransaction.id,
        )
    ).all()

    applied = 0
    for event in events:
        apply_event(session, event)
        applied += 1

    session.commit()
    return {"wallet_id": str(wallet_id), "events_processed": applied}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.apply_lots_for_wallet",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def apply_lots_for_wallet_task(
    self: Any,
    wallet_id: str,
    *,
    _session: Any = None,
) -> dict[str, Any]:
    """Apply lot tracking for all classified events of *wallet_id*."""
    log_ = log
    wid = uuid.UUID(wallet_id)

    if _session is not None:
        return process_wallet_lots(_session, wid)

    from lemon_ledger.config import get_settings
    from lemon_ledger.db.sync_session import worker_session

    settings = get_settings()
    res = resources.ensure(settings)

    with worker_session(res.sessionmaker) as session:
        result = process_wallet_lots(session, wid)
        log_.info(
            "apply_lots_done",
            extra={"wallet_id": wallet_id, "processed": result["events_processed"]},
        )
        return result
