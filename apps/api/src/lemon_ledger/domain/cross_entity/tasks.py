"""Celery task: run_cross_entity_detection.

Runs the counterparty-detection post-pass for all wallets belonging to a user.
Must execute AFTER classify_wallet and BEFORE apply_lots_for_wallet so that
transfer_resolution signals are present when the lot engine runs.

Idempotent: CTs already carrying transfer_resolution are skipped by detect_for_user.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import sqlalchemy.exc

from lemon_ledger.worker import celery_app, resources

log = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="lemon_ledger.run_cross_entity_detection",
    bind=True,
    autoretry_for=(sqlalchemy.exc.OperationalError,),
    retry_backoff=True,
    max_retries=3,
)
def run_cross_entity_detection_task(
    self: Any,
    user_id: str,
    since_iso: str | None = None,
    *,
    _session: Any = None,
) -> dict[str, Any]:
    """Run cross-entity detection for all wallets of *user_id*.

    *since* is an ISO-format datetime; if omitted the pass covers all time.
    Wire this task between classify_wallet and apply_lots_for_wallet in the
    per-user pipeline so the lot engine sees resolved transfer_resolutions.
    """
    from datetime import datetime

    uid = uuid.UUID(user_id)
    since = datetime.fromisoformat(since_iso) if since_iso else None

    if _session is not None:
        return _run_inner(_session, uid, since)

    from lemon_ledger.config import get_settings
    from lemon_ledger.db.sync_session import worker_session

    settings = get_settings()
    res = resources.ensure(settings)

    with worker_session(res.sessionmaker) as session:
        result = _run_inner(session, uid, since)
        log.info("cross_entity_detection_done", extra={"user_id": user_id, **result})
        return result


def _run_inner(session: Any, user_id: uuid.UUID, since: Any) -> dict[str, Any]:
    from lemon_ledger.domain.cross_entity.detection import detect_for_user

    counts = detect_for_user(session, user_id=user_id, since=since)
    session.commit()
    return {"user_id": str(user_id), **counts}
