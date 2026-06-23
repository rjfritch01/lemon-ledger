from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://lemon:changeme@localhost:5432/lemon_ledger"
    redis_url: str = "redis://localhost:6379/0"

    explorer_lemonchain_url: str = "https://explorer.lemonchain.io/api"
    explorer_lemonchain_testnet_url: str = "https://explorer-testnet.lemonchain.io/api"
    explorer_request_timeout_s: float = 15.0
    explorer_rate_limit_rps: float = 4.0
    explorer_rate_limit_burst: int = 4
    explorer_page_size: int = 1000
    explorer_max_retries: int = 5

    coingecko_api_key: str | None = None
    cmc_api_key: str | None = None

    oracle_contract_lemonchain: str | None = None  # deployed oracle contract address

    worker_db_pool_size: int = 2
    worker_db_max_overflow: int = 2
    sync_confirmations_lemonchain: int = 12
    sync_block_chunk: int = 100_000
    sync_lock_ttl_s: int = 1800
    sync_cli_wait_timeout_s: int = 600

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
