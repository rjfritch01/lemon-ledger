"""Nightly totalSupply() snapshot job.

For every L2 token with an l2_decoder_config row:
  1. Call totalSupply() on the ERC-20 contract via eth_call (read-only).
  2. If supply >= token_registry.max_supply - epsilon, set
     l2_decoder_config.distribution_complete = True.

This job runs at 03:00 UTC (after oracle finalization at ~00:00 UTC and the
nightly_oracle_sync at 02:00 UTC).  The distribution_complete flag is the
sole write; the classify hot path only reads it.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from celery.schedules import crontab
from sqlalchemy import select
from sqlalchemy.orm import Session

from lemon_ledger.models.classified import L2DecoderConfig
from lemon_ledger.models.token_registry import TokenRegistry
from lemon_ledger.worker import celery_app, resources

log = logging.getLogger(__name__)

# ABI selector for totalSupply() → keccak256("totalSupply()")[0:4]
_TOTAL_SUPPLY_SELECTOR = "0x18160ddd"
# Fraction of max_supply below which we still consider distribution complete
# (handles minor rounding and deflationary burns before LMLN logic lands).
_COMPLETION_EPSILON = Decimal("0.001")  # 0.1% tolerance


def read_total_supply(evm: Any, contract_address: str) -> int | None:
    """Call totalSupply() on *contract_address* and return the raw uint256."""
    try:
        result = evm.eth_call(contract_address, _TOTAL_SUPPLY_SELECTOR)
        if not result or result == "0x":
            return None
        return int(result, 16)
    except Exception:
        log.warning(
            "supply_snapshot: totalSupply call failed",
            extra={"contract": contract_address},
            exc_info=True,
        )
        return None


def check_completion(
    session: Session,
    evm: Any,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Check completion for all L2 tokens and update distribution_complete flags."""
    configs = session.scalars(select(L2DecoderConfig)).all()
    updated: list[str] = []
    skipped: list[str] = []

    for cfg in configs:
        token = session.get(TokenRegistry, cfg.token_id)
        if token is None:
            continue
        if not token.contract_address:
            skipped.append(str(cfg.token_id))
            continue
        if token.max_supply is None:
            skipped.append(str(cfg.token_id))
            continue
        if cfg.distribution_complete:
            continue  # already marked; no second read needed

        supply_raw = read_total_supply(evm, token.contract_address)
        if supply_raw is None:
            skipped.append(str(cfg.token_id))
            continue

        decimals = token.decimals
        supply = Decimal(supply_raw).scaleb(-decimals)
        max_supply = Decimal(str(token.max_supply))
        threshold = max_supply * (1 - _COMPLETION_EPSILON)

        if supply >= threshold:
            if not dry_run:
                cfg.distribution_complete = True
                session.add(cfg)
            updated.append(token.symbol)
            log.info(
                "supply_snapshot: distribution_complete set",
                extra={"symbol": token.symbol, "supply": str(supply), "max": str(max_supply)},
            )

    if not dry_run:
        session.commit()

    return {"updated": updated, "skipped": skipped}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.supply_snapshot",
    bind=True,
)
def supply_snapshot_task(self: Any, *, _evm: Any = None, _session: Any = None) -> dict[str, Any]:
    """Celery beat task: nightly totalSupply snapshot and completion check."""
    from lemon_ledger.config import get_settings
    from lemon_ledger.db.sync_session import worker_session

    settings = get_settings()
    res = resources.ensure(settings)

    if _evm is None:
        from lemon_ledger.clients.evm.provider import build_evm_provider

        rpc_url = getattr(settings, "rpc_url_lemonchain", "")
        if not rpc_url:
            log.warning("supply_snapshot: rpc_url_lemonchain not configured; skipping")
            return {"skipped": "no_rpc_url"}
        _evm = build_evm_provider(rpc_url, http=res.http)

    if _session is not None:
        return check_completion(_session, _evm)

    with worker_session(res.sessionmaker) as session:
        return check_completion(session, _evm)


# Beat schedule: 03:00 UTC daily
celery_app.conf.beat_schedule["nightly-supply-snapshot"] = {
    "task": "lemon_ledger.supply_snapshot",
    "schedule": crontab(hour=3, minute=0),
}
