from __future__ import annotations

import secrets
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

import sqlalchemy.exc
import structlog
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from lemon_ledger.clients.blockscout import build_blockscout_client
from lemon_ledger.clients.rate_limit import RedisTokenBucket
from lemon_ledger.config import Settings, get_settings
from lemon_ledger.db.sync_session import worker_session
from lemon_ledger.ingestion.sync import sync_wallet
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.worker import celery_app, resources

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
            with worker_session(res.sessionmaker) as session:
                wallet = session.get(Wallet, uuid.UUID(wallet_id))
                if wallet is None or not wallet.is_active:
                    raise ValueError(f"Wallet {wallet_id!r} not found or inactive")
                if from_block is not None:
                    wallet.last_synced_block = from_block
                limiter = RedisTokenBucket(
                    res.redis,
                    key=f"ratelimit:{wallet.chain}",
                    rate_per_sec=settings.explorer_rate_limit_rps,
                    burst=settings.explorer_rate_limit_burst,
                )
                client = build_blockscout_client(
                    wallet.chain, settings, http=res.http, rate_limiter=limiter
                )
                result = sync_wallet(
                    session,
                    client,
                    wallet,
                    confirmations=_confirmations_for(wallet.chain, settings),
                    chunk_blocks=settings.sync_block_chunk,
                )
        except SoftTimeLimitExceeded:
            log.warning("sync_soft_timeout")
            return {"wallet_id": wallet_id, "soft_timeout": True}

    payload: dict[str, Any] = asdict(result)
    payload["wallet_id"] = str(result.wallet_id)
    return payload


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
