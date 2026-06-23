import logging

from lemon_ledger.core.logging import configure_logging


def test_configure_logging_default_level() -> None:
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_debug_level() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    configure_logging()  # restore
