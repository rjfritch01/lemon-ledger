from decimal import Decimal


def from_token_units(raw: int, decimals: int) -> Decimal:
    """Convert raw integer token units to human-scale Decimal.

    Example: from_token_units(1_000_000, 6) == Decimal("1")
    """
    return Decimal(raw).scaleb(-decimals)


def from_oracle_price(raw: int, oracle_decimals: int) -> Decimal:
    """Convert a raw integer oracle price to a Decimal.

    Example: from_oracle_price(100_000_000, 8) == Decimal("1")
    """
    return Decimal(raw).scaleb(-oracle_decimals)
