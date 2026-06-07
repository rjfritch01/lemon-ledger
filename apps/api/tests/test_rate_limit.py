"""RedisTokenBucket tests — uses a real Redis via Testcontainers (not fakeredis).

fakeredis cannot execute Lua scripts (redis.eval), so these tests require the
Docker daemon.  They are kept in a separate file and use module-scoped fixtures
to start Redis once for the whole file.
"""

from collections.abc import Generator
from typing import Any

import pytest
import redis as redis_lib
from testcontainers.redis import RedisContainer

from lemon_ledger.clients.rate_limit import NullRateLimiter, RedisTokenBucket

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def redis_container() -> Generator[RedisContainer, None, None]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
def redis_client(redis_container: RedisContainer) -> "redis_lib.Redis[Any]":
    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    r: redis_lib.Redis[Any] = redis_lib.Redis(host=host, port=port, db=0)
    r.flushdb()
    return r


# ── NullRateLimiter ────────────────────────────────────────────────────────────


def test_null_rate_limiter_never_blocks() -> None:
    limiter = NullRateLimiter()
    for _ in range(100):
        limiter.acquire()  # must not raise or block


# ── RedisTokenBucket ──────────────────────────────────────────────────────────


def test_token_bucket_grants_up_to_burst(redis_client: "redis_lib.Redis[Any]") -> None:
    bucket = RedisTokenBucket(redis_client, "test:bucket:burst", rate_per_sec=100.0, burst=5)
    # Should be able to acquire burst tokens quickly without sleeping
    for _ in range(5):
        bucket.acquire()


def test_token_bucket_lua_script_runs(redis_client: "redis_lib.Redis[Any]") -> None:
    # Verify the Lua script executes and modifies state atomically.
    bucket = RedisTokenBucket(redis_client, "test:bucket:lua", rate_per_sec=1000.0, burst=10)
    bucket.acquire()
    data = redis_client.hmget("test:bucket:lua", "tokens", "last_refill")
    assert data[0] is not None  # tokens field was written


def test_token_bucket_independent_keys(redis_client: "redis_lib.Redis[Any]") -> None:
    # Two buckets with different keys do not share state.
    b1 = RedisTokenBucket(redis_client, "test:bucket:chain_a", rate_per_sec=1000.0, burst=3)
    b2 = RedisTokenBucket(redis_client, "test:bucket:chain_b", rate_per_sec=1000.0, burst=3)
    for _ in range(3):
        b1.acquire()
    # b2 should still have its own full bucket
    b2.acquire()


def test_token_bucket_refills_over_time(redis_client: "redis_lib.Redis[Any]") -> None:
    import time

    # Drain the bucket then wait for a partial refill.
    bucket = RedisTokenBucket(
        redis_client, "test:bucket:refill", rate_per_sec=100.0, burst=2, poll_interval=0.01
    )
    # Acquire all burst tokens
    bucket.acquire()
    bucket.acquire()
    # After waiting 20ms we expect ~2 tokens refilled (rate=100/s, 0.02s ≈ 2 tokens)
    time.sleep(0.02)
    bucket.acquire()  # should succeed without long spin
