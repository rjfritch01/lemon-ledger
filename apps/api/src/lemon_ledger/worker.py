from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import redis as redis_lib
from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init, worker_process_shutdown
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lemon_ledger.config import Settings, get_settings
from lemon_ledger.db.sync_session import build_sync_engine, build_sync_sessionmaker

celery_app: Any = Celery(
    "lemon_ledger",
    broker=get_settings().redis_url,
    backend=get_settings().redis_url,
    include=[
        "lemon_ledger.tasks",
        "lemon_ledger.pricing.tasks",
        "lemon_ledger.classify.tasks",
        "lemon_ledger.jobs.supply_snapshot",
    ],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    beat_schedule={
        "nightly-wallet-sync": {
            "task": "lemon_ledger.sync_all_active_wallets",
            "schedule": crontab(hour=4, minute=0),
        },
    },
)


@dataclasses.dataclass
class Resources:
    engine: Engine
    sessionmaker: sessionmaker[Session]
    redis: Any
    http: httpx.Client


def build_resources(settings: Settings) -> Resources:
    engine = build_sync_engine(settings)
    maker = build_sync_sessionmaker(engine)
    r = redis_lib.Redis.from_url(settings.redis_url)
    http = httpx.Client(timeout=settings.explorer_request_timeout_s)
    return Resources(engine=engine, sessionmaker=maker, redis=r, http=http)


class _LazyResources:
    def __init__(self) -> None:
        self._resources: Resources | None = None

    def ensure(self, settings: Settings) -> Resources:
        if self._resources is None:
            self._resources = build_resources(settings)
        return self._resources

    def dispose(self) -> None:
        if self._resources is not None:
            self._resources.engine.dispose()
            self._resources.http.close()
            self._resources = None


resources = _LazyResources()


@worker_process_init.connect  # type: ignore[untyped-decorator]
def _on_worker_init(**kwargs: Any) -> None:
    resources.ensure(get_settings())


@worker_process_shutdown.connect  # type: ignore[untyped-decorator]
def _on_worker_shutdown(**kwargs: Any) -> None:
    resources.dispose()
