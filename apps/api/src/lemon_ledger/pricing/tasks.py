"""Celery tasks for nightly oracle price synchronisation.

nightly_oracle_sync
-------------------
Runs at 02:00 UTC every day (buffer after midnight finalization) and reads the
last 30 days of daily averages from the oracle contract for every Tier-1
Lemonchain token.  It then upserts those rows into historical_prices.

If the oracle is degraded (paused, emergency, or any feed stale/failing), the
task raises an alert via _alert_if_oracle_degraded.  The alert is intentionally
a hard ERROR log with a structured payload so on-call tooling (e.g. Sentry,
PagerDuty log integration) can trigger on it.  Logging is the write path here;
no additional alerting SDK is wired in this PR.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from celery.schedules import crontab

from lemon_ledger.clients.oracle import OracleClient, OracleTokenNotSupported
from lemon_ledger.pricing.repository import HistoricalPriceRow, upsert_historical_prices
from lemon_ledger.pricing.types import TokenRegistryRepo, TokenRow
from lemon_ledger.worker import celery_app, resources

log = logging.getLogger(__name__)


def _build_row(token: TokenRow, entry: Any, *, chain: str = "lemonchain") -> HistoricalPriceRow:
    return HistoricalPriceRow(
        chain=chain,
        token_id=token.token_id,
        day_timestamp=entry.day_timestamp,
        average_price_usd=entry.average_price,
        data_points=entry.data_points,
        confidence=entry.confidence,
        source="oracle",
    )


def _alert_if_oracle_degraded(oracle: OracleClient) -> None:
    """Log a structured ERROR if the oracle is in a degraded state.

    Conditions that trigger the alert:
    - oracle.paused is True
    - oracle.emergency is True
    - any feed in feeds_ok is False (stale or unreachable)
    """
    health = oracle.get_health()
    issues: list[str] = []
    if health.paused:
        issues.append("oracle paused")
    if health.emergency:
        issues.append("oracle emergency mode")
    failing = [sym for sym, ok in health.feeds_ok.items() if not ok]
    if failing:
        issues.append(f"feeds failing: {failing}")
    if issues:
        log.error(
            "oracle_degraded",
            extra={
                "alert": True,
                "issues": issues,
                "oracle_paused": health.paused,
                "oracle_emergency": health.emergency,
                "oracle_seeding_complete": health.seeding_complete,
                "feeds_ok": health.feeds_ok,
                "ts": datetime.now(tz=UTC).isoformat(),
            },
        )


@celery_app.task(name="lemon_ledger.nightly_oracle_sync")  # type: ignore[untyped-decorator]
def nightly_oracle_sync(
    *,
    _registry: TokenRegistryRepo | None = None,
    _oracle: OracleClient | None = None,
    _session: Any | None = None,
) -> dict[str, Any]:
    """Upsert the last 30 days of oracle daily averages for all Tier-1 tokens.

    Injectable _registry, _oracle, and _session params allow test-time DI
    without touching the worker global or requiring a real DB/Redis.
    """
    from contextlib import contextmanager

    # Resolve oracle and registry — raise early if neither injected nor wired
    if _oracle is None:
        raise RuntimeError(
            "nightly_oracle_sync requires an OracleClient; "
            "wire one via the application factory or inject _oracle in tests."
        )
    if _registry is None:
        raise RuntimeError(
            "nightly_oracle_sync requires a TokenRegistryRepo; "
            "inject _registry in tests or wire via app factory."
        )

    oracle = _oracle
    registry = _registry

    # Session: use injected stub (tests) or build from worker resources
    if _session is not None:

        @contextmanager
        def _session_ctx() -> Any:
            yield _session

        session_ctx = _session_ctx
    else:
        from lemon_ledger.config import get_settings
        from lemon_ledger.db.sync_session import worker_session

        settings = get_settings()
        res = resources.ensure(settings)
        session_ctx = lambda: worker_session(res.sessionmaker)  # noqa: E731

    tokens = registry.tier1_lemonchain()
    total_rows = 0
    skipped: list[str] = []

    with session_ctx() as session:
        for token in tokens:
            try:
                entries = oracle.get_daily_averages_history(token, max_entries=30)
                rows = [_build_row(token, e) for e in entries]
                if rows:
                    upsert_historical_prices(session, rows)
                    total_rows += len(rows)
            except OracleTokenNotSupported:
                log.warning(
                    "oracle_token_not_supported",
                    extra={"symbol": token.symbol, "token_id": token.token_id},
                )
                skipped.append(token.symbol)
                continue

    _alert_if_oracle_degraded(oracle)

    return {
        "tokens_processed": len(tokens) - len(skipped),
        "tokens_skipped": skipped,
        "rows_upserted": total_rows,
    }


# Register in beat schedule (accessed by worker.py via celery_app.conf)
celery_app.conf.beat_schedule["nightly-oracle-sync"] = {
    "task": "lemon_ledger.nightly_oracle_sync",
    "schedule": crontab(hour=2, minute=0),
}
