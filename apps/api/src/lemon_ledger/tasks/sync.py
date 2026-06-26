from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

import sqlalchemy.exc
import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from lemon_ledger.clients.base import ChainClient
from lemon_ledger.clients.registry import build_chain_client
from lemon_ledger.config import Settings, get_settings
from lemon_ledger.db.sync_session import worker_session
from lemon_ledger.domain.chains import Chain
from lemon_ledger.ingestion.sync import SyncResult, sync_wallet
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.worker import Resources, celery_app, resources

_LUA_RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


@contextmanager
def wallet_sync_lock(redis: Any, wallet_id: str, ttl_s: int) -> Generator[bool, None, None]:
    key = f"lock:sync:{wallet_id}"
    token = secrets.token_hex(16)
    acquired = redis.set(key, token, nx=True, ex=ttl_s)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            redis.eval(_LUA_RELEASE_LOCK, 1, key, token)  # nosec B307


def _run_sync(
    wallet_id: str,
    from_block: int | None,
    res: Resources,
    settings: Settings,
    *,
    client_factory: Callable[[Chain, Resources, Settings], ChainClient] = build_chain_client,
) -> SyncResult:
    """Core sync logic extracted for testability via client_factory injection."""
    with worker_session(res.sessionmaker) as session:
        wallet = session.get(Wallet, uuid.UUID(wallet_id))
        if wallet is None or not wallet.is_active:
            raise ValueError(f"Wallet {wallet_id!r} not found or inactive")
        if from_block is not None:
            wallet.last_synced_block = from_block
        client = client_factory(Chain(wallet.chain), res, settings)
        return sync_wallet(
            session,
            client,
            wallet,
            confirmations=_confirmations_for(wallet.chain, settings),
            chunk_blocks=settings.sync_block_chunk,
        )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.sync_wallet",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def sync_wallet_task(self: Any, wallet_id: str, from_block: int | None = None) -> dict[str, Any]:
    log = structlog.get_logger().bind(task_id=self.request.id, wallet_id=wallet_id)
    settings = get_settings()
    res = resources.ensure(settings)

    with wallet_sync_lock(res.redis, wallet_id, settings.sync_lock_ttl_s) as locked:
        if not locked:
            log.info("sync_skipped_locked")
            return {"wallet_id": wallet_id, "skipped": "locked"}
        try:
            result = _run_sync(wallet_id, from_block, res, settings)
            payload: dict[str, Any] = asdict(result)
            payload["wallet_id"] = str(result.wallet_id)
            return payload
        except SoftTimeLimitExceeded:
            log.warning("sync_soft_timeout")
            return {"wallet_id": wallet_id, "soft_timeout": True}

    return {}  # pragma: no cover


def _confirmations_for(chain: str, settings: Settings) -> int:
    return settings.sync_confirmations_lemonchain


@celery_app.task(name="lemon_ledger.sync_all_active_wallets")  # type: ignore[untyped-decorator]
def sync_all_active_wallets() -> dict[str, Any]:
    settings = get_settings()
    res = resources.ensure(settings)
    with worker_session(res.sessionmaker) as session:
        wallet_ids = session.scalars(select(Wallet.id).where(Wallet.is_active == True)).all()  # noqa: E712
    for wid in wallet_ids:
        sync_wallet_task.apply_async(args=[str(wid)])
    return {"dispatched": len(wallet_ids)}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.run_user_lot_pipeline",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def run_user_lot_pipeline_task(
    self: Any,
    user_id: str,
    *,
    _session: Any = None,
) -> dict[str, Any]:
    """Per-user pipeline: cross-entity detection → apply_lots for all wallets.

    Must run AFTER classify_wallet completes for all user wallets.
    Ordering guarantee: wire as a separate beat task or chain downstream of
    classify completion; never inline during raw sync.

    Steps:
      1. run_cross_entity_detection (stamps transfer_resolution on CTs)
      2. apply_lots_for_wallet for each wallet (lot engine reads resolved signals)
    """
    from lemon_ledger.domain.cross_entity.tasks import _run_inner as _detect
    from lemon_ledger.domain.lots.engine import apply_event
    from lemon_ledger.models.classified import ClassifiedTransaction

    uid = uuid.UUID(user_id)

    if _session is not None:
        return _run_pipeline(_session, uid, _detect, apply_event, ClassifiedTransaction)

    settings = get_settings()
    res = resources.ensure(settings)

    with worker_session(res.sessionmaker) as session:
        result = _run_pipeline(session, uid, _detect, apply_event, ClassifiedTransaction)
    return result


def _run_pipeline(
    session: Any,
    user_id: uuid.UUID,
    detect_fn: Any,
    apply_event_fn: Any,
    ct_cls: Any,
) -> dict[str, Any]:
    detect_counts = detect_fn(session, user_id, None)

    wallet_ids = list(
        session.scalars(
            select(Wallet.id).where(
                Wallet.user_id == user_id,
                Wallet.is_active == True,  # noqa: E712
            )
        ).all()
    )

    events_applied = 0
    for wid in wallet_ids:
        events = session.scalars(
            select(ct_cls)
            .where(ct_cls.wallet_id == wid)
            .order_by(
                ct_cls.occurred_at,
                ct_cls.block_number,
                ct_cls.event_seq,
                ct_cls.id,
            )
        ).all()
        for event in events:
            apply_event_fn(session, event)
            events_applied += 1
    session.commit()

    return {
        "user_id": str(user_id),
        "wallets": len(wallet_ids),
        "events_applied": events_applied,
        **detect_counts,
    }
