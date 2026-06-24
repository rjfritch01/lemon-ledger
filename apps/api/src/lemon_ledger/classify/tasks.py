"""Celery task: classify_wallet.

Walks the settled block range (last_classified_block, last_synced_block] for
one wallet, groups raw rows into TxBundles, runs classify_bundle + replace_classified
per tx, and commits per chunk.  Chained after sync_wallet.

_pricing is injectable for test DI; in production it must be wired via the
app factory (post-1.5 scope). The task raises RuntimeError if _pricing is None
and no production wiring is available (fail-safe: no phantom basis).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import sqlalchemy.exc
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from lemon_ledger.classify.context import WalletContext
from lemon_ledger.classify.orchestrator import classify_bundle, replace_classified
from lemon_ledger.classify.types import TxBundle
from lemon_ledger.domain.chains import Chain
from lemon_ledger.models.raw import RawInternalTx, RawTokenTransfer, RawTransaction
from lemon_ledger.models.wallet import Wallet
from lemon_ledger.tasks.sync import wallet_sync_lock
from lemon_ledger.worker import celery_app, resources

log = logging.getLogger(__name__)

_CLASSIFY_CHUNK = 10_000


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.classify_wallet",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def classify_wallet_task(
    self: Any,
    wallet_id: str,
    *,
    _pricing: Any = None,
    _session: Any = None,
) -> dict[str, Any]:
    """Classify new raw rows for *wallet_id*.

    _pricing and _session are injected for test DI.  In production _pricing
    must be wired by the app factory; without it the task raises RuntimeError.
    """
    log_ = structlog.get_logger().bind(task_id=self.request.id, wallet_id=wallet_id)

    from lemon_ledger.config import get_settings
    from lemon_ledger.db.sync_session import worker_session

    settings = get_settings()
    res = resources.ensure(settings)

    with wallet_sync_lock(res.redis, wallet_id, settings.sync_lock_ttl_s) as locked:
        if not locked:
            log_.info("classify_skipped_locked")
            return {"wallet_id": wallet_id, "skipped": "locked"}

        if _session is not None:
            # Test path: use injected session directly (no context manager needed)
            pricing = _pricing
            if pricing is None:
                raise RuntimeError(
                    "classify_wallet_task requires a PricingService; inject via _pricing="
                )
            return _run_classify(wallet_id, pricing, _session, log_)

        # Production path
        with worker_session(res.sessionmaker) as session:
            pricing = _pricing
            if pricing is None:
                raise RuntimeError(
                    "classify_wallet_task: PricingService not wired — "
                    "set _pricing= or wire via app factory"
                )
            return _run_classify(wallet_id, pricing, session, log_)

    return {}  # pragma: no cover


def _run_classify(
    wallet_id: str,
    pricing: Any,
    session: Session,
    log_: Any,
) -> dict[str, Any]:
    wid = uuid.UUID(wallet_id)
    wallet = session.get(Wallet, wid)
    if wallet is None or not wallet.is_active:
        raise ValueError(f"Wallet {wallet_id!r} not found or inactive")

    from_block = (wallet.last_classified_block or 0) + 1
    to_block = wallet.last_synced_block or 0
    if from_block > to_block:
        return {"wallet_id": wallet_id, "classified": 0, "msg": "nothing_to_classify"}

    user_wallets = session.scalars(
        select(Wallet.address).where(
            Wallet.user_id == wallet.user_id,
            Wallet.is_active == True,  # noqa: E712
        )
    ).all()
    user_addrs: set[str] = {a.lower() for a in user_wallets}

    ctx = WalletContext(
        wallet=wallet,
        user_wallet_addresses=user_addrs,
        session=session,
        pricing=pricing,
    )

    total_classified = 0
    lo = from_block
    while lo <= to_block:
        hi = min(lo + _CLASSIFY_CHUNK - 1, to_block)
        classified = _classify_chunk(wid, wallet, ctx, session, lo, hi)
        total_classified += classified

        wallet.last_classified_block = hi
        session.commit()
        log_.debug(
            "classify_chunk_done",
            from_block=lo,
            to_block=hi,
            classified=classified,
        )
        lo = hi + 1

    return {
        "wallet_id": wallet_id,
        "classified": total_classified,
        "from_block": from_block,
        "to_block": to_block,
    }


def _classify_chunk(
    wallet_id: uuid.UUID,
    wallet: Wallet,
    ctx: WalletContext,
    session: Session,
    lo: int,
    hi: int,
) -> int:
    txs = session.scalars(
        select(RawTransaction)
        .where(
            RawTransaction.wallet_id == wallet_id,
            RawTransaction.block_number >= lo,
            RawTransaction.block_number <= hi,
        )
        .order_by(RawTransaction.block_number, RawTransaction.tx_hash)
    ).all()

    transfers = session.scalars(
        select(RawTokenTransfer).where(
            RawTokenTransfer.wallet_id == wallet_id,
            RawTokenTransfer.block_number >= lo,
            RawTokenTransfer.block_number <= hi,
        )
    ).all()

    internals = session.scalars(
        select(RawInternalTx).where(
            RawInternalTx.wallet_id == wallet_id,
            RawInternalTx.block_number >= lo,
            RawInternalTx.block_number <= hi,
        )
    ).all()

    tx_map: dict[str, RawTransaction] = {t.tx_hash: t for t in txs}
    transfer_map: dict[str, list[RawTokenTransfer]] = {}
    for tr in transfers:
        transfer_map.setdefault(tr.tx_hash, []).append(tr)
    internal_map: dict[str, list[RawInternalTx]] = {}
    for itx in internals:
        internal_map.setdefault(itx.tx_hash, []).append(itx)

    all_hashes: set[str] = set(tx_map) | set(transfer_map) | set(internal_map)

    total = 0
    for tx_hash in sorted(all_hashes):
        envelope = tx_map.get(tx_hash)
        rows_for_occurred_at = transfer_map.get(tx_hash, []) or internal_map.get(tx_hash, [])
        if envelope is None and not rows_for_occurred_at:
            continue

        occurred_at = envelope.occurred_at if envelope else rows_for_occurred_at[0].occurred_at
        block_number = envelope.block_number if envelope else rows_for_occurred_at[0].block_number

        bundle = TxBundle(
            wallet_id=wallet_id,
            chain=Chain(wallet.chain),
            tx_hash=tx_hash,
            block_number=block_number,
            occurred_at=occurred_at,
            envelope=envelope,
            transfers=transfer_map.get(tx_hash, []),
            internals=internal_map.get(tx_hash, []),
        )

        classified_rows = classify_bundle(bundle, ctx)
        replace_classified(session, wallet_id, tx_hash, classified_rows)
        total += len(classified_rows)

    return total
