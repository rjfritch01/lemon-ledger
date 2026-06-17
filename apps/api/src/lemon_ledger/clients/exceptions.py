class ChainClientError(Exception):
    """Base for all chain client errors."""


class ChainRequestError(ChainClientError):
    """Retryable transient error: 5xx, timeouts, transport failures."""


class ChainRateLimited(ChainRequestError):
    """Rate-limited by the chain explorer (HTTP 429 or API-level limit)."""


class ChainFatalError(ChainClientError):
    """Non-retryable: malformed envelope, unexpected 4xx."""


class ChainWindowExceeded(ChainClientError):
    """Result window exceeded — narrow the block range and retry."""
