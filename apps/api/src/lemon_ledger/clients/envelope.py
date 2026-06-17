from lemon_ledger.clients.exceptions import ChainFatalError, ChainRateLimited

_RATE_LIMIT_SIGNALS = ("rate limit", "max rate limit reached", "too many requests")

_EMPTY_MESSAGES = frozenset(
    {
        "no transactions found",
        "no token transfers found",
        "no internal transactions found",
        "no logs found",
        "no records found",
        "no tokens found",
    }
)


def parse_list_envelope(payload: object) -> list[dict[str, str]]:
    """Parse an Etherscan-compatible {status, message, result} envelope.

    Returns a list of string-keyed rows on success, or [] for legitimate empty
    responses.  Raises ChainRateLimited for rate-limit signals and
    ChainFatalError for anything else unexpected.
    """
    if not isinstance(payload, dict):
        raise ChainFatalError(f"Expected dict envelope, got {type(payload).__name__}")

    status: str = str(payload.get("status", ""))
    message: str = str(payload.get("message", "")).lower().strip()
    result = payload.get("result")

    if status == "1":
        if isinstance(result, list):
            return [{str(k): str(v) for k, v in row.items()} for row in result]
        raise ChainFatalError(f"status=1 but result is {type(result).__name__}, expected list")

    # status != "1" — check for rate-limit signals first
    result_str = str(result).lower() if result is not None else ""
    for signal in _RATE_LIMIT_SIGNALS:
        if signal in message or signal in result_str:
            raise ChainRateLimited(f"Rate limited: message={message!r}")

    # Legitimate empty responses
    if message in _EMPTY_MESSAGES or result == [] or result_str.startswith("no "):
        return []

    raise ChainFatalError(
        f"Unrecognised error response: status={status!r} message={message!r} result={result!r}"
    )
