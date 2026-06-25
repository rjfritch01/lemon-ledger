"""Celery tasks for the bridge correlation module.

Tasks:
  run_bridge_pass(user_id, since_iso=None): find candidates → pair → score/signal → rebuild.
  learn_custody_addresses_task(): nightly; promote learned custody addresses.
  surface_unmatched_task(): nightly; age-out unmatched legs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy.exc

from lemon_ledger.worker import celery_app, resources

log = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.run_bridge_pass",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def run_bridge_pass(
    self: Any,
    user_id: str,
    since_iso: str | None = None,
    *,
    _session: Any = None,
) -> dict[str, Any]:
    """Find candidates, pair, score, set signals, enqueue lot rebuilds.

    Idempotent: pairing never clobbers user-resolved rows; apply_event is
    idempotent on the lot side.

    Triggered after wallet sync completes and as a trailing-window sweep.
    """
    uid = uuid.UUID(user_id)
    since = datetime.fromisoformat(since_iso) if since_iso else None

    if _session is not None:
        return _run_bridge_pass_inner(_session, uid, since)

    from lemon_ledger.config import get_settings
    from lemon_ledger.db.sync_session import worker_session

    settings = get_settings()
    res = resources.ensure(settings)

    with worker_session(res.sessionmaker) as session:
        result = _run_bridge_pass_inner(session, uid, since)
        log.info("bridge_pass_done", extra={"user_id": user_id, **result})
        return result


def _run_bridge_pass_inner(
    session: Any,
    user_id: uuid.UUID,
    since: datetime | None,
) -> dict[str, Any]:
    from lemon_ledger.domain.bridge.candidates import find_candidate_legs
    from lemon_ledger.domain.bridge.confidence import score_pair, status_for_level
    from lemon_ledger.domain.bridge.custody import strongest_custody
    from lemon_ledger.domain.bridge.pairing import pair_and_persist
    from lemon_ledger.domain.bridge.workflow import set_classification_signal
    from lemon_ledger.domain.lots.engine import rebuild_wallet
    from lemon_ledger.models.bridge import BridgeStatus
    from lemon_ledger.models.classified import ClassifiedTransaction

    legs = find_candidate_legs(session, user_id=user_id, since=since)
    pairs = pair_and_persist(session, legs, user_id=user_id)
    session.flush()

    affected_wallets: set[uuid.UUID] = set()
    scored = 0

    for corr in pairs:
        if corr.status in (BridgeStatus.CONFIRMED, BridgeStatus.REJECTED):
            # User-resolved — do not re-score.
            if corr.resolved_by == "user":
                continue

        if corr.outflow_classified_event_id is None or corr.inflow_classified_event_id is None:
            continue

        out_ct = session.get(ClassifiedTransaction, corr.outflow_classified_event_id)
        in_ct = session.get(ClassifiedTransaction, corr.inflow_classified_event_id)
        if out_ct is None or in_ct is None:
            continue

        from lemon_ledger.domain.bridge.pairing import PairHypothesis

        hyp = PairHypothesis(
            outflow=_ct_to_leg(out_ct, user_id),
            inflow=_ct_to_leg(in_ct, user_id),
            time_delta_seconds=corr.time_delta_seconds or 0,
            amount_delta_bps=corr.amount_delta_bps or 0,
        )

        custody, winning_addr = strongest_custody(
            session,
            outflow_contract=out_ct.contract_address,
            inflow_contract=in_ct.contract_address,
            outflow_chain=out_ct.chain,
            inflow_chain=in_ct.chain,
        )

        level, score = score_pair(hyp, custody)
        status = status_for_level(level)

        corr.confidence_level = level.value
        corr.confidence_score = score
        corr.custody_recognition = custody.value
        corr.matched_custody_address = winning_addr
        corr.status = status
        corr.resolved_by = "auto" if status == BridgeStatus.CONFIRMED else None
        if status == BridgeStatus.CONFIRMED:
            corr.resolved_at = datetime.now(UTC)

        session.flush()
        set_classification_signal(session, corr, confirmed=(status == BridgeStatus.CONFIRMED))
        session.flush()

        scored += 1
        affected_wallets.add(out_ct.wallet_id)
        affected_wallets.add(in_ct.wallet_id)

    session.commit()

    # Enqueue lot rebuilds for affected wallets.
    for wallet_id in affected_wallets:
        rebuild_wallet(session, wallet_id)

    session.commit()

    return {
        "user_id": str(user_id),
        "legs_found": len(legs),
        "pairs_written": len(pairs),
        "pairs_scored": scored,
        "wallets_rebuilt": len(affected_wallets),
    }


def _ct_to_leg(ct: Any, user_id: uuid.UUID) -> Any:
    """Minimal CandidateLeg-like adapter for PairHypothesis construction."""

    from lemon_ledger.domain.bridge.candidates import CandidateLeg, LegDirection

    is_out = ct.classification in ("transfer-out", "bridge-out")
    return CandidateLeg(
        classified_event_id=ct.id,
        direction=LegDirection.OUTFLOW if is_out else LegDirection.INFLOW,
        wallet_id=ct.wallet_id,
        user_id=user_id,
        chain=ct.chain,
        logical_asset_id=uuid.UUID(int=0),  # populated by pairing; not needed here
        token_id=ct.token_id or uuid.UUID(int=0),
        amount=ct.amount,
        value_usd=ct.value_usd_at_event,
        occurred_at=ct.occurred_at,
        contract_address=ct.contract_address,
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.learn_custody_addresses",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def learn_custody_addresses_task(self: Any, *, _session: Any = None) -> dict[str, Any]:
    """Nightly: aggregate confirmed pairs → promote learned custody addresses."""
    if _session is not None:
        return _learn_inner(_session)

    from lemon_ledger.config import get_settings
    from lemon_ledger.db.sync_session import worker_session

    settings = get_settings()
    res = resources.ensure(settings)

    with worker_session(res.sessionmaker) as session:
        result = _learn_inner(session)
        log.info("learn_custody_done", extra=result)
        return result


def _learn_inner(session: Any) -> dict[str, Any]:
    from lemon_ledger.domain.bridge.custody import learn_custody_addresses

    promoted = learn_custody_addresses(session)
    session.commit()
    return {"promoted": len(promoted)}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.surface_unmatched",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def surface_unmatched_task(self: Any, *, _session: Any = None) -> dict[str, Any]:
    """Nightly: surface aged-out unmatched bridge legs."""
    if _session is not None:
        return _surface_inner(_session)

    from lemon_ledger.config import get_settings
    from lemon_ledger.db.sync_session import worker_session

    settings = get_settings()
    res = resources.ensure(settings)

    with worker_session(res.sessionmaker) as session:
        result = _surface_inner(session)
        log.info("surface_unmatched_done", extra=result)
        return result


def _surface_inner(session: Any) -> dict[str, Any]:
    from lemon_ledger.domain.bridge.workflow import surface_aged_unmatched

    surfaced = surface_aged_unmatched(session)
    session.commit()
    return {"surfaced": len(surfaced)}
