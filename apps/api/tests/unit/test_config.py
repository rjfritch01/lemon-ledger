def test_get_settings_has_database_url() -> None:
    from lemon_ledger.core.config import get_settings

    s = get_settings()
    assert s.DATABASE_URL is not None
    assert "postgresql" in str(s.DATABASE_URL)


def test_get_settings_is_cached() -> None:
    from lemon_ledger.core.config import get_settings

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_db_pool_defaults() -> None:
    from lemon_ledger.core.config import get_settings

    s = get_settings()
    assert s.DB_POOL_SIZE == 5
    assert s.DB_MAX_OVERFLOW == 10
    assert s.DB_POOL_RECYCLE_SECONDS == 1800
    assert s.DB_ECHO is False
