"""Build-time-verifiable external price-source identifiers for LEMX.

NOTE: Confirm these IDs against the live APIs before each release:
  CoinGecko: https://api.coingecko.com/api/v3/coins/lemon-2
  CMC:       https://pro-api.coinmarketcap.com/v2/cryptocurrency/info?symbol=LEMX

Only LEMX has external listings.  The 19 L2 ecosystem tokens on Lemonchain
are unlisted on CoinGecko and CMC and have no entries here.  Their prices
are derived from HEXDEX reserve ratios (not yet implemented).
"""

# CoinGecko coin ID for LEMX — primary price source.
LEMX_COINGECKO_ID: str = "lemon-2"

# CoinMarketCap numeric ID for LEMX — secondary price source.
# TODO: populate once the LEMX listing is live on CMC.
LEMX_CMC_ID: int | None = None
