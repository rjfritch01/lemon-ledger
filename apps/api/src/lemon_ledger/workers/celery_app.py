import os

from celery import Celery

_broker: str = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
_backend: str = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

app: Celery = Celery("lemon_ledger", broker=_broker, backend=_backend)
