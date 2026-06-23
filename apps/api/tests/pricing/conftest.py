"""Override the session-autouse migration fixture for pure-Python pricing tests.

The root conftest.py applies apply_migrations autouse to every pytest session,
which requires Docker. Pricing tests are pure-Python and must not need Docker.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def apply_migrations() -> None:
    """No-op override: pricing tests use no DB."""
    return
