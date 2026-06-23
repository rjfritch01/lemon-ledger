from lemon_ledger.workers.celery_app import app


def test_celery_app_name() -> None:
    assert app.main == "lemon_ledger"


def test_celery_broker_set() -> None:
    assert app.conf.broker_url is not None
