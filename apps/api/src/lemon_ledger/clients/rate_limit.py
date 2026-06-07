import time
from typing import Any, Protocol

from redis import Redis

# One atomic Lua script: refill tokens based on elapsed time, then take one.
# Returns 1 if a token was granted, 0 if the bucket is empty.
_LUA_REFILL_AND_TAKE = """
local key     = KEYS[1]
local rate    = tonumber(ARGV[1])
local burst   = tonumber(ARGV[2])
local now     = tonumber(ARGV[3])

local data       = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens     = tonumber(data[1]) or burst
local last_refill = tonumber(data[2]) or now

local elapsed = math.max(0, now - last_refill)
tokens = math.min(burst, tokens + elapsed * rate)

if tokens >= 1 then
    tokens = tokens - 1
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('PEXPIRE', key, 60000)
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('PEXPIRE', key, 60000)
    return 0
end
"""


class RateLimiter(Protocol):
    def acquire(self) -> None: ...


class NullRateLimiter:
    """Never blocks — for unit tests and single-client local use."""

    def acquire(self) -> None:
        pass


class RedisTokenBucket:
    """Global token bucket backed by a single atomic Lua script per acquire().

    Keyed per explorer host so chains share independent buckets.  Uses real
    Redis (not fakeredis) because fakeredis cannot evaluate Lua.
    """

    def __init__(
        self,
        redis: "Redis[Any]",  # type: ignore[type-arg]
        key: str,
        rate_per_sec: float,
        burst: int,
        *,
        poll_interval: float = 0.05,
    ) -> None:
        self._redis = redis
        self._key = key
        self._rate_per_sec = rate_per_sec
        self._burst = burst
        self._poll_interval = poll_interval

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            granted: Any = self._redis.eval(  # nosec B307 – redis.eval() executes a Lua script server-side on Redis, not Python's built-in eval()
                _LUA_REFILL_AND_TAKE,
                1,
                self._key,
                str(self._rate_per_sec),
                str(self._burst),
                str(now),
            )
            if granted == 1:
                return
            time.sleep(self._poll_interval)
