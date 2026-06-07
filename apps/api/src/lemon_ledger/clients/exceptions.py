class BlockscoutError(Exception):
    """Base for all Blockscout client errors."""


class BlockscoutTransientError(BlockscoutError):
    """Retryable: 5xx, 429, timeouts, transport errors, rate-limit responses."""


class BlockscoutResponseError(BlockscoutError):
    """Non-retryable: malformed envelope, unexpected 4xx."""


class BlockscoutWindowExceeded(BlockscoutResponseError):
    """Raised by the paginator when results would exceed the 10k Etherscan window."""
