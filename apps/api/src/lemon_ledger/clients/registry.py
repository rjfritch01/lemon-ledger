from lemon_ledger.clients.base import ChainClient
from lemon_ledger.clients.blockscout import build_blockscout_client
from lemon_ledger.clients.exceptions import ChainFatalError
from lemon_ledger.clients.rate_limit import RedisTokenBucket
from lemon_ledger.config import Settings
from lemon_ledger.domain.chains import Chain
from lemon_ledger.worker import Resources


def build_chain_client(chain: Chain, resources: Resources, settings: Settings) -> ChainClient:
    """Dispatch chain → concrete client, wiring rate limiter from resources."""
    if chain == Chain.LEMONCHAIN:
        limiter = RedisTokenBucket(
            resources.redis,
            key=f"ratelimit:{chain}",
            rate_per_sec=settings.explorer_rate_limit_rps,
            burst=settings.explorer_rate_limit_burst,
        )
        return build_blockscout_client(
            str(chain), settings, http=resources.http, rate_limiter=limiter
        )
    if chain == Chain.BSC:
        raise NotImplementedError("BSC client lands in Chat 1.3 Step 2")
    raise ChainFatalError(f"no chain client for {chain!r}")
